"""Record reproducible nominal and adverse simulation outcomes without hardware."""
import json
from dataclasses import asdict
from pathlib import Path

from pianotuner.domain import DEFAULTS
from pianotuner.runtime.simulation import SCENARIOS, SimulationSession, nominal_parameters

ROOT = Path(__file__).resolve().parents[1]


def main():
    cases = []
    for scenario in SCENARIOS:
        result = SimulationSession(target_hz=440, seed=7, scenario=scenario,
                                   initial_cents=-10, output_dir=None).run()
        cases.append({key: result[key] for key in (
            "scenario", "outcome", "faults", "issued_moves", "absolute_steps_reserved", "false_success")})
    expected = {"nominal": "SIM_VERIFIED", "lost_ack": "SIM_VERIFIED", "unload_shift": "VERIFY_FAILED"}
    passed = all(case["outcome"] == expected.get(case["scenario"], "FAULT")
                 and not case["false_success"] for case in cases)
    record = {
        "schema_version": 1, "mode": "SIMULATION", "model_version": 1,
        "seed": 7, "target_hz": 440, "initial_cents": -10,
        "base_parameters": {**nominal_parameters(7), "initial_cents": -10},
        "defaults": asdict(DEFAULTS), "cases": cases, "passed": passed,
        "scenario_effects": "Applied by SimulationSession; no_response, inverted direction, +10-cent unloading shift or slip, stale frames, disconnected transport, dropped ACK or DONE.",
        "physical_devices_accessed": False,
    }
    path = ROOT / "artifacts/validation/scenario-outcomes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"{len(cases)} scenarios: {'PASS' if passed else 'FAIL'}; {path}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
