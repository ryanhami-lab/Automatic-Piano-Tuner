"""AT01: independent unit/sign/boundary checks."""

import math
from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from pianotuner.domain import DEFAULTS, Target, cents_error, midi_to_hz, shift_cents, steps_to_degrees
from pianotuner.simulation.clock import VirtualClock


def test_named_notes_and_sign_convention():
    assert Target.named("A4").hz == 440
    assert Target.named("A3").hz == 220
    assert Target.named("Bb3") == Target.named("A#3")
    assert midi_to_hz(60) == pytest.approx(261.6255653005986)
    assert cents_error(880, 440) == pytest.approx(1200)
    assert cents_error(220, 440) == pytest.approx(-1200)


def test_seeded_frequency_cents_roundtrips():
    rng = np.random.default_rng(147)
    for hz, cents in zip(rng.uniform(20, 20_000, 1000), rng.uniform(-2400, 2400, 1000), strict=True):
        assert cents_error(shift_cents(float(hz), float(cents)), float(hz)) == pytest.approx(cents, abs=1e-10)


@pytest.mark.parametrize("invalid", [0, -1, float("nan"), float("inf"), -float("inf"), True, "440"])
def test_invalid_frequencies(invalid):
    with pytest.raises(ValueError):
        cents_error(invalid, 440)
    with pytest.raises(ValueError):
        Target.from_hz(invalid)


@pytest.mark.parametrize("note", ["C3", "A#4", "bad", 56, 70, True, 57.0])
def test_target_out_of_range(note):
    with pytest.raises(ValueError):
        Target.named(note)


def test_target_boundaries_and_identity():
    assert Target.from_hz(220).hz == 220
    assert Target.from_hz(440).hz == 440
    for hz in (219.999, 440.001):
        with pytest.raises(ValueError):
            Target.from_hz(hz)
    assert Target.named("A4").target_id == Target.named(69).target_id
    assert Target.named("A3").target_id != Target.named("A4").target_id
    with pytest.raises(FrozenInstanceError):
        Target.named("A3").target_hz = 440


def test_commanded_angle_is_ideal_unit_conversion():
    assert steps_to_degrees(200, 200, 1, 1) == 360
    assert steps_to_degrees(-100, 200, 16, 10) == -1.125
    with pytest.raises(ValueError):
        steps_to_degrees(True, 200, 16, 10)
    with pytest.raises(ValueError):
        steps_to_degrees(1, 0, 16, 10)


def test_default_units_and_virtual_time():
    assert DEFAULTS.frame_length / DEFAULTS.sample_rate == pytest.approx(0.6826666666667)
    assert DEFAULTS.hop / DEFAULTS.sample_rate == 0.1
    clock = VirtualClock()
    assert clock.advance(60) == 60
    assert clock.now() == clock.monotonic() == 60
    with pytest.raises(ValueError):
        clock.advance(-0.1)
    with pytest.raises(ValueError):
        clock.advance(math.nan)
