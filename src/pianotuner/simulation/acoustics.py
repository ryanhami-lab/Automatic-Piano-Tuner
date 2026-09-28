"""Deterministic audio fixtures, with actual first partial f1 as the input.

The generator is a test model, not a calibrated piano model. New strikes
randomize phase; continuous audio uses integrate_phase without phase resets.
"""

import numpy as np
from numpy.typing import NDArray

from pianotuner.domain import DEFAULTS, finite_number

MODEL_VERSION = "normalized-partials-v1"


def partial_frequency(frequency_hz: float, partial: int, inharmonicity: float) -> float:
    f1 = finite_number(frequency_hz, "frequency_hz", positive=True)
    b = finite_number(inharmonicity, "inharmonicity")
    if type(partial) is not int or partial < 1 or b < 0:
        raise ValueError("Partial must be positive integer and inharmonicity nonnegative")
    return float(partial * f1 * np.sqrt((1 + b * partial * partial) / (1 + b)))


def integrate_phase(frequencies_hz: NDArray[np.float64], sample_rate: int, phase: float = 0.0) -> tuple[NDArray[np.float64], float]:
    """Return each sample's phase and the next phase for chunk continuation."""
    frequencies = np.asarray(frequencies_hz, dtype=np.float64)
    if type(sample_rate) is not int or sample_rate <= 0:
        raise ValueError("sample_rate must be a positive integer")
    if frequencies.ndim != 1 or not np.all(np.isfinite(frequencies)) or np.any(frequencies <= 0):
        raise ValueError("frequencies must be a finite positive vector")
    initial = finite_number(phase, "phase")
    increments = 2 * np.pi * frequencies / sample_rate
    phases = initial + np.concatenate(([0.0], np.cumsum(increments[:-1]))) if frequencies.size else frequencies.copy()
    next_phase = float((initial + np.sum(increments)) % (2 * np.pi))
    return phases, next_phase


def synthesize(
    frequency_hz: float,
    seed: int = 0,
    sample_count: int = 57_600,
    sample_rate: int = DEFAULTS.sample_rate,
    inharmonicity: float = 0.0005,
    snr_db: float = 35.0,
    first_amplitude: float = 0.5,
    partial_count: int = 8,
    onset_s: float = 0.03,
) -> NDArray[np.float64]:
    """Unclipped decaying strike. SNR is exact over the post-attack interval.

    Partial 2 is normalized to amplitude 1. Others are at most 1; the first
    decays no faster than upper partials so its specified ratio is retained.
    """
    f1 = finite_number(frequency_hz, "frequency_hz", positive=True)
    b = finite_number(inharmonicity, "inharmonicity")
    amplitude = finite_number(first_amplitude, "first_amplitude")
    snr = finite_number(snr_db, "snr_db")
    onset = finite_number(onset_s, "onset_s")
    if type(sample_count) is not int or sample_count < 1 or type(sample_rate) is not int or sample_rate <= 0:
        raise ValueError("sample_count/sample_rate must be positive integers")
    if type(partial_count) is not int or partial_count < 1 or b < 0 or amplitude < 0 or onset < 0:
        raise ValueError("Invalid acoustic parameters")
    if f1 >= sample_rate / 2:
        raise ValueError("First partial must be below Nyquist")
    rng = np.random.default_rng(seed)
    t = np.arange(sample_count, dtype=np.float64) / sample_rate - onset
    positive_t = np.maximum(t, 0)
    attack = np.where(t >= 0, -np.expm1(-positive_t / 0.012), 0.0)
    output = np.zeros(sample_count, dtype=np.float64)
    base_decay = rng.uniform(1.5, 2.5)
    for k in range(1, partial_count + 1):
        frequency = partial_frequency(f1, k, b)
        if frequency >= sample_rate / 2:
            break
        amp = amplitude if k == 1 else (1.0 if k == 2 else rng.uniform(0.2, 0.8) / (k / 2))
        decay = base_decay if k == 1 else base_decay / rng.uniform(1.0, 1.8)
        phase = rng.uniform(-np.pi, np.pi)
        output += amp * attack * np.exp(-positive_t / decay) * np.sin(2 * np.pi * frequency * t + phase)
    noise = rng.normal(size=sample_count)
    segment_start = min(int((onset + DEFAULTS.attack_exclusion_s) * sample_rate), sample_count - 1)
    signal_rms = np.sqrt(np.mean(output[segment_start:] ** 2))
    noise_rms = np.sqrt(np.mean(noise[segment_start:] ** 2))
    noise *= signal_rms / (10 ** (snr / 20) * max(noise_rms, 1e-20))
    output += noise
    # Scaling preserves both frequency, amplitude ratios, and SNR.
    peak = float(np.max(np.abs(output)))
    return output * (0.85 / peak) if peak else output
