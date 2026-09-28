"""In-memory protocol port; never discovers physical devices."""
from __future__ import annotations

import uuid

from pianotuner.protocol import encode_request
from pianotuner.simulation.firmware import FirmwareModel, FirmwareProfile


class LoopbackActuator:
    def __init__(self, model: FirmwareModel | None = None, session_id: str | None = None,
                 profile_hash: str = "simulation-v1") -> None:
        self.model = model or FirmwareModel(FirmwareProfile.simulation(profile_hash))
        self.session_id = session_id or uuid.uuid4().hex
        self.profile_hash = profile_hash
        self.next_id = 1
        self.pending: list[dict] = []
        self.connected = True
        self.drop_ack = False
        self.drop_done = False

    def _filter(self, events: list[dict]) -> list[dict]:
        return [e for e in events if not (self.drop_ack and e["op"] == "ACK")
                and not (self.drop_done and e["op"] in ("DONE", "ABORTED"))]

    @property
    def disabled_confirmed(self) -> bool:
        return not self.model.enabled and not self.model.armed

    def handshake(self, now: float) -> int:
        return self._send("HELLO", now)

    def _send(self, op: str, now: float, **fields) -> int:
        if not self.connected:
            raise ConnectionError("simulated transport disconnected")
        req = dict(v=1, session=self.session_id, id=self.next_id, op=op, **fields)
        self.next_id += 1
        self.pending.extend(self._filter(self.model.feed(encode_request(req), now)))
        return req["id"]

    def arm(self, now: float) -> None:
        self.pending.extend(self.model.set_inputs(False, now=now))
        self._send("HELLO", now)
        self.pending.extend(self.model.set_inputs(True, now=now))
        self._send("ARM", now, profile_hash=self.profile_hash, operator_confirmed=True)

    def move(self, steps: int, rate_hz: int, max_duration_ms: int, now: float) -> int:
        return self._send("MOVE", now, steps=steps, rate_hz=rate_hz, max_duration_ms=max_duration_ms)

    def poll(self, now: float) -> list[dict]:
        if not self.connected:
            raise ConnectionError("simulated transport disconnected")
        self.pending.extend(self._filter(self.model.tick(now)))
        result, self.pending = self.pending, []
        return result

    def heartbeat(self, now: float) -> None:
        self._send("HEARTBEAT", now)

    def status(self, now: float) -> int:
        return self._send("STATUS", now)

    def disarm(self, now: float) -> None:
        self._send("DISARM", now)

    def stop(self, now: float) -> None:
        self.pending.extend(self.model.command({"v": 1, "op": "STOP"}, now))

    def close(self) -> None:
        self.stop(self.model.now_us / 1_000_000)
        self.connected = False
