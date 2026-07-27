# 晚晚 — 本地实时语音 AI 女友

A fully local, real-time voice companion. No API keys, no cloud, no per-minute
billing. Speak, she hears you, thinks, and answers out loud — and you can cut her
off mid-sentence like a real phone call.

Four models in a cascade, each swappable:

```
你的声音 ──► Silero VAD v5 ──► Whisper ──► llama.cpp ──► Kokoro TTS ──► 她的声音
             (说话检测)        (识别)      (思考)        (合成)
                  │
                  └── 她说话时你一开口，立刻打断她 (barge-in)
```

---

## Hardware reality check

This is the part most tutorials skip. **Read it before you start.**

The build this is modelled on assumed a 15GB GPU. This repo is tuned for **6GB**,
which is what a laptop RTX 3060/3070/4060 actually has:

Measured on an RTX 3060 Laptop (6144 MiB) with a normal desktop running:

| Stage | Model | VRAM | Latency |
|---|---|---|---|
| VAD | Silero v5 (ONNX) | 0 — CPU | 0.17ms / 32ms frame |
| STT | Whisper `base` int8 | 0 — CPU | ~670ms |
| LLM | Qwen3-4B Q4_K_M | **3174 MiB** | ~300ms to first token |
| TTS | Kokoro-82M (ONNX) | 0 — CPU | 0.41× realtime |

**Only the LLM is on the GPU, and that is the whole design.** The measurement
that forced it:

```
total VRAM                                 6144 MiB
Windows desktop + Chrome/OBS/Discord/VSCode 2404 MiB   <- the part tutorials ignore
llama.cpp (Qwen3-4B, 4096 ctx)              3174 MiB
                                            --------
free                                         566 MiB
```

Close the heavy apps and the desktop drops to ~1300 MiB, which is the difference
between "no room for anything else" and "room for a voice cloner". Measure your
own baseline with `nvidia-smi` before believing any table, including this one.

Whisper needs ~600MB on GPU. It does not fit. Running it on CPU costs 670ms per
turn and never OOMs mid-conversation, which is the right trade for something you
talk to.

### Choosing the Whisper size

Whisper pads every clip to a 30-second window, so transcription cost is **flat**
regardless of how briefly you spoke. Model size dominates. Measured on CPU with
Mandarin, scoring character overlap:

| model | latency | accuracy |
|---|---|---|
| `tiny` | 356ms | 71% |
| **`base`** | **671ms** | **87%** ← default |
| `small` | 2039ms | 89% |

`base` is 3× faster than `small` for two points of accuracy. Homophone errors
(在呢 → 再呢) survive at every size; the persona prompt tells the model to read
through them, which works well in practice.

### Moving Whisper to the GPU

Worth ~470ms if you have the headroom — close Chrome/OBS, or run a bigger card.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -GpuStt
```

That installs cuBLAS + cuDNN (~1.3GB), which CTranslate2 links dynamically but
doesn't bundle. Then set `STT_DEVICE=cuda` and `STT_COMPUTE_TYPE=int8_float16`
in `.env`. Check free VRAM first with `nvidia-smi`; if the server logs a fallback
to CPU on startup, you didn't have room.

### About lip-sync

Real neural lip-sync (MuseTalk, Wav2Lip, SadTalker) **is not possible on a 6GB
card alongside this pipeline.** MuseTalk alone wants ~8GB and adds 200–500ms per
frame. With four models already resident you have roughly 2GB free.

So the avatar is an idle/talking **video loop** — which is also what the original
build does.

What's here instead is a real signal, not a fake one: an `AnalyserNode` measures
the RMS of audio **actually playing** and passes it to `<Avatar>` as
`mouthOpenness` (0–1, per animation frame). Today it drives a subtle brightness
and scale lift. That is exactly the input a lip-sync backend consumes, so if you
later move to a 12GB+ GPU, see [Adding real lip-sync](#adding-real-lip-sync).

(Measuring in the browser rather than on the server is deliberate — TTS renders a
sentence in ~200ms that then takes ~1.6s to play, so a server-side level would
run well ahead of her voice.)

---

## Setup

**Requirements:** Windows 11, NVIDIA GPU (6GB+), Python 3.10–3.12, Node.js 20+,
~4GB disk.

**First, move into the project folder.** PowerShell opens in `C:\WINDOWS\system32`
by default, and every command below uses a relative path — running them from
anywhere else gives you `the argument '...' does not exist`:

```powershell
cd C:\Users\quekm\Desktop\projects\virtual-girlfriend
```

Then:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\download_models.ps1
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

Verify everything loaded before starting anything:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\doctor.ps1
```

This loads each model, times it, and does a TTS → STT round trip — Kokoro says
"今天天气真好" and Whisper reads it back. If that passes, the audio path is
correct end to end.

Then three terminals, **in this order**:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_llm.ps1
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_server.ps1
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_web.ps1
```

Open <http://localhost:3000>. The text box at the bottom works immediately —
**typing never needs microphone permission**. Click the mic button to add voice.

> First run of `run_server.ps1` downloads Whisper (~500MB) from HuggingFace and
> takes a few minutes. After that it starts in ~10 seconds.

---

## Add her face

Put your avatar video in `web/public/avatar/`:

| File | Required | What it is |
|---|---|---|
| `idle.mp4` | yes | 5–20s seamless loop, resting. |
| `talking.mp4` | no | Same framing, mouth moving. Crossfades in when she speaks. |
| `poster.jpg` | no | First frame, shown while video buffers. |

Shoot vertical (the pane is ~58vw wide, full height), keep the subject
right-of-centre, and make the loop seamless. Any image-to-video model (Veo,
Kling, Hailuo, Runway, Wan) generates these well from a single portrait.

Generate **one still first**, then use that same still as the input image for
both videos — if lighting or wardrobe drift between them, the crossfade is
visible.

Then process them for the web:

```bash
ffmpeg -i raw_idle.mp4 -filter_complex "[0]reverse[r];[0][r]concat=n=2:v=1:a=0" -an -c:v libx264 -preset slow -crf 23 -pix_fmt yuv420p -movflags +faststart web/public/avatar/idle.mp4
```

The reversed copy makes the loop seam mathematically perfect. For `talking.mp4`
skip the reverse — just strip audio and normalize:

```bash
ffmpeg -i raw_talking.mp4 -an -c:v libx264 -preset slow -crf 23 -pix_fmt yuv420p -movflags +faststart web/public/avatar/talking.mp4
```

```bash
ffmpeg -i web/public/avatar/idle.mp4 -vframes 1 -q:v 2 web/public/avatar/poster.jpg
```

`-pix_fmt yuv420p` and `-movflags +faststart` are load-bearing — without them
Chrome either refuses to play the file or stalls until fully buffered. Don't add
`fps=30` unless your source is already 30fps; upsampling from 24 duplicates every
fourth frame and adds visible judder on slow motion like breathing.

Without any video you get a placeholder card; everything else works.

---

## Making her *her*

Everything personality-related is in [`server/personas/default.yaml`](server/personas/default.yaml).
Edit it, restart `run_server.ps1`, done.

```yaml
name: 林晚
epigraph: ["玲珑骰子安红豆，", "入骨相思知不知。"]   # faint text, top-left
voice: zf_001
system_prompt: |
  你叫林晚…
greeting: 诶，你终于来了呀，我等你好久了。
```

Three rules the prompt must keep, or the illusion breaks:

1. **No markdown, no `*actions*`.** TTS reads asterisks out loud. (The server
   strips them as a backstop, but the model shouldn't emit them.)
2. **1–3 sentences.** A paragraph that reads fine on screen is interminable aloud.
3. **No assistant voice.** "有什么可以帮您的吗" instantly kills it.

### Picking a voice

Kokoro v1.1-zh has 100 Chinese voices with numeric names (`zf_001`–`zf_099`
female, `zm_009`–`zm_100` male), so you have to listen:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\list_voices.ps1 -Count 12
```

WAVs land in `samples\`. Put your pick in `.env` as `TTS_VOICE`.

---

## Tuning

All in `.env` ([`.env.example`](.env.example) documents every key).

**She interrupts herself / hears her own voice.** The browser's echo canceller
handles this on laptop speakers, but it's imperfect. In order: use headphones →
raise `VAD_THRESHOLD` to `0.75` → turn on **半双工模式** in settings (gates the
mic while she speaks; bulletproof, but you lose barge-in).

**She cuts you off mid-sentence.** Raise `VAD_SILENCE_MS` to `900`–`1100`.

**She's slow to answer.** Lower `VAD_SILENCE_MS` to `500`. Then check the server
log for `time-to-first-audio`; under 1200ms feels live. Measured here, typed
input (so excluding STT):

```
942ms to first audio   =  LLM first clause ~400ms  +  TTS first chunk ~540ms
```

Add ~670ms for voice input. The trick that makes this work is in
[`llm/client.py`](server/app/llm/client.py): the reply is split at clause
boundaries and the *first* chunk flushes after just 5 characters, so TTS starts
on "在煮咖啡呢，" while the model is still writing the rest. Buffering the whole
reply first measured 2918ms — 3× worse.

If it's still slow: STT dominating → `STT_MODEL=tiny`; LLM dominating → lower
`LLM_MAX_TOKENS`.

**Wrong words in the transcript.** Whisper `small` mishears names. Set
`STT_MODEL=medium` **only** if you also drop the LLM to Qwen3-1.7B — both won't
fit in 6GB.

---

## Switching TTS backends

All measured on this machine (RTX 3060 Laptop, 6GB):

| backend | her voice | emotion | speed | cost |
|---|---|---|---|---|
| `minimax` (fal.ai) | ✅ clones from ~5s | ⚠️ 7-value enum, no 撒娇 | real-time | $0.10 / 1k chars |
| `cosyvoice` (DashScope) | ✅ clones from 3-10s | ✅ free-text, 撒娇 works | real-time | ~$28 / 1M chars |
| `qwen3` (local) | ✅ clones from 5-10s | ❌ none | **3.8x realtime** | free |
| `kokoro` (local) | ❌ fixed voice bank | ❌ none | 0.4x realtime | free |

**`qwen3` cannot sustain live conversation.** At 3.8x realtime, generation is
slower than playback and audio underruns mid-sentence. dtype and streaming mode
made no difference (3.75x-3.91x across four variants). It is fine for rendering
lines offline; it is not fine for talking to her.

Switching is one line in `.env` -- weights, reference clip and transcript are
shared, so nothing needs re-downloading or re-cloning:

```
TTS_BACKEND=minimax     # hosted, real-time
TTS_BACKEND=qwen3       # local, free, offline rendering only
```

### What it costs to run hosted

Replies are capped at 40 characters by the persona, averaging ~30:

| | cost |
|---|---|
| one reply | $0.003 |
| a heavy day of testing (~500 replies) | $1.50 |
| a few days of demo work | ~$5 |

Cloning is a one-time charge of a few cents. A $10 top-up covers a full demo
cycle with room to spare.

---

## Voice cloning

Kokoro cannot clone a voice at any quality — its voices are a fixed embedding
bank. If you want her to sound like a specific person, you need Qwen3-TTS, which
clones from 5–10 seconds of reference audio.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -VoiceClone
```

That pulls torch + `qwen-tts` (~3GB). Then cut your reference clip:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\make_voice.ps1 -Source recording.mp3 -Start 12 -Duration 8
```

The script converts to mono 24kHz WAV and then **checks the clip** — duration,
peak level, clipping, and silence ratio — because a bad reference is the single
biggest cause of a robotic clone. Fix anything it flags before going further.

Then in `.env`:

```
TTS_BACKEND=qwen3
QWEN3_REF_AUDIO=voices/reference.wav
QWEN3_REF_TEXT=<exact transcript of the clip>
```

`QWEN3_REF_TEXT` is not optional. Cloning quality drops noticeably when the
transcript doesn't match the audio.

**Reference audio rules that actually matter:**

- 5–10s. Over ~15s makes cloning *worse*, not better.
- One speaker, no music, no reverb, no background noise.
- The delivery you want back — a flat reference clones to a flat voice.

Only clone a voice you have permission to use: your own, one recorded with
consent, or a public dataset sample. Cloning someone's voice without their
agreement carries real legal exposure in many places.

### The VRAM problem

On a 6GB card you cannot have the 1.7B cloner *and* a 4B LLM resident:

| Config | TTS | LLM | Total | Fits 6GB? |
|---|---|---|---|---|
| Kokoro (no cloning) | 0 (CPU) | 3174 MiB | ~4.1 GB | yes |
| **Qwen3-TTS 0.6B** | ~2.0 GB | 3174 MiB | **~6.0 GB** | barely |
| Qwen3-TTS 1.7B | ~4.2 GB | 3174 MiB | ~8.2 GB | **no** |
| Qwen3-TTS 1.7B + hosted LLM | ~4.2 GB | 0 | ~5.1 GB | yes |

The LLM is the biggest single consumer *and* the one that benefits most from
being larger. If you want the 1.7B voice and smarter replies, point
`LLM_BASE_URL` at a hosted OpenAI-compatible endpoint and give the whole GPU to
voice. Everything you say still gets transcribed locally; only the text of your
messages leaves the machine.

---

## Upgrading to qwentts.cpp

Kokoro is the default because it installs from pip with no compiler. Qwen3-TTS
(what the original build uses) sounds clearly better on Mandarin and supports
voice cloning and dialects — but you compile it yourself.

1. Build [qwentts.cpp](https://github.com/ServeurpersoCom/qwentts.cpp) with
   `buildcuda.cmd` (needs CUDA Toolkit + MSVC Build Tools).
2. Get GGUFs from [Serveurperso/Qwen3-TTS-GGUF](https://huggingface.co/Serveurperso/Qwen3-TTS-GGUF).
   The 0.6B Q4_K_M talker needs ~255MB.
3. Run its `tts-server` on port 8081.
4. In `.env`: `TTS_BACKEND=qwentts` and `TTS_VOICE=cherry`.

The adapter talks to it over HTTP ([`server/app/tts/qwentts.py`](server/app/tts/qwentts.py)),
so it can crash or be rebuilt without taking the pipeline down.

---

## Adding real lip-sync

Needs a 12GB+ GPU. The seam is already cut:

- **Server** — `Session._speak()` in [`server/app/session.py`](server/app/session.py)
  already slices synthesised audio before sending. A lip-sync stage slots in
  beside it: take the same PCM, produce frames, send them as a second binary
  stream tagged with the same `chunkId`.
- **Client** — `<Avatar>` in [`web/components/Avatar.tsx`](web/components/Avatar.tsx)
  receives `mouthOpenness`. Swap the two `<video>` elements for a `<canvas>` that
  blits incoming frames; nothing else in the UI changes.

Realistic budget: MuseTalk ~8GB and ~30ms/frame on a 4070. You'd also want to
move Whisper to CPU to free VRAM, which costs ~400ms per turn. It is a genuine
trade, not a free upgrade.

---

## Layout

```
server/
  app/
    vad/silero.py       Silero v5 on bare onnxruntime (no torch)
    stt/whisper.py      faster-whisper + hallucination filtering
    llm/client.py       streaming client + sentence chunking
    tts/                kokoro | qwentts | piper, behind one interface
    session.py          the cascade, barge-in, cancellation
    main.py             FastAPI + WebSocket
    doctor.py           end-to-end self-test
  personas/default.yaml
web/
  app/page.tsx          the scene
  components/           Avatar, Orb, Subtitles, Controls, TopBar
  lib/voice-client.ts   mic capture, WS, scheduled playback
scripts/                setup + run + model download
```

### Protocol

Browser → server: binary PCM16 mono @16kHz, plus JSON `{type: start|text|reset}`.

Server → browser: JSON events (`ready`, `user_transcript`, `assistant_delta`,
`audio_start`, `audio_end`, `interrupted`, `turn_end`, `error`) interleaved with
binary PCM16 at the rate given in `audio_start`. All outbound traffic is
serialised through one writer task so a JSON header always immediately precedes
the audio it describes.

---

## Troubleshooting

**`The argument 'scripts\...' does not exist`** — you're not in the project
folder. `cd C:\Users\quekm\Desktop\projects\virtual-girlfriend` first. Every
script here uses relative paths.

**`Cannot reach the LLM`** — `run_llm.ps1` isn't running, or it's still loading.
Wait for `server listening`, then check <http://127.0.0.1:18080/v1/models>; it
should return JSON with a `data` array. If you get an HTML error page instead,
something else owns that port — `run_llm.ps1` refuses to start in that case and
names the offending process. Port 18080 is used precisely because 8080 (Apache,
Jenkins, Tomcat) and 8090 (Windows notification helper) are commonly taken.

**Whisper falls back to CPU / `cublas64_12.dll is not found`** — CTranslate2
links cuBLAS and cuDNN dynamically but doesn't ship them. `setup.ps1` installs
them as pip wheels (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) and the STT module
registers their location at import, so this should be handled. If it persists,
reinstall those two packages inside `.venv`. CPU still works — `small` takes
~1.5s per turn there instead of ~0.3s.

**`CUDA out of memory` from llama.cpp** — something else is on the GPU (a
browser with hardware acceleration is the usual culprit). Lower `-GpuLayers`:
`scripts\run_llm.ps1 -GpuLayers 24`.

**Mic does nothing** — `getUserMedia` requires localhost or HTTPS. `127.0.0.1`
and `localhost` are fine; a LAN IP is not. Text chat still works regardless: the
session connects without the microphone, and a denied permission falls back to
text rather than dead-ending.

**Kokoro fails on Chinese text** — the misaki G2P frontend didn't install. Run
`pip install "misaki-fork[zh]"` inside `.venv`, or set `TTS_BACKEND=piper`.

**Silence, no errors** — run `scripts\doctor.ps1`; it isolates which stage is
dead. The settings gear in the UI also shows live STT/TTS/LLM status.

**Downloads crawl at a few hundred KB/s** — the scripts use `curl.exe` (ships
with Windows 10+) precisely because `Invoke-WebRequest` buffers whole responses
on PowerShell 5.1. If curl is missing the script falls back and will be slow.

> Editing note: don't run `Get-Content x | Set-Content x` on any file in this
> repo. PowerShell 5.1 reads as ANSI and writes as UTF-8, which turns every
> Chinese character into mojibake.

---

## Credits

Architecture follows [huggingface/speech-to-speech](https://github.com/huggingface/speech-to-speech).
Built on [Silero VAD](https://github.com/snakers4/silero-vad),
[faster-whisper](https://github.com/SYSTRAN/faster-whisper),
[llama.cpp](https://github.com/ggml-org/llama.cpp),
[Kokoro](https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh) and
[qwentts.cpp](https://github.com/ServeurpersoCom/qwentts.cpp).

Personal project. Keep in mind what it is: a language model with a persona
prompt, not a person. It doesn't remember anything past `LLM_HISTORY_TURNS`, and
clearing the conversation clears it completely.
