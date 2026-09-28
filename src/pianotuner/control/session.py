"""Deterministic state machine; no dependency on a plant, GUI, or live device."""
import math
from dataclasses import dataclass
from enum import StrEnum
from statistics import median

from pianotuner.domain import DEFAULTS, cents_error
from pianotuner.domain.profile import MotionProfile
from pianotuner.reporting.events import MemorySink


class State(StrEnum):
    IDLE = "IDLE"
    PRECHECK = "PRECHECK"
    WAIT_STRIKE = "WAIT_STRIKE"
    MEASURE = "MEASURE"
    DECIDE = "DECIDE"
    MOVE = "MOVE"
    SETTLE = "SETTLE"
    VERIFY_UNLOADED = "VERIFY_UNLOADED"
    COMPLETE = "COMPLETE"
    VERIFY_FAILED = "VERIFY_FAILED"
    ABORTED = "ABORTED"
    FAULT = "FAULT"


TERMINAL = {State.COMPLETE, State.VERIFY_FAILED, State.ABORTED, State.FAULT}


@dataclass(frozen=True)
class FrameContext:
    strike_id: int
    motion_epoch: int
    capture_start: float
    capture_end: float
    source_epoch: int = 0
    uncertainty_s: float = 0.0
    discontinuity: bool = False


class SessionController:
    def __init__(self, target_hz, actuator, profile=None, sink=None, mode="SIMULATION"):
        if mode not in {"SIMULATION", "HARDWARE"}:
            raise ValueError("Control sessions require SIMULATION or HARDWARE")
        if isinstance(target_hz, bool) or not math.isfinite(target_hz) or not 220 <= target_hz <= 440:
            raise ValueError("Target must be within 220–440 Hz")
        self._target_hz = float(target_hz)
        self.actuator = actuator
        self.profile = profile or MotionProfile()
        if self.profile.profile_kind != ("simulation" if mode == "SIMULATION" else "physical"):
            raise ValueError("Profile/mode mismatch")
        self.sink = sink if sink is not None else MemorySink()
        self.mode = mode
        self.state = State.IDLE
        self.started = 0.0
        self.now = 0.0
        self.boundary = 0.0
        self.wait_started = 0.0
        self.motion_epoch = 0
        self.source_epoch = 0
        self.last_strike_id = 0
        self.strike_id = None
        self.onset: float | None = None
        self.last_frame_end: float | None = None
        self.frames: list[float] = []
        self.frequency_hz = None
        self.error: float | None = None
        self.quality = "Not measured"
        self.move_count = 0
        self.absolute_steps = 0
        self.reversals = 0
        self.last_move_sign = 0
        self.alpha = DEFAULTS.alpha
        self.previous_error: float | None = None
        self.expected_change_sign = 0
        self.no_response_count = 0
        self.awaiting_response_comparison = False
        self.pending: dict | None = None
        self.acknowledged = False
        self.unloaded_at: float | None = None
        self.verification: list[dict] = []
        self.accepted_frames = 0
        self.rejected_frames = 0
        self.faults: list[str] = []
        self.outcome = None
        self.last_heartbeat = -math.inf
        self.log_failed = False

    @property
    def target_hz(self):
        return self._target_hz

    @property
    def terminal(self):
        return self.state in TERMINAL

    def _emit(self, kind, **payload):
        if self.log_failed:
            return
        try:
            self.sink.emit({"time_s": self.now, "kind": kind, "mode": self.mode,
                            "state": str(self.state), **payload})
        except (OSError, ValueError, RuntimeError):
            self.log_failed = True
            self._end(State.FAULT, "FAULT", "LOG_FAILURE")

    def _state(self, state):
        if self.terminal:
            return
        self.state = state
        self._emit("state", value=str(state))

    def start(self, now=0.0, operator_confirmed=False):
        if self.state != State.IDLE:
            raise RuntimeError("Create a new session to restart")
        self.now = self.started = self.wait_started = self.boundary = now
        self._state(State.PRECHECK)
        if self.terminal:
            return
        if self.mode == "HARDWARE" and not operator_confirmed:
            return self.fault("PRECHECK_REQUIRED")
        try:
            self.actuator.arm(now)
        except Exception as exc:
            return self.fault("ARM_FAILED", str(exc))
        self._emit("armed", profile_hash=self.profile.content_hash)
        self._state(State.WAIT_STRIKE)

    def _end(self, state, outcome, reason=None):
        if self.terminal and reason != "LOG_FAILURE":
            return
        # Set terminal before I/O to prevent reentrancy from ever authorizing motion.
        self.state, self.outcome = state, outcome
        self.strike_id = None
        self.frames.clear()
        try:
            if state in {State.FAULT, State.ABORTED}:
                self.actuator.stop(self.now)
            else:
                self.actuator.disarm(self.now)
        except Exception:
            if state == State.COMPLETE:
                self.state, self.outcome = State.FAULT, "FAULT"
                reason = "DISARM_UNCONFIRMED"
        if reason:
            self.quality = reason
            self.faults.append(reason)
        self._emit("terminal", outcome=self.outcome, reason=reason)

    def fault(self, reason, detail=None):
        self._end(State.FAULT, "FAULT", reason)
        if detail:
            self._emit("fault_detail", detail=detail)

    def stop(self, now=None):
        if now is not None:
            self.now = now
        self._end(State.ABORTED, "ABORTED", "OPERATOR_STOP")

    def tick(self, now, stop_requested=False, interlock_fault=None):
        if not math.isfinite(now) or now < self.now:
            return self.fault("CLOCK_DISCONTINUITY")
        self.now = now
        if self.terminal or self.state == State.IDLE:
            return
        if interlock_fault:
            return self.fault(interlock_fault)
        transport_error = None
        try:
            events = self.actuator.poll(now)
        except Exception as exc:
            events = []
            transport_error = str(exc)
        # Faults outrank normal completion in the same batch.
        for event in events:
            if event.get("op") in {"ERROR", "ABORTED", "FAULT"} or event.get("fault"):
                return self.fault(event.get("code") or event.get("fault") or "MOTION_ABORTED")
        if stop_requested:
            return self.stop()
        if transport_error is not None:
            return self.fault("TRANSPORT_FAILURE", transport_error)
        if now - self.started >= DEFAULTS.run_timeout_s:
            return self.fault("RUN_TIMEOUT")
        if self.state == State.MOVE:
            if self.pending is None:
                return self.fault("MISSING_MOTION_REQUEST")
            # An event received after its deadline cannot rescue the session.
            if not self.acknowledged and now - self.dispatched >= .250:
                return self.fault("ACK_TIMEOUT")
            if now - self.dispatched >= self.pending["max_duration_ms"] / 1000 + .100:
                return self.fault("MOVE_TIMEOUT")
            for event in events:
                if event.get("id") != self.pending.get("id"):
                    continue
                if event.get("op") == "ACK" and event.get("command", "MOVE") == "MOVE":
                    self.acknowledged = True
                if event.get("op") == "DONE":
                    if event.get("enabled", True) is not False:
                        return self.fault("OUTPUT_NOT_DISABLED")
                    if event.get("emitted_steps") != abs(self.pending["steps"]):
                        return self.fault("INCOMPLETE_MOTION")
                    self._emit("motion_result", result=event)
                    self.pending = None
                    self.motion_epoch += 1
                    self.boundary = now + DEFAULTS.settle_s
                    self.strike_id = None
                    self.frames.clear()
                    self._state(State.SETTLE)
                    # Remaining replies may include duplicate DONE or unrelated
                    # commands; the pending request has now been consumed.
                    break
        if self.terminal:
            return
        if (self.state == State.VERIFY_UNLOADED and not getattr(self.actuator, "disabled_confirmed", False)
                and now - self.wait_started >= DEFAULTS.ack_timeout_s):
            return self.fault("DISARM_UNCONFIRMED")
        if self.state == State.SETTLE and now >= self.boundary:
            self.wait_started = now
            self._state(State.WAIT_STRIKE)
        waiting = self.state in {State.WAIT_STRIKE, State.MEASURE, State.VERIFY_UNLOADED}
        due = self.wait_started
        if self.state == State.VERIFY_UNLOADED and self.unloaded_at is not None and len(self.verification) == 3:
            due = max(due, self.unloaded_at + DEFAULTS.later_check_s)
        if waiting and now - due >= DEFAULTS.strike_timeout_s:
            return self.fault("STRIKE_TIMEOUT")
        if self.state not in {State.VERIFY_UNLOADED} and now - self.last_heartbeat >= .1 - 1e-9:
            try:
                self.actuator.heartbeat(now)
                self.last_heartbeat = now
            except Exception as exc:
                self.fault("TRANSPORT_FAILURE", str(exc))

    def strike(self, strike_id, onset, source_epoch=0):
        if self.state not in {State.WAIT_STRIKE, State.MEASURE, State.VERIFY_UNLOADED}:
            return False
        if type(strike_id) is not int or strike_id <= self.last_strike_id:
            return False
        if not math.isfinite(onset) or onset < self.boundary or onset > self.now + 1e-9:
            return False
        if onset < self.wait_started or (self.onset is not None and onset <= self.onset):
            return False
        if source_epoch != self.source_epoch:
            return False
        if self.state == State.VERIFY_UNLOADED:
            if self.unloaded_at is None:
                return False
            if len(self.verification) == 3 and onset < self.unloaded_at + DEFAULTS.later_check_s:
                return False
        self.strike_id = self.last_strike_id = strike_id
        self.onset = onset
        self.frames.clear()
        self._emit("strike", strike_id=strike_id, onset=onset, motion_epoch=self.motion_epoch)
        if self.state != State.VERIFY_UNLOADED:
            self._state(State.MEASURE)
        return True

    def observe(self, estimate, context: FrameContext, now):
        self.tick(now)
        if self.state not in {State.MEASURE, State.VERIFY_UNLOADED} or self.strike_id is None:
            return
        if self.onset is None:
            return self.fault("MISSING_STRIKE_ONSET")
        valid_time = all(math.isfinite(v) for v in (context.capture_start, context.capture_end, context.uncertainty_s))
        reasons = list(estimate.reasons)
        if not valid_time or context.uncertainty_s < 0:
            reasons.append("UNKNOWN_CAPTURE_TIMING")
        elif (context.capture_end > now + 1e-9 or context.capture_end < context.capture_start
              or now - context.capture_end + context.uncertainty_s > DEFAULTS.freshness_s):
            reasons.append("STALE_AUDIO")
        if context.discontinuity:
            reasons.append("AUDIO_DISCONTINUITY")
        if (context.strike_id != self.strike_id or context.motion_epoch != self.motion_epoch
                or context.source_epoch != self.source_epoch):
            reasons.append("STALE_EPOCH")
        if context.capture_start - context.uncertainty_s < max(self.onset + DEFAULTS.attack_exclusion_s, self.boundary) - 1e-9:
            reasons.append("EXCLUDED_SAMPLES")
        if context.capture_end - context.capture_start < DEFAULTS.frame_length / DEFAULTS.sample_rate - 1e-9:
            reasons.append("SHORT_FRAME")
        if self.last_frame_end is not None and context.capture_end <= self.last_frame_end:
            reasons.append("REPEATED_FRAME")
        elif (self.last_frame_end is not None
              and context.capture_end - self.last_frame_end < DEFAULTS.hop / DEFAULTS.sample_rate - 1e-9):
            reasons.append("FRAME_HOP_TOO_SHORT")
        if (not estimate.accepted or estimate.frequency_hz is None
                or not math.isfinite(estimate.frequency_hz) or estimate.frequency_hz <= 0):
            reasons.append("INVALID_ESTIMATE")
        if reasons:
            self.frames.clear()
            self.rejected_frames += 1
            self.quality = ", ".join(dict.fromkeys(reasons))
            self._emit("observation", accepted=False, reasons=list(dict.fromkeys(reasons)),
                       strike_id=context.strike_id)
            return
        self.last_frame_end = context.capture_end
        self.frequency_hz = estimate.frequency_hz
        self.error = cents_error(self.frequency_hz, self.target_hz)
        self.accepted_frames += 1
        self.frames.append(self.error)
        self.frames = self.frames[-DEFAULTS.stable_frames:]
        self.quality = "Accepted; collecting stability frames"
        self._emit("observation", accepted=True, frequency_hz=self.frequency_hz, cents_error=self.error,
                   strike_id=context.strike_id, motion_epoch=self.motion_epoch)
        if len(self.frames) < DEFAULTS.stable_frames:
            return
        if max(self.frames) - min(self.frames) > DEFAULTS.stability_cents:
            self.quality = "UNSTABLE"
            return
        self.error = median(self.frames)
        self.quality = "Stable first partial"
        accepted_strike = self.strike_id
        self.strike_id = None  # consume the strike exactly once
        self.frames.clear()
        if self.state == State.VERIFY_UNLOADED:
            if abs(self.error) > DEFAULTS.verify_cents:
                return self._end(State.VERIFY_FAILED, "VERIFY_FAILED", "UNLOADED_OUT_OF_TOLERANCE")
            self.verification.append({"strike_id": accepted_strike, "cents_error": self.error, "time_s": now})
            self._emit("verification", **self.verification[-1])
            self.wait_started = now
            if len(self.verification) == 4:
                self._end(State.COMPLETE, "SIM_VERIFIED" if self.mode == "SIMULATION" else "HARDWARE_VERIFIED")
            return
        self._state(State.DECIDE)
        if not self.terminal:
            self._decide()

    def _decide(self):
        e = self.error
        if e is None:
            return self.fault("MISSING_ESTIMATE")
        if abs(e) > min(DEFAULTS.auto_entry_cents, self.profile.auto_entry_limit_cents):
            return self.fault("OUTSIDE_ENTRY_BAND")
        if self.awaiting_response_comparison:
            if self.previous_error is None:
                return self.fault("MISSING_PREVIOUS_ESTIMATE")
            change = e - self.previous_error
            self.awaiting_response_comparison = False
            if abs(change) > DEFAULTS.jump_cents:
                return self.fault("UNEXPECTED_JUMP")
            if change * self.expected_change_sign < -DEFAULTS.no_response_cents:
                return self.fault("DIRECTION_MISMATCH")
            self.no_response_count = self.no_response_count + 1 if abs(change) < DEFAULTS.no_response_cents else 0
            if self.no_response_count >= DEFAULTS.no_response_moves:
                return self.fault("NO_RESPONSE")
        if abs(e) <= DEFAULTS.attached_cents:
            try:
                self.actuator.disarm(self.now)
            except Exception as exc:
                return self.fault("DISARM_UNCONFIRMED", str(exc))
            self.wait_started = self.now
            self._state(State.VERIFY_UNLOADED)
            self._emit("attached_convergence", cents_error=e)
            return
        if self.move_count >= DEFAULTS.max_moves:
            return self.fault("MOVE_LIMIT")
        direction = -1 if e > 0 else 1
        signed_direction = direction * self.profile.tighten_sign
        if self.last_move_sign and self.last_move_sign != signed_direction:
            if self.reversals >= DEFAULTS.max_reversals:
                return self.fault("REVERSAL_LIMIT")
            self.reversals += 1
            self.alpha *= .5
        upper = self.profile.sensitivity_upper_cents_per_step
        steps = min(math.floor(self.alpha * abs(e) / upper), math.floor(DEFAULTS.max_pitch_change_cents / upper),
                    self.profile.max_move_steps, self.profile.total_absolute_steps - self.absolute_steps)
        if steps < self.profile.minimum_steps:
            return self.fault("RESOLUTION_LIMIT")
        duration_ms = math.ceil(1000 * steps / self.profile.rate_hz + self.profile.direction_setup_us / 1000
                                + self.profile.pulse_width_us / 1000 + self.profile.direction_hold_us / 1000) + 1
        if duration_ms > self.profile.max_duration_ms:
            return self.fault("DURATION_LIMIT")
        self.previous_error = e
        self.expected_change_sign = direction
        self.last_move_sign = signed_direction
        self.awaiting_response_comparison = True
        self.pending = {"steps": signed_direction * steps, "rate_hz": self.profile.rate_hz,
                        "max_duration_ms": duration_ms}
        # Reserve before dispatch. No refund on missing ACK/DONE or abort.
        self.move_count += 1
        self.absolute_steps += steps
        self.dispatched = self.now
        self.acknowledged = False
        self._state(State.MOVE)
        self._emit("move", **self.pending, move_count=self.move_count, absolute_steps=self.absolute_steps)
        if self.terminal:
            return
        try:
            request_id = self.actuator.move(**self.pending, now=self.now)
            self.pending["id"] = request_id
        except Exception as exc:
            self.fault("TRANSPORT_FAILURE", str(exc))

    def confirm_unloaded(self, now):
        self.tick(now)
        if self.state != State.VERIFY_UNLOADED or self.unloaded_at is not None:
            return False
        if not getattr(self.actuator, "disabled_confirmed", False):
            return False
        self.unloaded_at = self.boundary = self.wait_started = now
        self._emit("mechanically_unloaded", operator_confirmed=True)
        return True

    def snapshot(self):
        prompt = ""
        if self.state in {State.WAIT_STRIKE, State.MEASURE}:
            prompt = "Strike the selected string"
        elif self.state == State.VERIFY_UNLOADED:
            if self.unloaded_at is None:
                prompt = "Confirm the tool is mechanically unloaded"
            elif len(self.verification) < 3:
                prompt = f"Verification strike {len(self.verification) + 1} of 3"
            elif self.now < self.unloaded_at + DEFAULTS.later_check_s:
                prompt = f"Later stability check in {self.unloaded_at + DEFAULTS.later_check_s - self.now:.1f} s"
            else:
                prompt = "Strike for the later stability check"
        return {"mode": self.mode, "state": str(self.state), "frequency_hz": self.frequency_hz,
                "cents_error": self.error, "quality": self.quality, "move_count": self.move_count,
                "elapsed_s": self.now - self.started, "prompt": prompt, "outcome": self.outcome}

    def result(self):
        return {"schema_version": 1, "mode": self.mode, "outcome": self.outcome,
                "target_hz": self.target_hz, "final_cents_error": self.error,
                "issued_moves": self.move_count, "absolute_steps_reserved": self.absolute_steps,
                "accepted_observations": self.accepted_frames, "rejected_observations": self.rejected_frames,
                "faults": list(self.faults), "elapsed_s": self.now - self.started,
                "verification": list(self.verification), "log_failed": self.log_failed,
                "physical_tests_run": False, "independent_physical_reference": False}

    def finish_log(self):
        try:
            self.sink.finish(self.result())
        except (OSError, ValueError, RuntimeError):
            self.log_failed = True
            self._end(State.FAULT, "FAULT", "LOG_FAILURE")
        return self.result()
