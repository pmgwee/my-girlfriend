"use client";

import { useEffect, useRef } from "react";
import type { ClientState } from "@/lib/voice-client";

/**
 * The audio-reactive orb.
 *
 * Drawn on a canvas rather than with CSS because the rings need per-frame
 * amplitude response; CSS transitions lag the audio by enough to look dubbed.
 * The level is smoothed with an asymmetric follower -- fast attack so it snaps
 * to a syllable, slow release so it doesn't strobe between them.
 */
export function Orb({
  state,
  level,
  size = 132,
}: {
  state: ClientState;
  level: number;
  size?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Read through refs inside the animation loop so a re-render never restarts it.
  const levelRef = useRef(level);
  const stateRef = useRef(state);
  levelRef.current = level;
  stateRef.current = state;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.scale(dpr, dpr);

    let frame = 0;
    let smoothed = 0;
    let phase = 0;

    const render = () => {
      const target = levelRef.current;
      const currentState = stateRef.current;

      smoothed += (target - smoothed) * (target > smoothed ? 0.45 : 0.08);
      phase += 0.016;

      const centre = size / 2;
      const base = size * 0.26;
      // Idle states breathe on a slow sine so the orb never looks frozen.
      const idlePulse = currentState === "listening" ? Math.sin(phase * 1.6) * 0.035 : 0;
      const thinkingPulse = currentState === "thinking" ? Math.sin(phase * 5) * 0.06 : 0;
      const radius = base * (1 + smoothed * 0.42 + idlePulse + thinkingPulse);

      ctx.clearRect(0, 0, size, size);

      // Outer glow.
      const glow = ctx.createRadialGradient(centre, centre, radius * 0.3, centre, centre, size / 2);
      glow.addColorStop(0, `rgba(79, 214, 200, ${0.22 + smoothed * 0.3})`);
      glow.addColorStop(0.55, `rgba(79, 214, 200, ${0.07 + smoothed * 0.1})`);
      glow.addColorStop(1, "rgba(79, 214, 200, 0)");
      ctx.fillStyle = glow;
      ctx.fillRect(0, 0, size, size);

      // Concentric rings, each lagging the one inside it.
      for (let ring = 0; ring < 3; ring++) {
        const lag = ring * 0.14;
        const ringRadius = radius * (1 + ring * 0.3 + smoothed * lag);
        ctx.beginPath();
        ctx.arc(centre, centre, ringRadius, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(79, 214, 200, ${(0.5 - ring * 0.14) * (0.35 + smoothed * 0.65)})`;
        ctx.lineWidth = ring === 0 ? 1.6 : 1;
        ctx.stroke();
      }

      // Core.
      const core = ctx.createRadialGradient(centre, centre, 0, centre, centre, radius);
      core.addColorStop(0, `rgba(140, 245, 232, ${0.5 + smoothed * 0.4})`);
      core.addColorStop(0.7, `rgba(79, 214, 200, ${0.14 + smoothed * 0.2})`);
      core.addColorStop(1, "rgba(79, 214, 200, 0)");
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(centre, centre, radius, 0, Math.PI * 2);
      ctx.fill();

      // The five dots from the reference, shown while she's thinking.
      if (currentState === "thinking") {
        for (let dot = 0; dot < 5; dot++) {
          const offset = (dot - 2) * 9;
          const bounce = Math.sin(phase * 6 - dot * 0.55) * 2.4;
          ctx.beginPath();
          ctx.arc(centre + offset, centre + bounce, 1.7, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(180, 250, 240, ${0.35 + Math.sin(phase * 6 - dot * 0.55) * 0.3})`;
          ctx.fill();
        }
      }

      frame = requestAnimationFrame(render);
    };

    frame = requestAnimationFrame(render);
    return () => cancelAnimationFrame(frame);
  }, [size]);

  return (
    <canvas
      ref={canvasRef}
      style={{ width: size, height: size, display: "block" }}
      aria-hidden="true"
    />
  );
}
