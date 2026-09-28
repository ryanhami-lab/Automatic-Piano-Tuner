"""Explicit commissioned live route. Importing this module opens no device."""
import math
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread

import numpy as np

from pianotuner.adapters.audio import MicrophoneSource
from pianotuner.adapters.serial import SerialActuator
from pianotuner.control import FrameContext, SessionController, State
from pianotuner.domain import DEFAULTS
from pianotuner.domain.profile import load_profile
from pianotuner.dsp import PitchEstimator, detect_onset
from pianotuner.reporting.events import SessionLog


class LiveFramePipeline:
    """Bounded contiguous capture windows with real onset, epoch, and age checks."""
    def __init__(self, controller, clock=time.monotonic):
        self.controller = controller
        self.clock = clock
        self.estimator = PitchEstimator()
        self.source_epoch = None
        self.expected_index = None
        self.strikes = 0
        self.reset()

    def reset(self):
        self.buffer = np.empty(0, dtype=np.float32)
        self.origin = None
        self.uncertainty = 0.0
        self.frame_offset = None
        self.motion_epoch = self.controller.motion_epoch
        self.state_strike = None

    def _reject_capture(self, reason):
        c = self.controller
        c.source_epoch += 1
        c.strike_id = None
        c.frames.clear()
        c.quality = reason
        c._emit("capture_rejection", reason=reason)
        if c.state == State.MEASURE:
            c._state(State.WAIT_STRIKE)
        self.reset()

    def feed(self, block, now):
        c = self.controller
        c.tick(now)
        if c.terminal:
            return
        changed_epoch = self.source_epoch is not None and self.source_epoch != block.source_epoch
        gap = self.expected_index is not None and self.expected_index != block.sample_start
        self.source_epoch = block.source_epoch
        self.expected_index = block.sample_end
        if changed_epoch or gap or block.gap or block.overflow or not block.timing_known:
            self._reject_capture("UNKNOWN_CAPTURE_TIMING" if not block.timing_known else "AUDIO_DISCONTINUITY")
            return
        samples = np.asarray(block.samples)
        if (type(block.sample_rate) is not int or block.sample_rate != DEFAULTS.sample_rate
                or type(block.sample_start) is not int or type(block.sample_end) is not int
                or samples.ndim != 1 or not 0 < len(samples) <= DEFAULTS.sample_rate
                or block.sample_end - block.sample_start != len(samples)
                or not np.all(np.isfinite(samples))):
            self._reject_capture("INVALID_AUDIO_BLOCK")
            return
        if (block.timing_uncertainty_s < 0
                or not math.isclose(block.capture_end - block.capture_start,
                                    len(samples) / DEFAULTS.sample_rate, abs_tol=1 / DEFAULTS.sample_rate)):
            self._reject_capture("UNKNOWN_CAPTURE_TIMING")
            return
        if (block.capture_end > now + 1e-9
                or now - block.capture_end + block.timing_uncertainty_s > DEFAULTS.freshness_s):
            self._reject_capture("STALE_AUDIO")
            return
        if self.motion_epoch != c.motion_epoch:
            self.reset()
        if c.state not in {State.WAIT_STRIKE, State.MEASURE, State.VERIFY_UNLOADED}:
            self.reset()
            return
        if c.state == State.VERIFY_UNLOADED:
            if c.unloaded_at is None or (len(c.verification) == 3 and now < c.unloaded_at + DEFAULTS.later_check_s):
                self.reset()
                return
        if block.capture_start - block.timing_uncertainty_s < c.boundary - 1e-9:
            self.reset()
            return
        if self.origin is None:
            self.origin = block.capture_start
        expected_start = self.origin + len(self.buffer) / DEFAULTS.sample_rate
        timestamp_skew = abs(block.capture_start - expected_start)
        if timestamp_skew > self.uncertainty + block.timing_uncertainty_s + 1 / DEFAULTS.sample_rate:
            self._reject_capture("CAPTURE_CLOCK_DISCONTINUITY")
            return
        self.uncertainty = max(self.uncertainty, timestamp_skew + block.timing_uncertainty_s)
        self.buffer = np.concatenate((self.buffer, samples))
        if self.frame_offset is None:
            onset = detect_onset(self.buffer)
            if onset is None:
                # Keep enough quiet baseline while bounding accumulated data.
                if len(self.buffer) > DEFAULTS.sample_rate * 2:
                    kept = DEFAULTS.hop
                    self.origin += (len(self.buffer) - kept) / DEFAULTS.sample_rate
                    self.buffer = self.buffer[-kept:]
                return
            self.strikes += 1
            onset_time = self.origin + onset / DEFAULTS.sample_rate
            prompt_boundary = max(c.boundary, c.wait_started)
            if c.state == State.VERIFY_UNLOADED and len(c.verification) == 3:
                prompt_boundary = max(prompt_boundary, c.unloaded_at + DEFAULTS.later_check_s)
            if onset_time - self.uncertainty < prompt_boundary - 1e-9:
                self._reject_capture("ONSET_BEFORE_PROMPT")
                return
            if not c.strike(self.strikes, onset_time, c.source_epoch):
                self.reset()
                return
            self.state_strike = c.strike_id
            # Conservatively exclude an additional uncertainty interval at the start.
            self.frame_offset = onset + math.ceil((DEFAULTS.attack_exclusion_s + self.uncertainty) * DEFAULTS.sample_rate)
        if self.state_strike is None:
            self._reject_capture("NO_ACTIVE_STRIKE")
            return
        while self.frame_offset + DEFAULTS.frame_length <= len(self.buffer):
            start = self.frame_offset
            frame = self.buffer[start:start + DEFAULTS.frame_length]
            estimate = self.estimator.estimate(frame, c.target_hz)
            # Processing latency counts against freshness and every deadline.
            now = max(now, self.clock())
            c.observe(estimate, FrameContext(self.state_strike, self.motion_epoch,
                self.origin + start / DEFAULTS.sample_rate,
                self.origin + (start + DEFAULTS.frame_length) / DEFAULTS.sample_rate,
                c.source_epoch, self.uncertainty), now)
            self.frame_offset += DEFAULTS.hop
            if c.strike_id is None or c.terminal:
                self.reset()
                return
        if self.frame_offset > DEFAULTS.sample_rate:
            # Trim only samples already consumed by the frame assembler.
            trim = self.frame_offset
            self.buffer = self.buffer[trim:]
            self.origin += trim / DEFAULTS.sample_rate
            self.frame_offset = 0


def validate_route(mode, source_kind, actuator_kind):
    routes = {"SIMULATION": ("generated", "loopback"), "FILE_ANALYSIS": ("wav", "null"),
              "LIVE_MONITOR": ("microphone", "null"), "HARDWARE": ("microphone", "serial")}
    if mode not in routes or routes[mode] != (source_kind, actuator_kind):
        raise ValueError("Audio source and actuator are incompatible with the selected mode")


def run_hardware(profile_path, port, device, target_hz=440, output_dir=Path("runs"), *,
                 input_fn=input, output_fn=print):
    # Validate all commissioning evidence before opening audio/serial devices.
    profile, metadata = load_profile(profile_path, "physical")
    validate_route("HARDWARE", "microphone", "serial")
    output_fn("HARDWARE: supervised single-string session. Read your commissioned stop and unloading procedure.")
    output_fn(metadata["commissioning"]["stop_procedure"])
    confirmation = input_fn("Confirm correct isolated string/pin, secure supported tool, clear mechanism and tested stop. Type CHECKED: ")
    if confirmation != "CHECKED":
        raise ValueError("Operator precheck not confirmed")
    session_id = uuid.uuid4().hex
    log = SessionLog(Path(output_dir) / session_id, {"mode": "HARDWARE", "target_hz": target_hz,
                    "profile": metadata, "profile_sha256": profile.content_hash, "serial_port": port,
                    "audio_device": str(device), "raw_audio_recorded": False, "defaults": asdict(DEFAULTS)})
    actuator = None
    source = None
    controller = None
    try:
        output_fn("Release the physical hold-to-run control for handshake.")
        actuator = SerialActuator.open(port, profile_path, session_id=session_id)
        began = time.monotonic()
        actuator.handshake(began)
        while not actuator.handshake_ok:
            now = time.monotonic()
            events = actuator.poll(now)
            if any(e.get("op") in {"ERROR", "ABORTED"} for e in events) or now - began > .25:
                raise RuntimeError("Device handshake refused or timed out")
            time.sleep(.005)
        if input_fn("Deliberately engage and hold the local enable control, then type ARM: ") != "ARM":
            raise ValueError("Arm not confirmed")
        source = MicrophoneSource(device, timing_uncertainty_s=metadata["commissioning"]["audio_timing_uncertainty_s"])
        source.start()
        controller = SessionController(target_hz, actuator, profile, log, mode="HARDWARE")
        controller.start(time.monotonic(), operator_confirmed=True)
        pipeline = LiveFramePipeline(controller)
        commands: Queue[str] = Queue()
        finished = Event()

        def read_commands():
            while not finished.is_set():
                try:
                    command = input_fn("Commands: stop, unloaded (only after disengaging tool): ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    command = "stop"
                commands.put(command)
                if command == "stop":
                    return

        Thread(target=read_commands, daemon=True, name="operator-input").start()
        previous = None
        try:
            while not controller.terminal:
                now = time.monotonic()
                pending = []
                while True:
                    try:
                        pending.append(commands.get_nowait())
                    except Empty:
                        break
                controller.tick(now, stop_requested="stop" in pending)
                if "unloaded" in pending and not controller.terminal:
                    if controller.confirm_unloaded(now):
                        pipeline.reset()
                block = source.read()
                if block is not None:
                    pipeline.feed(block, time.monotonic())
                snapshot = controller.snapshot()
                key = (snapshot["state"], snapshot["prompt"], snapshot["cents_error"])
                if key != previous:
                    output_fn(f"HARDWARE {snapshot['state']} | {snapshot['prompt']} | {snapshot['cents_error']} cents")
                    previous = key
                finished.wait(.01)
        except KeyboardInterrupt:
            controller.stop(time.monotonic())
        finally:
            finished.set()
        summary = controller.finish_log()
        summary["session_dir"] = str(log.directory)
        return summary
    except Exception as exc:
        if controller is not None:
            controller.fault("RUNTIME_FAILURE", str(exc))
            return controller.finish_log()
        log.emit({"kind": "fault", "code": "PRECHECK_FAILED", "detail": str(exc)})
        log.finish({"schema_version": 1, "mode": "HARDWARE", "outcome": "FAULT",
                    "issued_moves": 0, "faults": [str(exc)]})
        raise
    finally:
        if actuator is not None:
            actuator.close()
        if source is not None:
            source.close()
        log.close()
