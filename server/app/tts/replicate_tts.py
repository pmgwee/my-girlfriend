"""MiniMax Speech via Replicate -- cloned voice with per-reply emotion.

Chosen over fal.ai for two reasons: half the price ($50 vs $100 per million
characters) and direct card billing instead of a prepaid credit balance.

MiniMax is a commercial API that Replicate proxies rather than a set of weights
it runs on its own GPUs, so the 30-60s cold starts that affect open-weight
models here should not apply. `scripts\\bench_replicate.ps1` measures it rather
than trusting that.

Known limitation, same as any MiniMax host: the emotion parameter is a fixed
seven-value enum with no 撒娇. app.emotion maps it to `happy`, which is audibly
not the same. Most of the coquettish effect is carried by word choice in the
persona prompt instead, which survives the provider switch.
"""

from __future__ import annotations

import io
import logging

import httpx
import numpy as np
import soundfile as sf

from ..emotion import EMOTIONS
from .base import TtsBackend

log = logging.getLogger(__name__)

_API = "https://api.replicate.com/v1"


class ReplicateTts(TtsBackend):
    sample_rate = 24_000
    supports_emotion = True

    def __init__(
        self,
        api_token: str,
        voice_id: str,
        model: str = "minimax/speech-02-hd",
    ) -> None:
        if not api_token.strip():
            raise ValueError(
                "REPLICATE_API_TOKEN is not set.\n"
                "Get one at https://replicate.com/account/api-tokens"
            )
        if not voice_id.strip():
            raise ValueError(
                "REPLICATE_VOICE_ID is not set.\n"
                "Clone your clip first:  scripts\\clone_voice_replicate.ps1"
            )

        self._voice_id = voice_id
        self._model = model
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
                # Blocks until the prediction finishes instead of returning a
                # polling URL. Without this every utterance costs an extra
                # round trip plus poll interval.
                "Prefer": "wait",
            },
            timeout=httpx.Timeout(connect=5.0, read=90.0, write=10.0, pool=5.0),
        )
        log.info("Replicate TTS ready (model=%s, voice=%s)", model, voice_id)

    @property
    def default_voice(self) -> str:
        return self._voice_id

    def synthesize(
        self, text: str, voice: str, speed: float, emotion: str | None = None
    ) -> np.ndarray:
        text = text.strip()
        if not text:
            return np.zeros(0, dtype=np.float32)

        spec = EMOTIONS.get(emotion or "")
        response = self._client.post(
            f"{_API}/models/{self._model}/predictions",
            json={
                "input": {
                    "text": text,
                    "voice_id": voice or self._voice_id,
                    "speed": speed,
                    "volume": 1.0,
                    "pitch": 0,
                    "emotion": spec.minimax if spec else "neutral",
                    "sample_rate": 24000,
                    "bitrate": 128000,
                    "channel": "mono",
                    # Keeps Mandarin from being mangled when a reply mixes in
                    # an English word, which happens constantly in Taiwanese
                    # casual speech ("好 chill", "OK 啦").
                    "language_boost": "Chinese",
                }
            },
        )
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Replicate returned {response.status_code}: {response.text[:300]}"
            )

        return self._decode(response.json())

    def _decode(self, payload: dict) -> np.ndarray:
        status = payload.get("status")
        if status == "failed":
            raise RuntimeError(f"Replicate prediction failed: {payload.get('error')}")
        if status not in ("succeeded", None):
            # Prefer: wait should prevent this, but a long queue can still time
            # out into a polling response rather than a finished one.
            raise RuntimeError(
                f"Replicate returned status {status!r} -- prediction did not "
                "complete within the wait window"
            )

        output = payload.get("output")
        url = output if isinstance(output, str) else (output or [None])[0]
        if not url:
            raise RuntimeError(f"no audio URL in Replicate response: {str(payload)[:300]}")

        fetched = self._client.get(url, timeout=30.0, headers={})
        fetched.raise_for_status()

        samples, rate = sf.read(io.BytesIO(fetched.content), dtype="float32")
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        self.sample_rate = rate
        return samples.astype(np.float32)

    def close(self) -> None:
        self._client.close()
