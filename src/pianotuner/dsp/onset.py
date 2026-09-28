"""Energy-rise onset detector; a prompt alone is never a strike."""

import numpy as np

from pianotuner.domain import DEFAULTS


def detect_onset(samples, sample_rate: int = DEFAULTS.sample_rate) -> int | None:
    x = np.asarray(samples, dtype=np.float64)
    if type(sample_rate) is not int or sample_rate <= 0 or x.ndim != 1 or not np.all(np.isfinite(x)):
        return None
    block = max(1, round(sample_rate * 0.005))
    count = len(x) // block
    if count < 6:
        return None
    energies = np.sqrt(np.mean(x[:count * block].reshape(count, block) ** 2, axis=1))
    for index in range(4, count - 1):
        baseline = max(float(np.median(energies[index - 4:index])), 1e-7)
        threshold = max(10 ** (DEFAULTS.minimum_rms_dbfs / 20), baseline * 4.0)
        if energies[index] >= threshold and energies[index + 1] >= threshold:
            # Conservatively place onset at the start of the detected block.
            return index * block
    return None
