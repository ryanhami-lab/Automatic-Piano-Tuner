"""Non-actuating file and microphone analysis."""
import math
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from threading import Event

import numpy as np

from pianotuner.adapters.audio import MicrophoneSource, load_wav
from pianotuner.domain import DEFAULTS
from pianotuner.dsp import PitchEstimator
from pianotuner.ports.actuator import NullActuator
from pianotuner.reporting.events import SessionLog


def json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def analyze_file(path, target_hz=440, channel=None, output_dir=Path("runs"), stop_event=None):
    stop_event = stop_event or Event()
    audio = load_wav(path, channel=channel)
    actuator = NullActuator()
    actuator.disarm(0)
    session_id = uuid.uuid4().hex
    log = SessionLog(Path(output_dir) / session_id, {"mode": "FILE_ANALYSIS", "target_hz": target_hz,
        "source_file_sha256": audio.sha256, "source_file": audio.source_path,
        "source_sample_rate": audio.source_sample_rate, "analysis_sample_rate": audio.sample_rate,
        "channel": audio.selected_channel, "raw_audio_recorded": False})
    try:
        estimate = None if stop_event.is_set() else PitchEstimator().estimate_strike(audio.samples, target_hz)
        result: dict = json_safe(asdict(estimate)) if estimate else {"accepted": False, "reasons": ["OPERATOR_STOP"]}
        if audio.source_clipped_fraction > DEFAULTS.maximum_clipped_fraction:
            result.update(accepted=False, reasons=[*result["reasons"], "SOURCE_CLIPPING"])
        log.emit({"kind": "analysis", "mode": "FILE_ANALYSIS", **result})
        summary = {"schema_version": 1, "mode": "FILE_ANALYSIS", "outcome": "ABORTED" if stop_event.is_set() else "ANALYSIS_COMPLETE",
                   "target_hz": target_hz, "measurement": result, "issued_moves": 0,
                   "session_dir": str(log.directory), "source_file_sha256": audio.sha256,
                   "physical_tests_run": False}
        log.finish(summary)
        return summary
    finally:
        log.close()


def monitor(device, target_hz=440, seconds=30, output_dir=Path("runs"), callback=None,
            stop_event=None, source=None):
    if not math.isfinite(seconds) or not 0 < seconds <= 3600:
        raise ValueError("Monitor duration must be 0..3600 seconds")
    stop_event = stop_event or Event()
    source = source or MicrophoneSource(device)
    estimator = PitchEstimator()
    log = SessionLog(Path(output_dir) / uuid.uuid4().hex, {"mode": "LIVE_MONITOR", "target_hz": target_hz,
                     "device": str(device), "raw_audio_recorded": False})
    buffer = np.empty(0)
    started = time.monotonic()
    accepted = rejected = 0
    outcome, faults = "ANALYSIS_COMPLETE", []
    try:
        source.start()
        while time.monotonic() - started < seconds and not stop_event.is_set():
            block = source.read()
            if block is None:
                stop_event.wait(.01)
                continue
            if block.gap or block.overflow:
                buffer = np.empty(0)
                rejected += 1
                log.emit({"kind": "rejection", "reason": "AUDIO_DISCONTINUITY"})
                continue
            buffer = np.concatenate((buffer, block.samples))[-DEFAULTS.frame_length:]
            if len(buffer) < DEFAULTS.frame_length:
                continue
            estimate = estimator.estimate(buffer, target_hz)
            accepted += estimate.accepted
            rejected += not estimate.accepted
            event = {"kind": "observation", "mode": "LIVE_MONITOR", **json_safe(asdict(estimate))}
            log.emit(event)
            if callback:
                callback(event)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        outcome, faults = "FAULT", [f"{type(exc).__name__}: {exc}"]
    finally:
        source.close()
    summary = {"schema_version": 1, "mode": "LIVE_MONITOR", "outcome": outcome, "issued_moves": 0,
               "accepted_observations": accepted, "rejected_observations": rejected,
               "faults": faults, "session_dir": str(log.directory), "physical_tests_run": False}
    try:
        log.finish(summary)
    finally:
        log.close()
    return summary
