import json
from pathlib import Path

import jsonschema
import pytest

from pianotuner.reporting.events import SessionLog
from pianotuner.runtime.simulation import SimulationSession

ROOT = Path(__file__).resolve().parents[1]


def schema(name):
    return json.loads((ROOT / f"schemas/{name}.schema.json").read_text())


def test_session_evidence_matches_schemas(tmp_path):
    session = SimulationSession(output_dir=tmp_path)
    result = session.run()
    jsonschema.validate(result, schema("result"))
    for event in session.sink.events:
        jsonschema.validate(event, schema("event"))
    for name in ("simulation", "hardware.UNCOMMISSIONED"):
        jsonschema.validate(json.loads((ROOT / f"configs/{name}.json").read_text()), schema("profile"))


def test_metadata_failure_cannot_publish_successful_summary(tmp_path, monkeypatch):
    log = SessionLog(tmp_path / "session", {"mode": "SIMULATION"})
    original = log._write_json
    def fail_metadata(name, value):
        if name == "metadata.json":
            raise OSError("disk full")
        original(name, value)
    monkeypatch.setattr(log, "_write_json", fail_metadata)
    with pytest.raises(OSError):
        log.finish({"outcome": "SIM_VERIFIED"})
    log.close()
    assert not (log.directory / "summary.json").exists()
