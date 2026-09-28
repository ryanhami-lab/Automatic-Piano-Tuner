"""AT02/AT03 audio gates and hardware-free capture-adapter tests."""

import json
import math
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.io import wavfile

from pianotuner.adapters.audio import MicrophoneSource, load_wav
from pianotuner.domain import DEFAULTS, cents_error, midi_to_hz, shift_cents
from pianotuner.dsp import PitchEstimator, detect_onset
from pianotuner.simulation.acoustics import integrate_phase, partial_frequency, synthesize
from pianotuner.simulation.plant import Plant


@pytest.mark.parametrize("midi", [57, 63, 69])
@pytest.mark.parametrize("offset", [-20, 0, 20])
@pytest.mark.parametrize("b", [0, 0.001])
def test_nominal_first_partial(midi, offset, b):
    target = midi_to_hz(midi)
    f1 = shift_cents(target, offset)
    samples = synthesize(f1, seed=3, inharmonicity=b, first_amplitude=.35, snr_db=20)
    estimate = PitchEstimator().estimate_strike(samples, target)
    assert estimate.accepted, estimate
    assert abs(cents_error(estimate.frequency_hz, f1)) < 1
    assert estimate.frame_count == 3


def frame(samples):
    return samples[8640:8640 + DEFAULTS.frame_length]


def test_silence_low_level_and_clipping():
    estimator = PitchEstimator()
    assert "LOW_LEVEL" in estimator.estimate(np.zeros(DEFAULTS.frame_length), 440).reasons
    tone = frame(synthesize(440, partial_count=1))
    assert "LOW_LEVEL" in estimator.estimate(tone * .001, 440).reasons
    assert "CLIPPED" in estimator.estimate(np.clip(tone * 10, -1, 1), 440).reasons


@pytest.mark.parametrize("frequency", [220, 400, 466.164, 880])
def test_wrong_notes_and_out_of_band(frequency):
    estimate = PitchEstimator().estimate(frame(synthesize(frequency)), 440)
    assert not estimate.accepted, estimate


def test_missing_first_partial_refused_even_with_strong_harmonics():
    for seed in range(4):
        estimate = PitchEstimator().estimate_strike(synthesize(440, seed=seed, first_amplitude=0, snr_db=20), 440)
        assert not estimate.accepted


def test_ambiguous_resolvable_peak_refused():
    first = frame(synthesize(435, partial_count=1, snr_db=60))
    second = frame(synthesize(445, seed=5, partial_count=1, snr_db=60))
    estimate = PitchEstimator().estimate((first + second) * .5, 440)
    assert "AMBIGUOUS_FIRST_PARTIAL" in estimate.reasons


def test_lower_fundamental_refused_as_octave():
    samples = frame(synthesize(220, first_amplitude=1, inharmonicity=0))
    assert "SUSPICIOUS_LOWER_PARTIAL" in PitchEstimator().estimate(samples, 440).reasons


@pytest.mark.parametrize("samples", [np.zeros((32768, 2)), np.zeros(10), np.full(32768, np.nan)])
def test_invalid_frames(samples):
    assert not PitchEstimator().estimate(samples, 440).accepted


def test_wrong_sample_rate_is_not_relabelled():
    result = PitchEstimator().estimate(np.ones(32768), 440, sample_rate=44100)
    assert result.reasons == ("UNSUPPORTED_SAMPLE_RATE",)


def test_onset_requires_energy_rise():
    assert detect_onset(np.zeros(57600)) is None
    t = np.arange(57600) / 48000
    assert detect_onset(.2 * np.sin(2 * np.pi * 440 * t)) is None
    onset = detect_onset(synthesize(440))
    assert 1440 <= onset <= 2880


def test_normalized_partial_relation_and_seed():
    for b in (0, .0001, .001):
        assert partial_frequency(440, 1, b) == 440
    assert partial_frequency(440, 2, .001) > 880
    assert np.array_equal(synthesize(440, seed=1), synthesize(440, seed=1))
    assert not np.array_equal(synthesize(440, seed=1), synthesize(440, seed=2))


def test_continuous_phase_integrates_frequency_changes():
    frequencies = np.linspace(430, 450, 1000)
    whole, _ = integrate_phase(frequencies, 48000)
    a, end = integrate_phase(frequencies[:300], 48000)
    b, _ = integrate_phase(frequencies[300:], 48000, end)
    assert np.allclose(np.sin(whole), np.sin(np.concatenate((a, b))), atol=1e-12)


def test_plant_motion_is_measured_from_audio():
    plant = Plant(initial_cents=-10, tighten_sign=-1, backlash_steps=1, unload_shift_cents=.5)
    estimator = PitchEstimator()
    before = estimator.estimate_strike(plant.render_strike(), 440)
    plant.step(-8)
    after = estimator.estimate_strike(plant.render_strike(), 440)
    assert after.cents_error - before.cents_error == pytest.approx(7 * .225, abs=.05)
    plant.unload()
    final = estimator.estimate_strike(plant.render_strike(), 440)
    assert final.cents_error - after.cents_error == pytest.approx(.5, abs=.05)
    with pytest.raises(RuntimeError):
        plant.step(1)


@pytest.mark.parametrize("rate", [44100, 48000])
def test_wav_resampling_keeps_pitch(tmp_path, rate):
    x = synthesize(437, sample_rate=rate, sample_count=round(rate * 1.2))
    path = tmp_path / "tone.wav"
    wavfile.write(path, rate, (x * 32767).astype(np.int16))
    loaded = load_wav(path)
    estimate = PitchEstimator().estimate_strike(loaded.samples, 440)
    assert loaded.sample_rate == 48000
    assert loaded.source_sample_rate == rate
    assert estimate.accepted
    assert abs(cents_error(estimate.frequency_hz, 437)) < 1


def test_explicit_multichannel_and_pcm_normalization(tmp_path):
    path = tmp_path / "stereo.wav"
    wavfile.write(path, 48000, np.array([[0, 128], [255, 0]], dtype=np.uint8))
    with pytest.raises(ValueError, match="explicit"):
        load_wav(path)
    assert load_wav(path, channel=1).samples.tolist() == [0, -1]
    with pytest.raises(ValueError):
        load_wav(path, channel=True)
    wavfile.write(path, 48000, np.array([-2147483648, 1073741824], dtype=np.int32))
    assert load_wav(path).samples.tolist() == [-1, .5]


class FakeStream:
    def __init__(self, **kwargs):
        self.callback = kwargs["callback"]
        self.samplerate = kwargs["samplerate"]
        self.time = 100.0
        self.closed = False

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        self.closed = True

    def emit(self, status=False):
        self.callback(np.ones((4800, 1), dtype=np.float32) * .1, 4800,
                      SimpleNamespace(inputBufferAdcTime=99.9, currentTime=100.0), status)


def test_microphone_fake_ring_timing_overflow_and_close():
    source = MicrophoneSource("fake", stream_factory=FakeStream, clock=lambda: 200.0,
                              max_blocks=2, timing_uncertainty_s=.01)
    source.start()
    stream = source._stream
    stream.emit()
    block = source.read()
    assert block.timing_known
    assert block.capture_start == pytest.approx(199.9)
    assert block.capture_end == pytest.approx(200)
    assert block.timing_uncertainty_s == .01
    stream.emit(True)
    assert source.read().overflow
    stream.emit()
    stream.emit()
    stream.emit()  # bounded ring drops this block
    source.read()
    stream.emit()
    source.read()
    assert source.read().gap
    source.close()
    assert stream.closed


def test_microphone_timing_is_unknown_until_commissioned():
    with MicrophoneSource("fake", stream_factory=FakeStream, clock=lambda: 200.0) as source:
        source._stream.emit()
        block = source.read()
        assert not block.timing_known
        assert math.isinf(block.timing_uncertainty_s)


def test_microphone_restart_invalidates_old_capture():
    source = MicrophoneSource("fake", stream_factory=FakeStream, clock=lambda: 200.0)
    source.start()
    old_epoch = source.source_epoch
    source._stream.emit()
    source.close()
    source.start()
    assert source.source_epoch != old_epoch
    assert source.read() is None
    source.close()


def test_packaged_nominal_manifest_matches_reviewed_fixture():
    fixture = Path(__file__).parent / "fixtures" / "audio_manifest.json"
    packaged = files("pianotuner").joinpath("data/audio_manifest.json")
    assert fixture.read_bytes() == packaged.read_bytes()
    cases = json.loads(packaged.read_text(encoding="utf-8"))["cases"]
    coordinates = {(c["midi"], c["offset_cents"], c["inharmonicity"], c["seed"]) for c in cases}
    assert len(cases) == len(coordinates) == 1040


def test_incomplete_benchmark_cannot_claim_full_at02_pass(tmp_path):
    from pianotuner.reporting.audio_benchmark import run_audio_benchmark
    packaged = json.loads(files("pianotuner").joinpath("data/audio_manifest.json").read_text(encoding="utf-8"))
    packaged["cases"] = packaged["cases"][:1]
    manifest = tmp_path / "subset.json"
    manifest.write_text(json.dumps(packaged), encoding="utf-8")
    summary = run_audio_benchmark(tmp_path / "result", manifest)
    assert summary["total"] == 1
    assert not summary["passed"]
