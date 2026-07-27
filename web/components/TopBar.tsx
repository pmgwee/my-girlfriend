"use client";

import { useState } from "react";

export interface Preferences {
  /** Gate the mic while she speaks. Kills echo at the cost of barge-in. */
  halfDuplex: boolean;
  showSubtitles: boolean;
  showTextInput: boolean;
}

export function TopBar({
  preferences,
  onChange,
  onReset,
  health,
}: {
  preferences: Preferences;
  onChange: (next: Preferences) => void;
  onReset: () => void;
  health: string | null;
}) {
  const [open, setOpen] = useState(false);

  const toggle = (key: keyof Preferences) => () =>
    onChange({ ...preferences, [key]: !preferences[key] });

  return (
    <div style={{ position: "absolute", top: 16, right: 18, zIndex: 40 }}>
      <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
        <SmallButton
          label="文字輸入"
          active={preferences.showTextInput}
          onClick={toggle("showTextInput")}
        >
          <PenIcon />
        </SmallButton>
        <SmallButton
          label="字幕"
          active={preferences.showSubtitles}
          onClick={toggle("showSubtitles")}
        >
          <CaptionsIcon />
        </SmallButton>
        <SmallButton label="設定" active={open} onClick={() => setOpen(!open)}>
          <GearIcon />
        </SmallButton>
      </div>

      {open && (
        <div
          style={{
            marginTop: 10,
            width: 288,
            padding: 16,
            borderRadius: 12,
            background: "rgba(18,17,16,0.94)",
            border: "1px solid var(--border)",
            backdropFilter: "blur(20px)",
            fontSize: 13,
          }}
        >
          <Row
            label="半雙工模式"
            hint="她說話時關閉麥克風。徹底杜絕回音，但不能打斷她。"
            checked={preferences.halfDuplex}
            onChange={toggle("halfDuplex")}
          />

          <button
            onClick={onReset}
            style={{
              width: "100%",
              marginTop: 14,
              padding: "9px 0",
              borderRadius: 8,
              background: "var(--control)",
              border: "1px solid var(--border)",
              color: "var(--text-dim)",
              fontSize: 12.5,
            }}
          >
            清空對話記憶
          </button>

          {health && (
            <div
              style={{
                marginTop: 14,
                paddingTop: 12,
                borderTop: "1px solid var(--border)",
                fontSize: 11,
                lineHeight: 1.8,
                color: "var(--text-faint)",
                fontFamily: "ui-monospace, Consolas, monospace",
                whiteSpace: "pre-wrap",
              }}
            >
              {health}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Row({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: () => void;
}) {
  return (
    <label style={{ display: "block", cursor: "pointer" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span>{label}</span>
        <input
          type="checkbox"
          checked={checked}
          onChange={onChange}
          style={{ accentColor: "var(--orb)", width: 15, height: 15, cursor: "pointer" }}
        />
      </div>
      <div style={{ marginTop: 5, fontSize: 11.5, lineHeight: 1.6, color: "var(--text-faint)" }}>
        {hint}
      </div>
    </label>
  );
}

function SmallButton({
  children,
  label,
  active,
  onClick,
}: {
  children: React.ReactNode;
  label: string;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      title={label}
      aria-label={label}
      aria-pressed={active}
      style={{
        width: 27,
        height: 27,
        display: "grid",
        placeItems: "center",
        borderRadius: 7,
        color: active ? "var(--text)" : "var(--text-faint)",
        background: active ? "var(--control-hover)" : "transparent",
        transition: "color 150ms ease, background 150ms ease",
      }}
    >
      {children}
    </button>
  );
}

function PenIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
    </svg>
  );
}

function CaptionsIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <rect x="2.5" y="5" width="19" height="14" rx="2.5" />
      <path d="M9 10.5a2.5 2.5 0 1 0 0 3M16.5 10.5a2.5 2.5 0 1 0 0 3" />
    </svg>
  );
}

function GearIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 9 19.4a1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.6 9a1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1Z" />
    </svg>
  );
}
