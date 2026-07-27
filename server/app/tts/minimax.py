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

# Attempts before giving up on one chunk. Two retries covers a transient stall
# without letting a genuinely broken call spin for a minute.
_MAX_ATTEMPTS = 3


class MiniMaxTts(TtsBackend):
    sample_rate = 24_000
    supports_emotion = True
    # fal.ai fronts this with a stateless HTTP API, so two chunks can render at
    # once: chunk k+1 synthesises while chunk k plays, closing the inter-sentence
    # gap that a single in-flight request leaves on a ~2-5s backend.
    concurrency = 2
    # fal scales the model down when idle; the next call then costs 15-22s
    # instead of ~3s. That penalty lands exactly where it hurts -- the first
    # thing she says after you've been quiet for a moment.
    needs_keepwarm = True

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
            # Short read timeout on purpose. A healthy call is 2-4s; anything
            # past ~9s is a stalled request, not a slow one. Observed in a live
            # session: two calls of 7 and 11 characters took 24.8s and 26.4s
            # while direct calls immediately afterwards ran at 1.8s -- a
            # transient provider stall. Waiting it out cost 26s of dead air;
            # abandoning and retrying costs a few seconds.
            timeout=httpx.Timeout(connect=5.0, read=9.0, write=10.0, pool=5.0),
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
        return self._with_retry(text, voice, speed, spec)

    def _with_retry(self, text: str, voice: str, speed: float, spec) -> np.ndarray:
        """Abandon a stalled request and try again rather than waiting it out.

        A retry usually lands on a healthy worker and returns in the normal 2-4s,
        so the worst case becomes ~2x normal instead of an open-ended stall.
        """
        last: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                return self._request(text, voice, speed, spec)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
                log.warning(
                    "TTS attempt %d/%d stalled (%s) -- retrying",
                    attempt + 1, _MAX_ATTEMPTS, type(exc).__name__,
                )
        raise RuntimeError(
            f"TTS failed after {_MAX_ATTEMPTS} attempts: {last}"
        ) from last

    def _request(self, text: str, voice: str, speed: float, spec) -> np.ndarray:
        # `voice` is ignored unless it is a real MiniMax id. TTS_VOICE and the
        # persona's `voice:` field carry backend-specific names (Kokoro's
        # zf_001, or the placeholder "cloned"), and forwarding one of those
        # gets a 422 "Voice with id ... does not exist".
        voice_id = voice if voice.startswith("Voice") else self._voice_id
        response = self._client.post(
            f"{_QUEUE_HOST}/{self._model}",
            json={
                "text": text,
                "voice_setting": {
                    "voice_id": voice_id,
                    "speed": speed or self._speed,
                    "vol": 1.0,
                    "pitch": 0,
                    "emotion": spec.minimax if spec else "neutral",
                },
                # mp3, not pcm. Measured: 36KB vs 86KB per chunk and ~370ms
                # faster to fetch, which is ~10% off a turn. The reason PCM was
                # here originally -- avoiding encoder delay at chunk boundaries
                # -- costs about 10ms, well under perception, and playback
                # timing is derived from the decoded sample count anyway.
                "audio_setting": {"sample_rate": 24000, "format": "mp3", "bitrate": 128000},
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
