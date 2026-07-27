"""Per-reply emotion, decided by the LLM and applied by the TTS.

The model prefixes each reply with a tag -- `[撒娇] 你怎么才来嘛` -- which is
stripped before synthesis and passed to the backend separately. Two reasons it
works this way rather than letting the TTS infer mood from the text:

  * Inference from text alone is unreliable and, worse, inconsistent between
    consecutive sentences of the same reply.
  * The LLM already knows the conversational context. It knows she's sulking
    because you were late, which no amount of reading one sentence can recover.

Backends map these to whatever their API exposes. Providers differ sharply here:
MiniMax takes a fixed 7-value enum with no 撒娇 at all, while CosyVoice 2 accepts
free-text style instructions, so it can express 撒娇 literally.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Leading tag, tolerant of both bracket styles and optional whitespace.
_TAG = re.compile(r"^\s*[\[【]\s*([^\]】]{1,8})\s*[\]】]\s*")


@dataclass(frozen=True)
class Emotion:
    key: str
    #: Natural-language style instruction, for instruct-capable backends.
    instruction: str
    #: Nearest match in MiniMax's fixed enum. Deliberately lossy -- documented
    #: so nobody assumes 撒娇 survives the trip.
    minimax: str


EMOTIONS: dict[str, Emotion] = {
    "温柔": Emotion("温柔", "用温柔、放松的语气说，语速稍慢，尾音轻一点", "neutral"),
    "撒娇": Emotion(
        "撒娇",
        "用撒娇的语气说，带鼻音，尾音拖长上扬，语速稍快，像在跟男朋友耍赖",
        "happy",
    ),
    "开心": Emotion("开心", "用开心、雀跃的语气说，语速稍快", "happy"),
    "生气": Emotion("生气", "用生气但克制的语气说，语速偏快，尾音短促", "angry"),
    "难过": Emotion("难过", "用低落、有点委屈的语气说，语速偏慢，声音小一点", "sad"),
    "平静": Emotion("平静", "用平静自然的语气说", "neutral"),
}

DEFAULT = "温柔"

#: Traditional forms of every key, plus a few synonyms the model reaches for.
#: A zh-TW persona writes 「[撒嬌]」, not 「[撒娇]」 -- without this the tag looks
#: unknown, gets stripped, and every reply silently falls back to neutral.
ALIASES: dict[str, str] = {
    "溫柔": "温柔",
    "撒嬌": "撒娇",
    "開心": "开心",
    "生氣": "生气",
    "難過": "难过",
    "平靜": "平静",
    # Near-misses seen in practice.
    "高興": "开心", "高兴": "开心",
    "傷心": "难过", "伤心": "难过",
    "委屈": "难过",
    "害羞": "撒娇",
    "無奈": "平静", "无奈": "平静",
}


#: Canonical key -> the form shown to a Traditional-script persona.
_TRADITIONAL: dict[str, str] = {
    "温柔": "溫柔", "撒娇": "撒嬌", "开心": "開心",
    "生气": "生氣", "难过": "難過", "平静": "平靜",
}


def canonical(tag: str) -> str | None:
    """Resolve a tag to a known emotion key, across scripts and synonyms."""
    tag = tag.strip()
    if tag in EMOTIONS:
        return tag
    return ALIASES.get(tag)


def split(text: str) -> tuple[str, str | None]:
    """Pull a leading emotion tag off a reply.

    Returns (speakable_text, emotion_key). Unknown or absent tags yield None so
    the caller can fall back rather than speaking a bracket out loud.
    """
    match = _TAG.match(text)
    if not match:
        return text, None

    resolved = canonical(match.group(1))
    # An unrecognised tag is still a tag -- strip it either way. Leaving it in
    # would have the TTS read "括號 高興 括號" aloud, which is the exact failure
    # the persona rules exist to prevent.
    return text[match.end():], resolved


def prompt_fragment() -> str:
    """The instruction block appended to the persona's system prompt.

    The 撒娇 guidance is deliberately about WORD CHOICE, not just the tag.
    Most of what makes Chinese sound coquettish lives in the text -- 人家 for 我,
    trailing 嘛/啦/呀, stretched vowels, pleading repetition. That survives on
    every backend, including ones whose emotion parameter has no 撒娇 at all
    (MiniMax exposes a fixed seven-value enum). Relying on the TTS alone would
    make the effect vanish the moment the provider changes.
    """
    # Offer exactly the six canonical moods, in Traditional to match a zh-TW
    # persona. Listing the synonym aliases too would just invite the model to
    # pick a near-miss; split() still accepts them if it does.
    options = "、".join(_TRADITIONAL[key] for key in EMOTIONS)
    return (
        f"\n每次回覆必須以情緒標籤開頭，格式是 [情緒] 內容。\n"
        f"可用情緒：{options}。\n"
        f"例如：[撒嬌] 你怎麼才來嘛，人家等好久了啦。\n"
        f"情緒要跟著對話走——他關心你就溫柔，他惹你了就生氣，等太久了就撒嬌。\n"
        f"標籤只寫一個，寫在最前面，後面正常說話。\n"
        f"\n撒嬌的時候，語氣詞和用字要跟著變，不能只是換個標籤：\n"
        f"　　自稱用「人家」，不要用「我」\n"
        f"　　句尾加「嘛」「啦」「呀」「喔」，例如「你都不理人家嘛」\n"
        f"　　可以拉長音，例如「好～嘛」「討厭啦～」\n"
        f"　　可以重複要求，例如「好不好嘛，好不好嘛」\n"
        f"生氣的時候相反：句子短、語氣詞少、不用「人家」。"
    )
