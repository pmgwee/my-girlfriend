"""Per-connection conversation state and the VAD -> STT -> LLM -> TTS cascade.

Concurrency model: one asyncio task owns the socket and runs the VAD inline
(Silero costs ~0.5ms per 32ms frame, so inline is both simpler and lower-latency
than a hop through an executor). Everything genuinely blocking -- Whisper, TTS --
runs in a thread pool. A single writer task serialises all outbound messages so
JSON metadata always lands immediately before the binary audio it describes.

Barge-in is the behaviour that makes this feel like a phone call rather than a
walkie-talkie: the moment Silero sees speech while she is talking, the response
task is cancelled, the client flushes its audio queue, and whatever she had
already said is written to history so she doesn't repeat it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import numpy as np

from . import emotion
from .audio import float32_to_pcm16, pcm16_to_float32
from .config import FRAME_SAMPLES, SAMPLE_RATE, Settings
from .llm.client import LlmClient
from .persona import Conversation, Persona
from .stt.whisper import WhisperStt
from .tts.base import TtsBackend
from .vad.silero import SileroVad, VadEvent

log = logging.getLogger(__name__)

# Audio is sent to the browser in ~120ms slices. Small enough that a barge-in
# cancels almost instantly, large enough to avoid per-message overhead.
_OUTPUT_SLICE_MS = 120


@dataclass
class Stages:
    """Models shared across all sessions. Loaded once at startup -- reloading
    Whisper per connection would cost seconds and duplicate VRAM."""

    stt: WhisperStt
    tts: TtsBackend
    llm: LlmClient
    persona: Persona
    settings: Settings
    # Whisper is not thread-safe and stays serialised on this lock. TTS
    # concurrency is governed per backend by TtsBackend.concurrency instead --
    # stateless HTTP backends render multiple chunks at once, ONNX ones do not.
    stt_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Session:
    def __init__(self, send, stages: Stages) -> None:
        self._send = send
        self._stages = stages
        self._settings = stages.settings

        self._vad = SileroVad(
            model_path=self._settings.models_dir / "silero_vad.onnx",
            threshold=self._settings.vad_threshold,
            silence_ms=self._settings.vad_silence_ms,
            min_speech_ms=self._settings.vad_min_speech_ms,
            prefix_ms=self._settings.vad_prefix_ms,
        )
        self._conversation = Conversation(stages.persona, self._settings.llm_history_turns)

        self._pending = np.zeros(0, dtype=np.float32)
        self._response: asyncio.Task | None = None
        self._chunk_id = 0
        self._speaking = False
        # Monotonic turn id. Bumped at the start of every turn and on every
        # barge-in; synthesis results carry the epoch they were started under and
        # are discarded if it has moved on, so a chunk rendered across an
        # interrupt can never reach the speaker.
        self._epoch = 0

    # ------------------------------------------------------------------ audio

    async def feed_audio(self, raw: bytes) -> None:
        """Consume PCM16 from the browser, one 32ms VAD frame at a time."""
        self._pending = np.concatenate([self._pending, pcm16_to_float32(raw)])

        while self._pending.size >= FRAME_SAMPLES:
            frame, self._pending = self._pending[:FRAME_SAMPLES], self._pending[FRAME_SAMPLES:]
            result = self._vad.process(frame)

            if result.event is VadEvent.SPEECH_START and self._speaking:
                await self._interrupt()

            if result.event is VadEvent.SPEECH_END and result.audio is not None:
                await self._start_turn(result.audio)

    async def _interrupt(self) -> None:
        log.info("barge-in")
        await self._cancel_response()
        await self._send({"type": "interrupted"})

    async def _cancel_response(self) -> None:
        # Invalidate the outgoing turn so synthesis still finishing in the thread
        # pool is discarded rather than spoken after the interrupt lands.
        self._epoch += 1
        task, self._response = self._response, None
        if task and not task.done():
            task.cancel()
            # Wait for the cancellation to actually land, otherwise the old task
            # can emit one more audio chunk after we've told the client to flush.
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._speaking = False

    # ------------------------------------------------------------------ turns

    async def _start_turn(self, audio: np.ndarray) -> None:
        await self._cancel_response()
        self._response = asyncio.create_task(self._run_turn(audio))

    async def submit_text(self, text: str) -> None:
        """Typed input, bypassing VAD and STT."""
        await self._cancel_response()
        self._response = asyncio.create_task(self._respond(text))

    async def greet(self) -> None:
        greeting = self._stages.persona.greeting
        if not greeting:
            return
        self._response = asyncio.create_task(self._speak_scripted(greeting))

    async def _run_turn(self, audio: np.ndarray) -> None:
        try:
            async with self._stages.stt_lock:
                text = await asyncio.to_thread(self._stages.stt.transcribe, audio)
            if not text:
                await self._send({"type": "turn_end", "reason": "no_speech"})
                return
            await self._respond(text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface to UI, keep session alive
            log.exception("turn failed")
            await self._send({"type": "error", "message": str(exc)})

    async def _respond(self, user_text: str) -> None:
        await self._send({"type": "user_transcript", "text": user_text})
        self._conversation.add_user(user_text)

        spoken: list[str] = []
        started = time.perf_counter()
        first_audio: float | None = None
        # The tag only appears on the first chunk, but her mood applies to the
        # whole reply, so it's carried forward across subsequent chunks.
        mood: str | None = None

        epoch = self._bump_epoch()
        # How many chunks may render at once. With a stateless HTTP backend this
        # is >1, so chunk k+1 synthesises *while* chunk k plays -- that overlap
        # is what removes the silence between sentences on a high-latency hosted
        # TTS. Kokoro's ONNX runtime is not re-entrant, so it stays at 1 and this
        # loop degenerates to the original one-chunk-at-a-time path.
        concurrency = max(1, getattr(self._stages.tts, "concurrency", 1))
        limit = asyncio.Semaphore(concurrency)
        pending: list[asyncio.Task] = []

        async def synth(chunk_text: str, chunk_mood: str | None):
            async with limit:
                samples = await asyncio.to_thread(
                    self._stages.tts.synthesize,
                    chunk_text,
                    self._settings.tts_voice,
                    self._settings.tts_speed,
                    chunk_mood,
                )
            # A barge-in bumps the epoch; anything rendered after that is stale
            # and must never reach the speaker, or she'd bleat a fragment after
            # you'd already cut her off.
            if self._epoch != epoch or samples.size == 0:
                return None
            return samples, self._stages.tts.sample_rate, chunk_text

        try:
            self._speaking = True
            async for chunk in self._stages.llm.stream(self._conversation.messages()):
                chunk, tag = emotion.split(chunk)
                mood = tag or mood
                if not chunk.strip():
                    continue

                await self._send({
                    "type": "assistant_delta",
                    "text": chunk,
                    "emotion": mood or emotion.DEFAULT,
                })
                spoken.append(chunk)
                # Backpressure: keep the LLM from running far ahead of synthesis.
                # Allow at most `concurrency` chunks buffered beyond the one
                # emitting, so a long reply can't pile up many chunks (and their
                # audio) in memory. TTFA is unaffected -- the queue starts empty,
                # so the first sentence still flushes immediately.
                while len(pending) > concurrency:
                    result = await pending.pop(0)
                    if result is not None:
                        await self._emit(result, epoch)
                        if first_audio is None:
                            first_audio = time.perf_counter() - started
                pending.append(asyncio.create_task(synth(chunk, mood)))

                # Emit anything that is both finished and at the front of the
                # queue, so audio always reaches the client in the order she said
                # it even when synthesis completes out of order.
                while pending and pending[0].done():
                    result = await pending.pop(0)
                    if result is not None:
                        await self._emit(result, epoch)
                        if first_audio is None:
                            first_audio = time.perf_counter() - started

            # Drain whatever is still rendering once the LLM stream ends.
            for task in pending:
                result = await task
                if result is not None:
                    await self._emit(result, epoch)
                    if first_audio is None:
                        first_audio = time.perf_counter() - started

            await self._send({"type": "turn_end", "reason": "complete"})
            if first_audio is not None:
                log.info("time-to-first-audio %.0fms", first_audio * 1000)
        except asyncio.CancelledError:
            # She was cut off. Cancel pending synthesis; the epoch guard also
            # drops any thread-pool result that lands after this. What she
            # already said is recorded so the next reply continues, not restarts.
            for task in pending:
                task.cancel()
            log.debug("response cancelled after %d chunks", len(spoken))
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("response failed")
            await self._send({"type": "error", "message": str(exc)})
        finally:
            self._speaking = False
            if spoken:
                self._conversation.add_assistant("".join(spoken))

    async def _speak_scripted(self, text: str) -> None:
        """Say a fixed line (the greeting) without involving the LLM."""
        try:
            self._speaking = True
            await self._send({"type": "assistant_delta", "text": text})
            await self._speak(text)
            await self._send({"type": "turn_end", "reason": "complete"})
            self._conversation.add_assistant(text)
        except asyncio.CancelledError:
            raise
        finally:
            self._speaking = False

    # ------------------------------------------------------------------ output

    async def _speak(self, text: str, mood: str | None = None) -> None:
        """Synthesise one fixed chunk and send it. Used by the greeting path,
        which has a single utterance and no pipelining to do."""
        epoch = self._bump_epoch()
        try:
            samples = await asyncio.to_thread(
                self._stages.tts.synthesize,
                text,
                self._settings.tts_voice,
                self._settings.tts_speed,
                mood,
            )
        except asyncio.CancelledError:
            raise
        if samples.size == 0 or self._epoch != epoch:
            return
        await self._emit((samples, self._stages.tts.sample_rate, text), epoch)

    async def _emit(self, result: tuple[np.ndarray, int, str], epoch: int) -> None:
        """Slice one synthesised chunk into ~120ms pieces and queue them, in
        order. The epoch guard keeps a stale chunk -- one rendered across a
        barge-in -- from ever reaching the speaker."""
        samples, rate, text = result
        if self._epoch != epoch:
            return

        self._chunk_id += 1
        chunk_id = self._chunk_id
        await self._send({
            "type": "audio_start",
            "chunkId": chunk_id,
            "sampleRate": rate,
            "text": text,
            "durationMs": int(samples.size / rate * 1000),
        })

        slice_samples = rate * _OUTPUT_SLICE_MS // 1000
        for offset in range(0, samples.size, slice_samples):
            piece = samples[offset:offset + slice_samples]
            # A cancellation between slices is the common barge-in path; this
            # yield gives the event loop a chance to deliver it.
            await asyncio.sleep(0)
            if self._epoch != epoch:
                return
            await self._send(float32_to_pcm16(piece))

        if self._epoch != epoch:
            return
        await self._send({"type": "audio_end", "chunkId": chunk_id})

    def _bump_epoch(self) -> int:
        self._epoch += 1
        return self._epoch

    # ------------------------------------------------------------------ misc

    async def reset(self) -> None:
        await self._cancel_response()
        self._conversation.reset()
        self._vad.reset()
        self._pending = np.zeros(0, dtype=np.float32)
        await self._send({"type": "reset_ok"})

    async def close(self) -> None:
        await self._cancel_response()


__all__ = ["Session", "Stages", "SAMPLE_RATE"]
