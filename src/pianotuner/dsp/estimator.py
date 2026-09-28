"""Targeted first-partial spectral estimator with explicit refusal reasons."""

import math
from dataclasses import dataclass, replace

import numpy as np
from scipy.fft import rfft, rfftfreq
from scipy.signal import find_peaks

from pianotuner.domain import DEFAULTS, Defaults, cents_error, finite_number, shift_cents

from .onset import detect_onset


@dataclass(frozen=True)
class PitchEstimate:
    frequency_hz: float | None
    cents_error: float | None
    accepted: bool
    reasons: tuple[str, ...]
    rms_dbfs: float
    peak_snr_db: float | None = None
    clipped_fraction: float = 0.0
    frame_count: int = 1
    spread_cents: float | None = None

    @property
    def first_partial_hz(self) -> float | None:
        return self.frequency_hz


class PitchEstimator:
    def __init__(self, defaults: Defaults = DEFAULTS) -> None:
        self.defaults = defaults
        self._window = np.hanning(defaults.frame_length)
        self._frequencies = rfftfreq(defaults.fft_length, 1 / defaults.sample_rate)

    def estimate(self, samples, target_hz: float, sample_rate: int = DEFAULTS.sample_rate) -> PitchEstimate:
        target = finite_number(target_hz, "target_hz", positive=True)
        x = np.asarray(samples, dtype=np.float64)
        d = self.defaults
        if type(sample_rate) is not int or sample_rate != d.sample_rate:
            return PitchEstimate(None, None, False, ("UNSUPPORTED_SAMPLE_RATE",), -math.inf)
        if x.ndim != 1 or len(x) != d.frame_length or not np.all(np.isfinite(x)):
            return PitchEstimate(None, None, False, ("INVALID_FRAME",), -math.inf)
        clip = float(np.mean(np.abs(x) >= 0.999))
        centered = x - np.mean(x)
        rms = float(np.sqrt(np.mean(centered ** 2)))
        rms_db = 20 * math.log10(max(rms, 1e-300))
        reasons: list[str] = []
        if rms_db < d.minimum_rms_dbfs:
            reasons.append("LOW_LEVEL")
        if clip > d.maximum_clipped_fraction:
            reasons.append("CLIPPED")
        if reasons:
            return PitchEstimate(None, None, False, tuple(reasons), rms_db, clipped_fraction=clip)
        magnitude = np.abs(rfft(centered * self._window, n=d.fft_length))
        freqs = self._frequencies
        lower = shift_cents(target, -d.capture_cents)
        upper = shift_cents(target, d.capture_cents)
        peaks, _ = find_peaks(magnitude)
        in_band = peaks[(freqs[peaks] >= lower) & (freqs[peaks] <= upper)]
        if not len(in_band):
            return PitchEstimate(None, None, False, ("NO_FIRST_PARTIAL",), rms_db, clipped_fraction=clip)
        candidate = int(in_band[np.argmax(magnitude[in_band])])
        height = float(magnitude[candidate])
        # A significant first partial must exist; spectral noise near a missing
        # fundamental must not be mistaken for a supported candidate.
        global_peak = float(np.max(magnitude[1:]))
        if height < 0.15 * global_peak:
            reasons.append("WEAK_FIRST_PARTIAL")
        main_lobe_hz = 4.0 * sample_rate / d.frame_length
        background = (freqs >= target * 0.65) & (freqs <= target * 1.4)
        for peak in peaks[(freqs[peaks] >= target * 0.65) & (freqs[peaks] <= target * 1.4)]:
            if magnitude[peak] >= height * 0.1:
                background &= np.abs(freqs - freqs[peak]) > main_lobe_hz
        floor = float(np.median(magnitude[background])) if np.any(background) else 0.0
        snr = 20 * math.log10(max(height, 1e-300) / max(floor, 1e-300))
        if snr < d.minimum_peak_snr_db:
            reasons.append("LOW_PEAK_SNR")
        others = in_band[(in_band != candidate) & (magnitude[in_band] >= height * 0.25)]
        if len(others):
            reasons.append("AMBIGUOUS_FIRST_PARTIAL")
        lower_peaks = peaks[(freqs[peaks] >= target * 0.35) & (freqs[peaks] <= target * 0.75)]
        if np.any(magnitude[lower_peaks] >= height * 0.3):
            reasons.append("SUSPICIOUS_LOWER_PARTIAL")
        logs = np.log(np.maximum(magnitude[candidate - 1:candidate + 2], 1e-300))
        denominator = float(logs[0] - 2 * logs[1] + logs[2])
        if denominator >= -1e-15:
            return PitchEstimate(None, None, False, tuple(reasons + ["INVALID_INTERPOLATION"]), rms_db, snr, clip)
        displacement = float(0.5 * (logs[0] - logs[2]) / denominator)
        if abs(displacement) > 0.5:
            return PitchEstimate(None, None, False, tuple(reasons + ["INVALID_INTERPOLATION"]), rms_db, snr, clip)
        frequency = (candidate + displacement) * sample_rate / d.fft_length
        error = cents_error(frequency, target)
        if abs(error) > d.capture_cents:
            reasons.append("OUT_OF_CAPTURE_BAND")
        return PitchEstimate(float(frequency), error, not reasons, tuple(reasons), rms_db, snr, clip)

    def estimate_strike(self, samples, target_hz: float, sample_rate: int = DEFAULTS.sample_rate) -> PitchEstimate:
        x = np.asarray(samples, dtype=np.float64)
        onset = detect_onset(x, sample_rate)
        if onset is None:
            return PitchEstimate(None, None, False, ("NO_ONSET",), -math.inf, frame_count=0)
        start = onset + math.ceil(self.defaults.attack_exclusion_s * sample_rate)
        estimates = [self.estimate(x[start + i * self.defaults.hop:start + i * self.defaults.hop + self.defaults.frame_length],
                                   target_hz, sample_rate) for i in range(self.defaults.stable_frames)]
        if any(not estimate.accepted for estimate in estimates):
            reasons = tuple(dict.fromkeys(reason for estimate in estimates for reason in estimate.reasons))
            return replace(estimates[-1], accepted=False, reasons=reasons, frame_count=len(estimates))
        errors = [estimate.cents_error for estimate in estimates if estimate.cents_error is not None]
        spread = float(np.ptp(errors))
        frequency = float(np.median([estimate.frequency_hz for estimate in estimates]))
        accepted = spread <= self.defaults.stability_cents
        return replace(estimates[-1], frequency_hz=frequency, cents_error=cents_error(frequency, target_hz),
                       accepted=accepted, reasons=() if accepted else ("UNSTABLE",), frame_count=len(estimates), spread_cents=spread)
