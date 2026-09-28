"""Deterministic local firmware reference model. All time arguments are seconds."""
from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass

from pianotuner.protocol import FrameDecoder, ProtocolError, canonical_request, parse_request


@dataclass(frozen=True)
class FirmwareProfile:
    profile_hash: str = "UNCOMMISSIONED"
    actuation_enabled: bool = False
    max_move_steps: int = 0
    max_rate_hz: int = 0
    max_duration_ms: int = 0
    absolute_step_budget: int = 0
    direction_setup_us: int = 0
    pulse_width_us: int = 0
    direction_hold_us: int = 0

    @classmethod
    def simulation(cls, profile_hash: str = "simulation-v1") -> FirmwareProfile:
        return cls(profile_hash, True, 8, 100, 250, 256, 10, 10, 10)

    @property
    def valid(self) -> bool:
        limits = (self.max_move_steps, self.max_rate_hz, self.max_duration_ms,
                  self.absolute_step_budget, self.direction_setup_us, self.pulse_width_us,
                  self.direction_hold_us)
        return self.actuation_enabled and all(type(v) is int and v > 0 for v in limits)


class FirmwareModel:
    def __init__(self, profile: FirmwareProfile | None = None) -> None:
        self.profile = profile or FirmwareProfile()
        self.session = ""
        self.high_id = 0
        self.cache: OrderedDict[int, tuple[str, list[dict]]] = OrderedDict()
        self.armed = False
        self.enabled = False
        self.fault = ""
        self.deadman = False
        self.stop_input = False
        self.driver_fault = False
        self.edge = False
        self.budget_used = 0
        self.lease_us = 0
        self.now_us = 0
        self.active: dict | None = None
        self.pulse_stalled = False
        self.last_outcome = "NONE"
        self.last_emitted_steps = 0
        self.decoder = FrameDecoder()

    @staticmethod
    def _us(now: float) -> int:
        if not math.isfinite(now) or now < 0:
            raise ValueError("time must be finite and nonnegative")
        return round(now * 1_000_000)

    def _reply(self, req: dict, op: str, **fields) -> dict:
        response = {"v": 1, "op": op}
        if "session" in req:
            response.update(session=req["session"], id=req["id"])
        response.update(fields)
        return response

    def _end(self, aborted: bool, code: str = "") -> list[dict]:
        self.enabled = False
        if self.active is None:
            return []
        req = self.active
        self.active = None
        self.last_outcome = "ABORTED" if aborted else "DONE"
        self.last_emitted_steps = req["emitted"]
        response = self._reply(req, self.last_outcome, emitted_steps=req["emitted"],
                               requested_steps=req["steps"], enabled=False, code=code)
        cached = self.cache.get(req["id"])
        if cached:
            cached[1].append(response)
        return [response]

    def _fault(self, code: str) -> list[dict]:
        newly_latched = not self.fault
        self.fault = self.fault or code
        self.armed = False
        self.edge = False
        events = self._end(True, self.fault)
        if newly_latched and not events:
            events.append({"v": 1, "op": "ERROR", "code": self.fault})
        return events

    def set_inputs(self, deadman: bool, stop: bool = False, driver_fault: bool = False,
                   now: float = 0) -> list[dict]:
        if any(type(v) is not bool for v in (deadman, stop, driver_fault)):
            raise ValueError("interlocks must be booleans")
        if deadman and not self.deadman and self.session:
            self.edge = True
        self.deadman, self.stop_input, self.driver_fault = deadman, stop, driver_fault
        return self.tick(now)

    def tick(self, now: float) -> list[dict]:
        current = self._us(now)
        if current < self.now_us:
            raise ValueError("clock moved backwards")
        self.now_us = current
        if self.active is not None and not self.pulse_stalled:
            req = self.active
            elapsed = max(0, min(current, req["deadline_us"]) - req["start_us"] - self.profile.direction_setup_us)
            req["emitted"] = min(abs(req["steps"]), elapsed * req["rate_hz"] // 1_000_000)
        # Interlocks, lease, deadline precede completion even on the same tick.
        if self.stop_input:
            return self._fault("STOP_INPUT")
        if self.driver_fault:
            return self._fault("DRIVER_FAULT")
        if self.armed and not self.deadman:
            return self._fault("DEADMAN_RELEASED")
        if self.armed and current >= self.lease_us:
            return self._fault("HEARTBEAT_TIMEOUT")
        if self.active is not None:
            req = self.active
            if current >= req["finish_us"] and not self.pulse_stalled:
                return self._end(False)
            if current >= req["deadline_us"]:
                return self._fault("MOVE_TIMEOUT")
        return []

    def malformed(self, code: str = "MALFORMED") -> list[dict]:
        events = self._fault(code) if self.armed else []
        return events + [{"v": 1, "op": "ERROR", "code": code}]

    def feed(self, data: bytes, now: float) -> list[dict]:
        result = []
        for frame in self.decoder.feed(data):
            result.extend(self.malformed(frame.code) if isinstance(frame, ProtocolError)
                          else self.command(frame, now))
        result.extend(self.tick(now))
        return result

    def command(self, request: dict | bytes | str, now: float) -> list[dict]:
        try:
            req = parse_request(request)
        except ProtocolError as exc:
            events = self.tick(now)
            return events + self.malformed(exc.code)
        # STOP takes precedence over a simultaneously due normal completion.
        if req["op"] == "STOP":
            self.now_us = max(self.now_us, self._us(now))
            if self.active is not None and not self.pulse_stalled:
                elapsed = max(0, min(self.now_us, self.active["deadline_us"]) - self.active["start_us"] - self.profile.direction_setup_us)
                self.active["emitted"] = min(abs(self.active["steps"]), elapsed * self.active["rate_hz"] // 1_000_000)
            events = self._fault("STOP")
            if "session" not in req:
                return events + [self._reply(req, "ACK", command="STOP")]
        else:
            events = self.tick(now)
        op = req["op"]
        payload = canonical_request(req)
        new_session = op == "HELLO" and req["session"] != self.session
        if new_session:
            if self.armed or self.deadman or self.fault:
                return events + [self._reply(req, "ERROR", code="HELLO_BLOCKED")]
            self.session = req["session"]
            self.high_id = self.budget_used = 0
            self.cache.clear()
            self.edge = False
            self.last_outcome = "NONE"
            self.last_emitted_steps = 0
        if req["session"] != self.session:
            return events + [self._reply(req, "ERROR", code="SESSION_MISMATCH")]
        if req["id"] in self.cache:
            old_payload, cached_replies = self.cache[req["id"]]
            if payload != old_payload:
                return events + self._fault("ID_REUSE") + [self._reply(req, "ERROR", code="ID_REUSE")]
            return events + [dict(r) for r in cached_replies]
        if req["id"] <= self.high_id:
            return events + [self._reply(req, "ERROR", code="STALE_ID")]
        self.high_id = req["id"]
        replies: list[dict] = []
        self.cache[req["id"]] = (payload, replies)
        while len(self.cache) > 32:
            self.cache.popitem(last=False)
        error = ""
        if op == "HELLO":
            replies.append(self._reply(req, "HELLO", device="pianotuner-v1", profile_hash=self.profile.profile_hash,
                firmware_build="pianotuner-v1-simulation" if self.profile.actuation_enabled else "pianotuner-v1-disabled",
                actuation_enabled=self.profile.actuation_enabled, max_move_steps=self.profile.max_move_steps,
                max_rate_hz=self.profile.max_rate_hz, max_duration_ms=self.profile.max_duration_ms,
                absolute_step_budget=self.profile.absolute_step_budget))
        elif op == "STATUS":
            replies.append(self._reply(req, "STATUS", armed=self.armed, enabled=self.enabled, fault=self.fault,
                deadman=self.deadman, stop_input=self.stop_input, driver_fault=self.driver_fault,
                budget_used=self.budget_used, busy=self.active is not None, last_outcome=self.last_outcome,
                emitted_steps=self.last_emitted_steps))
        elif op == "CLEAR_FAULT":
            if self.deadman or self.stop_input or self.driver_fault:
                error = "CAUSE_PRESENT"
            else:
                events.extend(self._end(True, "CLEAR_FAULT"))
                self.fault = ""
                self.armed = self.edge = False
        elif op == "DISARM":
            events.extend(self._end(True, "DISARM"))
            self.armed = self.edge = False
        elif op == "ARM":
            if self.fault:
                error = "FAULT_LATCHED"
            elif not self.profile.valid:
                error = "UNCOMMISSIONED"
            elif req["profile_hash"] != self.profile.profile_hash:
                error = "PROFILE_MISMATCH"
            elif self.armed or not self.deadman or not self.edge or not req["operator_confirmed"]:
                error = "ARM_INTERLOCK"
            else:
                self.armed = True
                self.edge = False
                self.lease_us = self.now_us + 500_000
        elif op == "HEARTBEAT":
            if self.armed:
                self.lease_us = self.now_us + 500_000
        elif op == "MOVE":
            n = abs(req["steps"])
            duration_us = self.profile.direction_setup_us + ((n * 1_000_000 + req["rate_hz"] - 1) // req["rate_hz"]) + self.profile.pulse_width_us + self.profile.direction_hold_us
            if self.fault:
                error = "FAULT_LATCHED"
            elif not self.armed:
                error = "NOT_ARMED"
            elif self.active is not None:
                error = "BUSY"
            elif n > self.profile.max_move_steps or req["rate_hz"] > self.profile.max_rate_hz or req["max_duration_ms"] > self.profile.max_duration_ms:
                error = "LIMIT_EXCEEDED"
            elif duration_us >= req["max_duration_ms"] * 1000 or 2 * self.profile.pulse_width_us * req["rate_hz"] >= 1_000_000:
                error = "DURATION_INVALID"
            elif self.budget_used + n > self.profile.absolute_step_budget:
                error = "BUDGET_EXHAUSTED"
            else:
                self.budget_used += n
                self.enabled = True
                self.active = dict(req, emitted=0, start_us=self.now_us,
                                   finish_us=self.now_us + duration_us,
                                   deadline_us=self.now_us + req["max_duration_ms"] * 1000)
        if error:
            replies.append(self._reply(req, "ERROR", code=error))
        elif not replies and op != "HEARTBEAT":
            replies.append(self._reply(req, "ACK", command=op))
        return events + [dict(r) for r in replies]
