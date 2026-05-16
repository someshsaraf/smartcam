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
 * Optional delay so boxes align with HLS (inference uses live RTSP).
 * Initial delay runs once per appearance; rapid WS updates must not reset it.
 */
export function useOverlaySyncedDetections(faces, personCount, opts) {
  const { videoRef, hlsRef, baseDelayMs, enabled } = opts;
  const [displayed, setDisplayed] = useState({ faces: [], personCount: 0 });
  const latestRef = useRef({ faces: [], personCount: 0 });
  const wasShowingRef = useRef(false);
  const initialTimerRef = useRef(null);
  const updateTimerRef = useRef(null);

  latestRef.current = {
    faces: Array.isArray(faces) ? faces : [],
    personCount: typeof personCount === "number" ? personCount : 0,
  };

  useEffect(() => {
    const latest = latestRef.current;
    const n = countPeople(latest.faces, latest.personCount);

    const clearUpdateTimer = () => {
      if (updateTimerRef.current) {
        clearTimeout(updateTimerRef.current);
        updateTimerRef.current = null;
      }
    };

    if (!enabled) {
      if (initialTimerRef.current) {
        clearTimeout(initialTimerRef.current);
        initialTimerRef.current = null;
      }
      clearUpdateTimer();
      setDisplayed(latest);
      wasShowingRef.current = n > 0;
      return undefined;
    }

    if (n === 0) {
      if (initialTimerRef.current) {
        clearTimeout(initialTimerRef.current);
        initialTimerRef.current = null;
      }
      clearUpdateTimer();
      wasShowingRef.current = false;
      updateTimerRef.current = setTimeout(() => {
        updateTimerRef.current = null;
        setDisplayed({ faces: [], personCount: 0 });
      }, 80);
      return () => clearUpdateTimer();
    }

    if (wasShowingRef.current) {
      clearUpdateTimer();
      updateTimerRef.current = setTimeout(() => {
        updateTimerRef.current = null;
        const snap = latestRef.current;
        setDisplayed({ faces: [...snap.faces], personCount: snap.personCount });
      }, 80);
      return () => clearUpdateTimer();
    }

    if (initialTimerRef.current != null) {
      return undefined;
    }

    const lagMs = Math.min(estimatePlaybackLagMs(videoRef?.current, hlsRef?.current), 8000);
    const delayMs = Math.max(baseDelayMs, Math.min(lagMs, 10000));

    initialTimerRef.current = setTimeout(() => {
      initialTimerRef.current = null;
      const snap = latestRef.current;
      setDisplayed({ faces: [...snap.faces], personCount: snap.personCount });
      wasShowingRef.current = countPeople(snap.faces, snap.personCount) > 0;
    }, delayMs);

    return undefined;
  }, [faces, personCount, baseDelayMs, enabled, videoRef, hlsRef]);

  useEffect(
    () => () => {
      if (initialTimerRef.current) {
        clearTimeout(initialTimerRef.current);
        initialTimerRef.current = null;
      }
      if (updateTimerRef.current) {
        clearTimeout(updateTimerRef.current);
        updateTimerRef.current = null;
      }
    },
    []
  );

  return displayed;
}
