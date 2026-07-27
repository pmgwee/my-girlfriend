"""CosyVoice on Alibaba Model Studio -- cloned voice WITH emotion control.

This is the only combination that satisfies all of:
  * her cloned timbre, which carries the Taiwanese accent automatically
  * per-reply emotion, driven by natural-language instructions
  * real-time synthesis

Qwen3-TTS splits those apart deliberately: the -Base/-vc tier clones but has no
emotion dial, and -CustomVoice has emotion but only nine preset voices (so it
would lose both her voice and her accent). CosyVoice v3 does both at once.

Model choice matters. `cosyvoice-v2` does NOT support the instruction parameter
-- only the v3/v3.5 series do -- and Singapore hosts the v3 series while Beijing
hosts v3.5. Picking v2 silently gives you flat delivery with no error.
"""

from __future__ import annotations

import io
import logging

import numpy as np
import soundfile as sf

from ..emotion import EMOTIONS
from .base import TtsBackend

log = logging.getLogger(__name__)


class CosyVoiceTts(TtsBackend):
    sample_rate = 24_000
    supports_emotion = True

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model: str = "cosyvoice-v3-flash",
        websocket_url: str = "",
        base_instruction: str = "",
    ) -> None:
        import dashscope

        if not api_key.strip():
            raise ValueError(
                "DASHSCOPE_API_KEY is not set.\n"
                "Get one at https://modelstudio.console.alibabacloud.com/"
            )
        if not voice_id.strip():
            raise ValueError(
                "DASHSCOPE_VOICE_ID is not set.\n"
                "Register your clip first:  scripts\\register_voice.ps1 -AudioUrl <url>"
            )
        if model.startswith("cosyvoice-v2") or model.startswith("cosyvoice-v1"):
            log.warning(
                "%s ignores the instruction parameter -- every reply will sound "
                "flat. Use cosyvoice-v3-flash (Singapore) or cosyvoice-v3.5-flash "
                "(Beijing) for emotion control.",
                model,
            )
            self.supports_emotion = False

        dashscope.api_key = api_key
        if websocket_url:
            dashscope.base_websocket_api_url = websocket_url

        self._model = model
        self._voice_id = voice_id
        # Prepended to every instruction. Use it for traits that never change,
        # like accent, so each reply only has to carry its mood.
        self._base_instruction = base_instruction.strip()
        log.info("CosyVoice ready (model=%s, voice=%s)", model, voice_id)

    @property
    def default_voice(self) -> str:
        return self._voice_id

    def _instruction_for(self, emotion: str | None) -> str | None:
        parts = [p for p in (self._base_instruction,) if p]
        if emotion and (spec := EMOTIONS.get(emotion)):
            parts.append(spec.instruction)
        return "，".join(parts) if parts else None

    def synthesize(
        self, text: str, voice: str, speed: float, emotion: str | None = None
    ) -> np.ndarray:
        from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

        text = text.strip()
        if not text:
            return np.zeros(0, dtype=np.float32)

        kwargs = {
            "model": self._model,
            "voice": voice or self._voice_id,
            # WAV rather than MP3: decoding is exact and cheap, and MP3's
            # encoder delay would shift every chunk against the subtitles.
            "format": AudioFormat.WAV_24000HZ_MONO_16BIT,
            "speech_rate": speed,
        }
        if instruction := self._instruction_for(emotion):
            kwargs["instruction"] = instruction

        # A synthesizer is built per utterance because `instruction` is a
        # constructor argument -- her mood can change between replies.
        audio = SpeechSynthesizer(**kwargs).call(text)
        if not audio:
            raise RuntimeError(f"CosyVoice returned no audio for {text[:40]!r}")

        samples, rate = sf.read(io.BytesIO(audio), dtype="float32")
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        self.sample_rate = rate
        return samples.astype(np.float32)
