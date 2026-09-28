"""Regenerate interchange schemas. Runtime profile/protocol validators are stricter."""
import json
from dataclasses import asdict
from pathlib import Path

from pianotuner.domain.profile import MotionProfile

ROOT = Path(__file__).resolve().parents[1]


def write(name, schema):
    folder = ROOT / "schemas"
    folder.mkdir(exist_ok=True)
    (folder / f"{name}.schema.json").write_text(json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": name, **schema}, indent=2) + "\n", encoding="utf-8")


def main():
    motion = {}
    for key, value in asdict(MotionProfile()).items():
        motion[key] = {"type": "string" if isinstance(value, str) else "integer" if isinstance(value, int) else "number"}
    motion["profile_kind"] = {"enum": ["simulation", "physical"]}
    for key in ("sensitivity_lower_cents_per_step", "sensitivity_upper_cents_per_step", "tighten_sign"):
        motion[key] = {"type": ["number", "null"]}
    write("profile", {"type": "object", "required": ["schema_version", "motion", "physical_actuation_enabled", "commissioning"],
        "additionalProperties": False, "properties": {"schema_version": {"const": 1},
        "physical_actuation_enabled": {"type": "boolean"}, "commissioning": {"type": ["object", "null"]},
        "motion": {"type": "object", "required": list(motion), "properties": motion, "additionalProperties": False}}})
    scalar = {"type": ["string", "number", "boolean", "null"]}
    keys = ("event_id time_s kind mode state value profile_hash strike_id onset motion_epoch accepted reasons "
            "frequency_hz cents_error steps rate_hz max_duration_ms move_count absolute_steps result "
            "operator_confirmed outcome reason detail true_cents_error within_final_band code rms_dbfs "
            "peak_snr_db clipped_fraction frame_count spread_cents")
    event = {key: dict(scalar) for key in keys.split()}
    event["reasons"] = {"type": "array", "items": {"type": "string"}}
    event["result"] = {"type": "object"}
    event["event_id"] = {"type": "integer", "minimum": 1}
    write("event", {"type": "object", "required": ["event_id", "kind"], "properties": event, "additionalProperties": False})
    result_keys = ("schema_version mode outcome target_hz final_cents_error issued_moves absolute_steps_reserved "
        "accepted_observations rejected_observations faults elapsed_s verification log_failed physical_tests_run "
        "independent_physical_reference session_id session_dir scenario seed simulation_evaluator false_success "
        "measurement source_file_sha256")
    result = {key: {} for key in result_keys.split()}
    result["schema_version"] = {"const": 1}
    result["mode"] = {"enum": ["SIMULATION", "FILE_ANALYSIS", "LIVE_MONITOR", "HARDWARE"]}
    result["outcome"] = {"enum": ["SIM_VERIFIED", "HARDWARE_VERIFIED", "ANALYSIS_COMPLETE", "ABORTED", "VERIFY_FAILED", "FAULT"]}
    result["issued_moves"] = {"type": "integer", "minimum": 0}
    result["faults"] = {"type": "array", "items": {"type": "string"}}
    write("result", {"type": "object", "required": ["schema_version", "mode", "outcome", "issued_moves"],
                     "properties": result, "additionalProperties": False})
    write("observation", {"type": "object", "required": ["frequency_hz", "cents_error", "accepted", "reasons", "rms_dbfs"],
        "additionalProperties": False, "properties": {"frequency_hz": {"type": ["number", "null"], "exclusiveMinimum": 0},
            "cents_error": {"type": ["number", "null"]}, "accepted": {"type": "boolean"},
            "reasons": {"type": "array", "items": {"type": "string"}},
            "rms_dbfs": {"type": ["number", "null"]}, "peak_snr_db": {"type": ["number", "null"]},
            "clipped_fraction": {"type": "number", "minimum": 0, "maximum": 1},
            "frame_count": {"type": "integer", "minimum": 1}, "spread_cents": {"type": ["number", "null"]}}})


if __name__ == "__main__":
    main()
