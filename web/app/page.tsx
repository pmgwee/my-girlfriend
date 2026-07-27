"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Avatar } from "@/components/Avatar";
import { Controls } from "@/components/Controls";
import { Subtitles, type SpokenLine } from "@/components/Subtitles";
import { TopBar, type Preferences } from "@/components/TopBar";
import { VoiceClient, type ClientState, type ServerEvent } from "@/lib/voice-client";

const SERVER_URL = process.env.NEXT_PUBLIC_SERVER_URL ?? "http://127.0.0.1:8765";

interface SceneConfig {
  displayName: string;
  epigraph: string[];
  avatar: { idle_video?: string; talking_video?: string; poster?: string };
}

export default function Page() {
  const clientRef = useRef<VoiceClient | null>(null);

  const [state, setState] = useState<ClientState>("idle");
  const [scene, setScene] = useState<SceneConfig | null>(null);
  const [line, setLine] = useState<SpokenLine | null>(null);
  const [userText, setUserText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<string | null>(null);
  const [micLevel, setMicLevel] = useState(0);
  const [outputLevel, setOutputLevel] = useState(0);
  const [draft, setDraft] = useState("");

  const [connected, setConnected] = useState(false);
  // Mirrors client.hasMicrophone into render state -- a ref field alone won't
  // re-render the controls when the mic turns on.
  const [micOn, setMicOn] = useState(false);

  const [preferences, setPreferences] = useState<Preferences>({
    halfDuplex: false,
    showSubtitles: true,
    // On by default. Voice needs a microphone permission grant; typing needs
    // nothing, so it should be the thing that always works out of the box.
    showTextInput: true,
  });

  // Fetch persona + backend status before connecting, so the scene renders (and
  // any misconfiguration is visible) without touching the microphone.
  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const [configResponse, healthResponse] = await Promise.all([
          fetch(`${SERVER_URL}/config`),
          fetch(`${SERVER_URL}/health`),
        ]);
        if (cancelled) return;

        setScene(await configResponse.json());

        const status = await healthResponse.json();
        setHealth(
          `STT  ${status.stt.model} @ ${status.stt.device}\n` +
            `TTS  ${status.tts.backend} @ ${status.tts.sampleRate}Hz\n` +
            `LLM  ${status.llm.reachable ? "已连接" : "未连接 ⚠"}`,
        );
        if (!status.llm.reachable) {
          setError("連不到大模型。檢查 .env 裡的 API key，或執行 scripts/run_llm.ps1。");
        }
      } catch {
        if (!cancelled) {
          setError(`連不上後端 ${SERVER_URL}。先執行 scripts/run_server.ps1。`);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  // Keep the client's duplex mode in sync without rebuilding the connection.
  useEffect(() => {
    if (clientRef.current) clientRef.current.halfDuplex = preferences.halfDuplex;
  }, [preferences.halfDuplex]);

  const handleEvent = useCallback((event: ServerEvent) => {
    switch (event.type) {
      case "user_transcript":
        setUserText(event.text);
        setLine(null);
        break;
      case "audio_start":
        setLine({
          chunkId: event.chunkId,
          text: event.text,
          durationMs: event.durationMs,
          startedAt: performance.now(),
        });
        break;
      case "interrupted":
        setLine(null);
        break;
      case "turn_end":
        if (event.reason === "no_speech") setUserText("");
        break;
    }
  }, []);

  /** Open a session if one isn't already open. Returns the live client. */
  const ensureConnected = useCallback(
    async (withMicrophone: boolean): Promise<VoiceClient | null> => {
      if (clientRef.current) {
        // Upgrading a text-only session to voice shouldn't drop the conversation.
        if (withMicrophone && !clientRef.current.hasMicrophone) {
          try {
            await clientRef.current.enableMicrophone();
            setMicOn(true);
          } catch (err) {
            setError(
              err instanceof Error && /Permission|denied|NotAllowed/i.test(err.message)
                ? "麦克风权限被拒绝。点地址栏的锁形图标允许麦克风，或者继续打字聊。"
                : err instanceof Error ? err.message : String(err),
            );
            return clientRef.current;
          }
        }
        return clientRef.current;
      }

      setError(null);
      const client = new VoiceClient(SERVER_URL, {
        onState: setState,
        onEvent: handleEvent,
        onMicLevel: setMicLevel,
        onOutputLevel: setOutputLevel,
        onError: setError,
      });
      client.halfDuplex = preferences.halfDuplex;
      clientRef.current = client;

      try {
        await client.connect({ microphone: withMicrophone });
        setConnected(true);
        setMicOn(client.hasMicrophone);
        return client;
      } catch {
        setMicOn(false);

        // Voice failed (usually a denied mic permission). Text still works, so
        // retry without the microphone rather than leaving a dead session.
        if (withMicrophone) {
          try {
            await client.connect({ microphone: false });
            setConnected(true);
            return client;
          } catch {
            // Backend is down too; connect() already surfaced the reason.
          }
        }

        clientRef.current = null;
        setConnected(false);
        return null;
      }
    },
    [handleEvent, preferences.halfDuplex],
  );

  const toggleCall = useCallback(async () => {
    if (clientRef.current?.hasMicrophone) {
      await clientRef.current.disconnect();
      clientRef.current = null;
      setConnected(false);
      setMicOn(false);
      setLine(null);
      setUserText("");
      return;
    }
    await ensureConnected(true);
  }, [ensureConnected]);

  // Connect text-only as soon as the box is visible, so the first thing you
  // type just sends instead of silently doing nothing.
  useEffect(() => {
    if (preferences.showTextInput && !clientRef.current) {
      void ensureConnected(false);
    }
  }, [preferences.showTextInput, ensureConnected]);

  useEffect(() => () => void clientRef.current?.disconnect(), []);

  const submitDraft = async (submitted: React.FormEvent) => {
    submitted.preventDefault();
    const text = draft.trim();
    if (!text) return;

    // Connect on demand rather than refusing to send. Covers the case where the
    // effect above hasn't finished, or an earlier connection dropped.
    const client = clientRef.current ?? (await ensureConnected(false));
    if (!client) return;

    setUserText(text);
    setLine(null);
    client.sendText(text);
    setDraft("");
  };

  const speaking = state === "speaking";

  return (
    <main
      style={{
        position: "fixed",
        inset: 0,
        background:
          "radial-gradient(140% 110% at 80% 40%, var(--bg-warm) 0%, var(--bg) 62%)",
      }}
    >
      <Avatar
        idleSrc={scene?.avatar?.idle_video ?? "/avatar/idle.mp4"}
        talkingSrc={scene?.avatar?.talking_video}
        poster={scene?.avatar?.poster}
        speaking={speaking}
        mouthOpenness={outputLevel}
      />

      {scene?.epigraph?.length ? (
        <div
          style={{
            position: "absolute",
            top: "17vh",
            left: "clamp(28px, 5vw, 76px)",
            fontSize: 13,
            lineHeight: 2.3,
            letterSpacing: "0.22em",
            color: "var(--text-faint)",
            userSelect: "none",
            zIndex: 10,
          }}
        >
          {scene.epigraph.map((row, index) => (
            <div key={index}>{row}</div>
          ))}
        </div>
      ) : null}

      <TopBar
        preferences={preferences}
        onChange={setPreferences}
        onReset={() => {
          clientRef.current?.reset();
          setLine(null);
          setUserText("");
        }}
        health={health}
      />

      <Controls
        state={state}
        outputLevel={outputLevel}
        micLevel={micLevel}
        micOn={micOn}
        onToggleCall={toggleCall}
        onInterrupt={() => clientRef.current?.interrupt()}
      />

      {preferences.showSubtitles && <Subtitles line={line} userText={userText} />}

      {preferences.showTextInput && (
        <form
          onSubmit={submitDraft}
          style={{
            position: "absolute",
            bottom: 26,
            left: "50%",
            transform: "translateX(-50%)",
            width: "min(560px, 78vw)",
            zIndex: 30,
          }}
        >
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={
              error ? "後端沒連上，先執行 run_server.ps1" : "打字跟她說…（Enter 送出）"
            }
            // Never gated on the microphone. Only a dead backend disables this.
            disabled={Boolean(error) && !connected}
            autoFocus
            style={{
              width: "100%",
              padding: "12px 18px",
              borderRadius: 999,
              background: "rgba(20,19,18,0.9)",
              border: "1px solid var(--border)",
              outline: "none",
              fontSize: 14,
              backdropFilter: "blur(16px)",
            }}
          />
        </form>
      )}

      {error && (
        <div
          style={{
            position: "absolute",
            bottom: 20,
            left: 20,
            maxWidth: 460,
            padding: "11px 15px",
            borderRadius: 9,
            background: "rgba(70,26,24,0.9)",
            border: "1px solid rgba(240,110,100,0.28)",
            color: "#f3b5ae",
            fontSize: 12.5,
            lineHeight: 1.65,
            zIndex: 50,
          }}
          onClick={() => setError(null)}
          role="alert"
        >
          {error}
        </div>
      )}
    </main>
  );
}
