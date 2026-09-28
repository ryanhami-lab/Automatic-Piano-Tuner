"""Fictional commanded-step mechanics. Ground truth is evaluator-only."""

from dataclasses import dataclass, field

from pianotuner.domain import finite_number, shift_cents

from .acoustics import synthesize


@dataclass
class Plant:
    target_hz: float = 440.0
    initial_cents: float = -10.0
    seed: int = 7
    tighten_sign: int = 1
    sensitivity: float = 0.225
    backlash_steps: float = 0.0
    unload_shift_cents: float = 0.0
    no_response: bool = False
    slip_cents: float = 0.0
    snr_db: float = 35.0
    inharmonicity: float = 0.0005
    _cents: float = field(init=False, repr=False)
    _last_direction: int = field(default=0, init=False, repr=False)
    _remaining_backlash: float = field(default=0.0, init=False, repr=False)
    _strikes: int = field(default=0, init=False, repr=False)
    _unloaded: bool = field(default=False, init=False, repr=False)
    commanded_position: int = field(default=0, init=False)
    absolute_commanded_steps: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        finite_number(self.target_hz, "target_hz", positive=True)
        self._cents = finite_number(self.initial_cents, "initial_cents")
        finite_number(self.sensitivity, "sensitivity", positive=True)
        finite_number(self.unload_shift_cents, "unload_shift_cents")
        finite_number(self.slip_cents, "slip_cents")
        if type(self.tighten_sign) is not int or self.tighten_sign not in (-1, 1):
            raise ValueError("tighten_sign must be -1 or +1")
        if finite_number(self.backlash_steps, "backlash_steps") < 0:
            raise ValueError("backlash_steps cannot be negative")

    def step(self, signed_steps: int) -> None:
        if type(signed_steps) is not int or signed_steps == 0:
            raise ValueError("signed_steps must be a nonzero integer")
        if self._unloaded:
            raise RuntimeError("The simulated tool is unloaded")
        self.commanded_position += signed_steps
        self.absolute_commanded_steps += abs(signed_steps)
        direction = 1 if signed_steps > 0 else -1
        if direction != self._last_direction:
            self._remaining_backlash = self.backlash_steps
        self._last_direction = direction
        consumed = min(abs(signed_steps), self._remaining_backlash)
        self._remaining_backlash -= consumed
        effective = max(0.0, abs(signed_steps) - consumed)
        if not self.no_response:
            self._cents += direction * self.tighten_sign * effective * self.sensitivity
        if self.slip_cents:
            self._cents += self.slip_cents
            self.slip_cents = 0.0

    def render_strike(self):
        self._strikes += 1
        return synthesize(shift_cents(self.target_hz, self._cents), seed=self.seed * 1009 + self._strikes,
                          snr_db=self.snr_db, inharmonicity=self.inharmonicity)

    def unload(self) -> None:
        if not self._unloaded:
            self._cents += self.unload_shift_cents
            self._unloaded = True

    def evaluator_truth_hz(self) -> float:
        """Scoring only: never provide this object or value to control policy."""
        return shift_cents(self.target_hz, self._cents)
