"use client";

import type { ClientState } from "@/lib/voice-client";
import { Orb } from "./Orb";

export function Controls({
  state,
  outputLevel,
  micLevel,
  micOn,
  onToggleCall,
  onInterrupt,
}: {
  state: ClientState;
  outputLevel: number;
  micLevel: number;
  /** Whether voice input is live. Distinct from being connected -- a text-only
   *  session is connected but has no microphone. */
  micOn: boolean;
  onToggleCall: () => void;
  onInterrupt: () => void;
}) {
  const connected = state !== "idle" && state !== "connecting";

  const label =
    state === "connecting" ? "连接中…"
    : state === "thinking" ? "想想怎么说…"
    : state === "speaking" ? ""
    : micOn ? "在听着呢"
    : "点麦克风开始语音";

  // The orb tracks her voice while she talks and your mic while she listens, so
  // it's always showing whoever currently holds the floor.
  const orbLevel = state === "speaking" ? outputLevel : micOn ? micLevel * 0.55 : 0;

  return (
    <div
      style={{
        position: "absolute",
        left: "clamp(60px, 26vw, 420px)",
        top: "50%",
        transform: "translate(-50%, -50%)",
        display: "flex",
        alignItems: "center",
        gap: 30,
        zIndex: 15,
      }}
    >
      <IconButton
        label={micOn ? "关闭麦克风" : "开始语音"}
        active={micOn}
        onClick={onToggleCall}
        disabled={state === "connecting"}
      >
        {micOn ? <PhoneOffIcon /> : <MicIcon />}
      </IconButton>

      <div style={{ position: "relative", display: "grid", placeItems: "center" }}>
        <Orb state={state} level={orbLevel} />
        {label && (
          <div
            style={{
              position: "absolute",
              bottom: -30,
              whiteSpace: "nowrap",
              fontSize: 12,
              letterSpacing: "0.12em",
              color: "var(--text-faint)",
            }}
          >
            {label}
          </div>
        )}
      </div>

      <IconButton
        label="打断她"
        onClick={onInterrupt}
        disabled={state !== "speaking"}
      >
        <StopIcon />
      </IconButton>
    </div>
  );
}

function IconButton({
  children,
  label,
  onClick,
  disabled,
  active,
}: {
  children: React.ReactNode;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      style={{
        width: 38,
        height: 38,
        display: "grid",
        placeItems: "center",
        borderRadius: 10,
        background: active ? "rgba(240, 90, 80, 0.14)" : "var(--control)",
        border: `1px solid ${active ? "rgba(240,90,80,0.3)" : "var(--border)"}`,
        color: active ? "#f0736a" : "var(--text-dim)",
        transition: "background 160ms ease, color 160ms ease, border-color 160ms ease",
      }}
      onMouseEnter={(event) => {
        if (!disabled) event.currentTarget.style.background = "var(--control-hover)";
      }}
      onMouseLeave={(event) => {
        event.currentTarget.style.background = active
          ? "rgba(240, 90, 80, 0.14)"
          : "var(--control)";
      }}
    >
      {children}
    </button>
  );
}

function MicIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v4" />
    </svg>
  );
}

function PhoneOffIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <path d="M3 3l18 18M10.7 5.1A13 13 0 0 1 21 9v3.5a1.5 1.5 0 0 1-1.7 1.5l-2.6-.4a1.5 1.5 0 0 1-1.2-1.1l-.4-1.6M6.2 8.3A13 13 0 0 0 3 9v3.5A1.5 1.5 0 0 0 4.7 14l2.6-.4a1.5 1.5 0 0 0 1.2-1.1l.4-1.6" />
    </svg>
  );
}

function StopIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
      <rect x="5" y="5" width="14" height="14" rx="2.5" />
    </svg>
  );
}
