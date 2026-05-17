import { useCallback, useEffect, useRef, useState } from "react";
import Hls from "hls.js";
import {
  API,
  detectionOverlayDelayMs,
  detectionOverlaySyncEnabled,
  HLS_BASE,
  MEDIAMTX_BASE,
  WS_DETECTIONS,
  WS_RECORDING,
} from "./envConfig";
import { useOverlaySyncedDetections } from "./useOverlaySyncedDetections";

const MAX_LIVE_TILES = 6;

function streamPathForCamera(cam) {
  if (cam.mediamtx_path && String(cam.mediamtx_path).trim()) {
    return String(cam.mediamtx_path).trim().replace(/^\//, "");
  }
  const url = cam.url || "";
  return url.split("/").pop() || "camera";
}

function streamUrlForCamera(cam) {
  const path = streamPathForCamera(cam).replace(/\/+$/, "");
  // MediaMTX embedded reader pages typically expect a trailing slash on the path.
  return `${MEDIAMTX_BASE}/${path}/`;
}

function hlsPlaylistUrlForCamera(cam, viaApi = true) {
  const path = streamPathForCamera(cam).replace(/\/+$/, "");
  if (viaApi) {
    return `${API}/cameras/${cam.id}/hls/index.m3u8`;
  }
  return `${HLS_BASE}/${path}/index.m3u8`;
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString();
}

function recordingFileUrl(camId, name) {
  return `${API}/recordings/${camId}/files/${encodeURIComponent(name)}`;
}

/** First-frame preview from clip metadata (same-origin API). */
function RecordingThumbnail({ src, className = "" }) {
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const seek = () => {
      const d = el.duration;
      const t = Number.isFinite(d) && d > 0 ? Math.min(0.5, d * 0.05) : 0.1;
      try {
        el.currentTime = t;
      } catch {
        /* seek before buffer */
      }
    };
    el.addEventListener("loadedmetadata", seek);
    if (el.readyState >= 1) seek();
    return () => el.removeEventListener("loadedmetadata", seek);
  }, [src]);
  return (
    <video
      ref={ref}
      src={src}
      preload="metadata"
      muted
      playsInline
      className={className}
      aria-hidden
    />
  );
}

/** Bento grid cell placement for layout D (index 0 = hero). */
function bentoTileClass(index, total) {
  if (total <= 1) return "col-span-3 row-span-2";
  if (index === 0) return "col-start-1 row-start-1 row-span-2";
  if (index === 1) return "col-start-2 row-start-1";
  if (index === 2) return "col-start-3 row-start-1";
  if (index === 3) return "col-start-2 row-start-2";
  if (index === 4) return "col-start-3 row-start-2";
  if (index === 5) return "col-span-3 row-start-3";
  return "";
}

function RecordingsTimeline({
  recordings,
  loading,
  hasCameras,
  onRefresh,
  onDelete,
}) {
  const [playing, setPlaying] = useState(null);

  const playUrl = playing ? recordingFileUrl(playing.camId, playing.name) : "";

  return (
    <>
      <section
        className="shrink-0 border-t border-gray-800 bg-[#070c16] flex flex-col max-h-[200px] min-h-[120px]"
        aria-label="Recordings timeline"
      >
        <div className="px-3 py-2 border-b border-gray-800 flex items-center justify-between gap-2 shrink-0">
          <h2 className="text-xs font-semibold text-gray-200">Recent clips</h2>
          <div className="flex items-center gap-2">
            {loading ? <span className="text-[10px] text-gray-500">Loading…</span> : null}
            <button
              type="button"
              disabled={loading || !hasCameras}
              onClick={onRefresh}
              className="text-[10px] px-2 py-1 rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Refresh
            </button>
          </div>
        </div>
        <div className="flex-1 overflow-x-auto overflow-y-hidden min-h-0 px-3 py-2">
          {!hasCameras ? (
            <p className="text-xs text-gray-500 py-2">Add a camera to see recordings.</p>
          ) : recordings.length === 0 && !loading ? (
            <p className="text-xs text-gray-500 py-2">No clips yet.</p>
          ) : (
            <ul className="flex gap-3 pb-1">
              {recordings.map((r) => {
                const url = recordingFileUrl(r.camId, r.name);
                const active = playing?.camId === r.camId && playing?.name === r.name;
                return (
                  <li
                    key={`${r.camId}-${r.name}`}
                    className={`shrink-0 w-[11rem] rounded-lg border p-1.5 flex flex-col gap-1 ${
                      active
                        ? "border-indigo-500 bg-[#111827]"
                        : "border-gray-800 bg-[#111827]/60 hover:border-gray-600"
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => setPlaying(r)}
                      className="w-full aspect-video rounded overflow-hidden bg-black border border-gray-700 hover:border-gray-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                      title="Play clip"
                    >
                      <RecordingThumbnail
                        src={url}
                        className="w-full h-full object-cover pointer-events-none"
                      />
                    </button>
                    <div className="min-w-0 text-[10px] px-0.5">
                      <p className="font-medium text-gray-200 truncate" title={r.camName}>
                        {r.camName}
                      </p>
                      <p className="text-gray-500 truncate">{formatTime(r.mtime)}</p>
                      <p className="text-gray-600">{formatBytes(r.size)}</p>
                    </div>
                    <div className="flex gap-2 text-[10px] px-0.5">
                      <button
                        type="button"
                        onClick={() => setPlaying(r)}
                        className="text-blue-400 hover:underline"
                      >
                        Play
                      </button>
                      <a href={url} download={r.name} className="text-gray-400 hover:underline">
                        Save
                      </a>
                      <button
                        type="button"
                        onClick={() => {
                          if (active) setPlaying(null);
                          onDelete(r.camId, r.name);
                        }}
                        className="text-red-400 hover:underline"
                      >
                        Delete
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </section>

      {playing ? (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 p-4"
          role="dialog"
          aria-modal="true"
          onClick={() => setPlaying(null)}
        >
          <div
            className="max-w-4xl w-full bg-[#111827] rounded-xl border border-gray-700 p-3 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex justify-between items-start gap-2 mb-2 text-xs">
              <div className="min-w-0">
                <p className="font-medium text-gray-200 truncate">{playing.camName}</p>
                <p className="font-mono text-gray-500 truncate">{playing.name}</p>
              </div>
              <button
                type="button"
                onClick={() => setPlaying(null)}
                className="text-gray-400 hover:text-white px-2 shrink-0"
                aria-label="Close"
              >
                ✕
              </button>
            </div>
            <video key={playUrl} className="w-full rounded bg-black max-h-[75vh]" src={playUrl} controls autoPlay />
          </div>
        </div>
      ) : null}
    </>
  );
}

function edgeDiscoveryKey(e) {
  return `${e.edge_base_url || ""}|${e.mqtt_camera_id || ""}`;
}

/** YOLOv8n / Hailo person boxes use label "person". */
function countPersonDetections(detections) {
  if (!Array.isArray(detections)) return 0;
  return detections.filter((d) => String(d?.label || "").toLowerCase() === "person").length;
}

/** Always-on debug line for YOLOv8n person test (not tied to recording mode). */
function formatPersonDebugLine(info, wsOpen) {
  if (!wsOpen) return "Person test: WS disconnected";
  if (!info?.ts && !info?.error) return "Person test: waiting for frames…";
  if (info.status === "buffering") {
    const age = typeof info.bufferAgeMs === "number" ? info.bufferAgeMs : 0;
    const need =
      typeof info.inferenceDelayMs === "number" ? info.inferenceDelayMs : 4500;
    return `Person test: syncing video (${age} / ${need} ms)…`;
  }
  if (info.error) return `Person test: — (${info.error})`;
  if (info.hailoError) return `Person test: — (Hailo: ${info.hailoError})`;
  const n =
    typeof info.personCount === "number" ? info.personCount : countPersonDetections(info.faces);
  if (n > 0) return `Person test: YES — ${n} person(s)`;
  const fc = typeof info.faceCount === "number" ? info.faceCount : (info.faces?.length ?? 0);
  if (fc > 0) return `Person test: no (${fc} face box(es), no person label)`;
  return "Person test: no person in frame";
}

/** Detection confidence for overlay label (backend sends 0–1). */
function formatDetectionLabel(det) {
  const n = Number(det?.score);
  const score = Number.isFinite(n)
    ? n <= 1
      ? `${Math.round(n * 1000) / 10}%`
      : n.toFixed(2)
    : "";
  const label = det?.label ? String(det.label) : "face";
  return score ? `${label} ${score}` : label;
}

function LiveTile({
  cam,
  recording,
  recordingMode,
  manualRecording,
  onManualToggle,
  faces,
  personCount,
  detectionInfo,
  detectionWsOpen,
  overlayDelayMs,
  layout = "default",
}) {
  const wrapRef = useRef(null);
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const hlsRef = useRef(null);
  const overlaySync = detectionOverlaySyncEnabled();
  const baseOverlayDelay =
    typeof overlayDelayMs === "number" && overlayDelayMs >= 0
      ? overlayDelayMs
      : detectionOverlayDelayMs();
  const [useIframeFallback, setUseIframeFallback] = useState(false);
  const synced = useOverlaySyncedDetections(faces, personCount, {
    videoRef,
    hlsRef,
    baseDelayMs: baseOverlayDelay,
    enabled: overlaySync && !useIframeFallback,
  });
  const rawFaces = Array.isArray(faces) ? faces : [];
  const drawFaces = overlaySync && !useIframeFallback ? synced.faces : rawFaces;
  const drawPersonCount =
    overlaySync && !useIframeFallback
      ? synced.personCount
      : typeof personCount === "number"
        ? personCount
        : countPersonDetections(rawFaces);
  const persons =
    typeof drawPersonCount === "number" ? drawPersonCount : countPersonDetections(drawFaces);
  const personDebugLine = formatPersonDebugLine(
    { ...detectionInfo, faces: rawFaces, personCount: detectionInfo?.personCount ?? drawPersonCount },
    detectionWsOpen
  );
  const personDebugPositive = persons > 0;
  const [scale, setScale] = useState(1);
  const [streamError, setStreamError] = useState("");
  const [edgeHint, setEdgeHint] = useState("");
  const streamUrl = streamUrlForCamera(cam);
  const hlsProxyUrl = hlsPlaylistUrlForCamera(cam, true);
  const hlsDirectUrl = hlsPlaylistUrlForCamera(cam, false);
  const [hlsUrl, setHlsUrl] = useState(hlsProxyUrl);
  const showManual =
    recordingMode === "off" && cam.edge_base_url && typeof onManualToggle === "function";

  const zoomIn = useCallback(() => setScale((s) => Math.min(4, s * 1.15)), []);
  const zoomOut = useCallback(() => setScale((s) => Math.max(0.5, s / 1.15)), []);

  const goFs = () => {
    const el = wrapRef.current;
    if (!el?.requestFullscreen) return;
    el.requestFullscreen().catch(() => {});
  };

  useEffect(() => {
    setUseIframeFallback(false);
    setStreamError("");
    setEdgeHint("");
    setHlsUrl(hlsProxyUrl);
    setScale(1);
  }, [cam.id, cam.url, hlsProxyUrl]);

  /** Wheel over <iframe> does not bubble; capture on the tile so zoom never hits the reader page. */
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const onWheel = (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.deltaY < 0) zoomIn();
      else zoomOut();
    };
    el.addEventListener("wheel", onWheel, { passive: false, capture: true });
    return () => el.removeEventListener("wheel", onWheel, { capture: true });
  }, [zoomIn, zoomOut]);

  useEffect(() => {
    const edge = cam.edge_base_url;
    if (!edge) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${edge}/health`);
        if (!res.ok || cancelled) return;
        const h = await res.json();
        if (cancelled) return;
        if (!h.publisher_running) {
          setEdgeHint(
            "Edge RTSP publisher is not running — check SURVEILLANCE_PI_CAMERA=1 and mediamtx on the Pi 4."
          );
        } else {
          setEdgeHint("");
        }
      } catch {
        if (!cancelled) {
          setEdgeHint(`Cannot reach edge API at ${edge}`);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cam.edge_base_url]);

  useEffect(() => {
    if (useIframeFallback) return undefined;
    const video = videoRef.current;
    if (!video) return undefined;
    let hls;
    const onVideoError = () => {
      setStreamError("");
      setUseIframeFallback(true);
    };
    const onPlaying = () => {
      setStreamError("");
      setEdgeHint("");
    };
    video.addEventListener("error", onVideoError);
    video.addEventListener("playing", onPlaying);
    if (Hls.isSupported()) {
      let triedDirectHls = false;
      // Use Count *or* Duration live-sync keys, not both (hls.js rejects mixed config).
      // MediaMTX serves mpegts HLS; overlay delay syncs boxes to playback.
      hls = new Hls({
        lowLatencyMode: false,
        maxLiveSyncPlaybackRate: 1.5,
        enableWorker: true,
        manifestLoadingTimeOut: 20000,
        manifestLoadingMaxRetry: 6,
        fragLoadingTimeOut: 20000,
        fragLoadingMaxRetry: 6,
      });
      hlsRef.current = hls;
      hls.loadSource(hlsUrl);
      hls.attachMedia(video);
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (!data.fatal) return;
        if (
          data.type === Hls.ErrorTypes.NETWORK_ERROR &&
          !triedDirectHls &&
          hlsUrl === hlsProxyUrl &&
          hlsDirectUrl !== hlsProxyUrl
        ) {
          triedDirectHls = true;
          setHlsUrl(hlsDirectUrl);
          hls.loadSource(hlsDirectUrl);
          return;
        }
        hls?.destroy();
        setStreamError("");
        setUseIframeFallback(true);
        fetch(`${API}/cameras/${cam.id}/stream_health?probe_rtsp=false`)
          .then((r) => (r.ok ? r.json() : null))
          .then((h) => {
            if (!h) return;
            if (Array.isArray(h.summary) && h.summary.length) {
              setEdgeHint(h.summary.join(" "));
              return;
            }
            setEdgeHint("");
          })
          .catch(() => {});
      });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = hlsUrl;
    } else {
      setStreamError("HLS not supported in this browser — using WebRTC reader.");
      setUseIframeFallback(true);
      return undefined;
    }
    return () => {
      video.removeEventListener("error", onVideoError);
      video.removeEventListener("playing", onPlaying);
      hlsRef.current = null;
      if (hls) hls.destroy();
      video.removeAttribute("src");
      video.load();
    };
  }, [cam.id, cam.url, hlsUrl, useIframeFallback]);

  useEffect(() => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || useIframeFallback) return undefined;
    const ctx = canvas.getContext("2d");
    if (!ctx) return undefined;

    const paint = () => {
      const cw = video.clientWidth;
      const ch = video.clientHeight;
      if (cw < 2 || ch < 2) return;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(cw * dpr);
      canvas.height = Math.round(ch * dpr);
      canvas.style.width = `${cw}px`;
      canvas.style.height = `${ch}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cw, ch);
      const vw = video.videoWidth;
      const vh = video.videoHeight;
      if (!vw || !vh) return;
      const scaleContain = Math.min(cw / vw, ch / vh);
      const dw = vw * scaleContain;
      const dh = vh * scaleContain;
      const ox = (cw - dw) / 2;
      const oy = (ch - dh) / 2;
      ctx.strokeStyle = "rgba(34, 197, 94, 0.95)";
      ctx.lineWidth = 2;
      ctx.font = "11px ui-monospace, system-ui, sans-serif";
      const faceList = (Array.isArray(drawFaces) ? drawFaces : []).filter(
        (d) => String(d?.label || "").toLowerCase() === "person",
      );
      for (const f of faceList) {
        const x = ox + Number(f.x) * vw * scaleContain;
        const y = oy + Number(f.y) * vh * scaleContain;
        const w = Number(f.w) * vw * scaleContain;
        const h = Number(f.h) * vh * scaleContain;
        ctx.strokeStyle = "rgba(59, 130, 246, 0.95)";
        ctx.strokeRect(x, y, w, h);
        const label = formatDetectionLabel(f);
        if (!label) continue;
        const padX = 4;
        const padY = 2;
        const textY = Math.max(12, y - 4);
        const metrics = ctx.measureText(label);
        const boxW = metrics.width + padX * 2;
        const boxH = 14;
        const labelX = Math.min(Math.max(0, x), cw - boxW);
        const labelTop = Math.max(0, textY - boxH + 2);
        ctx.fillStyle = "rgba(0, 0, 0, 0.72)";
        ctx.fillRect(labelX, labelTop, boxW, boxH);
        ctx.fillStyle = "rgba(96, 165, 250, 1)";
        ctx.fillText(label, labelX + padX, labelTop + boxH - padY - 2);
      }
    };

    paint();
    video.addEventListener("loadeddata", paint);
    video.addEventListener("timeupdate", paint);
    const ro = new ResizeObserver(paint);
    ro.observe(video);
    const overlayTick = setInterval(paint, 200);
    return () => {
      video.removeEventListener("loadeddata", paint);
      video.removeEventListener("timeupdate", paint);
      ro.disconnect();
      clearInterval(overlayTick);
    };
  }, [drawFaces, useIframeFallback, cam.id]);

  const isHero = layout === "hero";

  return (
    <div className="bg-[#111827] rounded-xl p-2 flex flex-col min-h-0 h-full">
      <div className="flex justify-between items-center text-xs mb-1 gap-2">
        <span className="truncate font-medium">{cam.name}</span>
        <div className="flex items-center gap-1.5 shrink-0">
          <span
            className={
              edgeHint ? "text-red-400" : useIframeFallback ? "text-amber-400" : "text-green-400"
            }
          >
            {edgeHint ? "NO SIGNAL" : useIframeFallback ? "WEBRTC" : "LIVE"}
          </span>
        </div>
      </div>
      <p
        className={`text-[10px] mb-1 font-mono leading-snug ${
          personDebugPositive
            ? "text-blue-300 font-semibold"
            : personDebugLine.includes("—") || personDebugLine.includes("waiting") || personDebugLine.includes("disconnected")
              ? "text-amber-400/90"
              : "text-gray-500"
        }`}
        title="YOLOv8n person detection debug (independent of recording mode)"
      >
        {personDebugLine}
      </p>
      {edgeHint && (
        <p className="text-[10px] text-amber-400/95 mb-1 leading-snug">
          {edgeHint}
          {cam.url ? (
            <>
              {" "}
              RTSP: <span className="font-mono text-gray-300">{cam.url}</span>
            </>
          ) : null}
        </p>
      )}
      <div
        ref={wrapRef}
        className={`relative flex-1 rounded-lg bg-black overflow-hidden touch-none ${
          isHero ? "min-h-[200px] max-h-none" : "min-h-[100px] max-h-[200px]"
        }`}
      >
        {recording ? (
          <div
            className="absolute top-2 right-2 z-20 h-5 w-5 rounded-full bg-red-600 shadow-lg ring-2 ring-white/90"
            title="Recording"
            aria-label="Recording"
          />
        ) : null}
        {useIframeFallback && countPersonDetections(rawFaces) > 0 ? (
          <div className="absolute top-10 left-2 right-2 z-20 rounded-md bg-amber-900/90 px-2 py-1 text-[10px] text-amber-100">
            Use HLS (LIVE badge) to see detection boxes on video.
          </div>
        ) : null}
        <div
          className="w-full h-full origin-center transition-transform duration-75"
          style={{ transform: `scale(${scale})` }}
        >
          {useIframeFallback ? (
            <div className="relative w-full h-full min-h-[140px]">
              <iframe
                title={cam.name}
                src={streamUrl}
                className="w-full h-full min-h-[140px] border-0 bg-black pointer-events-none"
                allow="autoplay; fullscreen"
                sandbox="allow-scripts allow-same-origin allow-autoplay allow-fullscreen"
              />
              <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-2 text-center text-[10px] text-gray-400/90">
                {edgeHint
                  ? "WebRTC reader — see message above."
                  : "WebRTC reader (no face overlay). HLS unavailable for detection boxes."}
              </div>
            </div>
          ) : (
            <div className="relative w-full h-full min-h-[140px] bg-black">
              <video
                ref={videoRef}
                className="absolute inset-0 w-full h-full object-contain bg-black"
                playsInline
                muted
                autoPlay
              />
              <canvas
                ref={canvasRef}
                className="absolute inset-0 z-10 w-full h-full pointer-events-none"
                aria-hidden="true"
              />
            </div>
          )}
        </div>
        <div className="absolute bottom-1 left-1 right-1 flex flex-wrap gap-1 z-10 pointer-events-auto items-center">
          {showManual ? (
            <button
              type="button"
              onClick={onManualToggle}
              className={`rounded px-2 py-0.5 text-[10px] font-medium shrink-0 ${
                manualRecording
                  ? "bg-red-600 text-white ring-1 ring-white/80 hover:bg-red-500"
                  : "bg-gray-700 text-gray-100 hover:bg-gray-600"
              }`}
              title={manualRecording ? "Stop manual recording" : "Start manual recording"}
            >
              {manualRecording ? "■ Stop" : "● Rec"}
            </button>
          ) : null}
          <button
            type="button"
            onClick={zoomOut}
            className="rounded bg-black/70 px-2 py-0.5 text-[10px] text-white hover:bg-black/90"
          >
            −
          </button>
          <button
            type="button"
            onClick={zoomIn}
            className="rounded bg-black/70 px-2 py-0.5 text-[10px] text-white hover:bg-black/90"
          >
            +
          </button>
          <button
            type="button"
            onClick={() => setScale(1)}
            className="rounded bg-black/70 px-2 py-0.5 text-[10px] text-white hover:bg-black/90"
          >
            1×
          </button>
          <button
            type="button"
            onClick={goFs}
            className="rounded bg-black/70 px-2 py-0.5 text-[10px] text-white hover:bg-black/90"
          >
            Fullscreen
          </button>
        </div>
      </div>
      <p
        className="text-[10px] text-gray-400 mt-1 font-mono break-all leading-snug"
        title={useIframeFallback ? "WebRTC reader (no overlay)" : "HLS playlist for video + face overlay"}
      >
        {useIframeFallback ? streamUrl : hlsUrl}
      </p>
    </div>
  );
}

export default function App() {
  const [cams, setCams] = useState([]);
  const [discoveredEdges, setDiscoveredEdges] = useState([]);
  const [recordingById, setRecordingById] = useState({});
  const [settingsCam, setSettingsCam] = useState(null);
  /** Flat list: one entry per file, newest first (all cameras). */
  const [allRecordings, setAllRecordings] = useState([]);
  const [recordingsLoading, setRecordingsLoading] = useState(false);
  const [form, setForm] = useState({
    recording_mode: "motion",
    pre_record_seconds: 10,
    post_record_seconds: 50,
    quality: "medium",
    flip_180: false,
  });
  const [connectionForm, setConnectionForm] = useState({ url: "", edge_base_url: "" });
  const [saving, setSaving] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [showDebugPanel, setShowDebugPanel] = useState(false);
  /** Manual recording on edge cameras with recording_mode === "off" (cam id → active). */
  const [manualRecordingById, setManualRecordingById] = useState({});
  const [edgeRtspOverrides, setEdgeRtspOverrides] = useState({});
  /** Phase 1: controller `/ws/detections` → per-camera inference + person debug */
  const [detectionsById, setDetectionsById] = useState({});
  const [detectionWsOpen, setDetectionWsOpen] = useState(false);
  const [detectionSystem, setDetectionSystem] = useState(null);

  const load = useCallback(async () => {
    const res = await fetch(`${API}/cameras`);
    const data = await res.json();
    setCams(data);
  }, []);

  const loadAllRecordings = useCallback(async (cameraList) => {
    if (!cameraList.length) {
      setAllRecordings([]);
      return;
    }
    setRecordingsLoading(true);
    try {
      const results = await Promise.all(
        cameraList.map(async (cam) => {
          try {
            const res = await fetch(`${API}/recordings/${cam.id}`);
            if (!res.ok) return [];
            const files = await res.json();
            if (!Array.isArray(files)) return [];
            return files.map((r) => ({
              camId: cam.id,
              camName: cam.name,
              name: r.name,
              size: r.size ?? 0,
              mtime: r.mtime ?? 0,
            }));
          } catch {
            return [];
          }
        })
      );
      const flat = results.flat();
      flat.sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
      setAllRecordings(flat);
    } finally {
      setRecordingsLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    loadAllRecordings(cams);
  }, [cams, loadAllRecordings]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const polled = {};
      for (const c of cams) {
        const mode = c.settings?.recording_mode || "motion";
        if (mode !== "off" || !c.edge_base_url) continue;
        try {
          const res = await fetch(`${API}/cameras/${c.id}/recordings/manual/status`);
          if (res.ok) {
            const j = await res.json();
            polled[c.id] = Boolean(j.active);
          }
        } catch {
          /* ignore */
        }
      }
      if (cancelled) return;
      setManualRecordingById((prev) => {
        const next = { ...prev };
        for (const c of cams) {
          const mode = c.settings?.recording_mode || "motion";
          if (mode !== "off" || !c.edge_base_url) delete next[c.id];
        }
        Object.assign(next, polled);
        return next;
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [cams]);

  useEffect(() => {
    let ws;
    let alive = true;
    const connect = () => {
      try {
        ws = new WebSocket(WS_RECORDING);
      } catch {
        return;
      }
      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          const camsMap = data.cameras || {};
          const next = {};
          Object.entries(camsMap).forEach(([id, row]) => {
            next[Number(id)] = Boolean(row.recording);
          });
          if (alive) setRecordingById(next);
        } catch {
          /* ignore */
        }
      };
      ws.onclose = () => {
        if (!alive) return;
        setTimeout(connect, 3000);
      };
    };
    connect();
    return () => {
      alive = false;
      if (ws) ws.close();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(`${API}/system/live_detection`);
        if (res.ok && !cancelled) {
          setDetectionSystem(await res.json());
        }
      } catch {
        /* ignore */
      }
    };
    poll();
    const iv = setInterval(poll, 4000);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
  }, []);

  useEffect(() => {
    let ws;
    let alive = true;
    const connect = () => {
      setDetectionWsOpen(false);
      try {
        ws = new WebSocket(WS_DETECTIONS);
      } catch {
        return;
      }
      ws.onopen = () => {
        if (alive) setDetectionWsOpen(true);
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (!alive) return;
          if (msg.type === "hello") {
            setDetectionSystem(msg);
            return;
          }
          if (msg.type === "detections" && msg.camera_id != null) {
            const id = Number(msg.camera_id);
            const faces = Array.isArray(msg.faces) ? msg.faces : [];
            const personCount =
              typeof msg.person_count === "number"
                ? msg.person_count
                : countPersonDetections(faces);
            setDetectionsById((prev) => ({
              ...prev,
              [id]: {
                faces,
                ts: msg.ts || "",
                personCount,
                personDetected: Boolean(msg.person_detected) || personCount > 0,
                faceCount: typeof msg.face_count === "number" ? msg.face_count : faces.length,
                error: msg.error || null,
                hailoError: msg.hailo_error || null,
                backend: msg.backend || null,
                status: msg.status || null,
                bufferAgeMs:
                  typeof msg.buffer_age_ms === "number" ? msg.buffer_age_ms : null,
                inferenceDelayMs:
                  typeof msg.inference_delay_ms === "number"
                    ? msg.inference_delay_ms
                    : null,
              },
            }));
          }
        } catch {
          /* ignore */
        }
      };
      ws.onclose = () => {
        if (alive) setDetectionWsOpen(false);
        if (!alive) return;
        setTimeout(connect, 3000);
      };
      ws.onerror = () => {
        if (alive) setDetectionWsOpen(false);
      };
    };
    connect();
    return () => {
      alive = false;
      setDetectionWsOpen(false);
      if (ws) ws.close();
    };
  }, []);

  const detectCameras = async () => {
    setDetecting(true);
    setDiscoveredEdges([]);
    try {
      const edgesRes = await fetch(`${API}/detect/edges`);
      if (edgesRes.ok) {
        setDiscoveredEdges(await edgesRes.json());
      } else {
        setDiscoveredEdges([]);
      }
    } finally {
      setDetecting(false);
    }
  };

  const addDiscovered = async (cam) => {
    const k = cam.edge_base_url ? edgeDiscoveryKey(cam) : "";
    const override = k && Object.prototype.hasOwnProperty.call(edgeRtspOverrides, k)
      ? edgeRtspOverrides[k]
      : "";
    const url =
      (override != null && String(override).trim()) ||
      (cam.url != null && String(cam.url).trim()) ||
      "";
    if (!url) {
      window.alert(
        "RTSP URL is missing — enter the stream URL for this edge, or enable SURVEILLANCE_PI_CAMERA=1 " +
          "with mediamtx on the Pi (see docs/SETUP_PI4.md)."
      );
      return;
    }
    const { kind: _k, incomplete: _i, ...rest } = cam;
    const payload = { ...rest, url };
    const res = await fetch(`${API}/cameras`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      window.alert(err.detail || "Add failed");
      return;
    }
    await load();
  };

  const openSettings = async (cam) => {
    setSettingsCam(cam);
    setConnectionForm({
      url: cam.url || "",
      edge_base_url: cam.edge_base_url || "",
    });
    const res = await fetch(`${API}/cameras/${cam.id}/settings`);
    if (res.ok) {
      const s = await res.json();
      setForm({
        recording_mode: s.recording_mode || "motion",
        pre_record_seconds: s.pre_record_seconds ?? 10,
        post_record_seconds: s.post_record_seconds ?? 50,
        quality: s.quality || "medium",
        flip_180: Boolean(s.flip_180),
      });
    }
  };

  const closeSettings = () => {
    setSettingsCam(null);
  };

  const deleteCamera = async (cam) => {
    if (!window.confirm(`Remove “${cam.name}” from the controller?`)) return;
    const res = await fetch(`${API}/cameras/${cam.id}`, { method: "DELETE" });
    if (!res.ok) {
      alert("Remove failed");
      return;
    }
    if (settingsCam && settingsCam.id === cam.id) {
      closeSettings();
    }
    await load();
  };

  const saveSettings = async () => {
    if (!settingsCam) return;
    setSaving(true);
    try {
      const connBody = {};
      const urlTrim = String(connectionForm.url || "").trim();
      const edgeTrim = String(connectionForm.edge_base_url || "").trim();
      if (urlTrim && urlTrim !== (settingsCam.url || "")) {
        connBody.url = urlTrim;
      }
      const prevEdge = settingsCam.edge_base_url || "";
      if (edgeTrim !== prevEdge) {
        connBody.edge_base_url = edgeTrim || null;
      }
      if (Object.keys(connBody).length > 0) {
        const connRes = await fetch(`${API}/cameras/${settingsCam.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(connBody),
        });
        if (!connRes.ok) {
          const err = await connRes.json().catch(() => ({}));
          alert(err.detail || "Failed to update stream URL");
          return;
        }
      }

      const res = await fetch(`${API}/cameras/${settingsCam.id}/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          recording_mode: form.recording_mode,
          pre_record_seconds: Number(form.pre_record_seconds),
          post_record_seconds: Number(form.post_record_seconds),
          quality: form.quality,
          flip_180: form.flip_180,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert(err.detail || "Failed to save settings");
        return;
      }
      const next = await res.json();
      setForm({
        recording_mode: next.recording_mode,
        pre_record_seconds: next.pre_record_seconds,
        post_record_seconds: next.post_record_seconds,
        quality: next.quality || "medium",
        flip_180: Boolean(next.flip_180),
      });
      await load();
      closeSettings();
    } finally {
      setSaving(false);
    }
  };

  const toggleManualRecording = async (cam) => {
    const mode = cam.settings?.recording_mode || "motion";
    if (mode !== "off") return;
    if (!cam.edge_base_url) {
      alert("Manual recording requires a Pi edge camera.");
      return;
    }
    const currentlyOn = manualRecordingById[cam.id] === true;
    const path = currentlyOn ? "stop" : "start";
    try {
      const res = await fetch(`${API}/cameras/${cam.id}/recordings/manual/${path}`, {
        method: "POST",
      });
      const errBody = await res.json().catch(() => ({}));
      if (!res.ok) {
        alert(typeof errBody.detail === "string" ? errBody.detail : `${path} failed`);
        return;
      }
      setManualRecordingById((prev) => ({ ...prev, [cam.id]: path === "start" }));
      await loadAllRecordings(cams);
    } catch (e) {
      alert(String(e));
    }
  };

  const deleteRecordingFor = async (camId, name) => {
    if (!window.confirm(`Delete ${name}?`)) return;
    const res = await fetch(
      `${API}/recordings/${camId}/files/${encodeURIComponent(name)}`,
      { method: "DELETE" }
    );
    if (!res.ok) {
      alert("Delete failed");
      return;
    }
    await loadAllRecordings(cams);
  };

  const liveCams = cams.slice(0, MAX_LIVE_TILES);

  return (
    <div className="flex h-screen bg-[#0b1220] text-white">
      <div className="w-56 lg:w-60 bg-[#070c16] p-3 flex flex-col gap-3 overflow-y-auto shrink-0 border-r border-gray-800">
        <h1 className="text-lg font-bold">Vigilance</h1>
        <p className="text-[10px] text-gray-500">Dashboard · up to {MAX_LIVE_TILES} cameras</p>

        <button
          type="button"
          onClick={() => setShowDebugPanel((v) => !v)}
          className="text-[10px] text-left text-gray-400 hover:text-gray-200"
        >
          {showDebugPanel ? "▼" : "▶"} Detection diagnostics
        </button>
        {showDebugPanel ? (
        <div className="rounded-lg border border-gray-700 bg-[#111827] p-2 text-[10px] space-y-1.5">
          <p className="font-semibold text-gray-300">Person detection (debug)</p>
          <p className={detectionWsOpen ? "text-green-400" : "text-amber-400"}>
            WebSocket: {detectionWsOpen ? "connected" : "disconnected"}
          </p>
          <p className="text-gray-400">
            Backend: <span className="text-gray-200">{detectionSystem?.backend || "—"}</span>
          </p>
          <p className="text-gray-400">
            Hailo:{" "}
            <span className="text-gray-200">
              {detectionSystem?.hailo_ready
                ? "ready"
                : detectionSystem?.hailo_error || "not ready"}
            </span>
          </p>
          <p className="text-gray-400">
            Inference workers:{" "}
            <span className="text-gray-200">{detectionSystem?.workers ?? 0}</span>
          </p>
          <p className="text-gray-400">
            Inference delay:{" "}
            <span className="text-gray-200">
              {typeof detectionSystem?.inference_delay_ms === "number"
                ? detectionSystem.inference_delay_ms
                : typeof detectionSystem?.overlay_delay_ms === "number"
                  ? detectionSystem.overlay_delay_ms
                  : "—"}{" "}
              ms
            </span>
            {detectionOverlaySyncEnabled() ? (
              <span className="text-gray-500">
                {" "}
                (+ UI {detectionOverlayDelayMs()} ms)
              </span>
            ) : null}
          </p>
          {liveCams.length > 0 ? (
            <div className="border-t border-gray-700 pt-1.5 space-y-0.5">
              {liveCams.map((c) => (
                <p key={c.id} className="text-gray-400 font-mono leading-snug">
                  {c.name}: {formatPersonDebugLine(detectionsById[c.id], detectionWsOpen)}
                </p>
              ))}
            </div>
          ) : null}
        </div>
        ) : null}

        <button
          type="button"
          disabled={detecting}
          onClick={detectCameras}
          className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed p-2 rounded text-sm"
        >
          {detecting ? "Detecting… (~3s)" : "Detect cameras"}
        </button>

        {discoveredEdges.length > 0 ? (
          <div className="flex flex-col gap-2">
            {discoveredEdges.map((e, i) => (
              <div
                key={`${e.edge_base_url}-${e.mqtt_camera_id}-${i}`}
                className="bg-[#111827] p-2 rounded text-xs flex flex-col gap-1"
              >
                <div className="flex justify-between items-center gap-1">
                  <span className="truncate font-medium">{e.name}</span>
                  <button
                    type="button"
                    onClick={() => addDiscovered(e)}
                    className="text-green-400 shrink-0"
                  >
                    Add
                  </button>
                </div>
                <span className="text-[10px] text-gray-500 font-mono truncate">
                  {e.edge_base_url} · id {e.mqtt_camera_id}
                </span>
                {e.incomplete ? (
                  <p
                    className="text-[10px] text-amber-500 leading-snug"
                    title="Edge did not advertise an RTSP URL in mDNS TXT (see docs/SETUP_PI4.md)."
                  >
                    No RTSP in mDNS — paste the RTSP URL here, then Add.
                  </p>
                ) : null}
                {e.incomplete ? (
                  <input
                    className="w-full bg-[#0b1220] border border-amber-700/80 rounded px-2 py-1 text-[10px] font-mono"
                    placeholder={`rtsp://…:8554/${(e.mediamtx_path || e.mqtt_camera_id || "camera1").replace(/^\//, "")}`}
                    value={edgeRtspOverrides[edgeDiscoveryKey(e)] ?? ""}
                    onChange={(ev) =>
                      setEdgeRtspOverrides((o) => ({
                        ...o,
                        [edgeDiscoveryKey(e)]: ev.target.value,
                      }))
                    }
                  />
                ) : null}
              </div>
            ))}
          </div>
        ) : null}

        <div>
          <h3 className="text-xs text-gray-400 mb-2">Detected Cameras</h3>
          {cams.map((c) => (
            <div
              key={c.id}
              className="bg-[#111827] p-2 rounded mb-2 text-sm flex justify-between items-center gap-1"
            >
              <span className="truncate flex-1">{c.name}</span>
              <div className="flex items-center gap-1 shrink-0">
                <button
                  type="button"
                  onClick={() => openSettings(c)}
                  className="text-gray-300 hover:text-white px-1"
                  title="Camera settings"
                >
                  ⚙
                </button>
                <button
                  type="button"
                  onClick={() => deleteCamera(c)}
                  className="text-red-400 hover:text-red-300 px-1 text-xs"
                  title="Remove camera"
                >
                  ✕
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        <div className="flex flex-wrap items-center gap-2 px-4 py-2 border-b border-gray-800 bg-[#070c16]/80 shrink-0">
          <span className="text-xs font-medium text-gray-200">
            {liveCams.length} camera{liveCams.length === 1 ? "" : "s"} live
          </span>
          <span
            className={`text-[10px] px-2 py-0.5 rounded-full border ${
              detectionWsOpen
                ? "border-green-500/40 text-green-400 bg-green-500/10"
                : "border-amber-500/40 text-amber-400 bg-amber-500/10"
            }`}
          >
            WS {detectionWsOpen ? "connected" : "disconnected"}
          </span>
          <span
            className={`text-[10px] px-2 py-0.5 rounded-full border ${
              detectionSystem?.hailo_ready
                ? "border-green-500/40 text-green-400 bg-green-500/10"
                : "border-gray-600 text-gray-400 bg-gray-800/50"
            }`}
          >
            Hailo {detectionSystem?.hailo_ready ? "ready" : "off"}
          </span>
          {liveCams.some((c) => {
            const n =
              detectionsById[c.id]?.personCount ??
              countPersonDetections(detectionsById[c.id]?.faces);
            return n > 0;
          }) ? (
            <span className="text-[10px] px-2 py-0.5 rounded-full border border-blue-500/40 text-blue-300 bg-blue-500/10">
              Person detected
            </span>
          ) : null}
          <span className="text-[10px] text-gray-500 ml-auto hidden lg:inline font-mono truncate max-w-[40%]">
            {API}
          </span>
        </div>

        <div className="flex-1 p-3 min-h-0 overflow-hidden flex flex-col">
          {liveCams.length === 0 ? (
            <p className="text-gray-500 text-sm p-4">
              No cameras saved. Use Detect cameras to add Pi edges — saved cameras persist across
              backend restarts until removed.
            </p>
          ) : (
            <div
              className={`grid flex-1 min-h-0 gap-3 grid-cols-3 ${
                liveCams.length > 4 ? "grid-rows-3" : "grid-rows-2"
              }`}
            >
              {liveCams.map((c, i) => (
                <div key={c.id} className={`min-h-0 min-w-0 ${bentoTileClass(i, liveCams.length)}`}>
                  <LiveTile
                    cam={c}
                    layout={i === 0 ? "hero" : "default"}
                    recording={
                      (c.settings?.recording_mode || "motion") === "off"
                        ? manualRecordingById[c.id] === true
                        : recordingById[c.id] === true
                    }
                    recordingMode={c.settings?.recording_mode || "motion"}
                    manualRecording={manualRecordingById[c.id] === true}
                    onManualToggle={() => toggleManualRecording(c)}
                    faces={detectionsById[c.id]?.faces}
                    personCount={detectionsById[c.id]?.personCount ?? 0}
                    detectionInfo={detectionsById[c.id]}
                    detectionWsOpen={detectionWsOpen}
                    overlayDelayMs={
                      typeof detectionSystem?.overlay_delay_ms === "number"
                        ? detectionSystem.overlay_delay_ms
                        : undefined
                    }
                  />
                </div>
              ))}
            </div>
          )}
        </div>

        <RecordingsTimeline
          recordings={allRecordings}
          loading={recordingsLoading}
          hasCameras={cams.length > 0}
          onRefresh={() => loadAllRecordings(cams)}
          onDelete={deleteRecordingFor}
        />
      </div>

      {settingsCam && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          role="dialog"
          aria-modal="true"
        >
          <div className="bg-[#111827] rounded-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto p-5 shadow-xl border border-gray-800">
            <div className="flex justify-between items-start mb-4">
              <div>
                <h2 className="text-lg font-semibold">{settingsCam.name}</h2>
                <p className="text-xs text-gray-400">{settingsCam.location}</p>
                {settingsCam.edge_base_url ? (
                  <p className="text-[10px] text-amber-400/90 mt-1">
                    Edge {settingsCam.edge_base_url} · MQTT id{" "}
                    {settingsCam.mqtt_camera_id || String(settingsCam.id)}
                  </p>
                ) : (
                  <p className="text-[10px] text-gray-500 mt-1">
                    Local recordings on controller (no edge_base_url).
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={closeSettings}
                className="text-gray-400 hover:text-white text-xl leading-none"
              >
                ×
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-xs text-gray-400 mb-1">RTSP URL (MediaMTX pull source)</label>
                <input
                  type="text"
                  className="w-full bg-[#0b1220] border border-gray-700 rounded px-3 py-2 text-sm font-mono"
                  value={connectionForm.url}
                  onChange={(e) =>
                    setConnectionForm((f) => ({ ...f, url: e.target.value }))
                  }
                  placeholder="rtsp://192.168.2.164:8554/camera1"
                />
                <p className="text-[10px] text-gray-500 mt-1">
                  Pi 4 edge with Pi camera: rtsp://&lt;edge-ip&gt;:8554/&lt;camera_id&gt;
                </p>
              </div>

              <div>
                <label className="block text-xs text-gray-400 mb-1">Edge HTTP API (optional)</label>
                <input
                  type="text"
                  className="w-full bg-[#0b1220] border border-gray-700 rounded px-3 py-2 text-sm font-mono"
                  value={connectionForm.edge_base_url}
                  onChange={(e) =>
                    setConnectionForm((f) => ({ ...f, edge_base_url: e.target.value }))
                  }
                  placeholder="http://192.168.2.164:8080"
                />
              </div>

              <div>
                <label className="block text-xs text-gray-400 mb-1">Recording</label>
                <select
                  className="w-full bg-[#0b1220] border border-gray-700 rounded px-3 py-2 text-sm"
                  value={form.recording_mode}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, recording_mode: e.target.value }))
                  }
                >
                  <option value="motion">Motion (person / vehicle / animal)</option>
                  <option value="continuous">Continuous</option>
                  <option value="off">Off (manual record button on live tile)</option>
                </select>
                {form.recording_mode === "continuous" ? (
                  <p className="text-xs text-amber-400 mt-2">
                    Continuous recording fills the SD card (or NAS) quickly. Use retention or
                    lower quality on the edge.
                  </p>
                ) : null}
                {form.recording_mode === "off" ? (
                  <p className="text-xs text-gray-400 mt-2">
                    Automatic recording is disabled. On the main page, edge cameras show a{" "}
                    <span className="font-mono text-gray-300">Rec</span> toggle on the live tile
                    to start and stop a clip (Pi edge only).
                  </p>
                ) : null}
              </div>

              <div>
                <label className="block text-xs text-gray-400 mb-1">
                  Stream quality (edge encoder / future libcamera)
                </label>
                <select
                  className="w-full bg-[#0b1220] border border-gray-700 rounded px-3 py-2 text-sm"
                  value={form.quality}
                  onChange={(e) => setForm((f) => ({ ...f, quality: e.target.value }))}
                >
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                </select>
              </div>

              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.flip_180}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, flip_180: e.target.checked }))
                  }
                />
                Flip image 180° (edge processing)
              </label>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Pre-roll (s)</label>
                  <input
                    type="number"
                    min={1}
                    max={120}
                    className="w-full bg-[#0b1220] border border-gray-700 rounded px-3 py-2 text-sm"
                    value={form.pre_record_seconds}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, pre_record_seconds: e.target.value }))
                    }
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Post-roll (s)</label>
                  <input
                    type="number"
                    min={1}
                    max={300}
                    className="w-full bg-[#0b1220] border border-gray-700 rounded px-3 py-2 text-sm"
                    value={form.post_record_seconds}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, post_record_seconds: e.target.value }))
                    }
                  />
                </div>
              </div>

              <button
                type="button"
                onClick={saveSettings}
                disabled={saving}
                className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-4 py-2 rounded text-sm"
              >
                {saving ? "Saving…" : "Save settings"}
              </button>

              <p className="text-[10px] text-gray-500 border-t border-gray-700 pt-3">
                Clips from all cameras appear in the <span className="text-gray-400">Recent clips</span> timeline at the bottom of the dashboard.
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
