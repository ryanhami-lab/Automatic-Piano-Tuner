"""Reproducible native/disabled Pico builds, with no flashing or device access."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("native", "rp2040", "all"), default="native")
    parser.add_argument("--sdk", type=Path, default=ROOT / ".tools/pico-sdk")
    args = parser.parse_args()
    env = dict(os.environ)
    prefixes = [str(Path(sys.executable).parent)]
    native_bin = ROOT / ".tools/llvm-mingw-20241217-ucrt-x86_64/bin"
    arm_bin = ROOT / ".tools/bin"
    prefixes += [str(p) for p in (native_bin, arm_bin) if p.exists()]
    env["PATH"] = os.pathsep.join(prefixes + [env.get("PATH", "")])
    cmake = shutil.which("cmake", path=env["PATH"])
    ctest = shutil.which("ctest", path=env["PATH"])
    if not cmake or not ctest:
        raise SystemExit("Install tested build requirements: pip install -r requirements/firmware-build.lock.txt")
    out = ROOT / "artifacts/firmware"
    out.mkdir(parents=True, exist_ok=True)
    commands = []

    def execute(command):
        completed = subprocess.run(list(map(str, command)), cwd=ROOT, env=env, text=True,
                                   encoding="utf-8", errors="replace", capture_output=True)
        log = dict(command=list(map(str, command)), exit_code=completed.returncode,
                   stdout=completed.stdout, stderr=completed.stderr)
        commands.append(log)
        print(completed.stdout[-4000:], end="")
        if completed.returncode:
            print(completed.stderr)
            (out / "build-log.json").write_text(json.dumps(commands, indent=2) + "\n")
            raise SystemExit(completed.returncode)

    targets = ("native", "rp2040") if args.target == "all" else (args.target,)
    for target in targets:
        build = ROOT / "build" / ("firmware-native" if target == "native" else "firmware-pico")
        configure = [cmake, "-S", ROOT / "firmware", "-B", build, "-G", "Ninja",
                     f"-DTUNER_TARGET={target}", f"-DPython3_EXECUTABLE={sys.executable}"]
        if target == "native" and native_bin.exists():
            configure += [f"-DCMAKE_C_COMPILER={native_bin / 'clang.exe'}",
                          f"-DCMAKE_CXX_COMPILER={native_bin / 'clang++.exe'}"]
        elif target == "rp2040":
            configure += ["-DPICO_BOARD=pico", f"-DPICO_SDK_PATH={args.sdk}", "-DPICO_NO_PICOTOOL=1"]
            if arm_bin.exists():
                configure += [f"-DPICO_TOOLCHAIN_PATH={arm_bin.parent}"]
        execute(configure)
        execute([cmake, "--build", build])
        if target == "native":
            execute([ctest, "--test-dir", build, "--output-on-failure"])
            native = build / ("tuner_native.exe" if os.name == "nt" else "tuner_native")
            execute([sys.executable, ROOT / "tools/check_firmware_conformance.py", native,
                     "--output", ROOT / "artifacts/validation/firmware-conformance.json"])
        else:
            for extension in ("bin", "elf"):
                shutil.copy2(build / f"pianotuner_disabled.{extension}", out)
    execute([cmake, "--version"])
    compiler = shutil.which("arm-none-eabi-g++" if "rp2040" in targets else "clang++", path=env["PATH"])
    if compiler:
        execute([compiler, "--version"])
    (out / "build-log.json").write_text(json.dumps(commands, indent=2) + "\n", encoding="utf-8")
    files = {p.name: dict(bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
             for p in out.glob("pianotuner_disabled.*")}
    record = dict(schema_version=1, platform=platform.platform(), python=sys.version,
                  physical_actuation_enabled=False, max_move_steps=0, max_rate_hz=0,
                  max_duration_ms=0, absolute_step_budget=0, gpio_pins_configured=[],
                  hardware_tested=False, pico_sdk_commit="a1438dff1d38bd9c65dbd693f0e5db4b9ae91779",
                  source_sha256={str(p.relative_to(ROOT)).replace("\\", "/"):hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in sorted((ROOT / "firmware").rglob("*")) if p.is_file()},
                  outputs=files)
    (out / "build-record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print("Build records saved to artifacts/firmware; no device accessed.")


if __name__ == "__main__":
    main()
