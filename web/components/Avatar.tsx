"use client";

import { useEffect, useRef, useState } from "react";

// --------------------------------------------------------------------- constants
// Module-level so they sit with the helpers that use them. Safe to reference
// from the component below: the module finishes evaluating before any instance
// renders or any effect fires.
const IDLE_PLAYBACK_RATE = 0.6; // calm the 24fps breathing toward "near-static"
const LIFT_GAIN = 0.004; // +0.4% max scale -- presence, not a VU meter
const STANDBY_MAX = 4; // seconds; cap on the random talking in-point

/**
 * Video avatar with a HARD CUT between idle and talking, motion-synced to speech.
 *
 * The previous version crossfaded opacity over 220ms -- the user rejected that
 * as "太假" / "回复UI 太假了": "说话时应该是人直接是talking.mp4, 而不是从idle
 * 用transition转换到talking.mp4". A real companion app cuts straight into the
 * talking clip when she speaks; there is no dissolve. The reference
 * (IMG_3556.mp4) is one continuous portrait -- near-static for ~3s, then the
 * mouth opens and motion ramps up *with the audio*. We mirror that here:
 *
 *   - The idle<->talking swap is a bare opacity toggle with NO transition. The
 *     cut is the point.
 *   - talking.mp4 is kept pre-seeked to a per-turn random "standby" in-point
 *     while idle (paused, hidden), so the first painted frame of the cut is a
 *     natural talking pose and motion begins in lockstep with audio_start --
 *     NOT at whatever random loop position a continuously-looping hidden video
 *     happened to be sitting at (the old bug, which desynced body/mouth motion
 *     from the TTS audio by up to the full loop length).
 *   - idle.mp4 is calmed (playbackRate ~0.6) so it reads as "still, but alive",
 *     closer to the reference's near-frozen hold and sharpening the cut
 *     contrast. (idle.mp4 still carries baked-in breathing; the reference IDLE
 *     is near-frozen. That is an asset limitation -- see "What is NOT solved".)
 *   - The old brightness pump that tracked RMS is gone -- a whole-frame
 *     brightness shift on every syllable read as a video-player effect, not as
 *     natural articulation. A sub-perceptual scale lift (cap +0.4%) is all that
 *     remains, just enough that she doesn't read as a frozen photograph when
 *     talking.mp4 lands on a still frame, and as a placeholder for the signal a
 *     real lip-sync backend would consume.
 *
 * What is NOT solved here, on purpose, so the next person knows:
 *   - talking.mp4's mouth is GENERIC loop motion, not phoneme-accurate lip-sync
 *     to the live TTS audio. Only a viseme/Wav2Lip/MuseTalk-style driven mouth
 *     fixes that.
 *   - talking.mp4 is ~10s. On replies longer than that it loops, and the loop
 *     seam (her pose snapping back to the clip's first frame) is visible
 *     mid-sentence. The reference has no seam because it is one continuous clip.
 *   - The hard cut is more revealing of any pose/lighting mismatch between
 *     idle.mp4 and talking.mp4 than the old crossfade was. For a seamless cut
 *     the two clips should share framing (ideally one continuous take).
 *
 * `mouthOpenness` is the smoothed RMS of her TTS output (0-1, ~30x/s). It drives
 * the sub-perceptual scale lift and is the signal a real lip-sync backend would
 * consume. See README 'Adding real lip-sync'.
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
  // A 404 on talking.mp4 degrades to idle-only rather than revealing a broken
  // element on the next cut. (An undefined talkingSrc is already covered by
  // hasTalkingLoop being false.)
  const [talkingBroken, setTalkingBroken] = useState(false);

  const hasTalkingLoop = Boolean(talkingSrc) && !talkingBroken;

  // One effect owns the full idle/talking setup AND every speaking transition.
  // It re-runs when a source changes OR when `speaking` toggles -- exactly the
  // moments the videos need (re)positioning. Re-running idle setup on a
  // speaking toggle is harmless: play() and playbackRate are idempotent.
  //
  // Autoplay is muted-only and Safari still needs an explicit play() call; the
  // sources are in the dep list because the persona (and therefore talkingSrc)
  // arrives from /config after mount, so the talking element does not exist on
  // the first pass and its standby seek would never fire with an empty dep list.
  useEffect(() => {
    const idle = idleRef.current;
    if (idle) {
      idle.playbackRate = IDLE_PLAYBACK_RATE;
      void idle.play().catch(() => {});
    }

    const talking = talkingRef.current;
    if (!talking || !hasTalkingLoop) return;

    if (speaking) {
      // talking was parked at a standby in-point (see the else branch), so the
      // first painted frame after the opacity cut below is already a natural
      // talking pose; play() resolves a few ms later and motion begins with the
      // audio. The brief "still, then she starts" onset matches the reference.
      talking.playbackRate = 1;
      void talking.play().catch(() => {});
    } else {
      // Pause where it is so it stops driving a hidden decode, then re-arm a
      // fresh random standby in-point for the next turn. Per-turn randomisation
      // breaks the "she always opens with the same nod" repeat. The seek is
      // initiated synchronously (currentTime is assigned before the first await
      // inside seekTo), so even a rapid false->true toggle lands play() on the
      // parked standby frame, never on a mid-seek artefact.
      talking.pause();
      void parkAtStandby(talking);
    }
  }, [idleSrc, talkingSrc, hasTalkingLoop, speaking]);

  const lift = speaking ? mouthOpenness : 0;

  const shared: React.CSSProperties = {
    position: "absolute",
    inset: 0,
    width: "100%",
    height: "100%",
    objectFit: "cover",
    objectPosition: "center 20%",
    // DELIBERATELY no `transition`. The whole point of this version is that the
    // idle->talking handover is a cut, not a 220ms dissolve. Any non-zero
    // opacity duration here brings back the "fake UI" crossfade the user
    // rejected. (The container below keeps its own transform transition for the
    // sub-perceptual lift, which is independent of the cut.)
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
        // Sub-perceptual presence lift (+0.4% max). The old `brightness` pump is
        // gone -- a whole-frame brightness shift on every syllable read as a VU
        // meter. This tiny scale stays below conscious perception; it just guards
        // against her reading as a frozen photo on a still talking frame.
        transform: `scale(${1 + lift * LIFT_GAIN})`,
        transition: "transform 120ms linear",
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
            // Belt and braces: some browsers reject play() before data arrives,
            // so retry on load AND re-assert the calm rate (Safari can ignore
            // playbackRate set before metadata is ready).
            onLoadedData={(event) => {
              const v = event.currentTarget;
              v.playbackRate = IDLE_PLAYBACK_RATE;
              void v.play().catch(() => {});
            }}
            // Hidden (opacity 0, no transition -> hard cut) only while talking
            // plays AND a talking clip exists.
            style={{ ...shared, opacity: speaking && hasTalkingLoop ? 0 : 1 }}
          />
          {hasTalkingLoop && (
            <video
              ref={talkingRef}
              src={talkingSrc}
              muted
              loop // loop stays ON so speech > 10s restarts seamlessly
              playsInline
              preload="auto"
              onError={() => setTalkingBroken(true)}
              // If the src arrives after the effect above ran (cold cache, late
              // /config), re-position here once data is decodeable. If she is
              // somehow already speaking at that moment, just play.
              onLoadedData={(event) => {
                const v = event.currentTarget;
                if (speaking) {
                  void v.play().catch(() => {});
                } else {
                  v.pause();
                  void parkAtStandby(v);
                }
              }}
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

// --------------------------------------------------------------------- helpers

/**
 * Pause + seek `v` to a fresh random in-point, ready for the next cut-in.
 *
 * Stale calls (e.g. a rapid speak/interrupt toggle) are harmless: they only
 * re-park a hidden, paused element, and the next rising edge of `speaking`
 * calls play() from whatever frame was parked -- always a valid talking frame,
 * never a mid-seek artefact visible to the user.
 */
async function parkAtStandby(v: HTMLVideoElement): Promise<void> {
  v.pause();
  await seekTo(v, pickInPoint(v.duration));
}

/**
 * Seek that always resolves. Safari occasionally drops the 'seeked' event for
 * tiny offsets (or fires it before a listener attaches); race it against a
 * short timeout so callers never hang waiting on a seek that already happened.
 */
function seekTo(v: HTMLVideoElement, t: number): Promise<void> {
  return new Promise((resolve) => {
    // Skip if metadata isn't loaded yet (duration NaN) or we're already there.
    if (Number.isNaN(v.duration) || Math.abs(v.currentTime - t) < 0.02) {
      return resolve();
    }
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      v.removeEventListener("seeked", finish);
      resolve();
    };
    v.addEventListener("seeked", finish);
    try {
      v.currentTime = t;
    } catch {
      finish();
    }
    setTimeout(finish, 200);
  });
}

/**
 * Random in-point within the first half of the clip (capped). Per-turn
 * randomisation prevents every reply from opening on the same mouth shape and
 * avoids landing on the end-of-loop seam. Falls back to 10s if duration isn't
 * known yet (metadata not loaded).
 *
 * NOTE: ideal would be a known mouth-closed "rest" frame that pose-matches
 * idle.mp4, so the cut is seamless. Without inspecting every frame we bias to
 * the first half (where a clip most often still sits near its rest pose) and
 * cap at STANDBY_MAX. If you find a better rest frame, replace this with a
 * constant (or a small set to randomly pick among).
 */
function pickInPoint(duration: number): number {
  const d = Number.isFinite(duration) && duration > 1 ? duration : 10;
  return Math.random() * Math.min(d * 0.5, STANDBY_MAX);
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
