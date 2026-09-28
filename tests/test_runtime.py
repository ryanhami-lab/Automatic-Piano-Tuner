import ast
import json
from pathlib import Path
from threading import Event

import pytest

from pianotuner.domain.profile import MotionProfile, load_profile
from pianotuner.ports.actuator import NullActuator
from pianotuner.runtime.analysis import analyze_file
from pianotuner.runtime.hardware import run_hardware, validate_route
from pianotuner.runtime.simulation import SimulationSession

ROOT = Path(__file__).resolve().parents[1]


def test_nominal_session_log_and_evaluator_agree(tmp_path):
    session = SimulationSession(output_dir=tmp_path, seed=7)
    result = session.run()
    assert result["outcome"] == "SIM_VERIFIED" and not result["false_success"]
    directory = Path(result["session_dir"])
    assert json.loads((directory / "summary.json").read_text()) == result
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    assert len([e for e in events if e["kind"] == "move"]) == result["issued_moves"]
    assert len([e for e in events if e["kind"] == "verification"]) == 4
    assert all(e["mode"] == "SIMULATION" for e in events)
    assert not session.actuator.port.model.enabled


@pytest.mark.parametrize("scenario,reason", [("no_response", "NO_RESPONSE"),
    ("wrong_direction", "DIRECTION_MISMATCH"), ("disconnect", "TRANSPORT_FAILURE"),
    ("unload_shift", "UNLOADED_OUT_OF_TOLERANCE"), ("sudden_slip", "UNEXPECTED_JUMP"),
    ("stale_audio", "STRIKE_TIMEOUT"), ("lost_done", "MOVE_TIMEOUT")])
def test_adverse_simulations_end_without_success(scenario, reason):
    result = SimulationSession(scenario=scenario, output_dir=None, initial_cents=-10).run()
    assert result["outcome"] in {"FAULT", "VERIFY_FAILED"}
    assert reason in result["faults"]
    if scenario == "stale_audio":
        assert result["issued_moves"] == 0


def test_ui_close_request_cancels_session():
    stop = Event()
    seen = []
    def callback(snapshot):
        seen.append(snapshot)
        if snapshot["state"] == "MOVE":
            stop.set()
    result = SimulationSession(output_dir=None).run(callback=callback, stop_event=stop)
    assert result["outcome"] == "ABORTED" and result["issued_moves"] == 1


def test_file_analysis_never_uses_motion(tmp_path, monkeypatch):
    monkeypatch.setattr("pianotuner.adapters.serial.SerialActuator.open",
                        lambda *a, **kw: pytest.fail("File analysis opened a serial port"))
    result = analyze_file(ROOT / "tests/fixtures/synthetic-a4.wav", output_dir=tmp_path)
    assert result["mode"] == "FILE_ANALYSIS" and result["issued_moves"] == 0
    assert result["measurement"]["accepted"] and abs(result["measurement"]["cents_error"]) < 1


def test_null_actuator_refuses_motion():
    with pytest.raises(RuntimeError):
        NullActuator().move(1, 1, 1, 0)


@pytest.mark.parametrize("route", [("HARDWARE", "wav", "serial"), ("SIMULATION", "microphone", "serial"),
    ("FILE_ANALYSIS", "wav", "loopback"), ("LIVE_MONITOR", "microphone", "serial")])
def test_mode_isolation(route):
    with pytest.raises(ValueError):
        validate_route(*route)


def test_uncommissioned_profile_rejected_before_io(tmp_path, monkeypatch):
    monkeypatch.setattr("pianotuner.adapters.serial.SerialActuator.open",
                        lambda *a, **kw: pytest.fail("Opened uncommissioned hardware"))
    with pytest.raises(ValueError, match="uncommissioned"):
        run_hardware(ROOT / "configs/hardware.UNCOMMISSIONED.json", "COM1", "mic", output_dir=tmp_path)


def test_simulation_profile_cannot_be_used_physically():
    with pytest.raises(ValueError):
        load_profile(ROOT / "configs/simulation.json", "physical")


@pytest.mark.parametrize("field,value", [("tighten_sign", True), ("rate_hz", True), ("max_move_steps", 0),
    ("sensitivity_upper_cents_per_step", float("nan")), ("auto_entry_limit_cents", 21),
    ("max_duration_ms", 251), ("total_absolute_steps", -1)])
def test_motion_profile_rejects_invalid_values(field, value):
    with pytest.raises(ValueError):
        MotionProfile(**{field: value})


def test_configuration_rejects_unknown_fields_and_duplicate_keys(tmp_path):
    data = json.loads((ROOT / "configs/simulation.json").read_text())
    data["motion"]["typo"] = 1
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_profile(path)
    path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError, match="Duplicate"):
        load_profile(path)


def test_core_cannot_import_simulator_or_live_adapters():
    for folder in (ROOT / "src/pianotuner/control", ROOT / "src/pianotuner/dsp"):
        for path in folder.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "evaluator_truth" not in text
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.ImportFrom):
                    assert not any(s in (node.module or "") for s in ("simulation", "adapters", "tkinter"))


def test_manifest_assignments_are_fixed_and_packaged():
    source = json.loads((ROOT / "tests/fixtures/control-nominal-v1.json").read_text())
    packaged = json.loads((ROOT / "src/pianotuner/data/control_manifest.json").read_text())
    assert source == packaged and len(source["cases"]) == 100
    assert {c["tighten_sign"] for c in source["cases"]} == {-1, 1}
    assert all(.20 <= c["sensitivity"] <= .25 and abs(c["initial_cents"]) <= 20 for c in source["cases"])
