# Validation of the software edition

This edition has been exercised on Windows and in an isolated Ubuntu 24.04 VM. The results below apply to software and synthesized audio. They do not establish physical tuning accuracy, microphone capture behavior, Raspberry Pi performance or actuator behavior.

## Recorded results

| Check | Result | Report |
| --- | --- | --- |
| Windows Python 3.12.14 | 225 tests passed; lint, type checks, dependency checks, CLI simulation and WAV analysis passed | [Platform summary](../artifacts/validation/platform-summary.json) |
| Ubuntu Python 3.12.3 | 224 tests passed; one Tk lifecycle test skipped without a display; CLI demo and native tests passed | [Platform summary](../artifacts/validation/platform-summary.json) |
| Synthetic first-partial corpus | 1,040/1,040 accepted; median absolute error 0.00246 cents, 95th percentile 0.0160 cents, worst 0.0306 cents | [Summary](../artifacts/validation/audio-summary.json), [all cases](../artifacts/validation/audio-cases.json) |
| Nominal control corpus | 100/100 verified; zero false successes under independent simulated truth | [Summary](../artifacts/validation/control-summary.json), [all cases](../artifacts/validation/control-cases.json) |
| Adverse scenarios | Nine reproducible scenarios matched expected outcomes | [Inputs and outcomes](../artifacts/validation/scenario-outcomes.json) |
| Firmware contract | Three native CTests and 44 shared Python/C++ golden traces passed | [Conformance](../artifacts/validation/firmware-conformance.json) |
| Parser corpus | 5,388 distinct differential cases passed, including 5,303 malformed requests rejected | [Corpus record](../artifacts/validation/parser-fuzz.json) |
| Disabled RP2040 target | Cross-build passed on Windows and Linux with Pico SDK 2.2.0 and Arm GCC 14.2.1 | [Platform summary](../artifacts/validation/platform-summary.json) |

The Windows GUI was exercised directly in simulation and WAV analysis. The Linux headless skip is explicit; it is not a recorded Linux desktop pass. GitHub CI uses a virtual display to exercise that test on Ubuntu.

The public platform summary preserves result counts and source hashes while omitting personal filesystem paths and raw host logs. Current CI produces its own revision-specific evidence. Historical source hashes cover the application and firmware files, which were copied unchanged into this edition.

Initial Linux provisioning attempts required a CPU instruction-set correction and a complete test dependency list. Only the final successful environment contributes to the platform summary.

## Reproduce

After installing `requirements/host.lock.txt` and the package:

```sh
python -m pytest -q
python -m ruff check src tests tools
python -m mypy src
python -m pip check
python -m pianotuner demo --seed 7
python -m pianotuner analyze tests/fixtures/synthetic-a4.wav --note A4
python -m pianotuner benchmark --suite all
python tools/check_scenarios.py
```

On a headless Linux host, use `xvfb-run -a python -m pytest -q` to include the Tk lifecycle test. [Firmware instructions](firmware-build.md) reproduce the native tests and output-disabled cross-build. Source and wheel distributions can be generated with `python -m build --no-isolation`.

All declared benchmark cases remain in their reports. Median processing time on the recorded Windows host was 7.10 ms, with an 8.13 ms 95th percentile for onset detection and three-frame analysis, excluding synthesis. These are machine-specific processing times, not end-to-end live latency.

## Physical boundary

The hardware route refuses the supplied uncommissioned profile. Physical motor outputs remain disabled. Driver-specific pulse timing, pin assignments, electrical interlocks, direction/sensitivity calibration, live capture timing and independent-reference string trials have not been performed for this edition. See the [commissioning checklist](hardware.md).
