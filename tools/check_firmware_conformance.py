"""Run identical committed golden traces through Python and native firmware."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from pianotuner.simulation.firmware import FirmwareModel, FirmwareProfile  # noqa: E402


def python_trace(lines: list[str], simulated: bool) -> list[list[dict]]:
    model = FirmwareModel(FirmwareProfile.simulation() if simulated else None)
    now = 0.0
    result = []
    for line in lines:
        if line.startswith("@time "):
            now = int(line[6:]) / 1_000_000
            events = model.tick(now)
        elif line.startswith("@inputs "):
            deadman, stop, fault = (bool(int(x)) for x in line[8:].split())
            events = model.set_inputs(deadman, stop, fault, now)
        elif line == "@reset":
            model = FirmwareModel(FirmwareProfile.simulation() if simulated else None)
            events = []
        elif line.startswith("@stall "):
            model.pulse_stalled = line[7:] == "1"
            events = []
        else:
            events = model.command(line + "\n", now)
        result.append(events)
    return result


def run(native: str | Path) -> dict:
    trace_file = ROOT / "tests/conformance/protocol_traces.json"
    traces = json.loads(trace_file.read_text(encoding="utf-8"))
    results = []
    for trace in traces:
        lines = trace["lines"]
        args = [str(Path(native).resolve())] + (["--simulation"] if trace["simulation"] else [])
        completed = subprocess.run(args, input="\n".join(lines) + "\n", text=True,
                                   capture_output=True, check=True, timeout=10)
        actual = [json.loads(x) for x in completed.stdout.splitlines()]
        expected = python_trace(lines, trace["simulation"])
        if actual != expected:
            for i, (py, cpp) in enumerate(zip(expected, actual, strict=False)):
                if py != cpp:
                    raise AssertionError(f'{trace["name"]} line {i+1}: {lines[i]}\nPython: {py}\nNative: {cpp}')
            raise AssertionError(f'{trace["name"]}: response count {len(actual)} != {len(expected)}')
        # Golden assertions prevent two identical wrong implementations passing parity.
        flat = [event for row in actual for event in row]
        for assertion in trace.get("expect", []):
            matching = [event for event in flat if all(event.get(k) == v for k, v in assertion.items())]
            if not matching:
                raise AssertionError(f'{trace["name"]}: missing expected outcome {assertion}')
        results.append(dict(name=trace["name"], commands=len(lines), passed=True))
    return dict(schema_version=1, suite="AT06", hardware_tested=False,
                traces=len(results), passed=True, results=results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("native")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args.native)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f'AT06: {report["traces"]} Python/native golden traces passed; hardware untested.')
