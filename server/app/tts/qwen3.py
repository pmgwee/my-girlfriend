"""Qwen3-TTS with zero-shot voice cloning.

This is the backend that makes her sound like a specific person rather than a
stock voice. You supply 5-10 seconds of clean reference audio and every reply is
synthesised in that timbre.

Unlike the Kokoro backend, this one needs torch + CUDA. That is the whole cost of
cloning: Kokoro's voices are a fixed embedding bank with no way to add your own.

Latency note: `create_voice_clone_prompt` encodes the reference audio once at
startup and the result is reused for every utterance. Passing `ref_audio` on each
call instead would re-encode it every time and add ~300ms per sentence.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from .base import TtsBackend

log = logging.getLogger(__name__)

# The model takes a language name, not an ISO code.
_LANGUAGE_NAMES = {
    "zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean",
    "de": "German", "fr": "French", "ru": "Russian", "pt": "Portuguese",
    "es": "Spanish", "it": "Italian",
}


class Qwen3Tts(TtsBackend):
    # Overwritten with whatever the model actually returns on first synthesis.
    sample_rate = 24_000

    def __init__(
        self,
        model_name: str,
        ref_audio: Path,
        ref_text: str,
        language: str = "zh",
        device: str = "cuda",
        dtype: str = "bfloat16",
    ) -> None:
        import torch
        from qwen_tts import Qwen3TTSModel

        if not ref_audio.exists():
            raise FileNotFoundError(
                f"Reference audio not found: {ref_audio}\n"
                "Create one with:  scripts\\make_voice.ps1 -Source your_audio.mp3 -Start 12 -Duration 8"
            )
        if not ref_text.strip():
            raise ValueError(
                "QWEN3_REF_TEXT is empty. It must be an exact transcript of the "
                "reference audio -- cloning quality drops sharply without it."
            )

        self._language = _LANGUAGE_NAMES.get(language, "Chinese")

        log.info("loading Qwen3-TTS %s on %s (%s)...", model_name, device, dtype)
        self._model = Qwen3TTSModel.from_pretrained(
            model_name,
            device_map=device if device != "cpu" else None,
            dtype=getattr(torch, dtype),
            # Not flash_attention_2: the flash-attn wheel needs a CUDA compiler
            # and rarely builds on Windows. sdpa is built into torch and is
            # within a few percent on sequences this short.
            attn_implementation="sdpa",
        )

        log.info("encoding reference voice from %s", ref_audio.name)
        self._clone_prompt = self._model.create_voice_clone_prompt(
            ref_audio=str(ref_audio), ref_text=ref_text.strip()
        )
        log.info("Qwen3-TTS ready, cloning '%s'", ref_audio.stem)

    @property
    def default_voice(self) -> str:
        # The voice comes from the reference audio, not a named preset.
        return "cloned"

    def synthesize(self, text: str, voice: str, speed: float, emotion: str | None = None) -> np.ndarray:
        text = text.strip()
        if not text:
            return np.zeros(0, dtype=np.float32)

        wavs, rate = self._model.generate_voice_clone(
            text=text,
            language=self._language,
            voice_clone_prompt=self._clone_prompt,
        )

        samples = np.asarray(wavs[0] if isinstance(wavs, (list, tuple)) else wavs, dtype=np.float32)
        if samples.ndim > 1:
            samples = samples.reshape(-1)
        self.sample_rate = int(rate)

        if speed != 1.0:
            # No native speed control, so resample. This shifts pitch slightly;
            # keep within roughly 0.9-1.1 or she stops sounding like herself.
            target = int(samples.size / speed)
            samples = np.interp(
                np.linspace(0, samples.size - 1, target),
                np.arange(samples.size),
                samples,
            ).astype(np.float32)

        return samples

    def warmup(self) -> None:
        try:
            self.synthesize("你好", self.default_voice, 1.0)
        except Exception as exc:  # noqa: BLE001 - warmup is best-effort
            log.warning("Qwen3-TTS warmup failed: %s", exc)
