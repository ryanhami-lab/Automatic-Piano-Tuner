"""Audio-in-the-loop runner and separate ground-truth evaluator."""
import math
import uuid
from dataclasses import asdict
from pathlib import Path
from queue import Empty
from threading import Event

from pianotuner.adapters.loopback import LoopbackActuator
from pianotuner.control import FrameContext, SessionController, State
from pianotuner.domain import DEFAULTS, cents_error
from pianotuner.domain.profile import MotionProfile
from pianotuner.dsp import PitchEstimator, detect_onset
from pianotuner.reporting.events import MemorySink, SessionLog
from pianotuner.simulation.plant import Plant

SCENARIOS = ("nominal", "no_response", "wrong_direction", "stale_audio", "disconnect", "unload_shift",
             "lost_ack", "lost_done", "sudden_slip")


def nominal_parameters(seed):
    """Version 1 manifest formula; exact 100 assignments are committed as JSON."""
    return {"seed": seed, "initial_cents": (-1 if seed % 2 == 0 else 1) * (3 + (seed * 17 % 161) / 10),
            "tighten_sign": -1 if seed % 4 < 2 else 1,
            "sensitivity": .20 + (seed % 11) * .005,
            "backlash_steps": (seed % 4) * .15,
            "unload_shift_cents": ((seed % 11) - 5) / 10,
            "snr_db": 30.0 + seed % 6}


class PlantActuator:
    """Connect emitted pulses to mechanics. Never exposes the plant to control."""
    def __init__(self, plant, port, scenario):
        self._plant, self.port, self.scenario = plant, port, scenario
        self._issued = None
        self._issued_id = None
        self._applied_count = 0

    def arm(self, now):
        self.port.arm(now)

    @property
    def disabled_confirmed(self):
        return getattr(self.port, "disabled_confirmed", False)

    def move(self, steps, rate_hz, max_duration_ms, now):
        self._issued = steps
        if self.scenario == "disconnect":
            self.port.connected = False
        if self.scenario == "lost_ack":
            self.port.drop_ack = True
        if self.scenario == "lost_done":
            self.port.drop_done = True
        self._issued_id = self.port.move(steps, rate_hz, max_duration_ms, now)
        self._applied_count = 0
        self._apply_model_pulses()
        return self._issued_id

    def _apply_model_pulses(self):
        """Simulator-only electrical pulse channel, independent of host replies."""
        if self._issued_id is None or self._issued is None:
            return
        model = self.port.model
        count = self._applied_count
        if model.active is not None and model.active["id"] == self._issued_id:
            count = model.active["emitted"]
        elif self._issued_id in model.cache:
            for event in model.cache[self._issued_id][1]:
                if event.get("op") in {"DONE", "ABORTED"}:
                    count = event["emitted_steps"]
        delta = count - self._applied_count
        if delta > 0:
            self._plant.step(delta * (1 if self._issued > 0 else -1))
            self._applied_count = count

    def poll(self, now):
        events = self.port.poll(now)
        self._apply_model_pulses()
        return events

    def heartbeat(self, now):
        self.port.heartbeat(now)
        self._apply_model_pulses()

    def disarm(self, now):
        self.port.disarm(now)
        self._apply_model_pulses()

    def stop(self, now):
        self.port.stop(now)
        self._apply_model_pulses()


class SimulationSession:
    def __init__(self, target_hz=440, seed=7, scenario="nominal", output_dir=Path("runs"),
                 initial_cents=None, manual=False, parameters=None):
        if type(seed) is not int or not 0 <= seed <= 2**31 - 1:
            raise ValueError("Seed must be an integer in 0..2147483647")
        if scenario not in SCENARIOS:
            raise ValueError("Unknown scenario")
        self.parameters = nominal_parameters(seed) if parameters is None else dict(parameters)
        if initial_cents is not None:
            if not math.isfinite(initial_cents):
                raise ValueError("Initial cents must be finite")
            self.parameters["initial_cents"] = initial_cents
        self.seed, self.scenario, self.manual = seed, scenario, manual
        self.profile = MotionProfile(tighten_sign=self.parameters["tighten_sign"])
        plant_parameters = dict(self.parameters)
        if scenario == "no_response":
            plant_parameters["no_response"] = True
        if scenario == "wrong_direction":
            plant_parameters["tighten_sign"] *= -1
        if scenario == "unload_shift":
            plant_parameters["unload_shift_cents"] = 10
        if scenario == "sudden_slip":
            plant_parameters["slip_cents"] = 10
        self._plant = Plant(target_hz=target_hz, **plant_parameters)
        self.session_id = uuid.uuid4().hex
        self.session_dir = None
        if output_dir is None:
            self.sink = MemorySink()
        else:
            self.session_dir = Path(output_dir) / self.session_id
            self.sink = SessionLog(self.session_dir, {"session_id": self.session_id,
                "mode": "SIMULATION", "target_hz": target_hz, "scenario": scenario,
                "seed": seed, "scenario_parameters": plant_parameters, "profile": asdict(self.profile),
                "profile_sha256": self.profile.content_hash, "defaults": asdict(DEFAULTS),
                "model_version": 1, "raw_audio_recorded": False})
        port = LoopbackActuator(session_id=self.session_id, profile_hash=self.profile.content_hash)
        self.actuator = PlantActuator(self._plant, port, scenario)
        self.controller = SessionController(target_hz, self.actuator, self.profile, self.sink)
        self.estimator = PitchEstimator()
        self._strike_id = 0
        self._truth_checks = []

    def _snapshot(self):
        return {**self.controller.snapshot(), "session_dir": str(self.session_dir) if self.session_dir else None,
                "virtual_time": True, "seed": self.seed, "scenario": self.scenario}

    def run(self, callback=None, stop_event=None, commands=None, realtime=False):
        stop_event = stop_event or Event()
        c = self.controller
        c.start(0)
        last_publish = -1.0

        def publish(force=False):
            nonlocal last_publish
            if callback and (force or c.now - last_publish >= .1):
                callback(self._snapshot())
                last_publish = c.now

        def advance(end):
            while c.now < end - 1e-10 and not c.terminal:
                c.tick(min(end, c.now + .05), stop_requested=stop_event.is_set())
                publish()
            if realtime and not c.terminal:
                stop_event.wait(.055)
            if stop_event.is_set():
                c.stop()

        def manual_action(expected):
            if not self.manual:
                return True
            while not c.terminal:
                if stop_event.is_set():
                    c.stop()
                    return False
                try:
                    command = commands.get(timeout=.05) if commands is not None else None
                except Empty:
                    command = None
                if command == expected:
                    return True
                if command == "stop":
                    c.stop()
                    return False
                advance(c.now + .05)
                publish()
            return False

        publish(True)
        try:
            while not c.terminal:
                if stop_event.is_set():
                    c.stop()
                    break
                if c.state in {State.MOVE, State.SETTLE}:
                    advance(c.now + .05)
                    continue
                if c.state == State.VERIFY_UNLOADED:
                    if c.unloaded_at is None:
                        publish(True)
                        if not manual_action("unload"):
                            continue
                        if c.confirm_unloaded(c.now):
                            self._plant.unload()
                        else:
                            advance(c.now + .05)
                            continue
                    if c.unloaded_at is not None and len(c.verification) == 3 and c.now < c.unloaded_at + DEFAULTS.later_check_s:
                        # Virtual clock, still tick and Stop throughout the later check.
                        advance(min(c.now + 1, c.unloaded_at + DEFAULTS.later_check_s))
                        continue
                if c.state not in {State.WAIT_STRIKE, State.MEASURE, State.VERIFY_UNLOADED}:
                    advance(c.now + .05)
                    continue
                publish(True)
                if not manual_action("strike"):
                    continue
                audio = self._plant.render_strike()
                onset_index = detect_onset(audio)
                if onset_index is None:
                    advance(c.now + 1.2)
                    continue
                capture_origin = c.now
                onset = capture_origin + onset_index / DEFAULTS.sample_rate
                advance(onset)
                self._strike_id += 1
                if not c.strike(self._strike_id, onset):
                    advance(c.now + .05)
                    continue
                epoch = c.motion_epoch
                before_verify = len(c.verification)
                start = onset_index + math.ceil(DEFAULTS.attack_exclusion_s * DEFAULTS.sample_rate)
                for index in range(DEFAULTS.stable_frames):
                    offset = start + index * DEFAULTS.hop
                    frame = audio[offset:offset + DEFAULTS.frame_length]
                    end = capture_origin + (offset + len(frame)) / DEFAULTS.sample_rate
                    advance(end)
                    if c.terminal:
                        break
                    estimate = self.estimator.estimate(frame, c.target_hz)
                    c.tick(c.now, stop_requested=stop_event.is_set())
                    if c.terminal:
                        break
                    delay = .5 if self.scenario == "stale_audio" else 0
                    context = FrameContext(self._strike_id, epoch,
                        capture_origin + offset / DEFAULTS.sample_rate - delay, end - delay)
                    c.observe(estimate, context, c.now)
                    publish(True)
                if len(c.verification) > before_verify:
                    # Independent scorer only: no ground truth enters controller/estimator.
                    truth_error = cents_error(self._plant.evaluator_truth_hz(), c.target_hz)
                    check = {"strike_id": self._strike_id, "true_cents_error": truth_error,
                             "within_final_band": abs(truth_error) <= DEFAULTS.verify_cents}
                    self._truth_checks.append(check)
                    c._emit("simulation_evaluator", **check)
                advance(c.now + .1)
        except Exception as exc:
            c.fault("RUNTIME_FAILURE", f"{type(exc).__name__}: {exc}")
        finally:
            if not c.terminal:
                c.stop()
        summary = c.result()
        summary.update({"session_id": self.session_id, "session_dir": str(self.session_dir) if self.session_dir else None,
                        "scenario": self.scenario, "seed": self.seed,
                        "simulation_evaluator": self._truth_checks,
                        "false_success": c.outcome == "SIM_VERIFIED" and
                          (len(self._truth_checks) != 4 or not all(x["within_final_band"] for x in self._truth_checks))})
        try:
            self.sink.finish(summary)
        except (OSError, ValueError, RuntimeError):
            c.log_failed = True
            c._end(State.FAULT, "FAULT", "LOG_FAILURE")
            summary.update(c.result())
        publish(True)
        return summary
