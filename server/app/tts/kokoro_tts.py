"""Kokoro-82M via ONNX Runtime.

Default backend because it is the only high-quality option that installs from pip
on Windows with no compiler, no CUDA toolkit and no build step. It runs on CPU in
roughly 200ms per sentence, which matters here: it leaves the entire 6GB card to
Whisper and the LLM.

Chinese needs a different call path than English, and this is the part that
silently breaks if you follow the English docs. The v1.1-zh checkpoint does NOT
accept raw text with a `lang="cmn"` argument -- you must convert to phonemes
yourself with misaki's ZHG2P and pass `is_phonemes=True`, and the model needs its
own `config.json` vocabulary. English still goes through the plain text path.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import numpy as np

from .base import TtsBackend

log = logging.getLogger(__name__)

_CJK = re.compile(r"[一-鿿]")


class KokoroTts(TtsBackend):
    sample_rate = 24_000

    def __init__(
        self,
        model_path: Path,
        voices_path: Path,
        config_path: Path | None = None,
        default_voice: str = "zf_001",
    ) -> None:
        from kokoro_onnx import Kokoro

        for path in (model_path, voices_path):
            if not path.exists():
                raise FileNotFoundError(
                    f"Kokoro asset missing: {path}\n"
                    "Run: powershell -ExecutionPolicy Bypass -File scripts/download_models.ps1"
                )

        if config_path and config_path.exists():
            self._kokoro = Kokoro(str(model_path), str(voices_path), vocab_config=str(config_path))
        else:
            if config_path:
                log.warning(
                    "Kokoro config.json missing at %s -- Chinese output will be garbled. "
                    "Re-run scripts/download_models.ps1",
                    config_path,
                )
            self._kokoro = Kokoro(str(model_path), str(voices_path))

        self._default_voice = default_voice
        self._g2p = None  # built on first Chinese sentence
        log.info("Kokoro TTS ready (voice=%s)", default_voice)

    @property
    def default_voice(self) -> str:
        return self._default_voice

    def _chinese_g2p(self):
        """Lazily construct the Mandarin frontend.

        Deferred because importing misaki pulls in jieba and pypinyin and costs
        ~2s, which is wasted if the persona only ever speaks English.
        """
        if self._g2p is None:
            try:
                from misaki import zh
            except ImportError as exc:
                raise RuntimeError(
                    "Chinese TTS needs the misaki G2P frontend.\n"
                    'Install it with:  pip install "misaki-fork[zh]"'
                ) from exc
            self._g2p = zh.ZHG2P(version="1.1")
        return self._g2p

    @staticmethod
    def _is_chinese(text: str, voice: str) -> bool:
        # Trust the voice first: a zf_/zm_ voice is Chinese-only, and routing its
        # text through the English path produces confident-sounding gibberish.
        if voice.startswith(("zf_", "zm_")):
            return True
        return bool(_CJK.search(text))

    def synthesize(self, text: str, voice: str, speed: float, emotion: str | None = None) -> np.ndarray:
        text = text.strip()
        if not text:
            return np.zeros(0, dtype=np.float32)

        voice = voice or self._default_voice

        if self._is_chinese(text, voice):
            result = self._chinese_g2p()(text)
            # misaki returns (phonemes, tokens); older builds return a bare string.
            phonemes = result[0] if isinstance(result, tuple) else result
            if not phonemes:
                return np.zeros(0, dtype=np.float32)
            samples, rate = self._kokoro.create(
                phonemes, voice=voice, speed=speed, is_phonemes=True
            )
        else:
            samples, rate = self._kokoro.create(text, voice=voice, speed=speed, lang="en-us")

        if rate != self.sample_rate:
            log.warning("Kokoro returned %dHz, expected %d", rate, self.sample_rate)
            self.sample_rate = rate

        return np.asarray(samples, dtype=np.float32)
