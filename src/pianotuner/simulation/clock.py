"""Injectable monotonic clocks; simulation never waits in real time."""

import time

from pianotuner.domain import finite_number


class RealClock:
    def now(self) -> float:
        return time.monotonic()

    monotonic = now


class VirtualClock:
    def __init__(self, start: float = 0.0) -> None:
        self._now = finite_number(start, "start")

    def now(self) -> float:
        return self._now

    monotonic = now

    def advance(self, seconds: float) -> float:
        delta = finite_number(seconds, "seconds")
        if delta < 0:
            raise ValueError("A monotonic clock cannot move backward")
        self._now += delta
        return self._now
