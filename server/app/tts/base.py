"""TTS adapter interface.

Synthesis is per-sentence rather than streaming-within-a-sentence. The LLM stage
already splits its output at clause boundaries, so each call here handles a short
fragment and returns in ~150-400ms -- short enough that intra-sentence streaming
would add complexity without changing how it feels.
"""

from __future__ import annotations

import abc

import numpy as np


class TtsBackend(abc.ABC):
    """Returns float32 mono audio in [-1, 1] plus its native sample rate.

    Implementations must be safe to call from a worker thread and must not hold
    the GIL for long stretches.
    """

    #: Native output rate. The orchestrator resamples to whatever the client wants.
    sample_rate: int = 24_000

    #: Backends that can vary delivery per utterance set this True. Cloning and
    #: emotion control are separate capabilities -- Qwen3-TTS-Base clones but has
    #: no emotion dial, CustomVoice has emotion but only preset voices. Only
    #: MiniMax and CosyVoice 2 do both.
    supports_emotion: bool = False

    #: How many synthesis calls the orchestrator may run at once. Stateful or
    #: non-re-entrant runtimes (ONNX, a single GPU context) stay at 1 so calls
    #: serialise; stateless HTTP backends can raise it to overlap one chunk's
    #: synthesis with the previous chunk's playback. That overlap is what removes
    #: the silence between sentences on a high-latency hosted backend -- without
    #: it, chunk k+1 cannot start rendering until chunk k finishes, so after a
    #: short first sentence the listener waits in silence for the next synthesis.
    concurrency: int = 1

    @abc.abstractmethod
    def synthesize(
        self, text: str, voice: str, speed: float, emotion: str | None = None
    ) -> np.ndarray:
        """Render `text`. Return an empty array for input with nothing speakable.

        `emotion` is one of the keys in app.emotion.EMOTIONS, or None. Backends
        that ignore it still produce correct speech, just flat delivery.
        """

    def warmup(self) -> None:
        """Run one throwaway synthesis so the first real reply isn't slow.

        Most backends lazily build their G2P tables or CUDA graphs on first call,
        which otherwise lands as a 2-3 second stall on her opening line.
        """
        try:
            self.synthesize("你好", self.default_voice, 1.0, None)
        except Exception:  # noqa: BLE001 - warmup is best-effort
            pass

    @property
    def default_voice(self) -> str:
        return ""

    def close(self) -> None:
        return None
