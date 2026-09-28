"""Musical units. Positive cents always means sharp."""

import math
import re
from dataclasses import dataclass
from hashlib import sha256


def finite_number(value: float, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")  # noqa: TRY004 - one validation error contract
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else ""))
    return result


def midi_to_hz(midi: int) -> float:
    if type(midi) is not int or not 0 <= midi <= 127:
        raise ValueError("MIDI note must be an integer from 0 through 127")
    return 440.0 * 2 ** ((midi - 69) / 12)


def cents_error(measured_hz: float, target_hz: float) -> float:
    measured = finite_number(measured_hz, "measured_hz", positive=True)
    target = finite_number(target_hz, "target_hz", positive=True)
    return 1200.0 * (math.log2(measured) - math.log2(target))


def shift_cents(hz: float, cents: float) -> float:
    frequency = finite_number(hz, "hz", positive=True)
    displacement = finite_number(cents, "cents")
    try:
        result = frequency * 2 ** (displacement / 1200.0)
    except OverflowError as exc:
        raise ValueError("shifted frequency outside finite positive range") from exc
    return finite_number(result, "shifted frequency", positive=True)


def steps_to_degrees(
    commanded_steps: int,
    full_steps_per_motor_revolution: int,
    microsteps_per_full_step: int,
    motor_revolutions_per_output_revolution: float,
) -> float:
    if type(commanded_steps) is not int:
        raise ValueError("commanded_steps must be an integer")
    for name, value in (("full steps", full_steps_per_motor_revolution),
                        ("microsteps", microsteps_per_full_step)):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    ratio = finite_number(motor_revolutions_per_output_revolution, "gear ratio", positive=True)
    return 360.0 * commanded_steps / (full_steps_per_motor_revolution * microsteps_per_full_step * ratio)


@dataclass(frozen=True)
class Target:
    label: str
    target_hz: float
    source: str = "explicit single-string reference"

    def __post_init__(self) -> None:
        hz = finite_number(self.target_hz, "target_hz", positive=True)
        if not 220 <= hz <= 440:
            raise ValueError("Target must be within A3–A4 (220–440 Hz)")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("Target label is required")

    @property
    def hz(self) -> float:
        return self.target_hz

    @property
    def target_id(self) -> str:
        value = f"{self.label}:{self.target_hz:.17g}:{self.source}"
        return sha256(value.encode("utf-8")).hexdigest()[:32]

    @classmethod
    def named(cls, note: str | int) -> "Target":
        if type(note) is int:
            midi = note
        elif isinstance(note, str):
            match = re.fullmatch(r"([A-Ga-g])([#b]?)([0-9])", note.strip())
            if not match:
                raise ValueError("Use a note such as A3, C#4, or Bb3")
            letter, accidental, octave = match.groups()
            semitone = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[letter.upper()]
            midi = (int(octave) + 1) * 12 + semitone + {"": 0, "#": 1, "b": -1}[accidental]
        else:
            raise ValueError("Named target requires a note name or integer MIDI number")
        if not 57 <= midi <= 69:
            raise ValueError("Named targets support MIDI 57–69 inclusive")
        names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
        return cls(f"{names[midi % 12]}{midi // 12 - 1}", midi_to_hz(midi), "12-TET A4=440 Hz")

    @classmethod
    def from_hz(cls, hz: float) -> "Target":
        value = finite_number(hz, "hz", positive=True)
        return cls(f"{value:g} Hz", value, "custom first-partial reference")
