"""TTS backend factory."""

from __future__ import annotations

import logging

from ..config import Settings
from .base import TtsBackend

log = logging.getLogger(__name__)


def build_tts(settings: Settings) -> TtsBackend:
    """Construct the configured backend, falling back to Piper if it fails.

    A dead TTS is the one failure that makes the whole app pointless, so we
    degrade to the most robust backend rather than refusing to start.
    """
    backend = settings.tts_backend
    try:
        return _construct(backend, settings)
    except Exception as exc:  # noqa: BLE001 - deliberately broad; see docstring
        if backend == "piper":
            raise
        log.error("TTS backend %r failed to start: %s", backend, exc)
        log.error("Falling back to Piper. Fix the above and restart for better audio.")
        return _construct("piper", settings)


def _construct(backend: str, settings: Settings) -> TtsBackend:
    models = settings.models_dir

    if backend == "minimax":
        from .minimax import MiniMaxTts

        return MiniMaxTts(
            api_key=settings.fal_key,
            voice_id=settings.minimax_voice_id,
            model=settings.minimax_model,
            speed=settings.tts_speed,
        )

    if backend == "cosyvoice":
        from .cosyvoice import CosyVoiceTts

        return CosyVoiceTts(
            api_key=settings.dashscope_api_key,
            voice_id=settings.dashscope_voice_id,
            model=settings.dashscope_model,
            websocket_url=settings.dashscope_websocket_url,
            base_instruction=settings.dashscope_instruction,
        )

    if backend == "dashscope":
        from .dashscope_tts import DashScopeTts

        return DashScopeTts(
            api_key=settings.dashscope_api_key,
            voice_id=settings.dashscope_voice_id,
            model=settings.dashscope_model,
            base_url=settings.dashscope_base_url,
            language=settings.stt_language or "zh",
        )

    if backend == "qwen3":
        from .qwen3 import Qwen3Tts

        return Qwen3Tts(
            model_name=settings.resolved_qwen3_model,
            ref_audio=settings.qwen3_ref_audio,
            ref_text=settings.qwen3_ref_text,
            language=settings.stt_language or "zh",
            device=settings.qwen3_device,
            dtype=settings.qwen3_dtype,
        )

    if backend == "kokoro":
        from .kokoro_tts import KokoroTts

        # The v1.1-zh checkpoint, not v1.0 -- only v1.1-zh contains the Chinese
        # voice bank. v1.0's voices file has no zf_*/zm_* entries at all.
        return KokoroTts(
            model_path=models / "kokoro" / "kokoro-v1.1-zh.onnx",
            voices_path=models / "kokoro" / "voices-v1.1-zh.bin",
            # v1.1-zh has its own phoneme vocabulary; without it the model maps
            # Mandarin phonemes to the wrong token ids and outputs noise.
            config_path=models / "kokoro" / "config.json",
            default_voice=settings.tts_voice,
        )

    if backend == "qwentts":
        from .qwentts import QwenTts

        return QwenTts(settings.qwentts_base_url, default_voice=settings.tts_voice)

    if backend == "piper":
        from .piper_tts import PiperTts

        return PiperTts(
            binary=models / "piper" / "piper.exe",
            model=models / "piper" / "zh_CN-huayan-medium.onnx",
        )

    raise ValueError(f"unknown TTS backend: {backend!r}")


__all__ = ["TtsBackend", "build_tts"]
