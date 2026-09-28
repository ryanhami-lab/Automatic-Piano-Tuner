# Hardware commissioning checklist

**Status: not performed — components and physical setup are unavailable.** This is the future handoff for one guarded string fixture. It is not approval to connect or energize an actuator. Save completed records under a new identified commissioning record; keep original observations and failed tests.

## Identify the exact setup

- [ ] Record Pi model, operating system, Python version, audio device and USB identifiers.
- [ ] Record MCU board, firmware build and source revision, driver and motor part numbers, gearbox and coupling, supply and fuse specifications, datasheet revisions and wiring revision.
- [ ] Record the fixture/string identity, supported steel tip and reaction structure, guarded region and operator procedure.
- [ ] Obtain mechanical/electrical review appropriate to the actual load before using a valuable instrument.
- [ ] Document the independent frequency reference and its acquisition path, resolution and uncertainty. Resolution alone does not establish accuracy.

## Keep the first build non-energizing

- [ ] Confirm the shipped firmware reports physical actuation disabled with zero caps.
- [ ] Inspect the physical profile; all measured fields must be populated from this setup rather than copied from simulation.
- [ ] Verify the driver stays disabled through power-up, MCU reset, disconnected MCU, and local enable loss.
- [ ] Verify signal levels, enable polarity, grounding or isolation, connectors, fusing and physical power interruption against actual datasheets.
- [ ] Document the actual stop/deadman circuit. A GUI Stop request is not the physical emergency stop path.

## Unloaded electronics and guarded dummy load

- [ ] Verify finite pulse count, pulse width, direction setup/hold and bounded duration with an appropriate measurement instrument.
- [ ] Verify Stop, deadman release, heartbeat loss, USB disconnection, host termination, driver fault where available and MCU reset.
- [ ] Measure stop latency and residual motion for each stop mechanism; record method and uncertainty.
- [ ] Confirm no resumed movement after restart, CLEAR_FAULT, reconnect or duplicate MOVE.
- [ ] Confirm absolute movement exposure is retained after aborted or unknown completion.
- [ ] Inspect behavior on motor-power loss and removal of holding torque. Support must keep the assembly controlled.
- [ ] Exercise the microphone on the actual host/Pi: sample rate, discontinuity handling, timestamp mapping, conservative freshness, estimator median and 95th-percentile processing time.

## Calibrate the identified mechanism

- [ ] Measure the sign of a small bounded movement on a guarded fixture.
- [ ] Measure sensitivity bounds in cents per commanded step, minimum useful motion, backlash, stiction and settling.
- [ ] Establish step-rate, per-move steps, duration, pulse timing, cumulative absolute-step budget and permitted initial error for this fixture.
- [ ] Confirm the permitted automatic-entry limit is no greater than 20 cents; tighten it where needed.
- [ ] Document the supported mechanical unload procedure and test it. Driver disable alone is not unloading.
- [ ] Populate a new commissioned profile, compile reviewed local firmware caps, and record matching profile/firmware hashes.
- [ ] Re-run host, firmware and protocol tests after changes. No host command may enlarge local caps.

## Physical single-string acceptance

Run all ten trials as a single recorded series. Do not omit aborted or failed attempts. If a fix is needed, retain that series and begin a new complete series.

| Trial field | Required record |
| --- | --- |
| Identity | Trial ID, session ID, setup and commissioning record, code/profile/firmware hashes |
| Initial condition | Target, independent initial error, permitted band, physical precheck |
| Movement | Issued commands, emitted-pulse counts, unknown results, interlock state, cumulative exposure |
| Attached result | Measured estimate and reason for convergence or termination |
| Unloading | Output disabled, supported tool unloaded/disengaged, operator and timestamp |
| Final observations | Three distinct unloaded strikes, then a fourth at least 60 seconds after unloading |
| Independent comparison | All final reference readings, method, uncertainty, acceptance decision |
| Outcome | Verified, failed, aborted or fault, elapsed time and all relevant failures |

AT10 targets at least 8 of 10 trials with every final reading within ±3 cents and reference uncertainty no greater than 1 cent. All ten trials must respect movement/interlock limits. A fixture result supports a fixture claim only. Any actual-piano test requires a separate R3 record.

## Approval record

Setup identity: __________  Commissioning record: __________  Revision: __________

Reviewed by: __________  Date: __________  Unresolved issues: __________

Permitted next stage: __________  Evidence location: __________
