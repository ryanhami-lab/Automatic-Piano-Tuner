"""Bounded USB serial adapter. MOVE is transmitted exactly once, never retried."""
from __future__ import annotations

import json
import threading
import uuid
from collections import deque

from pianotuner.protocol import FrameDecoder, ProtocolError, encode_request


def _reply_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate response key")
        result[key] = value
    return result


class SerialActuator:
    """An injected serial object is used by tests; open() is the only device opener.

    The runtime must call poll and heartbeat from its healthy control tick. There
    is deliberately no independent heartbeat thread. Constructor does no I/O.
    """

    def __init__(self, serial_object, profile_hash: str, session_id: str | None = None) -> None:
        self.serial = serial_object
        self.profile_hash = profile_hash
        self.session_id = session_id or uuid.uuid4().hex
        self.next_id = 1
        self.decoder = FrameDecoder()
        self.lock = threading.Lock()
        self.waiting: dict[int, dict] = {}
        self.events: deque[dict] = deque()
        self.failed = False
        self.closed = False
        self.handshake_ok = False
        self.disabled_confirmed = False
        self.expected_capabilities: dict = {}

    @classmethod
    def open(cls, port: str, profile_path, *, session_id: str | None = None):
        from pianotuner.domain.profile import load_profile
        profile, metadata = load_profile(profile_path, "physical")
        if not port:
            raise ValueError("an explicit serial port is required")
        import serial  # optional dependency, lazy and never used by default
        stream = serial.Serial(port=port, baudrate=115200, bytesize=8, parity="N", stopbits=1,
                               timeout=0, write_timeout=0.05)
        result = cls(stream, metadata["commissioning"]["firmware_profile_hash"], session_id)
        result.expected_capabilities = dict(device=metadata["commissioning"]["device_identity"],
            firmware_build=metadata["commissioning"]["firmware_build"],
            max_move_steps=profile.max_move_steps, max_rate_hz=profile.rate_hz,
            max_duration_ms=profile.max_duration_ms, absolute_step_budget=profile.total_absolute_steps)
        return result

    def _write(self, data: bytes) -> None:
        if self.closed:
            raise ConnectionError("serial port closed")
        try:
            # No retry even after a partial write: the outcome is ambiguous.
            written = self.serial.write(data)
            if written != len(data):
                raise ConnectionError("partial serial write")
        except Exception as exc:
            self.failed = True
            raise ConnectionError("serial write failed; no request will be retried") from exc

    def _send(self, op: str, now: float, **fields) -> int:
        with self.lock:
            if self.failed and op not in ("STOP", "STATUS", "DISARM"):
                raise ConnectionError("transport fault requires a new session")
            ident = self.next_id
            self.next_id += 1
            req = dict(v=1, session=self.session_id, id=ident, op=op, **fields)
            frame = encode_request(req)
            # Reserve bookkeeping before dispatch; an exception does not erase exposure.
            if op != "HEARTBEAT":
                self.waiting[ident] = dict(op=op, sent=now, ack=False,
                    result_deadline=now + fields.get("max_duration_ms", 0) / 1000 + 0.1,
                    requested_steps=fields.get("steps", 0))
            self._write(frame)
            return ident

    def handshake(self, now: float) -> int:
        return self._send("HELLO", now)

    def arm(self, now: float) -> None:
        if not self.handshake_ok:
            raise RuntimeError("complete handshake then deliberately enable the local deadman")
        self.disabled_confirmed = False
        self._send("ARM", now, profile_hash=self.profile_hash, operator_confirmed=True)

    def move(self, steps: int, rate_hz: int, max_duration_ms: int, now: float) -> int:
        if self.failed:
            raise ConnectionError("transport fault requires a new session")
        if not self.handshake_ok:
            raise RuntimeError("device handshake not complete")
        if any(w["op"] == "MOVE" for w in self.waiting.values()):
            raise RuntimeError("another MOVE outcome is outstanding")
        self.disabled_confirmed = False
        return self._send("MOVE", now, steps=steps, rate_hz=rate_hz, max_duration_ms=max_duration_ms)

    def heartbeat(self, now: float) -> None:
        self._send("HEARTBEAT", now)

    def status(self, now: float) -> int:
        return self._send("STATUS", now)

    def disarm(self, now: float) -> None:
        self.disabled_confirmed = False
        self._send("DISARM", now)

    def stop(self, now: float) -> None:
        del now
        self.disabled_confirmed = False
        with self.lock:
            self._write(b'{"v":1,"op":"STOP"}\n')

    def _fail(self, code: str, now: float, ident: int | None = None) -> None:
        if self.failed:
            return
        self.failed = True
        self.disabled_confirmed = False
        self.events.append(dict(v=1, op="ERROR", code=code, id=ident, session=self.session_id,
                                outcome="UNKNOWN"))
        # Best effort; a crashed/disconnected host cannot guarantee delivery.
        try:
            self.stop(now)
            self.status(now)
        except ConnectionError:
            pass

    def poll(self, now: float) -> list[dict]:
        try:
            if not getattr(self.serial, "is_open", True):
                raise ConnectionError("serial disconnected")
            raw = self.serial.read(4096)
        except Exception:
            self._fail("DISCONNECTED", now)
            raw = b""
        for frame in self.decoder.feed(raw):
            if isinstance(frame, ProtocolError):
                self._fail(frame.code, now)
                continue
            try:
                response = json.loads(frame.decode("ascii"), object_pairs_hook=_reply_pairs)
                if not isinstance(response, dict) or type(response.get("v")) is not int or response.get("v") != 1:
                    raise ValueError()
                if any(isinstance(v, (dict, list, float)) for v in response.values()):
                    raise ValueError()
                op = response.get("op")
                if op not in {"ACK", "ERROR", "HELLO", "STATUS", "DONE", "ABORTED"}:
                    raise ValueError()
                if "session" not in response:
                    if op == "ERROR":
                        self._fail(response.get("code", "MALFORMED_RESPONSE"), now)
                        continue
                    if op == "ACK" and response.get("command") == "STOP":
                        continue
                    raise ValueError()
                if response["session"] != self.session_id:
                    # A delayed prior session response cannot settle current motion.
                    continue
                ident = response.get("id")
                if type(ident) is not int or not 1 <= ident <= 2**31 - 1:
                    raise ValueError()
                request = self.waiting.get(ident)
                if op in {"ACK", "HELLO", "STATUS", "DONE", "ABORTED"} and not request:
                    continue
                if op == "HELLO":
                    if not request or request["op"] != "HELLO" or response.get("profile_hash") != self.profile_hash or response.get("actuation_enabled") is not True:
                        self._fail("CAPABILITY_MISMATCH", now, ident)
                        continue
                    if any(response.get(k) != v for k, v in self.expected_capabilities.items()):
                        self._fail("CAPABILITY_MISMATCH", now, ident)
                        continue
                    self.handshake_ok = True
                if request:
                    if op == "ACK" and response.get("command") != request["op"]:
                        raise ValueError()
                    if op == "ACK" and request["op"] == "MOVE":
                        if response.get("command") != "MOVE":
                            raise ValueError()
                        request["ack"] = True
                    elif op in {"DONE", "ABORTED"} and request["op"] == "MOVE":
                        emitted = response.get("emitted_steps")
                        if type(emitted) is not int or not 0 <= emitted <= abs(request["requested_steps"]):
                            raise ValueError()
                        if response.get("requested_steps") != request["requested_steps"] or response.get("enabled") is not False:
                            raise ValueError()
                        if op == "DONE" and emitted != abs(request["requested_steps"]):
                            raise ValueError()
                        self.waiting.pop(ident, None)
                    elif op in {"ERROR", "HELLO", "STATUS", "ACK"}:
                        self.waiting.pop(ident, None)
                    if op == "ACK" and request["op"] == "DISARM" and response.get("command") == "DISARM":
                        self.disabled_confirmed = True
                    elif op == "STATUS" and response.get("enabled") is False and response.get("armed") is False:
                        self.disabled_confirmed = True
                self.events.append(response)
            except (ValueError, TypeError, UnicodeDecodeError):
                self._fail("MALFORMED_RESPONSE", now)
        for ident, req in list(self.waiting.items()):
            if req["op"] == "MOVE" and now >= req["result_deadline"]:
                self._fail("MOVE_RESULT_TIMEOUT", now, ident)
            elif not req["ack"] and now - req["sent"] >= 0.250:
                self._fail("ACK_TIMEOUT", now, ident)
        result = list(self.events)
        self.events.clear()
        return result

    def close(self) -> None:
        try:
            self.stop(0)
        finally:
            self.serial.close()
            self.closed = True
