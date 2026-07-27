"""HTTP + WebSocket entrypoint."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import SAMPLE_RATE, settings
from .llm.client import LlmClient
from .persona import Persona
from .session import Session, Stages
from .stt.whisper import WhisperStt
from .tts import build_tts

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("virtual-girlfriend")

_stages: Stages | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load every model once, up front.

    Doing this eagerly means a slow start but a fast first word. Loading lazily
    on the first connection puts a 10-15s stall exactly where the user is
    listening for a reply.
    """
    global _stages

    persona = Persona.load(settings.persona_file)
    log.info("persona: %s (%s)", persona.display_name, settings.persona_file.name)

    log.info("loading Whisper '%s'...", settings.stt_model)
    stt = WhisperStt(
        model_size=settings.stt_model,
        device=settings.stt_device,
        compute_type=settings.stt_compute_type,
        language=settings.stt_language,
        # The persona's locale decides Simplified vs Traditional output.
        locale=persona.locale,
        # Her name is baked into the priming prompt so Whisper transcribes it
        # correctly instead of guessing a homophone.
        name=persona.display_name,
    )

    log.info("loading TTS (%s)...", settings.tts_backend)
    tts = build_tts(settings)
    await asyncio.to_thread(tts.warmup)

    llm_url, llm_key, llm_model = settings.resolved_llm
    llm = LlmClient(
        base_url=llm_url,
        api_key=llm_key,
        model=llm_model,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        disable_thinking=settings.llm_disable_thinking,
    )
    if await llm.health():
        log.info("LLM reachable: %s (%s)", llm_url, llm_model)
    else:
        log.warning(
            "LLM NOT reachable at %s. If local, start scripts/run_llm.ps1. "
            "The server will run, but she can't reply yet.",
            llm_url,
        )

    _stages = Stages(stt=stt, tts=tts, llm=llm, persona=persona, settings=settings)

    keepwarm = asyncio.create_task(_keep_tts_warm(tts, settings.tts_keepwarm_seconds))
    log.info("ready on ws://%s:%s/ws", settings.host, settings.port)

    yield

    keepwarm.cancel()
    await llm.aclose()
    tts.close()


async def _keep_tts_warm(tts, interval: int) -> None:
    """Ping a hosted TTS so the provider doesn't scale it down between turns.

    Runs at the app level rather than per-session so several open tabs don't
    multiply the ping rate. Failures are swallowed: a keep-alive that errors
    must never take the server down, and the next real call will surface the
    problem properly.
    """
    if interval <= 0 or not getattr(tts, "needs_keepwarm", False):
        return

    log.info("TTS keep-alive every %ds (cold start is 15-22s otherwise)", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(tts.synthesize, "嗯", tts.default_voice, 1.0, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - best-effort by design
            log.debug("keep-alive ping failed: %s", exc)


app = FastAPI(title="virtual-girlfriend", lifespan=lifespan)

# The Next.js dev server runs on a different port; everything stays on loopback.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    assert _stages is not None
    llm_url, _, llm_model = settings.resolved_llm
    return {
        "ok": True,
        "stt": {"model": settings.stt_model, "device": _stages.stt.device},
        "tts": {"backend": settings.tts_backend, "sampleRate": _stages.tts.sample_rate},
        "llm": {
            "url": llm_url,
            "model": llm_model,
            "reachable": await _stages.llm.health(),
        },
    }


@app.get("/config")
async def config() -> dict:
    """Everything the browser needs to render the UI."""
    assert _stages is not None
    return {"inputSampleRate": SAMPLE_RATE, **_stages.persona.client_config()}


@app.websocket("/ws")
async def websocket_endpoint(socket: WebSocket) -> None:
    await socket.accept()
    assert _stages is not None

    # All outbound traffic funnels through one writer task. Without this, two
    # concurrent sends can interleave a JSON header with another chunk's audio.
    outbox: asyncio.Queue = asyncio.Queue(maxsize=256)

    async def send(message) -> None:
        await outbox.put(message)

    async def writer() -> None:
        while True:
            message = await outbox.get()
            if isinstance(message, bytes):
                await socket.send_bytes(message)
            else:
                await socket.send_text(json.dumps(message, ensure_ascii=False))

    writer_task = asyncio.create_task(writer())
    session = Session(send, _stages)

    try:
        await send({
            "type": "ready",
            "inputSampleRate": SAMPLE_RATE,
            **_stages.persona.client_config(),
        })

        while True:
            message = await socket.receive()

            if message["type"] == "websocket.disconnect":
                break

            if (audio := message.get("bytes")) is not None:
                await session.feed_audio(audio)
                continue

            if (text := message.get("text")) is None:
                continue

            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue

            kind = event.get("type")
            if kind == "start":
                await session.greet()
            elif kind == "text" and event.get("text"):
                await session.submit_text(event["text"].strip())
            elif kind == "reset":
                await session.reset()

    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("websocket error")
    finally:
        await session.close()
        writer_task.cancel()
        log.info("session closed")


def run() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
