"""Run every committed AT02 nominal case; retain rejections and errors."""

import json
import math
import platform
import time
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path

import numpy as np
import scipy

from pianotuner.domain import DEFAULTS, cents_error, midi_to_hz, shift_cents
from pianotuner.dsp import PitchEstimator
from pianotuner.simulation.acoustics import MODEL_VERSION, synthesize


def run_audio_benchmark(output: str | Path, manifest_path: str | Path | None = None) -> dict:
    source = Path(manifest_path) if manifest_path else files("pianotuner").joinpath("data/audio_manifest.json")
    manifest = json.loads(source.read_text(encoding="utf-8"))
    if not manifest.get("cases"):
        raise ValueError("Audio manifest must contain at least one declared case")
    estimator = PitchEstimator()
    cases = []
    for case in manifest["cases"]:
        target_hz = midi_to_hz(case["midi"])
        actual_f1 = shift_cents(target_hz, case["offset_cents"])
        samples = synthesize(actual_f1, seed=case["seed"], inharmonicity=case["inharmonicity"],
                             first_amplitude=case["first_amplitude"], snr_db=case["snr_db"])
        start = time.perf_counter()
        estimate = estimator.estimate_strike(samples, target_hz)
        elapsed_ms = (time.perf_counter() - start) * 1000
        error = None if estimate.frequency_hz is None else abs(cents_error(estimate.frequency_hz, actual_f1))
        estimate_record = {key: (None if isinstance(value, float) and not math.isfinite(value) else value)
                           for key, value in asdict(estimate).items()}
        cases.append({**case, "actual_first_partial_hz": actual_f1, "estimate": estimate_record,
                      "absolute_error_cents": error, "analysis_ms": elapsed_ms})
    accepted = [case for case in cases if case["estimate"]["accepted"]]
    errors = [case["absolute_error_cents"] for case in accepted]
    timings = [case["analysis_ms"] for case in cases]
    summary: dict = {"mode": "SIMULATION", "acceptance_test": "AT02", "model_version": MODEL_VERSION,
               "total": len(cases), "accepted": len(accepted), "coverage": len(accepted) / len(cases),
               "median_absolute_error_cents": float(np.median(errors)) if errors else None,
               "p95_absolute_error_cents": float(np.percentile(errors, 95)) if errors else None,
               "worst_accepted_error_cents": max(errors) if errors else None,
               "analysis_median_ms": float(np.median(timings)), "analysis_p95_ms": float(np.percentile(timings, 95)),
               "timing_scope": "onset detection plus three frames and stability gate; excludes synthesis",
               "environment": {"python": platform.python_version(), "platform": platform.platform(),
                               "numpy": np.__version__, "scipy": scipy.__version__},
               "defaults": asdict(DEFAULTS), "failed_cases": [c["id"] for c in cases if not c["estimate"]["accepted"] or c["absolute_error_cents"] > 5]}
    summary["passed"] = bool(len(cases) == 1040 and len({c["id"] for c in cases}) == 1040
                             and summary["coverage"] >= .95 and errors and np.median(errors) <= 1
                             and np.percentile(errors, 95) <= 2 and max(errors) <= 5)
    folder = Path(output)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "audio-summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (folder / "audio-cases.json").write_text(json.dumps(cases, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/validation")
    args = parser.parse_args()
    result = run_audio_benchmark(args.output)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
