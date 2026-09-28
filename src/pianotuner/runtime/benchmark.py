"""Declared software acceptance suites. All attempts remain in the denominator."""
import json
import platform
from importlib.resources import files
from pathlib import Path

from pianotuner.runtime.simulation import SimulationSession, nominal_parameters


def run_control_benchmark(output="artifacts/validation", manifest=None):
    if manifest is None:
        manifest = json.loads(files("pianotuner").joinpath("data/control_manifest.json").read_text(encoding="utf-8"))["cases"]
    cases = []
    for params in manifest:
        result = SimulationSession(seed=params["seed"], parameters=params, output_dir=None).run()
        cases.append({"parameters": params, **result})
    successes = sum(c["outcome"] == "SIM_VERIFIED" for c in cases)
    false_successes = sum(c["false_success"] for c in cases)
    summary = {"acceptance_test": "AT04", "mode": "SIMULATION", "model_version": 1,
               "total": len(cases), "verified": successes, "success_fraction": successes / len(cases),
               "false_successes": false_successes,
               "passed": len(cases) == 100 and successes >= 95 and false_successes == 0,
               "failed_seeds": [c["seed"] for c in cases if c["outcome"] != "SIM_VERIFIED" or c["false_success"]],
               "environment": {"platform": platform.platform(), "python": platform.python_version()},
               "physical_tests_run": False}
    folder = Path(output)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "control-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (folder / "control-cases.json").write_text(json.dumps(cases, indent=2) + "\n", encoding="utf-8")
    return summary


def generate_control_manifest(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({"model_version": 1, "description": "Fictional nominal plant assignments fixed before AT04",
                                    "cases": [nominal_parameters(seed) for seed in range(100)]}, indent=2) + "\n", encoding="utf-8")
