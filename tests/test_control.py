import math
import random
from dataclasses import replace

import pytest

from pianotuner.control import FrameContext, SessionController, State
from pianotuner.domain import DEFAULTS, shift_cents
from pianotuner.domain.profile import MotionProfile
from pianotuner.dsp import PitchEstimate
from pianotuner.reporting.events import MemorySink


class Port:
    def __init__(self):
        self.moves = []
        self.events = []
        self.disabled_confirmed = True
        self.stops = 0
        self.fail_move = False

    def arm(self, now):
        self.disabled_confirmed = True

    def move(self, steps, rate_hz, max_duration_ms, now):
        self.moves.append(dict(steps=steps, rate_hz=rate_hz, max_duration_ms=max_duration_ms, now=now))
        if self.fail_move:
            raise ConnectionError("lost connection")
        self.disabled_confirmed = False
        self.events.append(dict(op="ACK", id=len(self.moves), command="MOVE"))
        return len(self.moves)

    def poll(self, now):
        events, self.events = self.events, []
        return events

    def heartbeat(self, now):
        pass

    def disarm(self, now):
        self.disabled_confirmed = True

    def stop(self, now):
        self.stops += 1
        self.disabled_confirmed = True


def session(profile=None, sink=None):
    port = Port()
    controller = SessionController(440, port, profile, sink)
    controller.start(0)
    return controller, port


def measure(c, error, **context_overrides):
    start = c.now + .05
    c.tick(start)
    strike = c.last_strike_id + 1
    assert c.strike(strike, start)
    for i in range(3):
        beginning = start + DEFAULTS.attack_exclusion_s + i * DEFAULTS.hop / DEFAULTS.sample_rate
        end = beginning + DEFAULTS.frame_length / DEFAULTS.sample_rate
        c.observe(PitchEstimate(shift_cents(440, error), error, True, (), -15),
                  replace(FrameContext(strike, c.motion_epoch, beginning, end), **context_overrides), end)


def finish_move(c, port, extra=None):
    port.disabled_confirmed = True
    event = dict(op="DONE", id=len(port.moves), emitted_steps=abs(port.moves[-1]["steps"]), enabled=False)
    port.events += [event] + (extra or [])
    c.tick(c.now + .1)
    if not c.terminal:
        c.tick(c.now + DEFAULTS.settle_s)


@pytest.mark.parametrize("error,direction", [(-10, 1), (10, -1)])
@pytest.mark.parametrize("tighten_sign", [-1, 1])
def test_signed_move_policy(error, direction, tighten_sign):
    c, port = session(MotionProfile(tighten_sign=tighten_sign))
    measure(c, error)
    assert port.moves[-1]["steps"] == direction * tighten_sign * 8
    assert c.absolute_steps == 8 and c.move_count == 1


@pytest.mark.parametrize("bad_context", [dict(motion_epoch=7), dict(source_epoch=2), dict(strike_id=99),
    dict(discontinuity=True), dict(uncertainty_s=math.inf), dict(uncertainty_s=-.1),
    dict(capture_start=0), dict(capture_end=.1)])
def test_invalid_frame_never_moves(bad_context):
    c, port = session()
    measure(c, -10, **bad_context)
    assert not port.moves and c.rejected_frames == 3


def test_consumed_strike_cannot_move_twice_and_old_data_cannot_follow_motion():
    c, port = session()
    measure(c, -10)
    assert c.state == State.MOVE
    old_id = c.last_strike_id
    finish_move(c, port)
    assert c.state == State.WAIT_STRIKE
    assert not c.strike(old_id, c.now)
    c.observe(PitchEstimate(430, -40, True, (), -15), FrameContext(old_id, 0, c.now-1, c.now), c.now)
    assert len(port.moves) == 1


@pytest.mark.parametrize("error,expected", [(30, "OUTSIDE_ENTRY_BAND"), (-30, "OUTSIDE_ENTRY_BAND")])
def test_entry_band_checked_at_every_decision(error, expected):
    c, port = session()
    measure(c, error)
    assert c.faults == [expected] and not port.moves


def test_unknown_move_consumes_full_exposure_and_never_retries():
    c, port = session()
    port.fail_move = True
    measure(c, -10)
    assert c.outcome == "FAULT" and c.absolute_steps == 8
    for _ in range(10):
        c.tick(c.now + 1)
    assert len(port.moves) == 1


def test_no_response_two_moves_faults():
    c, port = session()
    measure(c, -10)
    finish_move(c, port)
    measure(c, -10)
    finish_move(c, port)
    measure(c, -10)
    assert c.faults == ["NO_RESPONSE"] and len(port.moves) == 2


@pytest.mark.parametrize("after,reason", [(-12, "DIRECTION_MISMATCH"), (1, "UNEXPECTED_JUMP")])
def test_unsafe_response_stops(after, reason):
    c, port = session()
    measure(c, -10)
    finish_move(c, port)
    measure(c, after)
    assert c.faults == [reason] and len(port.moves) == 1


def test_reversal_budget_halves_gain_and_third_reversal_faults():
    c, port = session()
    for error in [-3.5, 3.5, -3.5, 3.5]:
        measure(c, error)
        if not c.terminal:
            finish_move(c, port)
    assert c.faults == ["REVERSAL_LIMIT"]
    assert c.reversals == 2 and c.alpha == .125 and len(port.moves) == 3


def test_cumulative_absolute_budget_does_not_cancel_opposite_moves():
    c, port = session(MotionProfile(max_move_steps=4, total_absolute_steps=8))
    measure(c, -3.5)
    finish_move(c, port)
    measure(c, 3.5)
    finish_move(c, port)
    measure(c, -3.5)
    finish_move(c, port)
    measure(c, -3.0)
    assert c.absolute_steps == 8 and c.faults == ["RESOLUTION_LIMIT"]


@pytest.mark.parametrize("state", [State.PRECHECK, State.WAIT_STRIKE, State.MEASURE, State.DECIDE,
                                  State.MOVE, State.SETTLE, State.VERIFY_UNLOADED])
def test_every_active_state_accepts_stop(state):
    c, port = session()
    c.state = state
    c.tick(c.now, stop_requested=True)
    assert c.state == State.ABORTED and port.stops == 1
    c.tick(c.now + 1)
    assert not port.moves


def test_stop_precedes_coalesced_done():
    c, port = session()
    measure(c, -10)
    port.events.append(dict(op="DONE", id=1, emitted_steps=8, enabled=False))
    c.tick(c.now + .1, stop_requested=True)
    assert c.state == State.ABORTED


def test_fault_precedes_stop_and_done():
    c, port = session()
    measure(c, -10)
    c.tick(c.now + .1, stop_requested=True, interlock_fault="DEADMAN_RELEASED")
    assert c.state == State.FAULT and c.faults == ["DEADMAN_RELEASED"]


def test_received_firmware_fault_precedes_stop():
    c, port = session()
    port.events.append({"op": "ERROR", "code": "DEADMAN_RELEASED"})
    c.tick(.1, stop_requested=True)
    assert c.faults == ["DEADMAN_RELEASED"] and c.outcome == "FAULT"


def test_stop_precedes_transport_timeout():
    c, port = session()
    port.poll = lambda now: (_ for _ in ()).throw(TimeoutError("late"))
    c.tick(.1, stop_requested=True)
    assert c.outcome == "ABORTED"


def test_delayed_or_other_command_done_does_not_complete_move():
    c, port = session()
    measure(c, -10)
    port.events.append(dict(op="DONE", id=99, emitted_steps=8, enabled=False))
    c.tick(c.now + .1)
    assert c.state == State.MOVE
    c.tick(c.now + .1)
    assert c.outcome == "FAULT"


def test_unloaded_verification_requires_four_distinct_strikes_and_full_wait():
    c, port = session()
    measure(c, 1)
    assert c.state == State.VERIFY_UNLOADED and not c.verification
    assert not c.strike(2, c.now)
    assert c.confirm_unloaded(c.now)
    unloaded_at = c.unloaded_at
    for _ in range(3):
        measure(c, 1)
    assert len(c.verification) == 3 and not c.terminal
    assert not c.strike(c.last_strike_id + 1, c.now)
    c.tick(unloaded_at + 59.99)
    assert not c.terminal
    c.tick(unloaded_at + 60)
    measure(c, 1)
    assert c.outcome == "SIM_VERIFIED" and not port.moves
    assert len({v["strike_id"] for v in c.verification}) == 4


def test_failed_verification_cannot_resume():
    c, port = session()
    measure(c, 1)
    c.confirm_unloaded(c.now)
    measure(c, 4)
    assert c.outcome == "VERIFY_FAILED" and not port.moves
    assert not c.strike(99, c.now)
    with pytest.raises(RuntimeError):
        c.start(c.now)


def test_disable_write_is_not_disabled_confirmation():
    c, port = session()
    port.disarm = lambda now: None
    port.disabled_confirmed = False
    measure(c, 1)
    assert not c.confirm_unloaded(c.now)
    c.tick(c.now + .25)
    assert c.faults == ["DISARM_UNCONFIRMED"]


@pytest.mark.parametrize("deadline,reason", [(30, "STRIKE_TIMEOUT"), (180, "RUN_TIMEOUT")])
def test_deadlines(deadline, reason):
    c, port = session()
    c.tick(deadline)
    assert c.faults == [reason]


def test_logging_failure_prevents_move_and_success():
    class BrokenLog(MemorySink):
        def emit(self, event):
            if event["kind"] == "move":
                raise OSError("disk full")
            super().emit(event)
    c, port = session(sink=BrokenLog())
    measure(c, -10)
    assert c.faults == ["LOG_FAILURE"] and not port.moves


def test_summary_failure_cannot_claim_success():
    class BrokenLog(MemorySink):
        def finish(self, result):
            raise OSError("disk full")
    c, port = session(sink=BrokenLog())
    c.state, c.outcome = State.COMPLETE, "SIM_VERIFIED"
    assert c.finish_log()["outcome"] == "FAULT"


def test_target_is_immutable_while_running():
    c, _ = session()
    with pytest.raises(AttributeError):
        c.target_hz = 220


def test_random_events_after_latched_fault_never_issue_new_motion():
    rng = random.Random(311)
    c, port = session()
    measure(c, -10)
    c.fault("INJECTED")
    for _ in range(300):
        c.tick(c.now + rng.random())
        c.strike(rng.randint(1, 10000), c.now)
        c.confirm_unloaded(c.now)
        c.observe(PitchEstimate(shift_cents(440, rng.uniform(-20, 20)), 0, True, (), -20),
                  FrameContext(1, 0, c.now - 1, c.now), c.now)
    assert len(port.moves) == 1 and c.faults == ["INJECTED"]
