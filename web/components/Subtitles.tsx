"use client";

import { useEffect, useRef, useState } from "react";

export interface SpokenLine {
  chunkId: number;
  text: string;
  durationMs: number;
  startedAt: number;
}

/**
 * Karaoke-style subtitles.
 *
 * The highlight position is interpolated linearly across the chunk's audio
 * duration -- it is not forced alignment, so it drifts by a syllable or two on
 * long sentences. Getting it exact would mean per-phoneme timestamps out of the
 * TTS, which neither Kokoro nor qwentts.cpp expose. At the ~1-2s chunk lengths
 * the LLM stage produces, linear is close enough to read as synchronised.
 */
export function Subtitles({ line, userText }: { line: SpokenLine | null; userText: string }) {
  const [cursor, setCursor] = useState(0);
  const frameRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!line) {
      setCursor(0);
      return;
    }

    const characters = [...line.text].length;

    const tick = () => {
      const elapsed = performance.now() - line.startedAt;
      const ratio = line.durationMs > 0 ? Math.min(1, elapsed / line.durationMs) : 1;
      setCursor(ratio * characters);
      if (ratio < 1) frameRef.current = requestAnimationFrame(tick);
    };

    frameRef.current = requestAnimationFrame(tick);
    return () => {
      if (frameRef.current !== undefined) cancelAnimationFrame(frameRef.current);
    };
  }, [line]);

  if (!line && !userText) return null;

  return (
    <div
      style={{
        position: "absolute",
        bottom: "7vh",
        left: "50%",
        transform: "translateX(-50%)",
        width: "min(76vw, 880px)",
        textAlign: "center",
        pointerEvents: "none",
        zIndex: 20,
      }}
    >
      {userText && (
        <div
          style={{
            fontSize: 15,
            color: "var(--text-faint)",
            marginBottom: 14,
            letterSpacing: "0.02em",
          }}
        >
          {userText}
        </div>
      )}

      {line && (
        <div
          style={{
            fontSize: "clamp(22px, 2.5vw, 34px)",
            fontWeight: 600,
            letterSpacing: "0.04em",
            lineHeight: 1.5,
            // Subtitles sit over video; a shadow keeps them legible on light frames.
            textShadow: "0 2px 18px rgba(0,0,0,0.85), 0 1px 3px rgba(0,0,0,0.9)",
          }}
        >
          {[...line.text].map((character, index) => {
            const distance = index - cursor;
            // A two-character window around the playhead burns amber.
            const active = distance >= -2 && distance <= 0;
            return (
              <span
                key={index}
                style={{
                  color: active ? "var(--accent)" : "var(--text)",
                  opacity: distance > 0 ? 0.55 : 1,
                  transition: "color 140ms ease, opacity 140ms ease",
                }}
              >
                {character}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
