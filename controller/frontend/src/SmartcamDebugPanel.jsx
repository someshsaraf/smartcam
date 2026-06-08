import { useCallback, useEffect, useMemo, useState } from "react";
import {
  API,
  HLS_BASE,
  MEDIAMTX_BASE,
  WS_DETECTIONS,
  WS_RECORDING,
  preferNativeHlsPlayback,
  preferWebRtcLive,
  showStreamDebugUrls,
} from "./envConfig";

/** Same merge as App `load` / `cameraRtspUrl` for rows from GET /cameras. */
function cameraRtspUrlFromObj(cam) {
  if (!cam || typeof cam !== "object") return "";
  return String(cam.url || cam.main_stream || cam.mainStream || "").trim();
}

function normalizeCamerasFromApiBody(data) {
  const raw = Array.isArray(data) ? data : data?.cameras ?? data?.items ?? [];
  const list = Array.isArray(raw) ? raw : [];
  return list
    .filter((c) => c && typeof c === "object")
    .map((c) => {
      const url = cameraRtspUrlFromObj(c);
      return url ? { ...c, url } : { ...c };
    });
}

function envLine(label, value) {
  return (
    <div className="grid grid-cols-[7.5rem_1fr] gap-x-2 gap-y-0.5 text-[11px] leading-snug">
      <span className="text-gray-500 shrink-0">{label}</span>
      <span className="text-gray-200 break-all">{value === undefined || value === "" ? "—" : String(value)}</span>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <section className="rounded-lg border border-white/[0.08] bg-black/30 p-2.5 space-y-1.5">
      <h3 className="text-[10px] font-bold uppercase tracking-wider text-indigo-300/90">{title}</h3>
      <div className="space-y-1">{children}</div>
    </section>
  );
}

/**
 * Full-screen overlay debug drawer: client URLs, session, WS, per-camera stream_health.
 * Fetches GET /cameras when opened so the list is not empty when App state is briefly stale.
 */
export function SmartcamDebugPanel({
  open,
  onClose,
  cameras = [],
  camerasLoadError = "",
  mainTab = "",
  activeCameraId = null,
  detectionWsOpen = false,
  detectionSystem = null,
}) {
  const [resolvedCameras, setResolvedCameras] = useState([]);
  const [camerasFetchLoading, setCamerasFetchLoading] = useState(false);
  const [camerasResolveError, setCamerasResolveError] = useState("");
  const [healthById, setHealthById] = useState({});
  const [healthLoading, setHealthLoading] = useState(false);
  const [healthUpdatedAt, setHealthUpdatedAt] = useState(null);

  const displayCameras = useMemo(() => {
    if (resolvedCameras.length > 0) return resolvedCameras;
    return Array.isArray(cameras) ? cameras : [];
  }, [resolvedCameras, cameras]);

  useEffect(() => {
    if (!open) {
      setResolvedCameras([]);
      setCamerasResolveError("");
      setCamerasFetchLoading(false);
      return;
    }
    let cancelled = false;
    setCamerasFetchLoading(true);
    setCamerasResolveError("");
    (async () => {
      try {
        const r = await fetch(`${API}/cameras`);
        const data = r.ok ? await r.json().catch(() => null) : null;
        if (cancelled) return;
        if (!r.ok) {
          setCamerasResolveError(`GET /cameras failed: HTTP ${r.status}`);
          setResolvedCameras([]);
          setCamerasFetchLoading(false);
          return;
        }
        setResolvedCameras(normalizeCamerasFromApiBody(data));
        setCamerasFetchLoading(false);
      } catch (e) {
        if (!cancelled) {
          setCamerasResolveError(e instanceof Error ? e.message : String(e));
          setResolvedCameras([]);
          setCamerasFetchLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  const refreshHealth = useCallback(async () => {
    const list = Array.isArray(displayCameras) ? displayCameras : [];
    if (list.length === 0) {
      setHealthById({});
      setHealthUpdatedAt(new Date().toISOString());
      return;
    }
    setHealthLoading(true);
    const next = {};
    for (const c of list) {
      if (!c || c.id === undefined || c.id === null) continue;
      const id = Number(c.id);
      if (!Number.isFinite(id)) continue;
      try {
        const r = await fetch(
          `${API}/cameras/${id}/stream_health?probe_rtsp=false&include_secrets=true`,
        );
        next[id] = r.ok ? await r.json() : { _error: `HTTP ${r.status}` };
      } catch (e) {
        next[id] = { _error: e instanceof Error ? e.message : String(e) };
      }
    }
    setHealthById(next);
    setHealthUpdatedAt(new Date().toISOString());
    setHealthLoading(false);
  }, [displayCameras]);

  useEffect(() => {
    if (!open) return undefined;
    refreshHealth();
    const t = setInterval(refreshHealth, 8000);
    return () => clearInterval(t);
  }, [open, refreshHealth]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const vite = import.meta.env;
  const ds = detectionSystem && typeof detectionSystem === "object" ? detectionSystem : null;

  return (
    <div
      className="fixed inset-0 z-[100] flex items-end sm:items-stretch sm:justify-end pointer-events-auto"
      role="dialog"
      aria-modal="true"
      aria-labelledby="smartcam-debug-title"
    >
      <button
        type="button"
        className="absolute inset-0 bg-black/60 backdrop-blur-[2px]"
        aria-label="Close debug panel"
        onClick={onClose}
      />
      <div
        className="relative z-[101] flex flex-col w-full sm:max-w-md sm:h-full max-h-[88dvh] sm:max-h-none rounded-t-2xl sm:rounded-none border border-white/10 bg-[#0c1018] shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="shrink-0 flex items-center justify-between gap-2 px-3 py-2.5 border-b border-white/10 bg-[#0a0e14]">
          <h2 id="smartcam-debug-title" className="text-sm font-semibold text-white tracking-tight">
            SmartCam debug
          </h2>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => refreshHealth()}
              disabled={healthLoading}
              className="text-[11px] px-2 py-1 rounded-md border border-white/15 text-gray-200 hover:bg-white/10 disabled:opacity-40"
            >
              {healthLoading ? "…" : "Refresh health"}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="text-[11px] px-2 py-1 rounded-md bg-indigo-600 text-white hover:bg-indigo-500"
            >
              Close
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto overscroll-contain p-3 space-y-3 text-left">
          <Section title="Build & client">
            {envLine("MODE", vite?.MODE)}
            {envLine("VITE_API_URL (raw)", vite?.VITE_API_URL)}
            {envLine("resolved API", API)}
            {envLine("HLS_BASE", HLS_BASE)}
            {envLine("MEDIAMTX_BASE", MEDIAMTX_BASE)}
            {envLine("WS_RECORDING", WS_RECORDING)}
            {envLine("WS_DETECTIONS", WS_DETECTIONS)}
            {envLine("VITE_LIVE_WEBRTC", vite?.VITE_LIVE_WEBRTC)}
            {envLine("preferWebRtcLive()", preferWebRtcLive() ? "true" : "false")}
            {envLine("preferNativeHls()", preferNativeHlsPlayback() ? "true" : "false")}
            {envLine("showStreamDebugUrls()", showStreamDebugUrls() ? "true" : "false")}
          </Section>

          <Section title="Session">
            {envLine("mainTab", mainTab)}
            {envLine("activeCameraId", activeCameraId ?? "—")}
            {envLine("cameras (App prop)", cameras?.length ?? 0)}
            {envLine("cameras (GET /cameras)", resolvedCameras.length)}
            {camerasFetchLoading ? envLine("camera list", "fetching…") : null}
            {camerasLoadError ? (
              <p className="text-[11px] text-amber-300 break-all whitespace-pre-wrap">{camerasLoadError}</p>
            ) : null}
            {camerasResolveError ? (
              <p className="text-[11px] text-rose-300 break-all whitespace-pre-wrap">{camerasResolveError}</p>
            ) : null}
          </Section>

          <Section title="Detections WebSocket">
            {envLine("connected", detectionWsOpen ? "yes" : "no")}
            {ds ? (
              <>
                {envLine("backend", ds.backend)}
                {envLine("person_pipeline", ds.person_pipeline)}
                {envLine("inference_delay_ms", ds.inference_delay_ms)}
                {envLine("person_detect_enabled", ds.person_detect_enabled)}
                {envLine("opencv_ssd_ready", ds.opencv_ssd_ready)}
                {envLine("hailo_ready", ds.hailo_ready)}
                {envLine("hailo_error", ds.hailo_error)}
              </>
            ) : (
              envLine("hello payload", "not received yet")
            )}
          </Section>

          <Section title="Per-camera stream_health">
            <p className="text-[10px] text-amber-200/95 leading-snug rounded border border-amber-500/35 bg-amber-950/25 px-2 py-1.5">
              This panel requests <span className="font-mono">include_secrets=true</span> so each
              camera&apos;s full <strong className="text-amber-100">RTSP URL including the password</strong> is
              shown when stored on the controller. Use only on a trusted LAN; do not expose port 8000 to the
              internet. The list is loaded from <span className="font-mono">GET {API}/cameras</span> when you open
              this panel.
            </p>
            {healthUpdatedAt ? (
              <p className="text-[10px] text-gray-500 mb-1">Updated {healthUpdatedAt}</p>
            ) : null}
            {camerasFetchLoading && resolvedCameras.length === 0 ? (
              <p className="text-gray-400 text-[11px]">Loading camera list from API…</p>
            ) : null}
            {displayCameras.length === 0 && !camerasFetchLoading ? (
              <div className="text-gray-500 text-[11px] space-y-1">
                <p>No cameras returned from the controller.</p>
                <p>
                  Open <strong>Devices</strong> and add a camera, or set <span className="font-mono">VITE_API_URL</span>{" "}
                  in <span className="font-mono">.env</span> to the Pi where the API runs (currently{" "}
                  <span className="font-mono text-gray-300">{API}</span>).
                </p>
              </div>
            ) : (
              <ul className="space-y-2">
                {displayCameras.map((c) => {
                  const id = Number(c?.id);
                  if (!Number.isFinite(id)) return null;
                  const h = healthById[id];
                  const clientRtsp = cameraRtspUrlFromObj(c);
                  const showUrl = h?.rtsp_url || clientRtsp;
                  return (
                    <li key={id} className="rounded border border-white/[0.07] bg-black/25 p-2">
                      <p className="text-[11px] font-semibold text-gray-100 mb-1">
                        {c?.name || `Camera ${id}`}{" "}
                        <span className="text-gray-500 font-normal">id={id}</span>
                      </p>
                      {!h ? (
                        <span className="text-gray-500 text-[10px]">Loading stream_health…</span>
                      ) : h._error ? (
                        <span className="text-rose-300 text-[10px] break-all">{h._error}</span>
                      ) : (
                        <div className="space-y-2">
                          {h._secrets_note || h._debug_note ? (
                            <p className="text-[9px] text-gray-400 leading-snug">
                              {h._secrets_note ? `${h._secrets_note} ` : ""}
                              {h._debug_note || ""}
                            </p>
                          ) : null}
                          <div className="rounded border border-amber-500/40 bg-amber-950/30 p-2">
                            <div className="flex items-center justify-between gap-2 mb-1">
                              <span className="text-[10px] font-bold uppercase tracking-wide text-amber-200">
                                RTSP (full URL, password visible)
                              </span>
                              {showUrl ? (
                                <button
                                  type="button"
                                  className="shrink-0 text-[10px] px-2 py-0.5 rounded bg-amber-600/80 text-white hover:bg-amber-500"
                                  onClick={() => {
                                    const t = h?.rtsp_url || clientRtsp;
                                    if (t && navigator.clipboard?.writeText) {
                                      navigator.clipboard.writeText(t).catch(() => {});
                                    }
                                  }}
                                >
                                  Copy
                                </button>
                              ) : null}
                            </div>
                            {h.rtsp_url ? (
                              <pre className="text-[11px] leading-relaxed text-amber-50 whitespace-pre-wrap break-all font-mono">
                                {h.rtsp_url}
                              </pre>
                            ) : clientRtsp ? (
                              <>
                                <p className="text-[9px] text-gray-400 mb-1">
                                  <span className="font-mono">stream_health</span> did not include{" "}
                                  <span className="font-mono">rtsp_url</span> (e.g.{" "}
                                  <span className="font-mono">SMARTCAM_DENY_STREAM_HEALTH_SECRETS=1</span> on server).
                                  Showing URL from <span className="font-mono">GET /cameras</span> (same as UI):
                                </p>
                                <pre className="text-[11px] leading-relaxed text-amber-50 whitespace-pre-wrap break-all font-mono">
                                  {clientRtsp}
                                </pre>
                              </>
                            ) : (
                              <p className="text-[10px] text-gray-500">
                                No RTSP URL for this camera (add <span className="font-mono">url</span> /{" "}
                                <span className="font-mono">main_stream</span>).
                              </p>
                            )}
                            {showUrl && String(showUrl).includes("CHANGE_ME") ? (
                              <p className="text-[10px] text-amber-300 mt-2 leading-snug border-t border-amber-500/30 pt-2">
                                <strong>Placeholder:</strong> URL still contains sample{" "}
                                <span className="font-mono">CHANGE_ME</span>. Use{" "}
                                <strong>Manage → camera settings</strong> or <strong>Find</strong> with your ONVIF
                                password.
                              </p>
                            ) : null}
                          </div>
                          <dl className="space-y-0.5 text-[10px] text-gray-300">
                            <div className="grid grid-cols-[6.5rem_1fr] gap-1">
                              <dt className="text-gray-500">mediamtx_path</dt>
                              <dd className="break-all">{h.mediamtx_path ?? "—"}</dd>
                            </div>
                            <div className="grid grid-cols-[6.5rem_1fr] gap-1">
                              <dt className="text-gray-500">rtsp_has_userinfo</dt>
                              <dd>{h.rtsp_has_userinfo ? "true" : "false"}</dd>
                            </div>
                            <div className="grid grid-cols-[6.5rem_1fr] gap-1">
                              <dt className="text-gray-500">rtsp (redacted)</dt>
                              <dd className="break-all text-gray-400">{h.rtsp_url_redacted || "—"}</dd>
                            </div>
                            <div className="grid grid-cols-[6.5rem_1fr] gap-1">
                              <dt className="text-gray-500">HLS via API</dt>
                              <dd className="break-all text-cyan-200/90">{h.hls_api_playlist_url || "—"}</dd>
                            </div>
                            <div className="grid grid-cols-[6.5rem_1fr] gap-1">
                              <dt className="text-gray-500">HLS MediaMTX</dt>
                              <dd className="break-all text-cyan-200/90">{h.hls_mediamtx_manifest_url || "—"}</dd>
                            </div>
                            {Array.isArray(h.warnings) && h.warnings.length > 0 ? (
                              <div className="mt-1 pt-1 border-t border-amber-500/20">
                                <p className="text-amber-300/95 text-[10px] font-medium">Warnings</p>
                                {h.warnings.map((w, i) => (
                                  <p key={i} className="text-amber-200/80 text-[10px] mt-0.5 break-words">
                                    {w}
                                  </p>
                                ))}
                              </div>
                            ) : null}
                          </dl>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </Section>
        </div>
      </div>
    </div>
  );
}
