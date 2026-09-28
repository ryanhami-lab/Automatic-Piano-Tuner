"""Strict protocol-v1 ASCII/NDJSON boundary shared by adapters and simulation.

JSON Schema cannot express duplicate keys or wire grammar; this validator is
authoritative for those restrictions. No import opens a device.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping

MAX_FRAME_BYTES = 512
MAX_ID = 2**31 - 1
OPS = frozenset({"HELLO", "STATUS", "ARM", "HEARTBEAT", "MOVE", "DISARM", "CLEAR_FAULT", "STOP"})
_BASE = {"v", "session", "id", "op"}


class ProtocolError(ValueError):
    """Invalid wire request, with a stable protocol error code."""

    def __init__(self, code: str = "MALFORMED") -> None:
        self.code = code
        super().__init__(code)


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError()
        result[key] = value
    return result


def _integer(value: str) -> int:
    if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)", value):
        raise ProtocolError()
    result = int(value)
    if not -MAX_ID <= result <= MAX_ID:
        raise ProtocolError()
    return result


def _reject(_: str) -> None:
    raise ProtocolError()


def parse_request(frame: bytes | str | Mapping) -> dict:
    if isinstance(frame, Mapping):
        try:
            frame = json.dumps(dict(frame), separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            raise ProtocolError() from None
    if isinstance(frame, str):
        try:
            frame = frame.encode("ascii")
        except UnicodeEncodeError:
            raise ProtocolError() from None
    if not isinstance(frame, bytes):
        raise ProtocolError()
    # A direct call may omit the final newline; reserve its wire byte anyway.
    if len(frame) + (not frame.endswith(b"\n")) > MAX_FRAME_BYTES:
        raise ProtocolError("FRAME_TOO_LONG")
    if b"\\" in frame or any(x > 127 or (x < 32 and x not in (9, 10, 13)) for x in frame):
        raise ProtocolError()
    if b"\n" in frame.rstrip(b"\r\n"):
        raise ProtocolError()
    try:
        obj = json.loads(frame.decode("ascii"), object_pairs_hook=_pairs,
                         parse_int=_integer, parse_float=_reject, parse_constant=_reject)
    except (ValueError, UnicodeDecodeError):
        raise ProtocolError() from None
    if not isinstance(obj, dict) or any(isinstance(v, (dict, list)) for v in obj.values()):
        raise ProtocolError()
    if type(obj.get("v")) is not int or obj["v"] != 1:
        raise ProtocolError()
    op = obj.get("op")
    if not isinstance(op, str) or op not in OPS:
        raise ProtocolError()
    if obj == {"v": 1, "op": "STOP"}:
        return obj
    expected = _BASE | ({"steps", "rate_hz", "max_duration_ms"} if op == "MOVE" else
                        {"profile_hash", "operator_confirmed"} if op == "ARM" else set())
    if set(obj) != expected:
        raise ProtocolError()
    if not isinstance(obj["session"], str) or not re.fullmatch("[0-9a-f]{32}", obj["session"]):
        raise ProtocolError()
    if type(obj["id"]) is not int or not 1 <= obj["id"] <= MAX_ID:
        raise ProtocolError()
    if op == "MOVE":
        if any(type(obj[k]) is not int for k in ("steps", "rate_hz", "max_duration_ms")):
            raise ProtocolError()
        if obj["steps"] == 0 or obj["rate_hz"] <= 0 or obj["max_duration_ms"] <= 0:
            raise ProtocolError()
    if op == "ARM":
        if not isinstance(obj["profile_hash"], str) or not re.fullmatch("[A-Za-z0-9_-]{1,64}", obj["profile_hash"]):
            raise ProtocolError()
        if type(obj["operator_confirmed"]) is not bool:
            raise ProtocolError()
    return obj


def encode_request(request: Mapping) -> bytes:
    validated = parse_request(request)
    return (json.dumps(validated, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def canonical_request(request: Mapping) -> str:
    return json.dumps(dict(request), sort_keys=True, separators=(",", ":"))


class FrameDecoder:
    """Incremental bounded framing; discard an oversize line through newline."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.discarding = False

    def feed(self, data: bytes) -> list[bytes | ProtocolError]:
        frames: list[bytes | ProtocolError] = []
        for byte in data:
            if self.discarding:
                if byte == 10:
                    self.discarding = False
                continue
            self.buffer.append(byte)
            if len(self.buffer) > MAX_FRAME_BYTES or (len(self.buffer) == MAX_FRAME_BYTES and byte != 10):
                self.buffer.clear()
                self.discarding = byte != 10
                frames.append(ProtocolError("FRAME_TOO_LONG"))
            elif byte == 10:
                frames.append(bytes(self.buffer))
                self.buffer.clear()
        return frames
