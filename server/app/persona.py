"""Persona loading and conversation memory."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


@dataclass
class Persona:
    name: str
    display_name: str
    system_prompt: str
    greeting: str = ""
    voice: str = ""
    locale: str = "zh"
    epigraph: list[str] = field(default_factory=list)
    avatar: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Persona":
        if not path.exists():
            raise FileNotFoundError(f"persona file not found: {path}")
        from . import emotion

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        # The emotion-tag instructions are appended rather than written into
        # every persona file, so adding an emotion can't leave personas stale.
        prompt = data["system_prompt"].strip() + "\n" + emotion.prompt_fragment()
        return cls(
            name=data.get("name", "AI"),
            display_name=data.get("display_name", data.get("name", "AI")),
            system_prompt=prompt,
            greeting=(data.get("greeting") or "").strip(),
            voice=data.get("voice", ""),
            locale=data.get("locale", "zh"),
            epigraph=list(data.get("epigraph") or []),
            avatar=dict(data.get("avatar") or {}),
        )

    def client_config(self) -> dict:
        """The subset the browser needs to render the UI."""
        return {
            "displayName": self.display_name,
            "epigraph": self.epigraph,
            "avatar": self.avatar,
            "locale": self.locale,
        }


class Conversation:
    """Rolling chat history.

    Trimmed by turn count rather than tokens: at 1-3 sentences per reply the
    variance is low enough that counting turns is accurate to within a few
    hundred tokens, and it avoids shipping a tokenizer just for bookkeeping.
    """

    def __init__(self, persona: Persona, max_turns: int) -> None:
        self._persona = persona
        self._max_turns = max_turns
        self._turns: list[dict[str, str]] = []

    def add_user(self, text: str) -> None:
        self._turns.append({"role": "user", "content": text})

    def add_assistant(self, text: str) -> None:
        if text.strip():
            self._turns.append({"role": "assistant", "content": text})
        self._trim()

    def _trim(self) -> None:
        # Keep pairs intact so the model never sees a dangling assistant turn.
        excess = len(self._turns) - self._max_turns * 2
        if excess > 0:
            del self._turns[:excess]

    def messages(self) -> list[dict[str, str]]:
        return [{"role": "system", "content": self._persona.system_prompt}, *self._turns]

    def reset(self) -> None:
        self._turns.clear()
