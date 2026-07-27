# Stack 建议 — 完美的实时女友通话体验

> 审计日期：2026-07-27。基于一次 9-agent 调研（5 路并行 web 研究 + 综合 + 3 路对抗式审查），
> 已对照本仓实测数字修正了第一版结论。**本文档只给建议，不含已实施的代码改动。**
>
> 凡标 **[实测]** 的数字来自本机（RTX 3060 Laptop / 6GB）；凡标 **[vendor]** 的来自厂商或社区，
> 在进入延迟/显存预算前必须在本机复测。这是本项目自己的规矩（见 `README.md:146`、`config.py:64`）。

---

## 0. 背景

- **产品**：雨桐 — 即时语音 AI 女友。台灣國語、克隆音色、6 种情绪、可中途打断（像真电话）。
- **硬约束（决定一切的那一个）**：RTX 3060 Laptop，6GB VRAM。Windows 桌面 + 浏览器/OBS 已吃 900–2400 MiB。
- **live stack**（`.env` 实际值）：`TTS_BACKEND=minimax`、`GLM_MODEL=glm-5.2`、`STT_MODEL=large-v3-turbo` / `cuda` / `int8_float16`。
  > 注意：`config.py` 的 default 是 `kokoro` + `glm-4.6`——**code default ≠ live**。`.env` 覆盖了。
  > Replicate backend（半价）代码已写好但 `.env` 还没 flip，migration 在半空。

### 各段延迟拆解

```
你讲完 ─► VAD silence 500ms ─► STT 290ms ─► LLM 首 token ~1.7–1.9s ─► TTS 首音频 ~400ms
        (可调)           [实测]      [实测 ❗占~73%]            [README 值]
```

只有 VAD/STT 是 local；LLM 与 TTS 是 API（6GB 墙所迫：本地 4B LLM = 3174 MiB，本地 Qwen3-TTS = 3.8× realtime）。

---

## 1. 直接结论：local stage 要不要全上 GPU？

**不用，硬搬 VAD 反而更慢。** 唯一相关的本地段：

| stage | 现状 | 搬 GPU？ | 理由 |
|---|---|---|---|
| **VAD** (Silero v5) | CPU, 0.17ms/frame, inline 在 asyncio loop | ❌ | 1.8MB 模型，0.17ms 已比 32ms frame 快 ~190×。GPU kernel-launch（~3–25µs/op × 多算子）+ 两次 host↔device memcpy > 它干的活。[`silero.py:73`](server/app/vad/silero.py#L73) 硬钉 `CPUExecutionProvider` 是刻意的。 |
| **STT** (Whisper) | GPU, 290ms, 981 MiB **[实测]** | ✅ 已在 | 你自己 benchmark 表里的最优解。 |

**hot-path 的 TTS 是 hosted（MiniMax/CosyVoice），占 0 本地 VRAM**——所以"全部跑在 GPU"没有目标。

> ⚠️ **撤回一个研究初版建议**：初版说"把 VAD 移出 asyncio loop"。这违背项目已量过的决定——[`session.py:3`](server/app/session.py#L3) 测过 inline ~0.5ms/frame，判定 executor hop 更慢。**别动 VAD。** barge-in 的真缺口在 transport 层（没有 WebRTC AEC，开扬声器会回授触发 VAD），不是 loop 阻塞。

---

## 2. 真正的瓶颈：LLM 首 token

VAD 可忽略，STT 290ms 且零网络往返（对 barge-in 关键），TTS ~400ms 可接受。**LLM 首 token (~1.7–1.9s) 是唯一明显离谱的一段，约总 first-audio 的 73%。**

→ 你不是在 GPU 搬运上赢，是在 **LLM 首 token + 撒娇质量** 上赢。

---

## 3. 排序的优化清单

每条都标了"必须先验证"——别拿 vendor 数字当预算。

| # | 杠杆 | 动作 | 预期 | effort | 必须先验证 |
|---|---|---|---|---|---|
| **1** | **LLM 首 token**（决定性） | 先量 Z.ai-direct 的 TTFT under load；若真 ~1.9s，换更快的 inference host（DeepInfra/OpenRouter）或更快 GLM 变体。 | ~1.9s → ~0.5s **[vendor P50]** | small | ① DeepInfra 确实 serve GLM-4.6-FP8？② 4.6 会不会是 5.2 的**人格降级**（5.2 更新，撒娇措辞可能回退）？③ failover **留在 GLM 家族内**（不要 Gemini/Claude），否则 [`emotion.py:117`](server/app/emotion.py#L117) 调好的撒娇措辞会在最坏时刻崩。 |
| **2** | **撒娇 → 已有 CosyVoice** ⭐ | route `[撒娇]` 到已接好的 `cosyvoice-v3-flash`，其余 5 mood 留 MiniMax。 | 真撒娇 prosody（现完全不存在） | small | 先注册 reference clip，A/B CosyVoice 撒娇 vs MiniMax。详见 §4。 |
| **3** | **完成 Replicate migration（要量）** | Replicate ≈ fal 半价 + 信用卡计费，这是真的赢。 | ~50% 成本 ↓ | small | ① [`check_replicate.ps1`](scripts/check_replicate.ps1) 跑一下看 Replicate 到底 host 哪个 revision（[`config.py:144`](server/app/config.py#L144) 警告不全）；② **`scripts\bench_replicate.ps1` 不存在**（[`replicate_tts.py:8`](server/app/tts/replicate_tts.py#L8) 引用它）——先建再实测。Turbo 是否更快是 vendor 说法。 |
| **4** | **SenseVoice STT 原型** | FunASR SenseVoice-Small：非自回归、Mandarin-native、流式、~70ms/10s **[vendor]**。 | 290ms → ~150ms + 可重叠 STT/LLM | medium | 只做 A/B，**永远别拔 Whisper**；台味口音若回退就退回。A/B 时别让两个 STT 同时驻留 OOM。 |
| **5** | **LiveKit transport（最后）** | WebRTC-grade barge-in + AEC + 可观测。 | 打断进入个位数 ms | large | 这是 [`session.py`](server/app/session.py) 的**重写**（epoch-cancel + 多 backend mood router），不是 plug-in。先 cost 轻量替代（WebRTC data channel + 浏览器端 AEC，保留现有 cascade）。**排最后**。 |

---

## 4. ⭐ 关键修正：撒娇不必引新 vendor，已有 CosyVoice 就能做

调研第一版建议为撒娇引入 **Step-Audio 2.5**（新 vendor）。三路对抗审查一致判定**错了**：

- 你**早就接好了** `cosyvoice` backend：[`cosyvoice.py`](server/app/tts/cosyvoice.py)、[`emotion.py:38-42`](server/app/emotion.py#L38) 已写好 free-text instruction「用撒娇的语气说，带鼻音，尾音拖长上扬…像在跟男朋友耍赖」、[`config.py:171`](server/app/config.py#L171) 的台味口音 base instruction；`README.md:260` 也明写「free-text, 撒嬌 works, real-time」。
- **撒娇的 defect 是"MiniMax 做不到"，不是"产品做不到"**——你只是默认 route 到了 MiniMax。
- 多 vendor 分 mood（5 mood→MiniMax，撒娇→Step-Audio）会 **endanger cloned-voice 一致性**：同一只雨桐在撒娇时变成"差不多雨桐"。且 Step-Audio 在大陆，台湾 RTT 抖动恰打在最重要的 mood 上。

**正确做法**：route `[撒娇]` 到已有的 CosyVoice，**同一只 clone 覆盖全部 6 mood**。Step-Audio 2.5 只在 CosyVoice 撒娇输了之后才 A/B，且必须先证明它能**同时**接受 cloned reference + free-text 撒娇 instruction（多数 instruction-TTS 是二选一）。

---

## 5. 要不要换 end-to-end speech-to-speech？

**不要。** 调研最稳的结论之一：

- **6GB 本地**：只有 GLM-4-Voice int4(~4.5GB) / Sesame CSM-1B int8(~3–4GB) 勉强塞下，**两者都放弃 cloning 或 Mandarin**。
- **hosted E2E**（GPT-Realtime / Qwen-Audio-3.0 / Step-Audio Realtime）能把首音频砍到 ~1.0s **[vendor]**，但**全是固定音色**——丢掉克隆的雨桐音色，也丢撒娇。**这两样就是产品本身。**
- **独立 benchmark**（Artificial Analysis）：原生 S2S 推理远弱于 cascade（Moshi speech-reasoning 仅 4%）——换单模型会让女朋友"变笨"。
- **cascade 是对的架构。** 把 E2E hosted 当"延迟标尺"去 beat，不要当 stack。

**行业印证**：Replika / Nomi / Character.AI / Talkie / Pi **全是 cascade**（Talkie 甚至同你栈：MiniMax-Text + MiniMax Speech 2.8 HD）。E2E 领先者（Sesame/Moshi/Doubao）都要 10GB+。B 站 DIY 同款本地女友的事实标准栈是 **Silero VAD + FunASR/SenseVoice + GPT-SoVITS + 本地/HF LLM**——你的架构完全主流。

---

## 6. offline fallback（GPT-SoVITS）——能塞但未实测

调研说 GPT-SoVITS v2Pro streaming 能在 ~1.6GiB 塞进剩余预算（2400+981+1600=4981<6144）。审查戳穿三点：

- **1.6GiB 是社区数字 [vendor]**，不是你这台 3060 Laptop 量出来的；真实 fp16 常见 ~3GiB，那时 2400+981+3000=6381 > Windows 可用池(~5.6–5.9GiB)→**OOM**。
- "gsv-tts-lite 0.8GiB" **查不到出处**，删掉。
- 而且这只对 **offline fallback** 有意义（hot path 已 hosted，0 本地 VRAM）。

→ 当**假设**不是事实。要试就实测：Whisper 驻留 + GPT-SoVITS streaming，OBS+浏览器+avatar 全开时读 `nvidia-smi`。

---

## 7. Meta 建议：先建 baseline，再换任何东西

现在的 400ms / 1.9s **都不是 log artifact**——[`minimax.py`](server/app/tts/minimax.py) 没计时代码，[`session.py:232`](server/app/session.py#L232) 只 log end-to-end `time-to-first-audio`。

> 先用 [`doctor.ps1`](scripts/doctor.ps1)（`TTS_BACKEND=minimax`、`GLM_MODEL` 钉死）跑一次，把 MiniMax HD + GLM 的延迟**抓成 log**，再做任何 swap 的 before/after。所有 vendor 数字在进入预算前都必须过同一把尺。

---

## 8. 建议执行顺序

1. **建 baseline**：doctor.ps1 抓 minimax+glm 实测延迟（顺带确认 `.env` 的 `GLM_MODEL=glm-5.2` 是 live，对齐 code default 的 4.6）。
2. **撒娇 → 已有 CosyVoice**（zero new vendor，最高质量 ROI）。
3. **完成 Replicate migration**：跑 check_replicate、补 bench_replicate.ps1、实测。半价是真的。
4. **LLM host 优化**：先量 Z.ai TTFT，若真 ~1.9s 再换更快的 GLM host；failover 留 GLM 家族；A/B 撒娇措辞别回退。
5. **SenseVoice + GPT-SoVITS** 并行 A/B（只验证，不动默认）。
6. **LiveKit 最后**，先 cost 轻量 WebRTC+AEC 替代。

**终态目标**：first-audio ~2.6s → ~1.0–1.1s，加上**真正的撒娇**（在 clone 的雨桐音色上）和结构性打断——在这台 6GB 机器上，这就是"完美的实时女友通话"。

> 两个延迟别混：**barge-in 取消延迟** <300ms 可达（现有 VAD-cancel）；**turn-end 后首音频**有 ~700ms 因果下限（LLM TTFT ~500ms + TTS TTFB ~200ms）。

---

## 附：调研覆盖范围（供溯源）

- **E2E S2S**：Moshi、GLM-4-Voice、Qwen2.5-Omni、MiniMax realtime、Sesame CSM、Ultravox、gpt-4o-realtime、Step-Audio。
- **hosted 各段**：Deepgram Nova-3、AssemblyAI Universal-2、Cartesia Sonic、ElevenLabs v3、MiniMax Speech-02 Turbo、Step-Audio2、GLM-4.6/Gemini 2.5 Flash/Claude Haiku 4.5（TTFT）。
- **产品架构**：Pi、Sesame、Replika、Character.AI、Nomi/Kindroid、Talkie、Doubao、小冰。
- **本地低显存**：distil-large-v3、Canary-1B-Flash（无中文）、Paraformer、FireRedASR、SenseVoice、GPT-SoVITS、CosyVoice 0.5B、F5-TTS、Kokoro、MeloTTS。
- **Mandarin+情绪+克隆**：CosyVoice 2/v3、Qwen3-TTS、GPT-SoVITS v2/v3、Fish Speech、IndexTTS2、Spark-TTS、Step-TTS、Doubao TTS。

主要参考：Artificial Analysis speech-to-speech benchmark、Vapi Humanness Index、Together AI / MiniMax 官方、FunASR/SenseVoice、Kyutai Moshi 论文、各产品工程博客与开源仓。
