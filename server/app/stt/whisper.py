"""Speech-to-text via faster-whisper (CTranslate2).

Chosen over openai-whisper and transformers because CTranslate2 ships working
Windows CUDA wheels and supports int8 quantisation, which is what lets the
`small` model share a 6GB card with the LLM.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


def _register_cuda_dlls() -> None:
    """Make pip-installed CUDA libraries visible to CTranslate2 on Windows.

    CTranslate2 links cuBLAS and cuDNN dynamically and searches only PATH. The
    nvidia-*-cu12 wheels drop their DLLs in site-packages\\nvidia\\*\\bin, which
    is not on PATH, so the load fails with "cublas64_12.dll is not found" even
    though the library is installed. Registering those directories fixes it
    without asking the user to install the full CUDA Toolkit.

    Must run before faster_whisper is imported -- CTranslate2 resolves its DLLs
    at import time.
    """
    if sys.platform != "win32":
        return

    for site_packages in map(Path, sys.path):
        nvidia = site_packages / "nvidia"
        if not nvidia.is_dir():
            continue
        for dll_dir in nvidia.glob("*/bin"):
            try:
                os.add_dll_directory(str(dll_dir))
            except (OSError, AttributeError):
                pass


_register_cuda_dlls()

from faster_whisper import WhisperModel  # noqa: E402 - must follow DLL registration

# Whisper is trained on 30s windows and will happily invent text for near-silent
# input. Anything below this is dropped before it reaches the model.
_MIN_AUDIO_SECONDS = 0.30

# Phrases Whisper emits when fed silence or noise -- subtitle credits bled into
# its training data. If a transcript is *only* one of these, it's a hallucination.
_HALLUCINATIONS = {
    # Simplified
    "字幕由amara.org社群提供", "字幕志愿者", "请不吝点赞订阅转发打赏支持明镜与点点栏目",
    "谢谢大家", "谢谢观看", "谢谢", "谢谢收看", "下次再见", "请订阅",
    # Traditional. A zh-TW persona makes Whisper emit these forms, so a
    # Simplified-only list silently misses them -- observed in a real session
    # where "謝謝大家。" was picked up off the speakers and treated as a real
    # user turn, which then interrupted her mid-reply.
    "字幕由amara.org社群提供", "字幕志願者", "謝謝大家", "謝謝觀看", "謝謝",
    "謝謝收看", "下次再見", "請訂閱", "請不吝點贊訂閱轉發打賞支持明鏡與點點欄目",
    "中文字幕由", "明鏡與點點欄目",
    # English / punctuation-only
    "byebye", "bye bye", "thanks for watching", "thank you for watching",
    "subscribe", "you", "bye", "。", ".", "！", "!", "?", "？",
}


# Stripped before matching. Whisper punctuates its hallucinations ("謝謝大家。"),
# so comparing raw text against the bare phrases misses nearly all of them.
_PUNCTUATION = " \t,，。.、！!？?~～…「」\"'"


def _normalise(text: str) -> str:
    stripped = text.strip().lower()
    for char in _PUNCTUATION:
        stripped = stripped.replace(char, "")
    return stripped


# Both sides go through the same normaliser, so the phrases above can be written
# naturally ("thanks for watching") without their spaces defeating the match.
_HALLUCINATIONS = {_normalise(phrase) for phrase in _HALLUCINATIONS}


def _looks_hallucinated(text: str) -> bool:
    normalised = _normalise(text)
    return normalised in _HALLUCINATIONS or len(normalised) == 0


class WhisperStt:
    def __init__(
        self,
        model_size: str,
        device: str,
        compute_type: str,
        language: str | None,
        locale: str = "zh-CN",
        name: str | None = None,
    ) -> None:
        # Whisper only understands "zh"; the region decides which script we
        # steer it toward via the priming prompt. `name` is baked into that
        # prompt so the spoken name maps to the right characters (see _prime).
        self.language = "zh" if language and language.startswith("zh") else language
        self._script_prompt = self._prime(self.language, locale, name)
        if self._script_prompt:
            log.info("STT priming prompt: %s", self._script_prompt)
        self._model, self.device = self._load(model_size, device, compute_type)

    @staticmethod
    def _prime(language: str | None, locale: str, name: str | None = None) -> str | None:
        if language != "zh":
            return None
        # Embed the persona's name so Whisper maps the *spoken* name to the
        # correct characters instead of a homophone -- e.g. 雨桐, not 雨彤/语桐.
        # A mis-transcribed name is the one STT error that breaks role-play: the
        # model sees the user "calling her the wrong name" and corrects them.
        who = f"女朋友{name}" if name else "女朋友"
        if locale.lower() in {"zh-tw", "zh-hk", "zh_tw", "zh_hk"}:
            # Traditional script, and Taiwanese particles in the prompt so
            # Whisper expects them rather than "correcting" them away.
            return (
                f"以下是{who}輕鬆的日常對話，台灣口音，"
                "會用「欸」「齁」「啦」「喔」這些語氣詞。"
            )
        return f"以下是{who}轻松的日常中文对话。"

    @staticmethod
    def _load(model_size: str, device: str, compute_type: str) -> tuple[WhisperModel, str]:  # noqa: C901
        """Load on GPU, fall back to CPU rather than dying.

        A missing cuDNN 9 install is the single most common Windows failure for
        CTranslate2, and it surfaces as an opaque DLL error at first inference
        rather than at load. Falling back keeps the app usable.
        """
        # float16 is a CUDA-only compute type. Passing it with device="cpu"
        # makes CTranslate2 fall back to float32, which quadruples memory and
        # halves speed without telling you.
        if device == "cpu" and "float16" in compute_type:
            log.info("compute_type %r is CUDA-only; using int8 on CPU", compute_type)
            compute_type = "int8"

        try:
            model = WhisperModel(model_size, device=device, compute_type=compute_type)
            # Force a real inference now so a broken CUDA setup fails here,
            # loudly, instead of mid-conversation. transcribe() returns a lazy
            # generator, so the list() is what actually runs the model -- without
            # it nothing executes and a broken GPU sails through this check.
            segments, _ = model.transcribe(np.zeros(16_000, dtype=np.float32), language="en")
            list(segments)
            log.info("Whisper '%s' loaded on %s (%s)", model_size, device, compute_type)
            return model, device
        except Exception as exc:  # noqa: BLE001 - any CUDA/DLL failure should degrade, not crash
            if device == "cpu":
                raise
            log.warning(
                "Whisper failed to start on %s (%s). Falling back to CPU int8. "
                "Fix with: pip install nvidia-cublas-cu12 nvidia-cudnn-cu12",
                device, exc,
            )
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            return model, "cpu"

    def transcribe(self, audio: np.ndarray) -> str:
        duration = audio.size / 16_000
        if duration < _MIN_AUDIO_SECONDS:
            return ""

        started = time.perf_counter()
        segments, info = self._model.transcribe(
            audio,
            language=self.language,
            beam_size=1,               # greedy: ~2x faster, negligible quality loss at this length
            vad_filter=False,          # Silero already gave us a clean utterance
            condition_on_previous_text=False,  # stops one bad turn poisoning the next
            temperature=0.0,
            no_speech_threshold=0.6,
            # Whisper picks a script from context and will flip between
            # Simplified and Traditional mid-conversation without a nudge. The
            # prompt is written in the target script, which is what steers it.
            initial_prompt=self._script_prompt,
        )

        parts: list[str] = []
        for segment in segments:
            if segment.no_speech_prob > 0.75:
                continue
            parts.append(segment.text)

        text = "".join(parts).strip()
        elapsed = time.perf_counter() - started
        log.info("STT %.2fs audio in %.2fs -> %r", duration, elapsed, text)

        if _looks_hallucinated(text):
            log.debug("dropping hallucinated transcript %r", text)
            return ""
        return text
