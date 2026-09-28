"""Save reproducible host validation logs without opening a physical device."""
import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pianotuner

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmarks", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/validation/windows-host.json")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    checks = [
        ("tests", ["-m", "pytest", "-q"]),
        ("lint", ["-m", "ruff", "check", "src", "tests", "tools"]),
        ("types", ["-m", "mypy", "src/pianotuner"]),
        ("dependencies", ["-m", "pip", "check"]),
        ("simulation", ["-m", "pianotuner", "demo", "--seed", "7", "--output", "artifacts/example-sessions"]),
        ("wav_analysis", ["-m", "pianotuner", "analyze", "tests/fixtures/synthetic-a4.wav", "--output", "runs/validation"]),
    ]
    if args.benchmarks:
        checks.append(("benchmarks", ["-m", "pianotuner", "benchmark", "--suite", "all", "--output", "artifacts/validation"]))
    results = []
    for name, command in checks:
        process = subprocess.run([sys.executable, *command], cwd=ROOT, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=300)
        results.append({"name": name, "command": [sys.executable, *command], "exit_code": process.returncode,
                        "stdout": process.stdout, "stderr": process.stderr})
        print(f"{name}: {'PASS' if process.returncode == 0 else 'FAIL'}")
    record = {"schema_version": 1, "time_utc": datetime.now(UTC).isoformat(),
              "platform": platform.platform(), "python": sys.version,
              "imported_package": pianotuner.__file__, "hardware_tested": False,
              "source_sha256": {str(p.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in sorted((ROOT / "src").rglob("*.py"))},
              "checks": results, "passed": all(r["exit_code"] == 0 for r in results)}
    args.output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
