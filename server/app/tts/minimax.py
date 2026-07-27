"""MiniMax Speech via fal.ai -- cloned voice with per-reply emotion.

Chosen after measuring the alternatives:

  * Local Qwen3-TTS 1.7B clones beautifully but runs at 3.8x realtime on a 6GB
    card, i.e. slower than playback. Unusable live.
  * ElevenLabs is the most popular hosted option but its instant cloning wants
    1-5 minutes of reference audio; a 9s clip produces a weak clone.
  * MiniMax clones from ~5s, is Chinese-native, and ranks top of the speech
    arenas. fal.ai fronts it with a plain HTTP API and no ID verification.

Known limitation: MiniMax exposes a fixed seven-value emotion enum with no
entry for 撒娇. app.emotion maps it to `happy`, which is the closest available
and audibly not the same thing. Backends with free-text instruction control
(CosyVoice v3) can express it literally; this one cannot.
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

_TTS_MODEL = "fal-ai/minimax/speech-2.8-hd"
_QUEUE_HOST = "https://fal.run"

# fal expires a cloned voice if it goes unused for 7 days. The pipeline calls
# TTS constantly so this never bites in practice, but if you park the project
# and come back, re-run scripts\clone_voice_fal.ps1 rather than debugging a
# suddenly-invalid voice id.
VOICE_IDLE_EXPIRY_DAYS = 7


class MiniMaxTts(TtsBackend):
    sample_rate = 24_000
    supports_emotion = True

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model: str = _TTS_MODEL,
        speed: float = 1.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError(
                "FAL_KEY is not set.\n"
                "Get one at https://fal.ai/dashboard/keys and put it in .env"
            )
        if not voice_id.strip():
            raise ValueError(
                "MINIMAX_VOICE_ID is not set.\n"
                "Clone your reference clip first:  scripts\\register_voice.ps1 -AudioUrl <url>"
            )

        self._voice_id = voice_id
        self._model = model
        self._speed = speed
        self._client = httpx.Client(
            headers={"Authorization": f"Key {api_key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(connect=5.0, read=45.0, write=10.0, pool=5.0),
        )
        log.info("MiniMax TTS ready (model=%s, voice=%s)", model, voice_id)

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
            f"{_QUEUE_HOST}/{self._model}",
            json={
                "text": text,
                "voice_setting": {
                    "voice_id": voice or self._voice_id,
                    "speed": speed or self._speed,
                    "vol": 1.0,
                    "pitch": 0,
                    "emotion": spec.minimax if spec else "neutral",
                },
                # PCM rather than mp3: no encoder delay, so chunk boundaries
                # line up exactly with the subtitle timing.
                "audio_setting": {"sample_rate": 24000, "format": "pcm"},
            },
        )
        if response.status_code != 200:
            raise RuntimeError(f"fal.ai returned {response.status_code}: {response.text[:300]}")

        return self._decode(response.json())

    def _decode(self, payload: dict) -> np.ndarray:
        audio = payload.get("audio") or {}
        url = audio.get("url")
        if not url:
            raise RuntimeError(f"no audio in fal.ai response: {str(payload)[:300]}")

        fetched = self._client.get(url, timeout=30.0)
        fetched.raise_for_status()
        raw = fetched.content

        # Raw PCM has no header for soundfile to read, so decode it directly and
        # fall back to the container parser if the response was mp3/flac.
        try:
            samples, rate = sf.read(io.BytesIO(raw), dtype="float32")
            if samples.ndim > 1:
                samples = samples.mean(axis=1)
            self.sample_rate = rate
            return samples.astype(np.float32)
        except Exception:
            self.sample_rate = 24_000
            return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    def close(self) -> None:
        self._client.close()
