"""Strict session configuration. Fictional simulator values are never physical defaults."""
import json
import math
from dataclasses import asdict, dataclass, fields
from hashlib import sha256
from pathlib import Path


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate configuration field: {key}")
        result[key] = value
    return result


@dataclass(frozen=True)
class MotionProfile:
    profile_kind: str = "simulation"
    profile_id: str = "simulation-v1"
    tighten_sign: int = 1
    sensitivity_lower_cents_per_step: float = 0.20
    sensitivity_upper_cents_per_step: float = 0.25
    minimum_steps: int = 1
    max_move_steps: int = 8
    rate_hz: int = 100
    max_duration_ms: int = 250
    total_absolute_steps: int = 256
    auto_entry_limit_cents: float = 20.0
    direction_setup_us: int = 10
    pulse_width_us: int = 10
    direction_hold_us: int = 10

    def __post_init__(self):
        if self.profile_kind not in {"simulation", "physical"}:
            raise ValueError("Unknown profile kind")
        if type(self.tighten_sign) is not int or self.tighten_sign not in (-1, 1):
            raise ValueError("tighten_sign must be +1 or -1")
        for field in ("minimum_steps", "max_move_steps", "rate_hz", "max_duration_ms",
                      "total_absolute_steps", "direction_setup_us", "pulse_width_us", "direction_hold_us"):
            value = getattr(self, field)
            if type(value) is not int or not 0 < value <= 2**31 - 1:
                raise ValueError(f"{field} must be a positive integer")
        for field in ("sensitivity_lower_cents_per_step", "sensitivity_upper_cents_per_step",
                      "auto_entry_limit_cents"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{field} must be finite and positive")
        if self.sensitivity_lower_cents_per_step > self.sensitivity_upper_cents_per_step:
            raise ValueError("Reversed sensitivity interval")
        if self.auto_entry_limit_cents > 20 or self.max_duration_ms > 250:
            raise ValueError("Profile may only tighten product limits")
        if self.minimum_steps > self.max_move_steps or self.max_move_steps > self.total_absolute_steps:
            raise ValueError("Inconsistent movement limits")
        if self.pulse_width_us * 2 * self.rate_hz >= 1_000_000:
            raise ValueError("Pulse cannot fit within period")
        if not isinstance(self.profile_id, str) or not self.profile_id:
            raise ValueError("Profile ID required")

    @property
    def content_hash(self):
        return sha256(json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_profile(path, expected_kind="simulation") -> tuple[MotionProfile, dict]:
    raw = Path(path).read_bytes()
    data = json.loads(raw, object_pairs_hook=_pairs,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f"Invalid constant {x}")))
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data.get("schema_version") != 1:
        raise ValueError("Expected profile schema version 1")
    expected = {"schema_version", "motion", "physical_actuation_enabled", "commissioning"}
    if set(data) != expected or not isinstance(data["motion"], dict):
        raise ValueError("Profile fields do not match schema")
    if expected_kind == "physical" and data["physical_actuation_enabled"] is not True:
        raise ValueError("Physical actuation is uncommissioned and disabled")
    if set(data["motion"]) != {field.name for field in fields(MotionProfile)}:
        raise ValueError("Every motion field must be explicit; no inherited calibration defaults")
    try:
        profile = MotionProfile(**data["motion"])
    except TypeError as exc:
        raise ValueError("Unknown or invalid motion fields") from exc
    if profile.profile_kind != expected_kind:
        raise ValueError("Profile kind does not match mode")
    if expected_kind == "physical":
        if data["physical_actuation_enabled"] is not True:
            raise ValueError("Physical actuation is uncommissioned and disabled")
        required = {"device_identity", "profile_revision", "firmware_build", "driver", "logic_interface",
                    "step_polarity", "full_steps_per_revolution", "microsteps_per_full_step", "gearbox_ratio",
                    "record_path", "record_sha256", "stop_procedure", "unload_procedure", "firmware_profile_hash",
                    "audio_timing_uncertainty_s"}
        record = data["commissioning"]
        if not isinstance(record, dict) or set(record) != required or any(v is None or v == "" for v in record.values()):
            raise ValueError("Complete commissioning record required")
        numeric_fields = {"full_steps_per_revolution", "microsteps_per_full_step", "gearbox_ratio", "audio_timing_uncertainty_s"}
        for key in required - numeric_fields:
            if not isinstance(record[key], str) or not record[key].strip():
                raise ValueError(f"Commissioning field {key} must be nonempty text")
        for field in ("full_steps_per_revolution", "microsteps_per_full_step", "gearbox_ratio"):
            value = record[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"Invalid commissioning units: {field}")
        evidence = Path(path).parent / record["record_path"]
        if not evidence.is_file() or sha256(evidence.read_bytes()).hexdigest() != record["record_sha256"]:
            raise ValueError("Missing or mismatched commissioning evidence")
        if record["firmware_profile_hash"] != profile.content_hash:
            raise ValueError("Firmware/profile capability hash mismatch")
        uncertainty = record["audio_timing_uncertainty_s"]
        if isinstance(uncertainty, bool) or not isinstance(uncertainty, (int, float)) or not math.isfinite(uncertainty) or not 0 <= uncertainty <= .25:
            raise ValueError("Audio capture timing uncertainty must be measured and at most 250 ms")
    elif data["physical_actuation_enabled"] is not False or data["commissioning"] is not None:
        raise ValueError("Simulation profile must remain nonphysical")
    return profile, {**data, "file_sha256": sha256(raw).hexdigest()}
