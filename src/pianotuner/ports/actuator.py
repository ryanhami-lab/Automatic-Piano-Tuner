from typing import Protocol


class ActuatorPort(Protocol):
    def arm(self, now: float) -> None: ...
    def move(self, steps: int, rate_hz: int, max_duration_ms: int, now: float) -> None: ...
    def poll(self, now: float) -> list[dict]: ...
    def heartbeat(self, now: float) -> None: ...
    def disarm(self, now: float) -> None: ...
    def stop(self, now: float) -> None: ...


class NullActuator:
    """Analysis modes cannot accidentally route motion to a device."""
    def arm(self, now: float) -> None:
        raise RuntimeError("Motion is forbidden in analysis modes")

    def move(self, steps: int, rate_hz: int, max_duration_ms: int, now: float) -> None:
        raise RuntimeError("Motion is forbidden in analysis modes")

    def poll(self, now: float) -> list[dict]:
        return []

    def heartbeat(self, now: float) -> None:
        pass

    def disarm(self, now: float) -> None:
        pass

    def stop(self, now: float) -> None:
        pass
