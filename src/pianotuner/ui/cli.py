import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from pianotuner.domain import DEFAULTS, Target
from pianotuner.runtime.analysis import analyze_file, monitor
from pianotuner.runtime.simulation import SCENARIOS, SimulationSession


def _target(args):
    return Target.from_hz(args.hz).hz if args.hz is not None else Target.named(args.note).hz


def build_parser():
    parser = argparse.ArgumentParser(description="Supervised single-string tuner. Hardware pending commissioning.")
    sub = parser.add_subparsers(dest="command")
    for name, help_text in [("demo", "Run the audio-driven SIMULATION"), ("analyze", "Analyze a WAV without motion"),
                            ("monitor", "Monitor an explicitly selected microphone without motion"),
                            ("hardware", "Run only with a commissioned physical profile and local interlocks")]:
        p = sub.add_parser(name, help=help_text)
        target = p.add_mutually_exclusive_group()
        target.add_argument("--note", default="A4", help="A3 through A4")
        target.add_argument("--hz", type=float, help="Custom frequency, 220–440 Hz")
        p.add_argument("--output", type=Path, default=Path("runs"))
        if name == "demo":
            p.add_argument("--seed", type=int, default=7)
            p.add_argument("--scenario", choices=SCENARIOS, default="nominal")
            p.add_argument("--initial-cents", type=float)
            p.add_argument("--trace", action="store_true")
        if name == "analyze":
            p.add_argument("wav", type=Path)
            p.add_argument("--channel", type=int, help="Explicit zero-based channel for multichannel audio")
        if name in {"monitor", "hardware"}:
            p.add_argument("--device", required=True, help="Microphone name or numeric device index")
        if name == "monitor":
            p.add_argument("--seconds", type=float, default=30)
        if name == "hardware":
            p.add_argument("--profile", type=Path, required=True)
            p.add_argument("--port", required=True)
    sub.add_parser("gui", help="Launch the local Tkinter simulation view")
    p = sub.add_parser("benchmark", help="Generate reproducible simulated acceptance reports")
    p.add_argument("--suite", choices=["audio", "control", "all"], default="all")
    p.add_argument("--output", type=Path, default=Path("artifacts/validation"))
    sub.add_parser("defaults", help="Print the canonical software defaults")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command in {None, "gui"}:
            from pianotuner.ui.gui import launch_gui
            launch_gui()
            return 0
        if args.command == "defaults":
            result = asdict(DEFAULTS)
        elif args.command == "demo":
            def trace(snapshot):
                if args.trace:
                    print(f"SIMULATION {snapshot['elapsed_s']:6.2f}s {snapshot['state']:18s} "
                          f"{snapshot['cents_error']} cents | {snapshot['prompt']}", file=sys.stderr)
            result = SimulationSession(_target(args), args.seed, args.scenario, args.output,
                                       args.initial_cents).run(callback=trace if args.trace else None)
        elif args.command == "analyze":
            result = analyze_file(args.wav, _target(args), args.channel, args.output)
        elif args.command == "monitor":
            device = int(args.device) if args.device.isdecimal() else args.device
            result = monitor(device, _target(args), args.seconds, args.output,
                             callback=lambda e: print(json.dumps(e), file=sys.stderr))
        elif args.command == "hardware":
            from pianotuner.runtime.hardware import run_hardware
            device = int(args.device) if args.device.isdecimal() else args.device
            result = run_hardware(args.profile, args.port, device, _target(args), args.output)
        elif args.command == "benchmark":
            result = {}
            if args.suite in {"audio", "all"}:
                # Script is also usable directly from a checkout; module is packaged for installations.
                from pianotuner.reporting.audio_benchmark import run_audio_benchmark
                result["audio"] = run_audio_benchmark(args.output)
            if args.suite in {"control", "all"}:
                from pianotuner.runtime.benchmark import run_control_benchmark
                result["control"] = run_control_benchmark(args.output)
            result["passed"] = all(v["passed"] for v in result.values())
        else:
            parser.error("Unknown command")
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0 if result.get("outcome") not in {"FAULT", "VERIFY_FAILED"} and result.get("passed", True) else 1
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        print(f"pianotuner: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Stopped by operator", file=sys.stderr)
        return 130
