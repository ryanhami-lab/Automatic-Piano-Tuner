"""Deterministic strict-parser differential corpus; native outputs stay disabled.

Each input gets a fresh default-disabled firmware model on both sides. The
corpus combines systematic type/grammar/boundary cases and seeded byte edits.
It does not open a serial port, flash firmware, or access physical devices.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pianotuner.simulation.firmware import FirmwareModel  # noqa: E402


def corpus(seed: int = 20260927, random_cases: int = 5000) -> list[str]:
    session = "0123456789abcdef0123456789abcdef"
    requests = [
        {"v": 1, "session": session, "id": 1, "op": "HELLO"},
        {"v": 1, "session": session, "id": 1, "op": "MOVE", "steps": -8,
         "rate_hz": 100, "max_duration_ms": 200},
        {"v": 1, "session": session, "id": 1, "op": "ARM", "profile_hash": "simulation-v1",
         "operator_confirmed": True},
        {"v": 1, "op": "STOP"},
    ]
    bases = [json.dumps(value, separators=(",", ":")) for value in requests]
    cases = list(bases)
    numeric_tokens = ["0", "-0", "1", "-1", "2147483647", "-2147483647", "2147483648",
                      "-2147483648", "9999999999999999999999999999999999999999999", "01", "-01",
                      "+1", "1.", ".1", "1.0", "1e0", "1e999", "NaN", "Infinity", "null",
                      "true", "false", '"1"', "[]", "{}", "", "--1", "0x1"]
    for base, request in zip(bases, requests, strict=True):
        for key, value in request.items():
            old = f'{json.dumps(key)}:{json.dumps(value, separators=(",", ":"))}'
            for token in numeric_tokens:
                cases.append(base.replace(old, f'{json.dumps(key)}:{token}'))
            cases.append(base[:-1] + "," + old + "}")
        cases.extend([
            base + " false", "false " + base, base[:-1] + ",}", base.replace(",", ":", 1),
            base.replace(":", ",", 1), base.replace(",", "", 1), base.replace(":", "", 1),
            base.replace('"op"', '"o\\u0070"'), base.replace('"op"', '"op\t"'),
            base.replace('"op"', '"unknown"'), base.replace("{", "[", 1),
            "\t " + base + " \t", base.replace(":", "\t : \t"), base.replace(",", "\r , \r"),
        ])
    for length in (0, 1, 31, 32, 33, 64, 65, 255, 500):
        cases.append(bases[0].replace(session, "a" * length))
        cases.append(bases[2].replace("simulation-v1", "x" * length))
    for length in (509, 510, 511, 512, 513, 1024):
        cases.append(bases[3] + " " * (length - len(bases[3])))
    rng = random.Random(seed)
    alphabet = '{}[],:"0123456789-+.eEtrufalsn \\_\t\x00\x1f\x7f'
    for _ in range(random_cases):
        base = rng.choice(bases)
        for _ in range(rng.randrange(1, 6)):
            index = rng.randrange(len(base) + 1)
            operation = rng.randrange(4)
            if operation == 0:
                base = base[:index] + rng.choice(alphabet) + base[index:]
            elif operation == 1 and index < len(base):
                base = base[:index] + base[index + 1:]
            elif operation == 2 and index < len(base):
                base = base[:index] + rng.choice(alphabet) + base[index + 1:]
            else:
                base = base[:index] + base[max(0, index - 3):index] + base[index:]
        cases.append(base)
    # De-duplicate while keeping the declared deterministic evaluation order.
    return list(dict.fromkeys(cases))


def run(native: str | Path, seed: int = 20260927, random_cases: int = 5000) -> dict:
    cases = corpus(seed, random_cases)
    payload = "".join("@reset\n" + case + "\n" for case in cases).encode("ascii")
    completed = subprocess.run([str(Path(native).resolve())], input=payload, capture_output=True,
                               check=True, timeout=60)
    rows = [json.loads(row) for row in completed.stdout.decode("ascii").splitlines()]
    if len(rows) != len(cases) * 2:
        raise AssertionError(f"Native reply count {len(rows)}; expected {len(cases) * 2}")
    failures = []
    accepted = 0
    for index, case in enumerate(cases):
        expected = FirmwareModel().command(case + "\n", 0)
        actual = rows[index * 2 + 1]
        accepted += not any(e.get("code") in {"MALFORMED", "FRAME_TOO_LONG"} for e in expected)
        if rows[index * 2] != [] or actual != expected:
            failures.append({"case": index, "input": case, "expected": expected, "actual": actual})
    return {"suite": "AT06 strict parser differential fuzz", "seed": seed,
            "random_mutation_attempts": random_cases, "distinct_cases": len(cases),
            "parsed_cases": accepted, "rejected_cases": len(cases) - accepted,
            "corpus_sha256": hashlib.sha256(payload).hexdigest(),
            "default_disabled": True, "physical_devices_accessed": False,
            "passed": not failures, "failures": failures}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("native")
    parser.add_argument("--seed", type=int, default=20260927)
    parser.add_argument("--cases", type=int, default=5000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args.native, args.seed, args.cases)
    serialized = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    raise SystemExit(0 if report["passed"] else 1)
