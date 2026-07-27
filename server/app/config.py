"""Runtime configuration.

Every stage of the pipeline is selected here so you can hot-swap a backend by
editing .env instead of touching code. Defaults are tuned for a 6GB laptop GPU
(RTX 3060 Laptop / 3070 Laptop / 4060) rather than the 15GB desktop card the
original build assumed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "models"

# The whole pipeline speaks 16kHz mono int16 internally. Whisper and Silero both
# want exactly this, so keeping one canonical format avoids resampling between
# stages -- we only resample at the two edges (mic in, speaker out).
SAMPLE_RATE = 16_000
FRAME_MS = 32
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 512 samples, Silero v5's native frame


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------------- server ----------------
    host: str = "127.0.0.1"
    port: int = 8765
    log_level: str = "INFO"

    # ---------------- VAD ----------------
    # Silero returns P(speech) per 32ms frame. 0.4 is forgiving enough for a
    # laptop mic across a desk; useful range is 0.35-0.45. Raise toward 0.5 only
    # if room noise keeps triggering turns.
    vad_threshold: float = 0.4
    # How much trailing silence ends a turn. Below ~400ms she cuts you off
    # mid-sentence; above ~1000ms the conversation feels sluggish.
    vad_silence_ms: int = 500
    # Ignore blips shorter than this so a cough doesn't trigger a turn.
    vad_min_speech_ms: int = 300
    # Hard ceiling on one turn. Guarantees a turn always ends even if room noise
    # keeps the speech probability above the exit threshold.
    vad_max_speech_ms: int = 20_000
    # Audio kept from *before* the VAD fired, so the first syllable isn't clipped.
    vad_prefix_ms: int = 320

    # ---------------- STT ----------------
    stt_backend: Literal["faster-whisper"] = "faster-whisper"
    # Measured on this box, Mandarin, character-overlap accuracy:
    #     base            cpu    605ms  90%   (misreads 在呢 as 再呢)
    #     large-v3-turbo  cuda   290ms  91%   (gets it right)  <- default
    # Whisper pads every clip to a 30s window, so the cost is flat regardless of
    # how briefly you spoke; model size and device dominate.
    stt_model: str = "large-v3-turbo"
    # Needs ~1GB VRAM and the cuBLAS/cuDNN wheels (scripts\setup.ps1 -GpuStt).
    # Falls back to CPU automatically if either is missing.
    stt_device: Literal["cuda", "cpu"] = "cuda"
    # int8_float16 matches float16's speed at half the VRAM (981 vs 1783 MiB).
    stt_compute_type: str = "int8_float16"
    # "zh", "en", or None to auto-detect each turn (costs ~100ms).
    stt_language: str | None = "zh"

    # ---------------- LLM ----------------
    # Points at a llama.cpp `llama-server` you start separately (scripts/run_llm.ps1).
    # Any OpenAI-compatible endpoint works here, including Ollama and vLLM.
    # High port, deliberately. llama.cpp's usual 8080 collides with Apache,
    # Jenkins and most dev servers, and 8090 is taken by Windows' notification
    # helper on some machines. run_llm.ps1 refuses to start on a busy port.
    llm_base_url: str = "http://127.0.0.1:18080/v1"
    llm_api_key: str = "no-key-needed"
    llm_model: str = "local"

    # Convenience passthrough for a Z.ai / GLM account. When ZAI_API_KEY is set
    # these win over the LLM_* values above, so you can keep the local llama.cpp
    # settings in .env and switch providers by adding or removing one key.
    zai_base_url: str = ""
    zai_api_key: str = ""
    glm_model: str = ""
    llm_temperature: float = 0.85
    llm_max_tokens: int = 320
    # None = auto-detect from the model name. Reasoning models emit a long
    # chain of thought before any speakable text, which is unusable live.
    llm_disable_thinking: bool | None = None
    # Turns of history kept in context. Each turn is ~80 tokens; 12 keeps us well
    # inside a 4096-token window while still remembering the conversation.
    llm_history_turns: int = 12

    # ---------------- TTS ----------------
    # dashscope -> Qwen3-TTS hosted by Alibaba. Cloning AND real-time (~400ms).
    #              The only option that delivers both on a 6GB card.
    # qwen3     -> the same model locally. Clones well but runs at 3.8x realtime
    #              here, i.e. slower than playback. Fine for offline rendering.
    # kokoro    -> ONNX, CPU, instant, fixed voice bank. No cloning.
    # qwentts   -> qwentts.cpp over HTTP; cloning, but you compile it yourself.
    # piper     -> offline fallback, lowest quality.
    # cosyvoice -> cloning + per-reply emotion + real-time. The only backend
    #              that does all three; see app/tts/cosyvoice.py.
    # dashscope -> hosted Qwen3-TTS. Clones, but no emotion control.
    # qwen3     -> Qwen3-TTS locally. Clones well, but 3.8x realtime here.
    # kokoro    -> ONNX, CPU, instant, fixed voice bank. No cloning.
    # qwentts   -> qwentts.cpp over HTTP; you compile it yourself.
    # piper     -> offline fallback, lowest quality.
    # minimax   -> cloning from ~5s + emotion, via fal.ai. Easiest signup, and
    #              the only hosted option that fits a sub-10s reference clip.
    # cosyvoice -> cloning + free-text emotion (can express 撒娇), via DashScope.
    #              Better emotion range; Alibaba verification is the cost.
    # dashscope -> hosted Qwen3-TTS. Clones, no emotion control.
    # qwen3     -> Qwen3-TTS locally. Clones well, but 3.8x realtime here.
    # kokoro    -> ONNX, CPU, instant, fixed voice bank. No cloning.
    tts_backend: Literal[
        "minimax", "cosyvoice", "dashscope", "qwen3", "kokoro", "qwentts", "piper"
    ] = "kokoro"
    # Kokoro v1.1-zh ships 100 numbered Chinese voices: zf_* female, zm_* male.
    # Run scripts/list_voices.ps1 to hear the full set and pick a different one.
    tts_voice: str = "zf_001"
    tts_speed: float = 1.0
    # Seconds between keep-alive syntheses for hosted backends. 0 disables.
    #
    # DISABLED because measurement showed it is unnecessary on fal.ai: calls
    # after 15s / 45s / 90s idle took 3.8s / 4.9s / 3.4s, versus 3.1s
    # back-to-back. There is no idle scale-down to defend against, so pinging
    # would just spend money. The 15-22s seen on a fresh process is
    # first-call-ever cost (TLS, client setup), which one warmup at startup
    # already covers. Kept as a switch in case another provider behaves worse.
    tts_keepwarm_seconds: int = 0
    # qwentts.cpp's OpenAI-compatible server, if tts_backend == "qwentts".
    qwentts_base_url: str = "http://127.0.0.1:8081"

    # ---------------- Qwen3-TTS voice cloning ----------------
    # 0.6B fits a 6GB card alongside a 4B LLM; 1.7B sounds better but needs the
    # LLM moved off-GPU. See README 'Voice cloning'.
    qwen3_model: str = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
    # 5-10s of clean, single-speaker audio. Quality here decides everything.
    qwen3_ref_audio: Path = REPO_ROOT / "voices" / "reference.wav"
    # Exact transcript of that clip. Cloning degrades noticeably if it's wrong.
    qwen3_ref_text: str = ""
    qwen3_device: str = "cuda"
    qwen3_dtype: str = "bfloat16"

    # ---------------- MiniMax via Replicate ----------------
    # Half fal.ai's price and billed to a card rather than a prepaid balance.
    replicate_api_token: str = ""
    # Written by scripts\clone_voice_replicate.ps1.
    replicate_voice_id: str = ""
    # Verified against the account with scripts\check_replicate.ps1 -- Replicate
    # does not host every MiniMax revision fal does.
    replicate_model: str = "minimax/speech-02-hd"

    # ---------------- MiniMax via fal.ai ----------------
    fal_key: str = ""
    # Written by scripts\register_voice.ps1 after cloning.
    minimax_voice_id: str = ""
    # 2.8-hd is the current flagship. speech-02-hd is two generations old --
    # fal's voice-clone docs still use it in their example, which is easy to
    # copy by mistake.
    minimax_model: str = "fal-ai/minimax/speech-2.8-hd"

    # ---------------- DashScope (hosted TTS) ----------------
    dashscope_api_key: str = ""
    # Set by scripts\register_voice.ps1 after enrolling the reference clip.
    dashscope_voice_id: str = ""
    # cosyvoice-v3-flash  : cloning + emotion instructions, Singapore
    # cosyvoice-v3.5-flash: same, Beijing region
    # cosyvoice-v2        : cloning only -- IGNORES instructions, flat delivery
    # qwen3-tts-vc-*      : cloning only, no emotion parameter at all
    # The target_model used at enrollment must match this EXACTLY, so changing
    # it means re-registering the voice.
    dashscope_model: str = "cosyvoice-v3-flash"
    dashscope_base_url: str = "https://dashscope-intl.aliyuncs.com/api/v1"
    # Leave blank for the SDK default. Set for a workspace-specific endpoint.
    dashscope_websocket_url: str = ""
    # Constant traits, prepended to every emotion instruction. The clone already
    # carries her accent, but restating it keeps delivery from drifting.
    dashscope_instruction: str = "用台湾女生的口音和语气说话"

    # ---------------- persona ----------------
    persona_file: Path = REPO_ROOT / "server" / "personas" / "default.yaml"

    @field_validator("qwen3_ref_audio", "persona_file", mode="after")
    @classmethod
    def _anchor_to_repo_root(cls, value: Path) -> Path:
        # Paths in .env are written relative to the repo, but the server runs
        # from server/ and scripts run from anywhere. Anchor them so a relative
        # value means the same thing regardless of the working directory.
        return value if value.is_absolute() else (REPO_ROOT / value).resolve()

    @field_validator("stt_language", mode="before")
    @classmethod
    def _blank_language_means_auto(cls, value: object) -> object:
        # `STT_LANGUAGE=` in .env arrives as "", which Whisper rejects. Treat a
        # blank value as "auto-detect" -- that is what leaving it empty means.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def resolved_qwen3_model(self) -> str:
        """Local directory if one exists under the repo, else a HuggingFace id.

        Both look like 'a/b', so the only reliable test is whether the path is
        actually on disk. Anchoring to REPO_ROOT matters because the server runs
        from server/ while scripts run from the repo root.
        """
        candidate = REPO_ROOT / self.qwen3_model
        return str(candidate.resolve()) if candidate.is_dir() else self.qwen3_model

    @property
    def resolved_llm(self) -> tuple[str, str, str]:
        """(base_url, api_key, model) after applying the Z.ai override."""
        if self.zai_api_key.strip():
            return (
                (self.zai_base_url or "https://api.z.ai/api/paas/v4").rstrip("/"),
                self.zai_api_key,
                self.glm_model or "glm-4.6",
            )
        return self.llm_base_url, self.llm_api_key, self.llm_model

    @property
    def models_dir(self) -> Path:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        return MODELS_DIR


settings = Settings()
