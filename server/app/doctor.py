"""End-to-end self-test: python -m app.doctor

Checks each stage in isolation, then does a TTS -> STT round trip, which is the
one test that proves the audio path is actually wired correctly. If Kokoro
renders "今天天气真好" and Whisper reads it back, then sample rates, dtypes,
channel counts and the resampler are all correct -- the failures that otherwise
show up as silence or static with no error message.
"""

from __future__ import annotations

import asyncio
import sys
import time

import numpy as np

from .audio import resample
from .config import FRAME_SAMPLES, REPO_ROOT, SAMPLE_RATE, settings

PROBE = "今天天气真好"

GREEN, RED, YELLOW, DIM, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"

_failures = 0


def report(ok: bool | None, label: str, detail: str = "") -> None:
    global _failures
    if ok is None:
        mark, colour = "!", YELLOW
    elif ok:
        mark, colour = "+", GREEN
    else:
        mark, colour = "x", RED
        _failures += 1
    print(f" {colour}[{mark}]{RESET} {label}" + (f"  {DIM}{detail}{RESET}" if detail else ""))


def _build_vad():
    from .vad.silero import SileroVad

    return SileroVad(
        model_path=settings.models_dir / "silero_vad.onnx",
        threshold=settings.vad_threshold,
        silence_ms=settings.vad_silence_ms,
        min_speech_ms=settings.vad_min_speech_ms,
        prefix_ms=settings.vad_prefix_ms,
    )


def check_vad() -> None:
    print("\nVAD")
    try:
        from .vad.silero import VadEvent

        started = time.perf_counter()
        vad = _build_vad()
        report(True, "Silero v5 loaded", f"{(time.perf_counter() - started) * 1000:.0f}ms")

        # Silence must not trigger. A VAD that fires on quiet makes her respond
        # to nothing, which is the most common symptom of a bad threshold.
        silence = np.zeros(FRAME_SAMPLES, dtype=np.float32)
        triggered = any(
            vad.process(silence).event is VadEvent.SPEECH_START for _ in range(30)
        )
        report(not triggered, "silence stays quiet",
               "fires on silence -- raise VAD_THRESHOLD" if triggered else "")

        started = time.perf_counter()
        for _ in range(100):
            vad.process(silence)
        per_frame = (time.perf_counter() - started) / 100 * 1000
        # Frames arrive every 32ms; anything near that is too slow to keep up.
        report(per_frame < 8, "fast enough for realtime", f"{per_frame:.2f}ms/frame")
    except Exception as exc:  # noqa: BLE001
        report(False, "Silero VAD", str(exc)[:120])


def check_vad_hears(spoken: np.ndarray | None) -> None:
    """The check that matters: does the VAD actually detect a turn?

    Testing only that silence stays quiet is not enough -- a completely deaf VAD
    passes that trivially, and the symptom in the app is 'she ignores me', with
    nothing in the logs to explain it.
    """
    print("\nVAD (speech detection)")
    if spoken is None:
        report(None, "speech triggers a turn", "skipped, TTS produced nothing")
        return

    try:
        from .vad.silero import VadEvent

        vad = _build_vad()
        # A real turn: silence, speech, then enough trailing silence to end it.
        pad = np.zeros(SAMPLE_RATE, dtype=np.float32)
        stream = np.concatenate([pad, spoken, pad])

        starts = ends = 0
        peak = 0.0
        for index in range(stream.size // FRAME_SAMPLES):
            frame = stream[index * FRAME_SAMPLES:(index + 1) * FRAME_SAMPLES]
            result = vad.process(frame)
            peak = max(peak, result.probability)
            if result.event is VadEvent.SPEECH_START:
                starts += 1
            elif result.event is VadEvent.SPEECH_END:
                ends += 1

        report(peak > settings.vad_threshold, "speech scores above threshold",
               f"peak P={peak:.3f} vs threshold {settings.vad_threshold}")
        report(starts >= 1, "detected speech starting", f"{starts} start(s)")
        report(ends >= 1, "detected the turn ending",
               f"{ends} end(s)" if ends else "never ended -- she'd listen forever")
    except Exception as exc:  # noqa: BLE001
        report(False, "VAD speech detection", str(exc)[:160])


def check_tts() -> np.ndarray | None:
    print("\nTTS")
    try:
        from .tts import build_tts

        started = time.perf_counter()
        tts = build_tts(settings)
        report(True, f"{settings.tts_backend} loaded", f"{time.perf_counter() - started:.1f}s")

        # Warm first. The initial call builds jieba's dictionary and the ONNX
        # graph, which costs seconds -- timing it would misreport steady-state
        # latency by 3-4x. The real server warms up at startup too.
        tts.warmup()

        started = time.perf_counter()
        audio = tts.synthesize(PROBE, settings.tts_voice, settings.tts_speed)
        elapsed = time.perf_counter() - started

        if audio.size == 0:
            report(False, "synthesis", "returned no audio")
            return None

        duration = audio.size / tts.sample_rate
        report(True, f"spoke {PROBE!r}",
               f"{duration:.2f}s audio in {elapsed:.2f}s "
               f"({elapsed / duration:.2f}x realtime) @ {tts.sample_rate}Hz")
        report(duration > 0.4, "audio length is plausible",
               "suspiciously short" if duration <= 0.4 else "")
        # Near-silent output means a bad voice id or a missing G2P frontend.
        peak = float(np.abs(audio).max())
        report(peak > 0.05, "audio is audible", f"peak {peak:.2f}")

        return resample(audio, tts.sample_rate, SAMPLE_RATE).astype(np.float32)
    except Exception as exc:  # noqa: BLE001
        report(False, f"TTS ({settings.tts_backend})", str(exc)[:200])
        return None


def check_stt(spoken: np.ndarray | None) -> None:
    print("\nSTT")
    try:
        from .stt.whisper import WhisperStt

        started = time.perf_counter()
        stt = WhisperStt(
            model_size=settings.stt_model,
            device=settings.stt_device,
            compute_type=settings.stt_compute_type,
            language=settings.stt_language,
        )
        report(True, f"Whisper '{settings.stt_model}' loaded",
               f"on {stt.device}, {time.perf_counter() - started:.1f}s")
        report(stt.device == settings.stt_device, f"running on {stt.device}",
               "fell back to CPU -- see README 'cuDNN'" if stt.device != settings.stt_device else "")

        if spoken is None:
            report(None, "round trip", "skipped, TTS produced nothing")
            return

        started = time.perf_counter()
        heard = stt.transcribe(spoken)
        elapsed = time.perf_counter() - started

        # Compare by character overlap: Whisper may add punctuation or pick a
        # homophone, so exact equality would be too strict a bar.
        overlap = len(set(PROBE) & set(heard)) / len(set(PROBE)) if heard else 0.0
        report(overlap >= 0.5, "TTS -> STT round trip",
               f"heard {heard!r} ({overlap:.0%} match, {elapsed:.2f}s)")
    except Exception as exc:  # noqa: BLE001
        report(False, "Whisper", str(exc)[:200])


async def check_llm() -> None:
    print("\nLLM")
    from .llm.client import LlmClient

    # resolved_llm, not the raw llm_* fields: a Z.ai key in .env overrides them.
    base_url, api_key, model = settings.resolved_llm
    client = LlmClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=settings.llm_temperature,
        max_tokens=64,
        disable_thinking=settings.llm_disable_thinking,
    )
    try:
        if not await client.health():
            hint = (
                "start scripts/run_llm.ps1"
                if "127.0.0.1" in base_url or "localhost" in base_url
                else "check the key and base URL"
            )
            report(False, f"reachable at {base_url}", hint)
            return
        report(True, f"reachable at {base_url}", model)

        started = time.perf_counter()
        chunks = [
            chunk
            async for chunk in client.stream([
                {"role": "system", "content": "你是一个说中文的朋友。回复要很短。"},
                {"role": "user", "content": "在吗"},
            ])
        ]
        elapsed = time.perf_counter() - started
        report(bool(chunks), "streamed a reply", f"{''.join(chunks)[:60]!r} in {elapsed:.1f}s")
    except Exception as exc:  # noqa: BLE001
        report(False, "LLM", str(exc)[:200])
    finally:
        await client.aclose()


def check_persona() -> None:
    print("\nPersona")
    try:
        from .persona import Persona

        persona = Persona.load(settings.persona_file)
        report(True, f"loaded {persona.display_name}", f"{len(persona.system_prompt)} chars")

        idle = persona.avatar.get("idle_video", "")
        video = REPO_ROOT / "web" / "public" / idle.lstrip("/")
        report(
            video.exists() if idle else False,
            "avatar video present",
            "" if video.exists() else f"missing {idle} -- see README 'Add her face'",
        )
    except Exception as exc:  # noqa: BLE001
        report(False, "persona", str(exc)[:200])


def main() -> int:
    print(f"{DIM}virtual-girlfriend doctor{RESET}")
    check_persona()
    check_vad()
    spoken = check_tts()
    check_vad_hears(spoken)
    check_stt(spoken)
    asyncio.run(check_llm())

    print()
    if _failures:
        print(f"{RED}{_failures} check(s) failed.{RESET} See README 'Troubleshooting'.\n")
        return 1
    print(f"{GREEN}All checks passed.{RESET} Start the server with scripts/run_server.ps1\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
