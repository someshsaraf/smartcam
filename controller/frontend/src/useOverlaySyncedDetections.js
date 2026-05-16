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
 * Delay overlay detections so boxes align with HLS video (inference uses live RTSP).
 * Full delay applies when a person first appears; while they stay in frame, updates
 * debounce quickly so rapid WebSocket messages do not reset a multi-second timer.
 */
export function useOverlaySyncedDetections(faces, personCount, opts) {
  const { videoRef, hlsRef, baseDelayMs, enabled } = opts;
  const [displayed, setDisplayed] = useState({ faces: [], personCount: 0 });
  const latestRef = useRef({ faces: [], personCount: 0 });
  const wasShowingRef = useRef(false);
  const timerRef = useRef(null);

  latestRef.current = {
    faces: Array.isArray(faces) ? faces : [],
    personCount: typeof personCount === "number" ? personCount : 0,
  };

  useEffect(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }

    const latest = latestRef.current;
    const n = countPeople(latest.faces, latest.personCount);

    if (!enabled) {
      setDisplayed(latest);
      wasShowingRef.current = n > 0;
      return undefined;
    }

    let delayMs = 100;
    if (n > 0 && !wasShowingRef.current) {
      const lagMs = Math.min(estimatePlaybackLagMs(videoRef?.current, hlsRef?.current), 8000);
      delayMs = Math.max(baseDelayMs, Math.min(lagMs, 10000));
    } else if (n === 0) {
      delayMs = 100;
    }

    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      const snap = latestRef.current;
      setDisplayed({ faces: [...snap.faces], personCount: snap.personCount });
      wasShowingRef.current = countPeople(snap.faces, snap.personCount) > 0;
    }, delayMs);

    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [faces, personCount, baseDelayMs, enabled, videoRef, hlsRef]);

  return displayed;
}
