"""Piper TTS -- the bulletproof fallback.

Lower quality than Kokoro or Qwen3, but it is a self-contained binary with
prebuilt Windows releases and no Python-side G2P dependencies. If the other two
backends fight you, this one will work.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import numpy as np

from .base import TtsBackend

log = logging.getLogger(__name__)


class PiperTts(TtsBackend):
    # Piper's zh_CN voices are trained at 22.05kHz.
    sample_rate = 22_050

    def __init__(self, binary: Path, model: Path) -> None:
        for path in (binary, model):
            if not path.exists():
                raise FileNotFoundError(
                    f"Piper asset missing: {path}\n"
                    "Run: powershell -ExecutionPolicy Bypass -File scripts/download_models.ps1 -Backend piper"
                )
        self._binary = binary
        self._model = model
        log.info("Piper TTS ready (%s)", model.name)

    def synthesize(self, text: str, voice: str, speed: float, emotion: str | None = None) -> np.ndarray:
        text = text.strip()
        if not text:
            return np.zeros(0, dtype=np.float32)

        result = subprocess.run(
            [
                str(self._binary),
                "--model", str(self._model),
                "--output-raw",
                # Piper expresses tempo as seconds-per-phoneme, so it's inverted
                # relative to our speed multiplier.
                "--length-scale", str(round(1.0 / max(speed, 0.1), 3)),
            ],
            input=text.encode("utf-8"),
            capture_output=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            log.error("piper failed: %s", result.stderr.decode("utf-8", "replace")[:300])
            return np.zeros(0, dtype=np.float32)

        return np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
