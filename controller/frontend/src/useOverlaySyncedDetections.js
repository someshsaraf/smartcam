import { useEffect, useRef, useState } from "react";

/** Estimate how far behind the live edge the <video> element is playing (ms). */
export function estimatePlaybackLagMs(video, hls) {
  if (!video || video.readyState < 2) return 0;
  if (hls && Number.isFinite(hls.latency) && hls.latency > 0) {
    return Math.round(hls.latency * 1000);
  }
  try {
    const b = video.buffered;
    if (b.length > 0) {
      const lagSec = b.end(b.length - 1) - video.currentTime;
      if (Number.isFinite(lagSec) && lagSec > 0) {
        return Math.round(lagSec * 1000);
      }
    }
  } catch {
    /* ignore */
  }
  return 0;
}

function countPeople(faces, personCount) {
  if (typeof personCount === "number") return personCount;
  if (!Array.isArray(faces)) return 0;
  return faces.filter((d) => String(d?.label || "").toLowerCase() === "person").length;
}

/**
 * Optional extra UI delay on top of backend inference delay.
 * Uses a release queue so rapid WebSocket updates never reset a multi-second timer.
 */
export function useOverlaySyncedDetections(faces, personCount, opts) {
  const { videoRef, hlsRef, baseDelayMs, enabled } = opts;
  const [displayed, setDisplayed] = useState({ faces: [], personCount: 0 });
  const latestRef = useRef({ faces: [], personCount: 0 });
  const queueRef = useRef([]);

  latestRef.current = {
    faces: Array.isArray(faces) ? faces : [],
    personCount: typeof personCount === "number" ? personCount : 0,
  };

  useEffect(() => {
    const latest = latestRef.current;
    if (!enabled) {
      queueRef.current = [];
      setDisplayed(latest);
      return undefined;
    }

    const lagMs = Math.min(estimatePlaybackLagMs(videoRef?.current, hlsRef?.current), 12000);
    const delayMs = Math.max(0, baseDelayMs + Math.round(lagMs * 0.35));

    queueRef.current.push({
      faces: [...latest.faces],
      personCount: latest.personCount,
      releaseAt: performance.now() + delayMs,
    });
    if (queueRef.current.length > 80) {
      queueRef.current = queueRef.current.slice(-80);
    }

    return undefined;
  }, [faces, personCount, baseDelayMs, enabled, videoRef, hlsRef]);

  useEffect(() => {
    if (!enabled) return undefined;

    let raf = 0;
    const tick = () => {
      const now = performance.now();
      const q = queueRef.current;
      let chosen = null;
      for (let i = q.length - 1; i >= 0; i -= 1) {
        if (q[i].releaseAt <= now) {
          chosen = q[i];
          break;
        }
      }
      if (chosen) {
        setDisplayed({ faces: [...chosen.faces], personCount: chosen.personCount });
        queueRef.current = q.filter((e) => e.releaseAt > chosen.releaseAt);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [enabled]);

  return displayed;
}
