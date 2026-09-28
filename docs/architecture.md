# Software architecture

The software is organized around one supervised, single-string tuning session. A target is fixed at session start. Audio observations drive a bounded state machine; adapters supply input and execute output requests. Neither a GUI callback nor an audio callback commands movement directly.

## Audio path

`dsp/onset.py` detects a new strike. `dsp/estimator.py` estimates the first partial from 48 kHz samples, using 32,768-sample frames, a 4,800-sample hop and a 131,072-point FFT. The first 150 ms after onset are excluded. Zero-padding refines peak interpolation; it does not add independent acoustic information.

The estimator reports frequency, error in cents, signal level, clipping, peak signal-to-noise ratio and refusal reasons. Three stable frames form one accepted strike observation. They never count as three separate verification strikes. Live input also tracks capture timestamps, discontinuities, overflow and motion epochs.

The target range is 220–440 Hz. Measurement capture is limited to ±50 cents, automatic correction to ±20 cents or a tighter commissioned profile. Stale, ambiguous, clipped or weak measurements cannot authorize a move.

## Control path

`control/session.py` implements the deterministic state machine. The main flow is precheck, strike acquisition, measurement, bounded decision, finite movement, settling and a new strike. Stop, transport faults, timeouts, unexpected response, reversals and budget limits terminate the sequence.

Move selection uses a bounded proportional correction and sensitivity limits. Exposure is the sum of absolute requested steps, not net position. An ambiguous result retains the full requested exposure. A move is never automatically retried.

Attached convergence is provisional. Final verification requires confirmed output disable, explicit unloading, three distinct accepted strikes, and a later strike at least 60 seconds after unloading. Failed verification never restarts motion automatically.

## Simulation and independent evaluation

`simulation/acoustics.py` renders decaying string partials and noise. `simulation/plant.py` models movement sensitivity, backlash, direction, unloading shift and adverse behavior. `simulation/firmware.py` implements the same protocol semantics as the portable MCU core.

`runtime/simulation.py` connects mechanics to rendered audio and passes the samples through the production estimator. The controller cannot import or read simulated ground truth. Benchmark evaluation reads truth separately to detect false success. Seeded manifests define the 1,040 audio cases and 100 nominal control sessions before results are collected.

## Modes and adapters

The CLI and Tk interface route through session runtimes. Simulation uses a loopback actuator and virtual clock. WAV analysis uses a null actuator. Live monitoring uses an explicit microphone and never moves anything. The hardware runtime combines microphone and serial adapters only after commissioned-profile validation.

The serial adapter has one serialized writer, bounded I/O, incremental framing, command deadlines and no MOVE retry. Heartbeats originate from healthy runtime ticks. A received disabled-state report is not proof of mechanical unloading or electrical power isolation.

The GUI runs session work outside the Tk thread. It displays mode, target, measurement quality, cents error, state and movement count, and locks active-session settings. Closing or stopping a session requests termination.

## Firmware contract

The portable C++ core uses fixed storage and a strict JSON parser based on pinned jsmn. Requests are ASCII, newline-delimited and limited to 512 bytes. A session has a 32-character lowercase hexadecimal identity and ordered command IDs. The last 32 consumed IDs retain canonical requests and replies.

Duplicate MOVE commands replay replies without repeating motion. Reordered keys and insignificant whitespace do not change request identity. Reusing an ID with a changed payload faults. Duplicate heartbeats do not renew the lease. The minimal stop request is `{"v":1,"op":"STOP"}`.

ARM requires session binding, matching local caps/profile, explicit operator confirmation and a fresh deadman edge. Recovery remains disarmed. The host cannot enlarge firmware caps. The native harness uses a fake clock and simulated outputs; the RP2040 target compiles with zero caps and outputs disabled. Physical pulse and interlock adapters are integration work for this edition.

## Evidence

Each session stores metadata, ordered JSONL events and a summary. Required persistence failures prevent a verified outcome. Raw live audio is not recorded by default. The schemas in `schemas/` describe configuration, observations, protocol frames and evidence.

Tests cover numerical conversions, malformed audio, mode isolation, stale observations, state transitions, logging failures, protocol framing, duplicate suppression, fault precedence and GUI lifecycle behavior. Native tests and shared golden traces check the Python/C++ contract. [Validation](validation.md) separates synthetic results, host checks and unperformed physical tests.
