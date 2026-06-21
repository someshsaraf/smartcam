/**
 * Vite exposes only VITE_* vars (see ../.env and ../.env.local).
 * Restart `npm run dev` after editing env files.
 */

function trimUrl(v) {
  return String(v || "").replace(/\/$/, "");
}

function parseUrl(raw) {
  const s = trimUrl(raw);
  if (!s) return null;
  try {
    return new URL(s.startsWith("http") ? s : `http://${s}`);
  } catch {
    return null;
  }
}

/** LAN UI opened at :5173 should talk to API on the same host (not a stale .env IP). */
function preferPageHostname(envUrl) {
  if (typeof window === "undefined" || !window.location?.hostname) {
    return null;
  }
  const pageHost = window.location.hostname;
  if (!pageHost || pageHost === "localhost" || pageHost === "127.0.0.1") {
    return null;
  }
  const parsed = parseUrl(envUrl);
  if (parsed && parsed.hostname === pageHost) {
    return null;
  }
  const proto = window.location.protocol === "https:" ? "https" : "http";
  return `${proto}://${pageHost}:8000`;
}

/** @returns {string} */
export function resolveApiUrl() {
  const env = import.meta.env.VITE_API_URL;
  const fromPage = typeof env === "string" && env.trim() ? preferPageHostname(env) : null;
  if (fromPage) {
    if (import.meta.env.DEV) {
      console.warn(
        "[SmartCam] VITE_API_URL host differs from this page — using",
        fromPage,
        "(update controller/frontend/.env and restart Vite)",
      );
    }
    return trimUrl(fromPage);
  }
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

/**
 * When true, live tiles show RTSP/HLS debug from `GET /cameras/{id}/stream_health` on top of video.
 * Default off — use the Debug panel (bottom-right) or enable with `VITE_SHOW_STREAM_DEBUG=1`.
 * Errors still show stream debug inside the failure overlay.
 */
export function showStreamDebugUrls() {
  const show = String(import.meta.env.VITE_SHOW_STREAM_DEBUG || "")
    .trim()
    .toLowerCase();
  if (show === "1" || show === "true" || show === "yes" || show === "on") {
    return true;
  }
  const hide = String(import.meta.env.VITE_HIDE_STREAM_DEBUG || "")
    .trim()
    .toLowerCase();
  if (hide === "1" || hide === "true" || hide === "yes" || hide === "on") {
    return false;
  }
  return false;
}

export const MEDIAMTX_BASE = baseFromApi(8889);

export const HLS_BASE = baseFromApi(8888);

function wsFromApi(path) {
  const base = API.replace(/^http/, "ws").replace(/^https/, "wss");
  return trimUrl(`${base}${path.startsWith("/") ? path : `/${path}`}`);
}

export const WS_RECORDING = wsFromApi("/ws/recording");

export const WS_DETECTIONS = wsFromApi("/ws/detections");

/**
 * Extra UI delay (ms) on top of backend inference delay.
 * Set to 0 when SMARTCAM_DETECTION_OVERLAY_DELAY_MS is tuned on the Pi.
 */
export function detectionOverlayDelayMs() {
  const raw = import.meta.env.VITE_DETECTION_OVERLAY_DELAY_MS;
  const n = parseInt(String(raw ?? "0"), 10);
  return Number.isFinite(n) && n >= 0 ? n : 0;
}

/** Extra frontend-only HLS sync (backend already delays inference frames). */
export function detectionOverlaySyncEnabled() {
  const v = String(import.meta.env.VITE_DETECTION_OVERLAY_SYNC ?? "1")
    .trim()
    .toLowerCase();
  return v === "1" || v === "true" || v === "yes" || v === "on";
}

/** True on iPhone/iPad (incl. iPadOS desktop UA). */
export function isIosDevice() {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent || "";
  return (
    /iPad|iPhone|iPod/i.test(ua) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
  );
}

/** Clips and live HLS: native Safari HLS on iOS; hls.js elsewhere. */
export function preferNativeHlsPlayback() {
  if (typeof navigator === "undefined") return false;
  const android = /Android/i.test(navigator.userAgent || "");
  const narrow = typeof window !== "undefined" && window.innerWidth < 1024;
  return isIosDevice() || (android && narrow);
}

/**
 * WebRTC iframe reader (MediaMTX :8889). Disabled on iOS/narrow mobile — Safari shows a
 * blank iframe while detection overlays still render on assumed 16:9.
 * Also disabled on touch-first devices (coarse pointer + no hover) — Android tablets often
 * get a broken embed even when HLS works.
 * Set VITE_LIVE_WEBRTC=0 to force HLS everywhere.
 */
export function preferWebRtcLive() {
  const v = String(import.meta.env.VITE_LIVE_WEBRTC ?? "1")
    .trim()
    .toLowerCase();
  const enabled = v === "1" || v === "true" || v === "yes" || v === "on";
  if (!enabled) return false;
  if (preferNativeHlsPlayback()) return false;
  if (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(pointer: coarse) and (hover: none)").matches
  ) {
    return false;
  }
  return true;
}
