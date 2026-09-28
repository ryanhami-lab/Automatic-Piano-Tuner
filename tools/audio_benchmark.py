"""Repository convenience wrapper; implementation and manifest ship in wheel."""

import argparse
import json

from pianotuner.reporting.audio_benchmark import run_audio_benchmark

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/validation")
    args = parser.parse_args()
    result = run_audio_benchmark(args.output)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
