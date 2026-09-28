"""Timed microphone fakes exercise the live path without opening any device."""

import math
from dataclasses import replace

import numpy as np
import pytest

from pianotuner.adapters.audio import AudioBlock
from pianotuner.control import SessionController, State
from pianotuner.domain import DEFAULTS, shift_cents
from pianotuner.dsp import detect_onset
from pianotuner.runtime.hardware import LiveFramePipeline, validate_route
from pianotuner.simulation.acoustics import synthesize


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeActuator:
    def __init__(self):
        self.moves = []
        self.pending = None
        self.disabled_confirmed = True

    def arm(self, now):
        self.disabled_confirmed = False

    def move(self, steps, rate_hz, max_duration_ms, now):
        self.moves.append(dict(steps=steps, rate_hz=rate_hz, max_duration_ms=max_duration_ms, now=now))
        self.pending = dict(id=len(self.moves), steps=steps, due=now + max_duration_ms / 1000, ack=False)
        self.disabled_confirmed = False
        return len(self.moves)

    def poll(self, now):
        events = []
        if self.pending:
            if not self.pending["ack"]:
                events.append({"op": "ACK", "command": "MOVE", "id": self.pending["id"]})
                self.pending["ack"] = True
            if now >= self.pending["due"]:
                events.append({"op": "DONE", "id": self.pending["id"],
                               "emitted_steps": abs(self.pending["steps"]), "enabled": False})
                self.pending = None
                self.disabled_confirmed = True
        return events

    def heartbeat(self, now):
        pass

    def disarm(self, now):
        self.disabled_confirmed = True

    def stop(self, now):
        self.disabled_confirmed = True
        self.pending = None


class CaptureHarness:
    def __init__(self):
        self.clock = FakeClock()
        self.actuator = FakeActuator()
        self.controller = SessionController(440, self.actuator)
        self.controller.start(0)
        self.pipeline = LiveFramePipeline(self.controller, clock=self.clock)
        self.index = 0
        self.time = .1

    def block(self, samples=None, **changes):
        samples = np.zeros(DEFAULTS.hop) if samples is None else samples
        end = self.time + len(samples) / DEFAULTS.sample_rate
        block = AudioBlock(np.asarray(samples), DEFAULTS.sample_rate, self.index, self.index + len(samples),
                           self.time, end, .002, "device-source-1", False, False)
        self.index += len(samples)
        self.time = end
        return replace(block, **changes)

    def feed(self, samples=None, *, delay=0, **changes):
        block = self.block(samples, **changes)
        self.clock.now = max(self.clock.now, self.time + delay, (block.capture_end or 0) + delay)
        self.pipeline.feed(block, self.clock.now)
        return block

    def audio(self, samples, **changes):
        for offset in range(0, len(samples), DEFAULTS.hop):
            self.feed(samples[offset:offset + DEFAULTS.hop], **changes)


def test_live_contiguous_three_frame_decision_uses_real_audio():
    h = CaptureHarness()
    h.audio(synthesize(shift_cents(440, -10), seed=7))
    assert len(h.actuator.moves) == 1
    assert h.actuator.moves[0]["steps"] > 0
    assert h.controller.accepted_frames == 3
    assert h.controller.strike_id is None


@pytest.mark.parametrize("changes,reason", [
    ({"gap": True}, "AUDIO_DISCONTINUITY"),
    ({"overflow": True}, "AUDIO_DISCONTINUITY"),
    ({"timing_uncertainty_s": math.inf}, "UNKNOWN_CAPTURE_TIMING"),
    ({"capture_start": None, "capture_end": None}, "UNKNOWN_CAPTURE_TIMING"),
    ({"sample_rate": 44100}, "INVALID_AUDIO_BLOCK"),
    ({"sample_end": 1}, "INVALID_AUDIO_BLOCK"),
    ({"timing_uncertainty_s": -.01}, "UNKNOWN_CAPTURE_TIMING"),
])
def test_bad_capture_refuses_motion(changes, reason):
    h = CaptureHarness()
    h.feed(**changes)
    assert h.controller.quality == reason
    assert not h.actuator.moves
    assert h.pipeline.origin is None


def test_capture_gap_discards_partially_measured_strike():
    h = CaptureHarness()
    audio = synthesize(shift_cents(440, -10))
    h.audio(audio[:43200])
    assert h.controller.accepted_frames == 1
    h.feed(audio[43200:48000], gap=True)
    h.audio(audio[48000:])
    assert not h.actuator.moves
    assert h.controller.frames == []
    assert h.controller.state == State.WAIT_STRIKE
    h.feed()
    h.audio(synthesize(shift_cents(440, -10), seed=4))
    assert len(h.actuator.moves) == 1


def test_contiguous_indices_cannot_hide_changed_capture_clock():
    h = CaptureHarness()
    h.feed()
    h.feed(capture_start=.25, capture_end=.35)
    assert h.controller.quality == "CAPTURE_CLOCK_DISCONTINUITY"
    assert h.pipeline.origin is None


def test_stale_blocks_never_accumulate_a_movable_strike():
    h = CaptureHarness()
    h.audio(synthesize(shift_cents(440, -10)), delay=.3)
    assert h.controller.quality == "STALE_AUDIO"
    assert h.controller.accepted_frames == 0
    assert not h.actuator.moves


def test_changed_source_epoch_clears_a_pending_strike():
    h = CaptureHarness()
    audio = synthesize(shift_cents(440, -10))
    h.audio(audio[:43200])
    assert h.controller.frames
    h.feed(audio[43200:48000], source_epoch="device-source-2")
    assert h.controller.frames == []
    assert h.controller.strike_id is None
    assert not h.actuator.moves


def test_processing_time_counts_against_freshness():
    h = CaptureHarness()
    original = h.pipeline.estimator

    class SlowEstimator:
        def estimate(self, *args):
            result = original.estimate(*args)
            h.clock.now += .3
            return result

    h.pipeline.estimator = SlowEstimator()
    h.audio(synthesize(shift_cents(440, -10)))
    assert not h.actuator.moves
    assert h.controller.accepted_frames == 0


def test_motor_noise_and_old_epoch_cannot_trigger_second_move():
    h = CaptureHarness()
    h.audio(synthesize(shift_cents(440, -10)))
    assert len(h.actuator.moves) == 1
    h.audio(np.sin(2 * np.pi * 440 * np.arange(48000) / 48000) * .2)
    assert h.controller.motion_epoch == 1
    assert len(h.actuator.moves) == 1
    h.feed()
    h.audio(synthesize(shift_cents(440, -8.2), seed=4))
    assert len(h.actuator.moves) == 2


def test_live_verification_requires_four_real_new_onsets_and_later_time():
    h = CaptureHarness()
    h.audio(synthesize(440))
    assert h.controller.state == State.VERIFY_UNLOADED
    assert not h.actuator.moves
    assert h.controller.confirm_unloaded(h.clock.now)
    for seed in range(1, 4):
        h.feed()
        h.audio(synthesize(440, seed=seed))
        assert len(h.controller.verification) == seed
    assert h.controller.outcome is None
    # A well formed but early fourth strike cannot complete verification.
    h.feed()
    h.audio(synthesize(440, seed=4))
    assert len(h.controller.verification) == 3
    due = h.controller.unloaded_at + DEFAULTS.later_check_s
    while h.time < due + .1:
        h.feed()
    h.audio(synthesize(440, seed=5))
    assert h.controller.outcome == "SIM_VERIFIED"
    assert len({v["strike_id"] for v in h.controller.verification}) == 4
    assert h.controller.verification[-1]["time_s"] >= due


def test_ringing_then_quiet_can_establish_a_new_onset():
    ringing = .2 * np.sin(2 * np.pi * 440 * np.arange(9600) / 48000)
    samples = np.concatenate((ringing, np.zeros(4800), synthesize(440)))
    onset = detect_onset(samples)
    assert onset is not None
    assert onset >= 14400


@pytest.mark.parametrize("mode,source,actuator", [
    ("HARDWARE", "wav", "serial"), ("SIMULATION", "generated", "serial"),
    ("FILE_ANALYSIS", "wav", "loopback"), ("LIVE_MONITOR", "microphone", "serial"),
])
def test_prohibited_live_routes(mode, source, actuator):
    with pytest.raises(ValueError):
        validate_route(mode, source, actuator)
