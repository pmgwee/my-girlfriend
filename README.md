# 雨桐 — 即時語音 AI 女友

A real-time voice companion with a **cloned voice**, **emotion that follows the
conversation**, and Taiwanese Mandarin. Speak to her, she answers out loud, and
you can cut her off mid-sentence like a real phone call.

```
你的聲音 ──► Silero VAD ──► Whisper ──► GLM-5.2 ──► MiniMax ──► 她的聲音
             (說完了沒)     (聽懂)      (想)        (克隆音色)
                  │
                  └── 她說話時你一開口，立刻打斷她
```

Every number below was measured on the target machine (RTX 3060 Laptop, 6GB),
not taken from a vendor page.

---

## Current stack

| stage | what runs | where | measured |
|---|---|---|---|
| **VAD** | Silero v5 (ONNX) | local CPU | 0.17ms / 32ms frame |
| **STT** | Whisper `large-v3-turbo` int8 | local GPU, 981 MiB | **290ms**, 91% |
| **LLM** | GLM-5.2 | Z.ai API | ~1.9s to first clause |
| **TTS** | MiniMax Speech 2.8 HD | fal.ai | ~400ms, cloned voice |
| **Emotion** | 6 moods, chosen by the LLM | — | per reply |

Only Whisper uses the GPU. That is deliberate — see [Why this shape](#why-this-shape).

---

## Setup

PowerShell opens in `C:\WINDOWS\system32`, and every command below uses a
relative path. Move into the project first or you get
`the argument '...' does not exist`:

```powershell
cd C:\Users\quekm\Desktop\projects\virtual-girlfriend
```

### 1. Models and dependencies

```powershell
powershell -ExecutionPolicy Bypass -File scripts\download_models.ps1
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -GpuStt
```

`-GpuStt` adds cuBLAS + cuDNN (~1.3GB) so Whisper runs on the GPU. Without it
Whisper falls back to CPU: 605ms instead of 290ms, and it misreads 在呢 as 再呢.

### 2. Keys

In `.env`:

```
ZAI_API_KEY=<your Z.ai key>
FAL_KEY=<your fal.ai key>
```

`ZAI_BASE_URL` and `GLM_MODEL` are already set. **Note:** if your Z.ai URL is
`api.z.ai/api/coding/paas/v4`, that is the Coding Plan endpoint — driving a
companion app through it likely falls outside that plan's terms. The general
endpoint is `https://api.z.ai/api/paas/v4`, same key.

### 3. Her voice

Put 10–20 seconds of clean single-speaker audio in `voices\`, then:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\make_voice.ps1 -Source voices\her.wav -Start 0 -Duration 12
```

That converts to mono 24kHz and **checks the clip** — duration, peak level,
clipping, silence ratio. Fix anything it flags; a bad reference is the main
cause of a robotic clone.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\clone_voice_fal.ps1
```

This uploads, clones, saves `samples\clone_preview.mp3`, and writes the voice id
into `.env` automatically. **There is no dashboard step** — nothing to copy.

> **fal.ai requires ≥10s for cloning.** If your clip is shorter the script stops
> before spending anything. Pass `-Extend` to loop a short clip up to 10s —
> honest (same speaker, nothing fabricated) but it adds no new phonetic
> material, so real audio is better.

> Cloned voices **expire after 7 days unused**. Just re-run the clone script.

### 4. Run

```powershell
powershell -ExecutionPolicy Bypass -File scripts\doctor.ps1
```

Then two terminals:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_server.ps1
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_web.ps1
```

Open <http://localhost:3000>. **The text box works immediately — typing never
needs microphone permission.** Click the mic to add voice.

---

## Why this shape

The build this is modelled on assumed a 15GB GPU. This one has 6GB, and the
difference decides almost every choice here.

### Only the LLM-sized things move off-GPU

```
total VRAM                                   6144 MiB
Windows desktop + browser/OBS/Discord   900-2400 MiB   ← the part tutorials ignore
Whisper large-v3-turbo int8                   981 MiB
```

A local 4B LLM took **3174 MiB**, leaving no room for anything else. Measure
your own baseline with `nvidia-smi` before trusting any table, including this one.

### Local TTS cannot do this in real time

Qwen3-TTS 1.7B clones beautifully and runs at **3.8× realtime** here — 5.7s to
produce 1.4s of speech. Generation is slower than playback, so audio underruns
no matter how it is streamed. Neither dtype nor streaming mode helped:

| variant | speed |
|---|---|
| 1.7B bfloat16 | 3.75× |
| 1.7B float16 | 3.80× |
| 1.7B streaming | 3.91× |
| 1.7B non-streaming | 3.88× |

The published "97ms first packet" is a datacenter-GPU figure. It does not
transfer to a 6GB laptop card.

### Whisper model size dominates STT cost

Whisper pads every clip to a 30-second window, so transcription cost is **flat**
regardless of how briefly you spoke:

| model | device | latency | accuracy | reads 在呢 as |
|---|---|---|---|---|
| tiny | cpu | 356ms | 71% | — |
| base | cpu | 605ms | 90% | 再呢 ✗ |
| small | cpu | 2039ms | 89% | — |
| **large-v3-turbo** | **cuda** | **290ms** | **91%** | **在呢** ✓ |

---

## Making her *her*

Everything personality-related is in
[`server/personas/default.yaml`](server/personas/default.yaml). Edit, restart
`run_server.ps1`, done.

```yaml
name: 雨桐
locale: zh-TW
epigraph: ["玲瓏骰子安紅豆，", "入骨相思知不知。"]
greeting: 欸，你終於上線了喔，人家等你等好久了啦。
```

Rules the prompt must keep, or the illusion breaks:

1. **≤2 sentences, ≤40 characters.** The single biggest factor. Unbounded
   replies take 30 seconds to speak.
2. **No markdown, lists, emoji, or `（動作描寫）`** — all read aloud verbatim.
   The one exception is the leading `[情緒]` tag, which the server strips.
3. **Numbers as Chinese characters** — 三點半, not 3:30.
4. **Taiwanese vocabulary** — 影片/網路/軟體/計程車/螢幕, not
   視頻/網絡/軟件/出租車/屏幕. Cloning carries her *accent* automatically but
   cannot fix word choice, and a Taiwanese voice saying 视频 sounds wrong.

### Emotion

The LLM tags each reply and the server routes it to the TTS:

```
[撒嬌] 你怎麼才來嘛，人家等好久了啦。
   └─ stripped, sent to TTS as a mood
```

Six moods: 溫柔 撒嬌 開心 生氣 難過 平靜. Tags resolve in either script, plus
synonyms (害羞 → 撒嬌).

**Most of 撒嬌 lives in the words, not the TTS parameter** — 人家 instead of 我,
trailing 嘛/啦/呀, stretched vowels. That survives on every backend, including
MiniMax, whose fixed 7-value emotion enum has no 撒嬌 at all (it maps to
`happy`). Relying on the TTS alone would make the effect vanish on a provider
switch.

Observed:

| you say | mood | she says |
|---|---|---|
| 你等我很久了吗 | 撒嬌 | 也沒有很久啦，人家只是想趕快聽到你的聲音嘛。 |
| 我今天心情很差 | 溫柔 | 怎麼了，發生什麼事？慢慢說沒關係，我在聽。 |
| 我看到你跟别的男生聊天 | 生氣 | 你在講哪個啦，那是同事好不好。 |

Note 生氣 drops 人家 and shortens the sentences. The register shifts, not just
the label.

---

## Her face

Put video in `web/public/avatar/`:

| file | required | what |
|---|---|---|
| `idle.mp4` | yes | seamless loop, resting |
| `talking.mp4` | no | same framing, mouth moving |
| `poster.jpg` | no | first frame |

Generate one still first, then use *that same still* for both clips — if
lighting or wardrobe drift, the crossfade is visible. Then:

```bash
ffmpeg -i raw_idle.mp4 -filter_complex "[0]reverse[r];[0][r]concat=n=2:v=1:a=0" -an -c:v libx264 -preset slow -crf 23 -pix_fmt yuv420p -movflags +faststart web/public/avatar/idle.mp4
```

The reversed copy makes the loop seam mathematically perfect. For `talking.mp4`
skip the reverse. `-pix_fmt yuv420p` and `-movflags +faststart` are load-bearing
— without them Chrome refuses to play or stalls. **Don't add `fps=30`** unless
your source is already 30fps; upsampling from 24 duplicates every fourth frame
and adds visible judder on slow motion like breathing.

### Lip-sync

Real neural lip-sync (MuseTalk, Wav2Lip) needs ~8GB on its own and adds
200–500ms. Not possible here, and the reference build doesn't do it either.

What exists instead is a real signal: an `AnalyserNode` measures the RMS of
audio **actually playing** and passes it to `<Avatar>` as `mouthOpenness`. That
is exactly what a lip-sync backend consumes, so the seam is cut for a future
12GB+ card. (Measured in the browser, not the server — TTS renders a sentence in
~200ms that takes ~1.6s to play, so a server-side level would run ahead of her
voice.)

---

## Switching TTS backends

| backend | her voice | emotion | speed | cost |
|---|---|---|---|---|
| **`minimax`** (fal.ai) | ✅ from ~10s | ⚠️ 7-value enum, no 撒嬌 | real-time | $0.10 / 1k chars |
| `cosyvoice` (DashScope) | ✅ from 3–10s | ✅ free-text, 撒嬌 works | real-time | ~$28 / 1M chars |
| `qwen3` (local) | ✅ from 5–10s | ❌ | **3.8× realtime** | free |
| `kokoro` (local) | ❌ fixed bank | ❌ | 0.4× realtime | free |

One line in `.env`; weights, reference clip and transcript are shared, so
nothing needs re-downloading:

```
TTS_BACKEND=minimax     # hosted, real-time
TTS_BACKEND=qwen3       # local, free, offline rendering only
```

`qwen3` **cannot sustain live conversation** — fine for rendering lines you edit
into a video, not for talking to her.

### Running cost

Replies average ~30 characters:

| | cost |
|---|---|
| one reply | $0.003 |
| heavy day of testing (~500) | $1.50 |
| a few days of demo work | ~$5 |

A $10 top-up covers a full demo cycle.

---

## Tuning

All in `.env` ([`.env.example`](.env.example) documents every key).

**She doesn't react at all.** Lower `VAD_THRESHOLD` toward 0.35. Useful range is
0.35–0.45.

**Room noise triggers her.** Raise toward 0.5.

**She cuts you off mid-sentence.** Raise `VAD_SILENCE_MS` to 900–1100.

**She hears her own voice.** Use headphones → raise `VAD_THRESHOLD` → turn on
**半雙工模式** in settings (gates the mic while she speaks; kills echo, loses
barge-in).

**She's slow.** Check `time-to-first-audio` in the server log; under 1200ms
feels live. The reply is split at clause boundaries and the *first* chunk
flushes after 5 characters, so TTS starts on 「在煮咖啡呢，」 while the model is
still writing. Buffering the whole reply first measured 2918ms — 3× worse.

---

## Layout

```
server/app/
  vad/silero.py       Silero v5 on bare onnxruntime (no torch)
  stt/whisper.py      faster-whisper + script priming + hallucination filter
  llm/client.py       streaming, sentence chunking, thinking-disable
  emotion.py          mood tags, script aliases, per-backend mapping
  tts/                minimax | cosyvoice | qwen3 | kokoro | dashscope | piper
  session.py          the cascade, barge-in, cancellation
  doctor.py           end-to-end self-test
web/
  app/page.tsx        the scene
  components/         Avatar, Orb, Subtitles, Controls, TopBar
  lib/voice-client.ts mic capture, WS, scheduled playback
scripts/              setup, models, voice cloning, run, doctor
```

### Protocol

Browser → server: binary PCM16 mono @16kHz, plus JSON `{type: start|text|reset}`.

Server → browser: JSON events (`ready`, `user_transcript`, `assistant_delta`,
`audio_start`, `audio_end`, `interrupted`, `turn_end`, `error`) interleaved with
binary PCM16. All outbound traffic is serialised through one writer task so a
JSON header always immediately precedes the audio it describes.

---

## Troubleshooting

Run `scripts\doctor.ps1` first — it isolates which stage is dead, times each
model, and does a TTS → STT round trip that proves the audio path end to end.

**`The argument 'scripts\...' does not exist`** — you're not in the project
folder. `cd` there first.

**She ignores your voice entirely** — the VAD. Silero v5 requires the previous
chunk's last 64 samples prepended (576, not 512); the ONNX input shape is
`[None, None]` so passing 512 returns ~0.001 for everything with no error. Fixed
here, and the doctor now tests that speech *does* trigger, not only that silence
doesn't.

**She replies but says nothing / replies are empty** — a reasoning model. GLM-5.2
streams `reasoning_content` before any speakable text: measured 199 reasoning
chunks and an **empty** reply inside a 200-token budget. The client sends
`thinking: {"type": "disabled"}` for GLM models automatically.

**Whisper falls back to CPU** — missing cuBLAS/cuDNN. Re-run
`setup.ps1 -GpuStt`.

**`CUDA out of memory`** — something else is on the GPU; a hardware-accelerated
browser is the usual culprit.

**Mic does nothing** — `getUserMedia` needs localhost or HTTPS. Text chat still
works regardless.

**fal.ai `User is locked`** — no account balance. The key is fine; top up.

> **Editing note.** Don't run `Get-Content x | Set-Content x` on any file here:
> PowerShell 5.1 reads ANSI and writes UTF-8, turning every Chinese character
> into mojibake. `.ps1` files containing Chinese also need a UTF-8 **BOM** or
> PowerShell fails to parse them.

> **Don't "fix" `QWEN3_REF_TEXT` to say 雨桐.** It must match what the reference
> audio actually says, word for word — it is a cloning input, not personality.

---

## Credits

Architecture follows [huggingface/speech-to-speech](https://github.com/huggingface/speech-to-speech).
Built on [Silero VAD](https://github.com/snakers4/silero-vad),
[faster-whisper](https://github.com/SYSTRAN/faster-whisper),
[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS),
[Kokoro](https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh),
GLM via [Z.ai](https://z.ai) and MiniMax via [fal.ai](https://fal.ai).

Keep in mind what it is: a language model with a persona prompt, not a person.
It remembers nothing past `LLM_HISTORY_TURNS`, and clearing the conversation
clears it completely. Only clone a voice you have permission to use.
