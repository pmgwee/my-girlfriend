"""Audio format helpers.

The pipeline's canonical format is 16kHz mono int16. The browser gives us
whatever its AudioContext runs at (usually 48kHz float32), and TTS backends emit
22.05kHz or 24kHz float32, so conversion happens only at those two edges.
"""

from __future__ import annotations

import numpy as np
from scipy import signal


def float32_to_pcm16(samples: np.ndarray) -> bytes:
    """Clamp then scale to int16. Clipping matters: TTS output routinely
    overshoots 1.0 slightly and wrapping would produce audible crackle."""
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16).tobytes()


def pcm16_to_float32(raw: bytes) -> np.ndarray:
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Polyphase resampling. Higher quality than naive decimation and fast
    enough to run per-chunk on the audio thread."""
    if src_rate == dst_rate:
        return samples
    gcd = np.gcd(src_rate, dst_rate)
    return signal.resample_poly(samples, dst_rate // gcd, src_rate // gcd)


def rms(samples: np.ndarray) -> float:
    """Loudness of a chunk, used to drive the orb and the avatar's mouth."""
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples))))
