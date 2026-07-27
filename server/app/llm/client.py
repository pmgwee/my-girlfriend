"""Streaming LLM client for any OpenAI-compatible server.

Targets llama.cpp's `llama-server` by default, but Ollama, vLLM and LM Studio all
speak the same protocol, so switching is a URL change in .env.

The important behaviour here is *sentence chunking*: we don't wait for the full
reply before speaking. Tokens are accumulated and flushed at clause boundaries so
TTS can start on sentence one while the model is still writing sentence two. This
is what takes time-to-first-audio from ~3s down to well under a second.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator

import httpx

log = logging.getLogger(__name__)

# Hard sentence terminators in both scripts.
_TERMINATORS = "。！？!?…\n"
# Soft breaks. Used only once a chunk is already long enough to be worth speaking,
# otherwise we'd emit two-character fragments that sound clipped.
_SOFT_BREAKS = "，,；;：:、"

_MIN_CHUNK_CHARS = 6
# The first chunk is flushed as early as possible -- it is the only one the user
# waits on in silence, so a short clause like "在煮咖啡呢，" starting 2s sooner
# matters far more than its prosody. Later chunks buffer more, because they are
# synthesised while earlier audio is still playing and longer spans sound better.
_FIRST_SOFT_BREAK_CHARS = 5
_SOFT_BREAK_MIN_CHARS = 24
# Safety valve: if the model rambles without punctuation, flush anyway so the
# user isn't left in silence.
_MAX_CHUNK_CHARS = 90
_FIRST_MAX_CHUNK_CHARS = 32

# The persona forbids these, but small models leak them anyway. Strip before TTS
# so she never reads "asterisk smiles asterisk" out loud.
_STAGE_DIRECTIONS = re.compile(r"[\(（\[【\*][^\)）\]】\*]{0,40}[\)）\]】\*]")
_MARKDOWN = re.compile(r"[*_`#>]+")


# A leading emotion tag looks exactly like a stage direction to the stripper
# below, so it has to be lifted out before cleaning and put back afterwards.
# Without this the tag is silently deleted and every reply reads as neutral --
# the model was emitting it correctly the whole time.
_LEADING_TAG = re.compile(r"^\s*([\[【][^\]】]{1,8}[\]】])")


def clean_for_speech(text: str) -> str:
    tag = ""
    if match := _LEADING_TAG.match(text):
        tag, text = match.group(1), text[match.end():]

    text = _STAGE_DIRECTIONS.sub("", text)
    text = _MARKDOWN.sub("", text)
    cleaned = re.sub(r"[ \t]+", " ", text).strip()
    return f"{tag}{cleaned}" if tag else cleaned


class SentenceAccumulator:
    """Turns a token stream into speakable chunks."""

    def __init__(self) -> None:
        self._buffer = ""
        self._emitted_any = False

    def push(self, token: str) -> list[str]:
        self._buffer += token
        chunks: list[str] = []
        while True:
            chunk = self._extract()
            if chunk is None:
                break
            chunks.append(chunk)
        return chunks

    def _extract(self) -> str | None:
        first = not self._emitted_any
        soft_min = _FIRST_SOFT_BREAK_CHARS if first else _SOFT_BREAK_MIN_CHARS
        hard_max = _FIRST_MAX_CHUNK_CHARS if first else _MAX_CHUNK_CHARS

        for index, char in enumerate(self._buffer):
            length = index + 1
            if char in _TERMINATORS and length >= _MIN_CHUNK_CHARS:
                return self._take(length)
            if char in _SOFT_BREAKS and length >= soft_min:
                return self._take(length)
            if length >= hard_max:
                return self._take(length)
        return None

    def _take(self, length: int) -> str:
        chunk, self._buffer = self._buffer[:length], self._buffer[length:]
        self._emitted_any = True
        return chunk

    def flush(self) -> str | None:
        """Remaining text once the stream ends."""
        remaining, self._buffer = self._buffer.strip(), ""
        return remaining or None


class LlmClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float,
        max_tokens: int,
        disable_thinking: bool | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        # Auto-detect by model family: the parameter is a GLM extension and
        # providers that don't know it reject the whole request with a 400.
        self._disable_thinking = (
            disable_thinking
            if disable_thinking is not None
            else model.lower().startswith(("glm", "zai"))
        )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0),
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def health(self) -> bool:
        """Confirm an OpenAI-compatible server is listening, not just *something*.

        Port 8080 is popular -- Apache, Jenkins, Tomcat and half a dozen dev
        servers claim it. Those answer the socket, so a plain reachability check
        passes and the real failure only surfaces later as an opaque 400.

        /models is the cheap probe, but plenty of hosted providers don't
        implement it, so a 404 there falls through to a one-token completion --
        the only check that actually proves the endpoint and key work.
        """
        try:
            response = await self._client.get(f"{self._base_url}/models", timeout=5.0)
            if response.status_code < 400 and isinstance(response.json().get("data"), list):
                return True
        except (httpx.HTTPError, ValueError):
            pass

        try:
            response = await self._client.post(
                f"{self._base_url}/chat/completions",
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 1,
                    "stream": False,
                },
                timeout=20.0,
            )
            if response.status_code >= 400:
                log.warning(
                    "LLM probe failed: %s %s",
                    response.status_code,
                    response.text[:200].replace("\n", " "),
                )
                return False
            return "choices" in response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("LLM probe error: %s", exc)
            return False

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        """Yield speakable chunks as the model writes them."""
        payload: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "stream": True,
            # Small models get repetitive over a long conversation; this keeps
            # her from opening every reply with the same word.
            "presence_penalty": 0.4,
            "frequency_penalty": 0.3,
        }
        if self._disable_thinking:
            # Reasoning models stream a chain of thought as `reasoning_content`
            # before any real `content`. Measured on glm-5.2: 199 reasoning
            # chunks and an EMPTY reply within a 200-token budget, versus a
            # correct reply in 1.7s with thinking off. Fatal for live voice.
            payload["thinking"] = {"type": "disabled"}

        accumulator = SentenceAccumulator()
        try:
            async with self._client.stream(
                "POST", f"{self._base_url}/chat/completions", json=payload
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", "replace")[:400]
                    raise RuntimeError(f"LLM returned {response.status_code}: {body}")

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0].get("delta", {})
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    # Never speak `reasoning_content`. Even with thinking
                    # disabled, some providers leak a little, and hearing her
                    # narrate "Analyze the Input:" would be absurd.
                    token = delta.get("content")
                    if not token:
                        continue
                    for chunk in accumulator.push(token):
                        cleaned = clean_for_speech(chunk)
                        if cleaned:
                            yield cleaned
        except httpx.ConnectError as exc:
            raise RuntimeError(
                f"Cannot reach the LLM at {self._base_url}. "
                "Start it with: scripts/run_llm.ps1"
            ) from exc

        tail = accumulator.flush()
        if tail:
            cleaned = clean_for_speech(tail)
            if cleaned:
                yield cleaned

    async def aclose(self) -> None:
        await self._client.aclose()
