import { useEffect, useState } from "react";

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

/**
 * Delay overlay detections so boxes align with HLS video (inference uses live RTSP).
 * @param {Array|undefined} faces
 * @param {number|undefined} personCount
 * @param {{ videoRef: import('react').RefObject<HTMLVideoElement>, hlsRef: import('react').RefObject<unknown>, baseDelayMs: number, enabled: boolean }} opts
 */
export function useOverlaySyncedDetections(faces, personCount, opts) {
  const { videoRef, hlsRef, baseDelayMs, enabled } = opts;
  const [displayed, setDisplayed] = useState({ faces: [], personCount: 0 });

  useEffect(() => {
    const nextFaces = Array.isArray(faces) ? faces : [];
    const nextCount = typeof personCount === "number" ? personCount : 0;

    if (!enabled) {
      setDisplayed({ faces: nextFaces, personCount: nextCount });
      return undefined;
    }

    const lagMs = estimatePlaybackLagMs(videoRef?.current, hlsRef?.current);
    const delayMs = Math.max(0, Math.max(baseDelayMs, lagMs));
    const releaseAt = performance.now() + delayMs;
    let raf = 0;

    const tick = () => {
      if (performance.now() >= releaseAt) {
        setDisplayed({ faces: nextFaces, personCount: nextCount });
        return;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    return () => {
      if (raf) cancelAnimationFrame(raf);
    };
  }, [faces, personCount, baseDelayMs, enabled, videoRef, hlsRef]);

  return displayed;
}
