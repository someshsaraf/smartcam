/**
 * Vite exposes only VITE_* vars (see ../.env and ../.env.local).
 * Restart `npm run dev` after editing env files.
 */

function trimUrl(v) {
  return String(v || "").replace(/\/$/, "");
}

/** @returns {string} */
export function resolveApiUrl() {
  const env = import.meta.env.VITE_API_URL;
  if (typeof env === "string" && env.trim()) {
    return trimUrl(env.trim());
  }
  if (typeof window !== "undefined" && window.location?.hostname) {
    const { hostname } = window.location;
    if (hostname && hostname !== "localhost" && hostname !== "127.0.0.1") {
      const proto = window.location.protocol === "https:" ? "https" : "http";
      return `${proto}://${hostname}:8000`;
    }
  }
  return "http://127.0.0.1:8000";
}

/** Same host as API, different port (MediaMTX / HLS on controller). */
function baseFromApi(port) {
  try {
    const raw = resolveApiUrl();
    const u = new URL(raw.startsWith("http") ? raw : `http://${raw}`);
    return `${u.protocol}//${u.hostname}:${port}`;
  } catch {
    return `http://127.0.0.1:${port}`;
  }
}

export const API = resolveApiUrl();

export const MEDIAMTX_BASE = trimUrl(
  import.meta.env.VITE_MEDIAMTX_BASE || baseFromApi(8889)
);

export const HLS_BASE = trimUrl(import.meta.env.VITE_HLS_BASE || baseFromApi(8888));

export const WS_RECORDING = trimUrl(
  import.meta.env.VITE_WS_RECORDING_URL ||
    `${API.replace(/^http/, "ws").replace(/^https/, "wss")}/ws/recording`
);

export const WS_DETECTIONS = trimUrl(
  import.meta.env.VITE_WS_DETECTIONS_URL ||
    `${API.replace(/^http/, "ws").replace(/^https/, "wss")}/ws/detections`
);

/** Base delay before drawing detection boxes over HLS (inference is on live RTSP). */
export function detectionOverlayDelayMs() {
  const raw = import.meta.env.VITE_DETECTION_OVERLAY_DELAY_MS;
  const n = parseInt(String(raw ?? "3000"), 10);
  return Number.isFinite(n) && n >= 0 ? n : 3000;
}

/** When false (default), draw boxes immediately from WebSocket (recommended). */
export function detectionOverlaySyncEnabled() {
  const v = String(import.meta.env.VITE_DETECTION_OVERLAY_SYNC ?? "0")
    .trim()
    .toLowerCase();
  return v === "1" || v === "true" || v === "yes" || v === "on";
}
