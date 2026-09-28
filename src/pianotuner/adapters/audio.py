"""Offline WAV and optional microphone adapters. Importing opens no device."""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly

from pianotuner.domain import DEFAULTS, finite_number


@dataclass(frozen=True)
class WavAudio:
    samples: np.ndarray
    sample_rate: int
    source_sample_rate: int
    source_channels: int
    selected_channel: int
    sha256: str
    source_path: str
    source_clipped_fraction: float


def load_wav(path: str | Path, channel: int | None = None, target_rate: int = DEFAULTS.sample_rate) -> WavAudio:
    """Normalize PCM using its storage type and antialias with polyphase FIR.

    SciPy returns 24-bit PCM left-aligned in int32, so int32 normalization
    deliberately uses 2**31. No channel averaging is performed implicitly.
    """
    if type(target_rate) is not int or target_rate <= 0:
        raise ValueError("target_rate must be a positive integer")
    source = Path(path)
    rate, raw = wavfile.read(source)
    if type(rate) is not int or rate <= 0 or raw.ndim not in (1, 2) or raw.size == 0:
        raise ValueError("Invalid or empty WAV")
    channels = 1 if raw.ndim == 1 else raw.shape[1]
    if channels > 1 and channel is None:
        raise ValueError("Multichannel WAV requires an explicit zero-based channel")
    selected = 0 if channel is None else channel
    if type(selected) is not int or not 0 <= selected < channels:
        raise ValueError("Channel index outside WAV channel range")
    values = raw if raw.ndim == 1 else raw[:, selected]
    if values.dtype == np.uint8:
        samples = (values.astype(np.float64) - 128.0) / 128.0
    elif np.issubdtype(values.dtype, np.signedinteger):
        samples = values.astype(np.float64) / float(2 ** (8 * values.dtype.itemsize - 1))
    elif np.issubdtype(values.dtype, np.floating):
        samples = values.astype(np.float64)
    else:
        raise ValueError(f"Unsupported WAV sample encoding: {values.dtype}")
    if not np.all(np.isfinite(samples)):
        raise ValueError("WAV contains nonfinite samples")
    source_clipping = float(np.mean(np.abs(samples) >= 0.999))
    if rate != target_rate:
        divisor = math.gcd(rate, target_rate)
        samples = resample_poly(samples, target_rate // divisor, rate // divisor)
    return WavAudio(samples, target_rate, rate, channels, selected,
                    sha256(source.read_bytes()).hexdigest(), str(source.resolve()), source_clipping)


read_wav = load_wav


@dataclass(frozen=True)
class AudioBlock:
    samples: np.ndarray
    sample_rate: int
    sample_start: int
    sample_end: int
    capture_start: float | None
    capture_end: float | None
    timing_uncertainty_s: float
    source_epoch: str
    overflow: bool
    gap: bool

    @property
    def timing_known(self) -> bool:
        return self.capture_start is not None and self.capture_end is not None and math.isfinite(self.timing_uncertainty_s)


class MicrophoneSource:
    """Single-producer/single-consumer preallocated audio ring.

    The callback copies audio and records timestamps only. Allocation/FFT/UI/
    file work belongs to the consumer. Unknown capture-time uncertainty remains
    infinite, making it unusable for motion. Commissioned timing uncertainty
    must be supplied explicitly; fake tests do not validate physical capture.
    """

    def __init__(self, device: int | str, *, stream_factory: Callable[..., Any] | None = None,
                 clock: Callable[[], float] = time.monotonic, blocksize: int = DEFAULTS.hop,
                 max_blocks: int = 16, timing_uncertainty_s: float | None = None) -> None:
        if device is None or isinstance(device, bool) or not isinstance(device, (int, str)):
            raise ValueError("Select a microphone device explicitly")
        if type(blocksize) is not int or blocksize <= 0 or type(max_blocks) is not int or max_blocks < 2:
            raise ValueError("Invalid capture ring dimensions")
        if timing_uncertainty_s is not None and finite_number(timing_uncertainty_s, "timing_uncertainty_s") < 0:
            raise ValueError("Timing uncertainty must be nonnegative")
        self.device = device
        self._factory = stream_factory
        self._clock = clock
        self.blocksize = blocksize
        self.capacity = max_blocks
        self._samples = np.empty((max_blocks, blocksize), dtype=np.float32)
        self._indices = np.empty(max_blocks, dtype=np.int64)
        self._times = np.empty(max_blocks, dtype=np.float64)
        self._uncertainties = np.empty(max_blocks, dtype=np.float64)
        self._flags = np.empty((max_blocks, 2), dtype=np.bool_)
        self._write = 0
        self._read = 0
        self._sample_index = 0
        self._pending_gap = False
        self._stream = None
        self._offset: float | None = None
        self._mapping_uncertainty = math.inf
        self._declared_uncertainty = timing_uncertainty_s
        self.source_epoch = uuid4().hex

    def start(self) -> None:
        if self._stream is not None:
            raise RuntimeError("Microphone already started")
        self._write = self._read = self._sample_index = 0
        self._offset = None
        self._mapping_uncertainty = math.inf
        self._pending_gap = False
        self.source_epoch = uuid4().hex
        if self._factory is None:
            try:
                import sounddevice
            except ImportError as exc:
                raise RuntimeError("Install the live extra to use microphone capture") from exc
            factory = sounddevice.InputStream
        else:
            factory = self._factory
        stream = factory(device=self.device, samplerate=DEFAULTS.sample_rate,
                         channels=1, dtype="float32", blocksize=self.blocksize, callback=self._callback)
        self._stream = stream
        try:
            if stream.samplerate != DEFAULTS.sample_rate:
                raise RuntimeError("Microphone did not accept 48000 Hz; streaming resampling is unsupported")
            stream.start()
            before = self._clock()
            device_now = float(stream.time)
            after = self._clock()
            if math.isfinite(device_now) and after >= before:
                self._offset = (before + after) / 2 - device_now
                self._mapping_uncertainty = (after - before) / 2
        except Exception:
            self.close()
            raise

    def _callback(self, indata, frames: int, timing, status) -> None:
        index = self._sample_index
        self._sample_index += frames
        if frames != self.blocksize or indata.shape != (frames, 1) or self._write - self._read >= self.capacity:
            self._pending_gap = True
            return
        slot = self._write % self.capacity
        self._samples[slot, :] = indata[:, 0]
        self._indices[slot] = index
        self._times[slot] = math.nan
        self._uncertainties[slot] = math.inf
        if self._offset is not None:
            try:
                adc_time = float(timing.inputBufferAdcTime)
                current_time = float(timing.currentTime)
                if math.isfinite(adc_time) and math.isfinite(current_time) and current_time >= adc_time:
                    self._times[slot] = adc_time + self._offset
                    if self._declared_uncertainty is not None:
                        drift = abs(self._clock() - (current_time + self._offset))
                        self._uncertainties[slot] = self._declared_uncertainty + self._mapping_uncertainty + drift
            except (AttributeError, TypeError, ValueError):
                pass
        self._flags[slot, 0] = bool(status)
        self._flags[slot, 1] = self._pending_gap
        self._pending_gap = False
        self._write += 1

    def read(self) -> AudioBlock | None:
        if self._read >= self._write:
            return None
        slot = self._read % self.capacity
        samples = self._samples[slot].copy()
        index = int(self._indices[slot])
        timestamp = float(self._times[slot])
        capture_start = timestamp if math.isfinite(timestamp) else None
        capture_end = None if capture_start is None else capture_start + self.blocksize / DEFAULTS.sample_rate
        result = AudioBlock(samples, DEFAULTS.sample_rate, index, index + self.blocksize,
                            capture_start, capture_end, float(self._uncertainties[slot]), self.source_epoch,
                            bool(self._flags[slot, 0]), bool(self._flags[slot, 1]))
        self._read += 1
        return result

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_args) -> None:
        self.close()
