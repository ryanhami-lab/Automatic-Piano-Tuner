"""Canonical software defaults from Product Specification v1.0, section 5."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Defaults:
    sample_rate: int = 48_000
    frame_length: int = 32_768
    hop: int = 4_800
    fft_length: int = 131_072
    attack_exclusion_s: float = 0.150
    settle_s: float = 0.250
    freshness_s: float = 0.250
    capture_cents: float = 50.0
    auto_entry_cents: float = 20.0
    attached_cents: float = 2.0
    verify_cents: float = 3.0
    stable_frames: int = 3
    stability_cents: float = 1.5
    minimum_rms_dbfs: float = -50.0
    maximum_clipped_fraction: float = 0.001
    minimum_peak_snr_db: float = 12.0
    alpha: float = 0.5
    max_pitch_change_cents: float = 2.0
    max_moves: int = 20
    max_reversals: int = 2
    no_response_moves: int = 2
    no_response_cents: float = 0.25
    jump_cents: float = 8.0
    run_timeout_s: float = 180.0
    strike_timeout_s: float = 30.0
    verification_strikes: int = 3
    later_check_s: float = 60.0
    heartbeat_interval_s: float = 0.100
    heartbeat_timeout_s: float = 0.500
    move_duration_s: float = 0.250
    ack_timeout_s: float = 0.250
    result_grace_s: float = 0.100
    wire_frame_bytes: int = 512


DEFAULTS = Defaults()
