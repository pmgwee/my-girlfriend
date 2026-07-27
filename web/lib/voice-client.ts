/**
 * Browser side of the voice pipeline.
 *
 * Owns three things: the microphone graph, the WebSocket, and a scheduled
 * playback queue. The subtle part is echo: her voice coming out of the speakers
 * is picked up by the microphone, the server's VAD hears speech, and she
 * interrupts herself in a loop. Two defences, in order of preference:
 *
 *   1. Browser AEC (echoCancellation) cancels her voice from the mic feed. This
 *      is what lets you interrupt her mid-sentence, and it works well on laptop
 *      speakers in Chrome/Edge.
 *   2. Half-duplex mode gates the mic entirely while she speaks. Bulletproof,
 *      but you lose barge-in. Use it if AEC isn't holding up in your room.
 */

export type ServerEvent =
  | { type: "ready"; inputSampleRate: number; displayName: string; epigraph: string[]; avatar: Record<string, string>; locale: string }
  | { type: "user_transcript"; text: string }
  | { type: "assistant_delta"; text: string }
  | { type: "audio_start"; chunkId: number; sampleRate: number; text: string; durationMs: number }
  | { type: "audio_end"; chunkId: number }
  | { type: "interrupted" }
  | { type: "turn_end"; reason: string }
  | { type: "reset_ok" }
  | { type: "error"; message: string };

export type ClientState = "idle" | "connecting" | "listening" | "thinking" | "speaking";

export interface VoiceClientHandlers {
  onState: (state: ClientState) => void;
  onEvent: (event: ServerEvent) => void;
  /** Mic loudness, 0-1, ~30x/sec. Drives the input meter. */
  onMicLevel: (level: number) => void;
  /**
   * Her output loudness, 0-1, measured from audio that is actually playing.
   * Drives the orb and the avatar mouth signal.
   */
  onOutputLevel: (level: number) => void;
  onError: (message: string) => void;
}

const INPUT_SAMPLE_RATE = 16_000;
/** Scheduling cushion. Below ~80ms, chunk boundaries click on slower machines. */
const PLAYBACK_LEAD_SECONDS = 0.12;

export class VoiceClient {
  private socket: WebSocket | null = null;
  private micContext: AudioContext | null = null;
  private micStream: MediaStream | null = null;
  private micNode: AudioWorkletNode | null = null;

  private playbackContext: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private analyserBuffer: Float32Array | null = null;
  private levelFrame: number | null = null;
  private scheduled: AudioBufferSourceNode[] = [];
  private playheadTime = 0;

  /** Sample rate of the chunk currently arriving, from the last audio_start. */
  private incomingRate = 24_000;
  private state: ClientState = "idle";

  /** When true, the mic is gated while she speaks. See class docstring. */
  public halfDuplex = false;
  private sheIsSpeaking = false;
  private micEnabled = false;

  constructor(
    private readonly serverUrl: string,
    private readonly handlers: VoiceClientHandlers,
  ) {}

  // ------------------------------------------------------------------ lifecycle

  /**
   * Open a session.
   *
   * `microphone: false` connects for typing only. Text chat must not depend on
   * getUserMedia -- a denied permission, a missing device, or a non-localhost
   * origin would otherwise make the whole app unusable.
   */
  async connect({ microphone = true }: { microphone?: boolean } = {}): Promise<void> {
    this.setState("connecting");
    try {
      if (microphone) {
        await this.startMicrophone();
        this.micEnabled = true;
      }
      await this.openSocket();
      this.setState("listening");
      this.send({ type: "start" });
    } catch (error) {
      this.setState("idle");
      const message = error instanceof Error ? error.message : String(error);
      this.handlers.onError(
        message.includes("Permission") || message.includes("denied")
          ? "麥克風權限被拒絕。點網址列的鎖頭圖示允許麥克風，或者繼續打字聊。"
          : message,
      );
      await this.disconnect();
      throw error;
    }
  }

  async disconnect(): Promise<void> {
    this.stopPlayback();

    this.socket?.close();
    this.socket = null;

    this.micNode?.port.close();
    this.micNode?.disconnect();
    this.micNode = null;

    this.micStream?.getTracks().forEach((track) => track.stop());
    this.micStream = null;

    await this.micContext?.close().catch(() => {});
    this.micContext = null;
    this.micEnabled = false;

    await this.playbackContext?.close().catch(() => {});
    this.playbackContext = null;
    this.analyser = null;
    this.analyserBuffer = null;

    this.setState("idle");
  }

  // ------------------------------------------------------------------ microphone

  private async startMicrophone(): Promise<void> {
    this.micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        // All three matter. AEC is what makes barge-in possible at all; without
        // noise suppression the VAD triggers on fan noise and keyboard clicks.
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    // Asking for 16kHz directly lets the browser resample in native code, which
    // is both faster and higher quality than doing it in JS.
    this.micContext = new AudioContext({ sampleRate: INPUT_SAMPLE_RATE });
    if (this.micContext.state === "suspended") await this.micContext.resume();

    await this.micContext.audioWorklet.addModule("/worklet/mic-processor.js");

    const source = this.micContext.createMediaStreamSource(this.micStream);
    this.micNode = new AudioWorkletNode(this.micContext, "mic-processor");

    this.micNode.port.onmessage = (event: MessageEvent) => {
      const { frame, level } = event.data as { frame: Float32Array; level: number };
      this.handlers.onMicLevel(Math.min(1, level * 6));

      if (this.socket?.readyState !== WebSocket.OPEN) return;
      // Half-duplex: drop frames while she talks so her own voice can never
      // reach the server's VAD.
      if (this.halfDuplex && this.sheIsSpeaking) return;

      this.socket.send(floatToPcm16(frame));
    };

    source.connect(this.micNode);
    // The worklet emits no audio, but Chrome won't pull from a node that isn't
    // connected to the destination, so route it through a muted gain node.
    const silence = this.micContext.createGain();
    silence.gain.value = 0;
    this.micNode.connect(silence).connect(this.micContext.destination);
  }

  // ------------------------------------------------------------------ socket

  private openSocket(): Promise<void> {
    const url = this.serverUrl.replace(/^http/, "ws") + "/ws";
    this.socket = new WebSocket(url);
    this.socket.binaryType = "arraybuffer";

    return new Promise((resolve, reject) => {
      if (!this.socket) return reject(new Error("socket not created"));

      const timeout = setTimeout(
        () => reject(new Error(`連線逾時：${url}。後端啟動了嗎？(scripts/run_server.ps1)`)),
        8000,
      );

      this.socket.onopen = () => {
        clearTimeout(timeout);
        resolve();
      };
      this.socket.onerror = () => {
        clearTimeout(timeout);
        reject(new Error(`無法連線到後端 ${url}`));
      };
      this.socket.onclose = () => {
        this.stopPlayback();
        this.setState("idle");
      };
      this.socket.onmessage = (event) => this.handleMessage(event);
    });
  }

  private handleMessage(event: MessageEvent): void {
    if (event.data instanceof ArrayBuffer) {
      this.enqueueAudio(event.data);
      return;
    }

    const message = JSON.parse(event.data as string) as ServerEvent;

    switch (message.type) {
      case "audio_start":
        this.incomingRate = message.sampleRate;
        this.sheIsSpeaking = true;
        this.setState("speaking");
        break;
      case "user_transcript":
        this.setState("thinking");
        break;
      case "interrupted":
        this.stopPlayback();
        this.sheIsSpeaking = false;
        this.setState("listening");
        break;
      case "turn_end":
        // Let queued audio finish before flipping back to listening, otherwise
        // the orb drops to idle while she's still mid-word.
        this.afterPlayback(() => {
          this.sheIsSpeaking = false;
          this.setState("listening");
        });
        break;
      case "error":
        this.handlers.onError(message.message);
        break;
    }

    this.handlers.onEvent(message);
  }

  /** True once a session is open, whether or not the microphone is on. */
  get isConnected(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  get hasMicrophone(): boolean {
    return this.micEnabled;
  }

  /** Add voice to an existing text-only session. */
  async enableMicrophone(): Promise<void> {
    if (this.micEnabled) return;
    await this.startMicrophone();
    this.micEnabled = true;
  }

  send(payload: object): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(payload));
    }
  }

  sendText(text: string): void {
    this.stopPlayback();
    this.send({ type: "text", text });
    this.setState("thinking");
  }

  reset(): void {
    this.stopPlayback();
    this.send({ type: "reset" });
  }

  /** Manual interrupt, for the stop button. */
  interrupt(): void {
    this.stopPlayback();
    this.sheIsSpeaking = false;
    this.setState("listening");
  }

  // ------------------------------------------------------------------ playback

  private ensurePlaybackContext(): AudioContext {
    if (!this.playbackContext || this.playbackContext.state === "closed") {
      this.playbackContext = new AudioContext();
      this.playheadTime = 0;

      // Every source routes through this analyser, so the level we report is
      // measured from audio the user is hearing *right now*. Deriving it
      // server-side instead would run ahead of playback: TTS generates a
      // sentence in ~200ms that then takes ~1.6s to play, so the orb would
      // finish moving long before she finished talking.
      this.analyser = this.playbackContext.createAnalyser();
      this.analyser.fftSize = 512;
      this.analyser.smoothingTimeConstant = 0.4;
      this.analyserBuffer = new Float32Array(this.analyser.fftSize);
      this.analyser.connect(this.playbackContext.destination);
    }
    if (this.playbackContext.state === "suspended") void this.playbackContext.resume();
    return this.playbackContext;
  }

  private startLevelTracking(): void {
    if (this.levelFrame !== null) return;

    const tick = () => {
      const analyser = this.analyser;
      const buffer = this.analyserBuffer;
      if (!analyser || !buffer) {
        this.levelFrame = null;
        return;
      }

      analyser.getFloatTimeDomainData(buffer);
      let sumSquares = 0;
      for (let i = 0; i < buffer.length; i++) sumSquares += buffer[i] * buffer[i];
      const level = Math.sqrt(sumSquares / buffer.length);
      this.handlers.onOutputLevel(Math.min(1, level * 3.2));

      if (this.scheduled.length > 0) {
        this.levelFrame = requestAnimationFrame(tick);
      } else {
        this.levelFrame = null;
        this.handlers.onOutputLevel(0);
      }
    };

    this.levelFrame = requestAnimationFrame(tick);
  }

  private stopLevelTracking(): void {
    if (this.levelFrame !== null) {
      cancelAnimationFrame(this.levelFrame);
      this.levelFrame = null;
    }
    this.handlers.onOutputLevel(0);
  }

  private enqueueAudio(raw: ArrayBuffer): void {
    const context = this.ensurePlaybackContext();
    const pcm = new Int16Array(raw);
    if (pcm.length === 0) return;

    const buffer = context.createBuffer(1, pcm.length, this.incomingRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768;

    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.analyser ?? context.destination);

    // Chunks are scheduled back-to-back against a moving playhead. If we've
    // fallen behind (first chunk, or a stall), restart the playhead ahead of
    // `currentTime` so playback isn't scheduled in the past and dropped.
    const now = context.currentTime;
    if (this.playheadTime < now + 0.01) this.playheadTime = now + PLAYBACK_LEAD_SECONDS;

    source.start(this.playheadTime);
    this.playheadTime += buffer.duration;

    this.scheduled.push(source);
    source.onended = () => {
      this.scheduled = this.scheduled.filter((node) => node !== source);
    };

    this.startLevelTracking();
  }

  private stopPlayback(): void {
    for (const source of this.scheduled) {
      try {
        source.onended = null;
        source.stop();
      } catch {
        // Already finished; nothing to stop.
      }
    }
    this.scheduled = [];
    this.playheadTime = 0;
    this.stopLevelTracking();
  }

  /** Run `callback` once every queued chunk has finished playing. */
  private afterPlayback(callback: () => void): void {
    const context = this.playbackContext;
    if (!context || this.scheduled.length === 0) {
      callback();
      return;
    }
    const remainingMs = Math.max(0, (this.playheadTime - context.currentTime) * 1000);
    setTimeout(callback, remainingMs + 40);
  }

  // ------------------------------------------------------------------ state

  private setState(next: ClientState): void {
    if (this.state === next) return;
    this.state = next;
    this.handlers.onState(next);
  }
}

function floatToPcm16(samples: Float32Array): ArrayBuffer {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    out[i] = clamped * 32767;
  }
  return out.buffer;
}
