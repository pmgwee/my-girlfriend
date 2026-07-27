"""Qwen3-TTS hosted on Alibaba Cloud Model Studio (DashScope).

Same model family as the local `qwen3` backend, so a voice cloned here sounds
like the one you auditioned locally -- but synthesis runs on Alibaba's GPUs
instead of yours.

Why this exists: measured on an RTX 3060 Laptop, local Qwen3-TTS 1.7B runs at
3.8x realtime (5.7s to produce 1.4s of speech). Generation is slower than
playback, so audio underruns no matter how it's streamed. dtype and streaming
mode made no difference (3.75x-3.91x across four variants). Hosted synthesis is
the only way to get a *cloned* voice in real time on this hardware.

Voice registration is a one-time step: `scripts\register_voice.ps1` uploads the
reference clip, DashScope returns a voice id, and that id goes in .env. Nothing
is uploaded at conversation time -- only the text of each reply.
"""

from __future__ import annotations

import base64
import io
import logging

import httpx
import numpy as np
import soundfile as sf

from .base import TtsBackend

log = logging.getLogger(__name__)

_SYNTHESIS_PATH = "/services/aigc/multimodal-generation/generation"


class DashScopeTts(TtsBackend):
    # Qwen3-TTS emits 24kHz mono.
    sample_rate = 24_000

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model: str = "qwen3-tts-vc-2026-01-22",
        base_url: str = "https://dashscope-intl.aliyuncs.com/api/v1",
        language: str = "zh",
    ) -> None:
        if not api_key.strip():
            raise ValueError(
                "DASHSCOPE_API_KEY is not set.\n"
                "Get one at https://modelstudio.console.alibabacloud.com/ and put it in .env"
            )
        if not voice_id.strip():
            raise ValueError(
                "DASHSCOPE_VOICE_ID is not set.\n"
                "Register your reference clip first:  scripts\\register_voice.ps1"
            )

        self._voice_id = voice_id
        self._model = model
        self._language = language
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            # Read timeout is generous because the first call after an idle
            # period can cold-start; steady-state is a few hundred ms.
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
        )
        log.info("DashScope TTS ready (model=%s, voice=%s)", model, voice_id)

    @property
    def default_voice(self) -> str:
        return self._voice_id

    def synthesize(self, text: str, voice: str, speed: float, emotion: str | None = None) -> np.ndarray:
        text = text.strip()
        if not text:
            return np.zeros(0, dtype=np.float32)

        response = self._client.post(
            f"{self._base_url}{_SYNTHESIS_PATH}",
            json={
                "model": self._model,
                "input": {"text": text, "voice": voice or self._voice_id},
                "parameters": {"language_type": self._language, "format": "wav"},
            },
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"DashScope returned {response.status_code}: {response.text[:300]}"
            )

        payload = response.json()
        audio = self._extract_audio(payload)
        samples, rate = sf.read(io.BytesIO(audio), dtype="float32")
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        self.sample_rate = rate

        if speed != 1.0:
            target = int(samples.size / speed)
            samples = np.interp(
                np.linspace(0, samples.size - 1, target),
                np.arange(samples.size),
                samples,
            ).astype(np.float32)

        return samples.astype(np.float32)

    def _extract_audio(self, payload: dict) -> bytes:
        """Pull the waveform out, whether it came back inline or as a URL.

        DashScope returns base64 for short utterances and a signed URL for
        longer ones, and the exact nesting has moved between API revisions --
        so search rather than index blindly.
        """
        audio = (payload.get("output") or {}).get("audio") or {}

        if encoded := audio.get("data"):
            return base64.b64decode(encoded)

        if url := audio.get("url"):
            fetched = self._client.get(url, timeout=20.0)
            fetched.raise_for_status()
            return fetched.content

        raise RuntimeError(f"no audio in DashScope response: {str(payload)[:300]}")

    def close(self) -> None:
        self._client.close()
