"""Qwen3-TTS through qwentts.cpp's OpenAI-compatible server.

This is the backend the original build uses, and it sounds noticeably better than
Kokoro on Mandarin -- it also supports zero-shot voice cloning and dialects
(Sichuanese, Beijing). The cost is that qwentts.cpp has no prebuilt Windows
binaries, so you compile it once. See README 'Upgrading to Qwen3-TTS'.

We talk to its HTTP server rather than binding the library so the TTS process can
crash, be rebuilt, or move to another machine without taking the pipeline down.
"""

from __future__ import annotations

import io
import logging

import httpx
import numpy as np
import soundfile as sf

from .base import TtsBackend

log = logging.getLogger(__name__)


class QwenTts(TtsBackend):
    sample_rate = 24_000

    def __init__(self, base_url: str, default_voice: str = "cherry") -> None:
        self._base_url = base_url.rstrip("/")
        self._default_voice = default_voice
        # Generous read timeout: a cold qwentts.cpp server takes a few seconds to
        # page the model in on the very first request.
        self._client = httpx.Client(timeout=httpx.Timeout(connect=3.0, read=30.0, write=5.0, pool=3.0))

        try:
            self._client.get(f"{self._base_url}/v1/audio/voices", timeout=3.0)
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"qwentts.cpp server unreachable at {self._base_url}. "
                "Start it with scripts/run_qwentts.ps1, or set TTS_BACKEND=kokoro in .env."
            ) from exc
        log.info("Qwen3-TTS ready at %s (voice=%s)", self._base_url, default_voice)

    @property
    def default_voice(self) -> str:
        return self._default_voice

    def synthesize(self, text: str, voice: str, speed: float, emotion: str | None = None) -> np.ndarray:
        text = text.strip()
        if not text:
            return np.zeros(0, dtype=np.float32)

        response = self._client.post(
            f"{self._base_url}/v1/audio/speech",
            json={
                "model": "qwen3-tts",
                "input": text,
                "voice": voice or self._default_voice,
                "response_format": "wav",
                # Low temperature keeps prosody stable across turns; the default
                # wanders enough that she sounds like a different person each reply.
                "temperature": 0.7,
                "top_p": 0.9,
                "repetition_penalty": 1.05,
            },
        )
        response.raise_for_status()

        samples, rate = sf.read(io.BytesIO(response.content), dtype="float32")
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        self.sample_rate = rate

        if speed != 1.0:
            # qwentts.cpp has no speed parameter, so resample to change tempo.
            # This shifts pitch slightly; keep speed within ~0.9-1.1.
            target = int(samples.size / speed)
            samples = np.interp(
                np.linspace(0, samples.size - 1, target),
                np.arange(samples.size),
                samples,
            ).astype(np.float32)

        return samples

    def close(self) -> None:
        self._client.close()
