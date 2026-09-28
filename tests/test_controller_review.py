"""Regression tests from independent review of capture and transport boundaries."""

from dataclasses import replace

import pytest

from pianotuner.adapters.loopback import LoopbackActuator
from pianotuner.control import FrameContext, SessionController, State
from pianotuner.domain import DEFAULTS, cents_error, shift_cents
from pianotuner.dsp import PitchEstimate
from pianotuner.runtime.simulation import PlantActuator
from pianotuner.simulation.plant import Plant


class FakeActuator:
    def __init__(self):
        self.events = []
        self.moves = []
        self.disabled_confirmed = True

    def arm(self, now):
        self.disabled_confirmed = False

    def heartbeat(self, now):
        pass

    def poll(self, now):
        events, self.events = self.events, []
        return events

    def move(self, **request):
        self.moves.append(request)
        return len(self.moves)

    def disarm(self, now):
        self.disabled_confirmed = True

    def stop(self, now):
        pass


def setup_controller(error=-10):
    actuator = FakeActuator()
    controller = SessionController(440, actuator)
    controller.start()
    controller.tick(.03)
    assert controller.strike(1, .03)
    estimate = PitchEstimate(shift_cents(440, error), error, True, (), -20)
    return controller, actuator, estimate


def context(index=0, uncertainty=0):
    start = .18 + index * .1
    return FrameContext(1, 0, start, start + DEFAULTS.frame_length / DEFAULTS.sample_rate,
                        uncertainty_s=uncertainty)


def test_capture_uncertainty_cannot_hide_attack_samples():
    controller, actuator, estimate = setup_controller()
    frame = context(uncertainty=.02)
    controller.observe(estimate, frame, frame.capture_end)
    assert "EXCLUDED_SAMPLES" in controller.quality
    assert controller.accepted_frames == 0
    assert not actuator.moves


def test_subhop_frames_cannot_manufacture_stability():
    controller, actuator, estimate = setup_controller()
    first = context()
    controller.observe(estimate, first, first.capture_end)
    second = replace(first, capture_start=first.capture_start + .001, capture_end=first.capture_end + .001)
    controller.observe(estimate, second, second.capture_end)
    assert "FRAME_HOP_TOO_SHORT" in controller.quality
    assert not actuator.moves


def test_coalesced_duplicate_done_is_consumed_once():
    controller, actuator, estimate = setup_controller()
    for index in range(3):
        frame = context(index)
        controller.observe(estimate, frame, frame.capture_end)
    assert controller.state == State.MOVE
    request = actuator.moves[0]
    done = {"op": "DONE", "id": 1, "enabled": False, "emitted_steps": abs(request["steps"])}
    actuator.events = [{"op": "ACK", "id": 1, "command": "MOVE"}, done, dict(done)]
    controller.tick(controller.now + .1)
    assert controller.state == State.SETTLE
    assert controller.motion_epoch == 1


def test_replayed_onset_does_not_create_distinct_verification_strike():
    controller, _actuator, estimate = setup_controller(error=0)
    for index in range(3):
        frame = context(index)
        controller.observe(estimate, frame, frame.capture_end)
    assert controller.state == State.VERIFY_UNLOADED
    assert controller.confirm_unloaded(controller.now)
    onset = controller.now + .03
    controller.tick(onset)
    assert controller.strike(2, onset)
    for index in range(3):
        start = onset + .15 + .1 * index
        frame = FrameContext(2, 0, start, start + DEFAULTS.frame_length / DEFAULTS.sample_rate)
        controller.observe(estimate, frame, frame.capture_end)
    assert len(controller.verification) == 1
    assert not controller.strike(3, onset)
    assert not controller.strike(3, onset + .01)


def test_lost_completion_packet_does_not_erase_simulated_motion():
    plant = Plant(initial_cents=0)
    port = LoopbackActuator()
    actuator = PlantActuator(plant, port, "lost_done")
    actuator.arm(0)
    actuator.poll(0)
    actuator.move(8, 100, 100, 0)
    events = actuator.poll(.09)
    assert not any(event["op"] == "DONE" for event in events)
    assert cents_error(plant.evaluator_truth_hz(), 440) == pytest.approx(8 * .225)
    actuator.poll(.1)
    assert plant.absolute_commanded_steps == 8


def test_partial_abort_has_real_simulated_step_effect():
    plant = Plant(initial_cents=0)
    actuator = PlantActuator(plant, LoopbackActuator(), "nominal")
    actuator.arm(0)
    actuator.poll(0)
    actuator.move(8, 100, 100, 0)
    actuator.poll(.045)
    actuator.stop(.045)
    assert plant.absolute_commanded_steps == 4
    assert cents_error(plant.evaluator_truth_hz(), 440) == pytest.approx(4 * .225)
