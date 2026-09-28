# Firmware builds

The firmware contains a portable C++17 protocol/control core, a native test harness and an RP2040/Pico SDK target. The RP2040 target has zero motion caps, physical actuation disabled and no configured actuation GPIO. Building it does not flash or contact a board.

## Windows

Use the Python environment from the README. The bootstrap downloads checksum-verified portable toolchains under `.tools/`, without a system installation:

```powershell
.venv/Scripts/python.exe -m pip install -r requirements/firmware-build.lock.txt
.venv/Scripts/python.exe tools/bootstrap_firmware_windows.py
.venv/Scripts/python.exe tools/build_firmware.py --target all
```

Tested versions are LLVM-MinGW 19.1.6 (20241217), Arm GNU 14.2.Rel1, CMake 3.31.6, Ninja 1.11.1.4 and Pico SDK 2.2.0. The SDK is pinned to `a1438dff1d38bd9c65dbd693f0e5db4b9ae91779`. Vendored jsmn retains its revision and license under `firmware/vendor/`.

## Linux

With a C++17 compiler on PATH:

```sh
python -m pip install -r requirements/firmware-build.lock.txt
python tools/build_firmware.py --target native
python tools/fuzz_parser_parity.py build/firmware-native/tuner_native
```

For the RP2040 target, install an Arm bare-metal GCC toolchain and check out the pinned Pico SDK with TinyUSB, then run:

```sh
python tools/build_firmware.py --target rp2040 --sdk /path/to/pico-sdk
```

The native CTests cover allocation-free execution, fragmented/coalesced stream framing and 44 shared Python/C++ golden traces. The parser differential corpus exercises valid and malformed requests against both implementations.

## Outputs and integration

Builds generate command logs and hashes under `artifacts/firmware/`, plus `.elf` and `.bin` images for the disabled target. These generated files are ignored by Git because logs and debug images may contain local paths. CI stores its own build artifacts.

The physical STEP/DIR/ENABLE backend and electrical interlock sampling must be implemented for the selected driver and wiring. Finite pulse counts, deadlines, stop latency, watchdog behavior, direction setup/hold and capture timing must be measured during [commissioning](hardware.md). Native simulated pulse counts are not measured shaft or tuning-pin displacement.
