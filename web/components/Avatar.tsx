"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Video avatar with an idle/talking crossfade.
 *
 * Both loops are always playing and stacked; only opacity changes, so the cut
 * has no decode hitch. `mouthOpenness` is the smoothed RMS of her TTS output --
 * it drives a subtle brightness/scale lift while she talks, and it is the signal
 * a real lip-sync backend would consume. See README 'Adding real lip-sync'.
 */
export function Avatar({
  idleSrc,
  talkingSrc,
  poster,
  speaking,
  mouthOpenness,
}: {
  idleSrc: string;
  talkingSrc?: string;
  poster?: string;
  speaking: boolean;
  mouthOpenness: number;
}) {
  const idleRef = useRef<HTMLVideoElement>(null);
  const talkingRef = useRef<HTMLVideoElement>(null);
  const [missing, setMissing] = useState(false);

  // Autoplay is only permitted for muted video, and even then Safari needs an
  // explicit play() call.
  //
  // The sources must be in the dependency list: the persona (and therefore
  // talkingSrc) arrives from /config after mount, so the talking element does
  // not exist on the first pass. With an empty dep list its play() never fires
  // and the crossfade reveals a frozen first frame.
  useEffect(() => {
    for (const ref of [idleRef, talkingRef]) {
      ref.current?.play().catch(() => {});
    }
  }, [idleSrc, talkingSrc]);

  const hasTalkingLoop = Boolean(talkingSrc);
  const lift = speaking ? mouthOpenness : 0;

  const shared: React.CSSProperties = {
    position: "absolute",
    inset: 0,
    width: "100%",
    height: "100%",
    objectFit: "cover",
    objectPosition: "center 20%",
    transition: "opacity 220ms ease",
  };

  return (
    <div
      style={{
        position: "absolute",
        top: 0,
        right: 0,
        bottom: 0,
        width: "min(58vw, 900px)",
        overflow: "hidden",
        // Feathers the video's left edge into the background so it reads as one
        // scene rather than a pasted-in rectangle.
        maskImage:
          "linear-gradient(to right, transparent 0%, rgba(0,0,0,0.45) 18%, #000 42%)",
        WebkitMaskImage:
          "linear-gradient(to right, transparent 0%, rgba(0,0,0,0.45) 18%, #000 42%)",
        // Scale is deliberately tiny -- it should read as breathing, not zooming.
        transform: `scale(${1 + lift * 0.012})`,
        filter: `brightness(${1 + lift * 0.09})`,
        transition: "transform 90ms linear, filter 90ms linear",
      }}
    >
      {missing ? (
        <FallbackPortrait />
      ) : (
        <>
          <video
            ref={idleRef}
            src={idleSrc}
            poster={poster}
            muted
            loop
            playsInline
            preload="auto"
            onError={() => setMissing(true)}
            // Belt and braces: some browsers reject play() before the element
            // has data, so retry once it does.
            onLoadedData={(event) => void event.currentTarget.play().catch(() => {})}
            style={{ ...shared, opacity: speaking && hasTalkingLoop ? 0 : 1 }}
          />
          {hasTalkingLoop && (
            <video
              ref={talkingRef}
              src={talkingSrc}
              muted
              loop
              playsInline
              preload="auto"
              onLoadedData={(event) => void event.currentTarget.play().catch(() => {})}
              style={{ ...shared, opacity: speaking ? 1 : 0 }}
            />
          )}
        </>
      )}

      {/* Warms the shadows so she doesn't sit on flat black. */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          background:
            "radial-gradient(120% 90% at 70% 35%, rgba(255,180,120,0.07), transparent 60%)",
        }}
      />
    </div>
  );
}

/** Shown when the avatar videos haven't been added yet. */
function FallbackPortrait() {
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        display: "grid",
        placeItems: "center",
        background:
          "radial-gradient(60% 60% at 55% 40%, #241d1a 0%, #100d0c 55%, transparent 100%)",
      }}
    >
      <div style={{ textAlign: "center", color: "var(--text-faint)", lineHeight: 2, fontSize: 13 }}>
        <div style={{ fontSize: 40, marginBottom: 14, opacity: 0.5 }}>◍</div>
        把 idle.mp4 放到
        <br />
        <code style={{ color: "var(--text-dim)" }}>web/public/avatar/</code>
        <br />
        就能看到她了
      </div>
    </div>
  );
}
