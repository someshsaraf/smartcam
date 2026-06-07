import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Hls from "hls.js";
import {
  API,
  detectionOverlayDelayMs,
  detectionOverlaySyncEnabled,
  HLS_BASE,
  MEDIAMTX_BASE,
  preferNativeHlsPlayback,
  preferWebRtcLive,
  WS_DETECTIONS,
  WS_RECORDING,
} from "./envConfig";
import { useOverlaySyncedDetections } from "./useOverlaySyncedDetections";
import { IconClose, MobilePageHeader } from "./mobileScreens";
import {
  CameraInfoTable,
  CameraInsights,
  DeviceCard,
  DeviceConfigTabs,
  DeviceDetailHeader,
  DevicesDashboardPage,
  LiveDashboardPage,
  PlaybackDashboardPage,
  PlaybackTimelineBar,
  VigilanceShell,
} from "./dashboardLayout";

const MAX_LIVE_TILES = 6;
function canPlayNativeHls(video) {
  if (!video) return false;
  const types = ["application/vnd.apple.mpegurl", "application/x-mpegURL"];
  return types.some((t) => {
    const v = video.canPlayType(t);
    return v === "probably" || v === "maybe";
  });
}

function computeObjectContainLayout(containerW, containerH, videoW, videoH) {
  if (containerW < 2 || containerH < 2 || videoW < 2 || videoH < 2) return null;
  const scale = Math.min(containerW / videoW, containerH / videoH);
  const drawW = videoW * scale;
  const drawH = videoH * scale;
  return {
    containerW,
    containerH,
    offsetX: (containerW - drawW) / 2,
    offsetY: (containerH - drawH) / 2,
    drawW,
    drawH,
    videoW,
    videoH,
    scale,
  };
}

function personDetections(faces) {
  return (Array.isArray(faces) ? faces : []).filter(
    (d) => String(d?.label || "").toLowerCase() === "person",
  );
}

/** Format model score 0–1 as percentage string. */
function formatConfidencePct(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return n <= 1 ? `${Math.round(n * 1000) / 10}%` : `${n.toFixed(1)}%`;
}

/** Detection confidence for overlay label (backend sends 0–1). */
function formatDetectionLabel(det) {
  const score = formatConfidencePct(det?.score);
  const label = det?.label ? String(det.label) : "person";
  return score !== "—" ? `${label} ${score}` : label;
}

function PersonBoxesOverlay({ faces, videoRef, containerRef, assumedAspect }) {
  const [layout, setLayout] = useState(null);
  const people = personDetections(faces);

  useEffect(() => {
    const container = containerRef?.current;
    const video = videoRef?.current;
    if (!container) return undefined;

    const measure = () => {
      const cw = container.clientWidth;
      const ch = container.clientHeight;
      const vw =
        video && video.videoWidth > 0
          ? video.videoWidth
          : assumedAspect?.w ?? 16;
      const vh =
        video && video.videoHeight > 0
          ? video.videoHeight
          : assumedAspect?.h ?? 9;
      setLayout(computeObjectContainLayout(cw, ch, vw, vh));
    };

    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(container);
    if (video) {
      video.addEventListener("loadedmetadata", measure);
      video.addEventListener("loadeddata", measure);
    }
    window.addEventListener("orientationchange", measure);
    const tick = window.setInterval(measure, 250);
    return () => {
      ro.disconnect();
      if (video) {
        video.removeEventListener("loadedmetadata", measure);
        video.removeEventListener("loadeddata", measure);
      }
      window.removeEventListener("orientationchange", measure);
      window.clearInterval(tick);
    };
  }, [faces, videoRef, containerRef, assumedAspect?.w, assumedAspect?.h]);

  if (!layout || people.length === 0) return null;

  return (
    <div className="absolute inset-0 z-10 pointer-events-none overflow-hidden" aria-hidden>
      {people.map((f, i) => {
        const x = layout.offsetX + Number(f.x) * layout.videoW * layout.scale;
        const y = layout.offsetY + Number(f.y) * layout.videoH * layout.scale;
        const w = Math.max(2, Number(f.w) * layout.videoW * layout.scale);
        const h = Math.max(2, Number(f.h) * layout.videoH * layout.scale);
        const leftPct = (x / layout.containerW) * 100;
        const topPct = (y / layout.containerH) * 100;
        const wPct = (w / layout.containerW) * 100;
        const hPct = (h / layout.containerH) * 100;
        const label = formatDetectionLabel(f);
        return (
          <div
            key={`${i}-${leftPct}-${topPct}`}
            className="absolute box-border border-2 border-blue-400/95 rounded-sm shadow-[0_0_0_1px_rgba(0,0,0,0.35)]"
            style={{
              left: `${leftPct}%`,
              top: `${topPct}%`,
              width: `${wPct}%`,
              height: `${hPct}%`,
            }}
          >
            {label ? (
              <span className="absolute left-0 bottom-full mb-0.5 max-w-[8rem] truncate rounded px-1 py-px text-[10px] font-mono font-semibold leading-tight text-blue-100 bg-black/80 border border-blue-500/40">
                {label}
              </span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/** RTSP source URL — API / JSON may expose `url` or `main_stream` only. */
function cameraRtspUrl(cam) {
  if (!cam || typeof cam !== "object") return "";
  return (
    (cam.url && String(cam.url).trim()) ||
    (cam.main_stream && String(cam.main_stream).trim()) ||
    (cam.mainStream && String(cam.mainStream).trim()) ||
    ""
  );
}

function streamPathForCamera(cam) {
  if (cam.mediamtx_path && String(cam.mediamtx_path).trim()) {
    return String(cam.mediamtx_path).trim().replace(/^\//, "");
  }
  const url = cameraRtspUrl(cam);
  const parts = url.split("/").filter(Boolean);
  const last = parts[parts.length - 1] || "";
  // VIGI/TP-Link URLs often end in /stream1 — controller MediaMTX paths are usually per-camera id (cam0, …).
  if (cam.id != null && String(cam.id) !== "" && /^stream\d*$/i.test(last)) {
    return `cam${cam.id}`;
  }
  return last || (cam.id != null ? `cam${cam.id}` : "camera");
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
  const bytes = Number(n);
  if (!Number.isFinite(bytes) || bytes <= 0) return "…";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function cameraDisplayMeta(cam) {
  if (!cam) return { resolution: "—", fps: "—", ip: "—", bitrate: "—" };
  const q = cam.settings?.quality || "medium";
  const resolution =
    q === "high" ? "1920 × 1080" : q === "low" ? "1280 × 720" : "1920 × 1080";
  const fps = "25";
  const bitrate = q === "high" ? "4096 kbps" : q === "low" ? "1024 kbps" : "2048 kbps";
  let ip = "—";
  try {
    const rtsp = cameraRtspUrl(cam);
    if (rtsp) ip = new URL(rtsp).hostname;
    else if (cam.edge_base_url) ip = new URL(cam.edge_base_url).hostname;
  } catch {
    /* invalid url */
  }
  return { resolution, fps, ip, bitrate };
}

function formatTime(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString();
}

function edgeBaseUrlForCamera(cameras, camId) {
  if (!Array.isArray(cameras) || !isSetCameraId(camId)) return "";
  const cam = cameras.find((c) => cameraIdsMatch(c.id, camId));
  return cam?.edge_base_url ? String(cam.edge_base_url).trim() : "";
}

/**
 * Clip URL for <video>. Mobile/iOS uses controller proxy (auto-finalize + byte-range).
 * Desktop may use edge direct; append playback=1 so edge converts fMP4 if needed.
 */
function recordingFileUrl(camId, name, edgeBaseUrl, cameras, opts = {}) {
  const viaController =
    opts.viaController === true ||
    (opts.viaController !== false && preferNativeHlsPlayback());
  if (!viaController) {
    const edge =
      (edgeBaseUrl && String(edgeBaseUrl).trim()) || edgeBaseUrlForCamera(cameras, camId);
    if (edge) {
      const base = `${edge.replace(/\/$/, "")}/recordings/files/${encodeURIComponent(name)}`;
      return opts.forPlayback ? `${base}?playback=1` : base;
    }
  }
  return `${API}/recordings/${encodeURIComponent(String(camId))}/files/${encodeURIComponent(name)}`;
}

function recordingThumbnailUrl(camId, name) {
  if (!isSetCameraId(camId) || !name) return "";
  return `${API}/recordings/${encodeURIComponent(String(camId))}/files/${encodeURIComponent(name)}/thumbnail`;
}

/** JPEG preview from saved thumbnail; falls back to video metadata seek if missing. */
function RecordingThumbnail({ camId, name, videoFallbackSrc, className = "" }) {
  const [useVideoFallback, setUseVideoFallback] = useState(false);
  const videoRef = useRef(null);
  const thumbSrc = recordingThumbnailUrl(camId, name);

  useEffect(() => {
    setUseVideoFallback(false);
  }, [camId, name, thumbSrc]);

  useEffect(() => {
    if (useVideoFallback) return undefined;
    const el = videoRef.current;
    if (!el) return undefined;
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
  }, [useVideoFallback, videoFallbackSrc]);

  if (!useVideoFallback && thumbSrc) {
    return (
      <img
        src={thumbSrc}
        alt=""
        loading="lazy"
        decoding="async"
        onError={() => {
          if (!preferNativeHlsPlayback()) setUseVideoFallback(true);
        }}
        className={className}
      />
    );
  }

  if (!videoFallbackSrc) {
    return <div className={`bg-gray-900 ${className}`} aria-hidden />;
  }

  return (
    <video
      ref={videoRef}
      src={videoFallbackSrc}
      preload="metadata"
      muted
      playsInline
      className={className}
      aria-hidden
    />
  );
}

/** Bento grid cell placement for layout D (index 0 = hero). Desktop only. */
function bentoTileClass(index, total) {
  if (total <= 1) return "md:col-span-3 md:row-span-2";
  if (index === 0) return "md:col-start-1 md:row-start-1 md:row-span-2";
  if (index === 1) return "md:col-start-2 md:row-start-1";
  if (index === 2) return "md:col-start-3 md:row-start-1";
  if (index === 3) return "md:col-start-2 md:row-start-2";
  if (index === 4) return "md:col-start-3 md:row-start-2";
  if (index === 5) return "md:col-span-3 md:row-start-3";
  return "";
}

async function readApiError(res) {
  try {
    const j = await res.json();
    if (typeof j?.detail === "string") return j.detail;
    if (Array.isArray(j?.detail)) {
      return j.detail.map((d) => (typeof d === "string" ? d : d?.msg || String(d))).join("; ");
    }
    if (j?.detail != null) return String(j.detail);
    return res.statusText || `HTTP ${res.status}`;
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

function LiveCameraThumbStrip({ cameras, activeId, onSelect, renderThumb, mobile = false }) {
  const others = cameras.filter((c) => !cameraIdsMatch(c.id, activeId));
  if (others.length === 0) return null;
  return (
    <div className={`shrink-0 ${mobile ? "px-1" : ""}`} aria-label="Other camera feeds">
      {mobile ? (
        <p className="text-[10px] font-semibold uppercase tracking-wider text-gray-500 px-1 mb-2">
          Other cameras
        </p>
      ) : null}
      <div
        className={`flex gap-2.5 mobile-scroll-x snap-x snap-mandatory ${mobile ? "pb-2" : "pb-1"}`}
      >
        {others.map((c) => (
          <div
            key={c.id}
            role="button"
            tabIndex={0}
            onClick={() => onSelect(c.id)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelect(c.id);
              }
            }}
            className={`shrink-0 snap-start cursor-pointer group active:scale-[0.98] transition-transform ${
              mobile ? "w-[8.5rem]" : "w-[7.5rem]"
            }`}
            title={`Switch to ${c.name}`}
          >
            <div
              className={`overflow-hidden aspect-video bg-black pointer-events-none ${
                mobile
                  ? "rounded-xl ring-1 ring-white/10 shadow-lg"
                  : "rounded-lg ring-1 ring-gray-700 group-hover:ring-indigo-500/70"
              }`}
            >
              {renderThumb(c)}
            </div>
            <p
              className={`truncate mt-1.5 px-0.5 font-medium ${
                mobile
                  ? "text-xs text-gray-300"
                  : "text-[10px] text-gray-400 group-hover:text-gray-200"
              }`}
            >
              {c.name}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}


const EVENT_TYPE_LABELS = {
  person_detected: "Person detected",
};

function formatEventType(eventType) {
  const t = String(eventType || "");
  return EVENT_TYPE_LABELS[t] || t.replace(/_/g, " ");
}

function timelineActivityKind(eventType) {
  const t = String(eventType || "").toLowerCase();
  if (t.includes("person")) return "person";
  if (t.includes("vehicle")) return "vehicle";
  if (t.includes("motion")) return "motion";
  return undefined;
}

function formatEventTime(ts) {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return ts;
    return d.toLocaleString();
  } catch {
    return ts;
  }
}

/** Clock time for activity timeline (e.g. 10:24 PM). */
function formatTimelineClock(ts) {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleTimeString(undefined, {
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    });
  } catch {
    return "—";
  }
}

/** Match a person_detected event to a motion clip row (filename or time proximity). */
function resolveRecordingForEvent(ev, recordings, cameraId) {
  if (!ev || !isSetCameraId(cameraId) || !Array.isArray(recordings)) return null;
  const camRecordings = recordings.filter((r) => cameraIdsMatch(r.camId, cameraId));
  const fn = String(ev.filename || "").trim();
  if (fn) {
    const byName = camRecordings.find((r) => r.name === fn);
    if (byName) return byName;
  }
  const evtMs = Date.parse(ev.ts);
  if (!Number.isFinite(evtMs)) return null;
  const motionClips = camRecordings.filter((r) => String(r.name || "").startsWith("evt_"));
  let best = null;
  let bestDelta = Infinity;
  for (const r of motionClips) {
    const clipMs = (r.mtime || 0) * 1000;
    if (clipMs < evtMs - 15000) continue;
    const delta = Math.abs(clipMs - evtMs);
    if (delta < bestDelta) {
      bestDelta = delta;
      best = r;
    }
  }
  return bestDelta <= 120000 ? best : null;
}

/** Local date (YYYY-MM-DD) + optional time (HH:MM) → UTC ISO for API filters. */
function localDateTimeToIso(dateStr, timeStr, endOfDay = false) {
  const d = String(dateStr || "").trim();
  if (!d) return null;
  const tRaw = String(timeStr || "").trim();
  const t =
    tRaw && /^\d{1,2}:\d{2}(:\d{2})?$/.test(tRaw)
      ? tRaw.length === 5
        ? `${tRaw}:00`
        : tRaw
      : endOfDay
        ? "23:59:59"
        : "00:00:00";
  const dt = new Date(`${d}T${t}`);
  if (Number.isNaN(dt.getTime())) return null;
  return dt.toISOString();
}

function buildEventFilterRange(fromDate, fromTime, toDate, toTime) {
  const fromIso = localDateTimeToIso(fromDate, fromTime, false);
  const toIso = localDateTimeToIso(toDate, toTime, true);
  if (fromIso && toIso && fromIso > toIso) {
    return { error: "From date/time must be before To date/time." };
  }
  if (!fromIso && !toIso) return { fromIso: null, toIso: null };
  return { fromIso, toIso };
}

function eventsApiQuery(appliedFilter) {
  const params = new URLSearchParams({ limit: "200" });
  if (appliedFilter?.fromIso) params.set("from_ts", appliedFilter.fromIso);
  if (appliedFilter?.toIso) params.set("to_ts", appliedFilter.toIso);
  return params.toString();
}

function todayStartIso() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
}

function EventsPanel({
  cameraId,
  cameraName,
  recordings = [],
  cameras = [],
  playingClip,
  onPlayClip,
  onClearPlay,
  variant = "default",
  className = "",
}) {
  const isSidebar = variant === "sidebar";
  const isCompact = variant === "compact";
  const isPage = variant === "page";
  const [events, setEvents] = useState([]);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [filterFromDate, setFilterFromDate] = useState("");
  const [filterFromTime, setFilterFromTime] = useState("");
  const [filterToDate, setFilterToDate] = useState("");
  const [filterToTime, setFilterToTime] = useState("");
  const [appliedFilter, setAppliedFilter] = useState(null);
  const [filterError, setFilterError] = useState("");

  const load = useCallback(async () => {
    if (!isSetCameraId(cameraId)) {
      setEvents([]);
      return;
    }
    setLoading(true);
    try {
      const q = eventsApiQuery(appliedFilter);
      const res = await fetch(
        `${API}/cameras/${encodeURIComponent(String(cameraId))}/events?${q}`
      );
      if (!res.ok) return;
      const data = await res.json();
      const rows = Array.isArray(data.events) ? data.events : [];
      setEvents(rows.filter((ev) => ev?.event_type === "person_detected"));
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, [cameraId, appliedFilter]);

  useEffect(() => {
    load();
    const iv = window.setInterval(load, 3000);
    return () => window.clearInterval(iv);
  }, [load]);

  const applyFilter = () => {
    const built = buildEventFilterRange(
      filterFromDate,
      filterFromTime,
      filterToDate,
      filterToTime
    );
    if (built.error) {
      setFilterError(built.error);
      return;
    }
    setFilterError("");
    if (!built.fromIso && !built.toIso) {
      setAppliedFilter(null);
      return;
    }
    setAppliedFilter({ fromIso: built.fromIso, toIso: built.toIso });
  };

  const clearFilter = () => {
    setFilterFromDate("");
    setFilterFromTime("");
    setFilterToDate("");
    setFilterToTime("");
    setFilterError("");
    setAppliedFilter(null);
  };

  const deleteEvent = async (ev) => {
    if (!isSetCameraId(cameraId) || !ev?.id) return;
    const clip = resolveRecordingForEvent(ev, recordings, cameraId);
    if (
      !window.confirm(
        `Delete this person detected event from ${formatEventTime(ev.ts)}?`
      )
    ) {
      return;
    }
    setDeleting(true);
    try {
      const res = await fetch(
        `${API}/cameras/${encodeURIComponent(String(cameraId))}/events/${encodeURIComponent(String(ev.id))}`,
        { method: "DELETE" }
      );
      if (!res.ok) {
        alert(await readApiError(res));
        return;
      }
      if (
        playingClip &&
        clip &&
        cameraIdsMatch(playingClip.camId, clip.camId) &&
        playingClip.name === clip.name &&
        typeof onClearPlay === "function"
      ) {
        onClearPlay();
      }
      await load();
    } catch (e) {
      alert(String(e));
    } finally {
      setDeleting(false);
    }
  };

  const deleteAllEvents = async () => {
    if (!isSetCameraId(cameraId)) return;
    const filtered = Boolean(appliedFilter?.fromIso || appliedFilter?.toIso);
    const msg = filtered
      ? `Delete all ${events.length} event(s) matching the current date/time filter?`
      : `Delete all events for ${cameraName || "this camera"}?`;
    if (!window.confirm(msg)) return;
    setDeleting(true);
    try {
      const q = eventsApiQuery(appliedFilter);
      const res = await fetch(
        `${API}/cameras/${encodeURIComponent(String(cameraId))}/events?${q}`,
        { method: "DELETE" }
      );
      if (!res.ok) {
        alert(await readApiError(res));
        return;
      }
      if (typeof onClearPlay === "function") onClearPlay();
      await load();
    } catch (e) {
      alert(String(e));
    } finally {
      setDeleting(false);
    }
  };

  const filterActive = Boolean(appliedFilter?.fromIso || appliedFilter?.toIso);

  if (!isSetCameraId(cameraId)) {
    return (
      <div className={`rounded-xl border border-gray-800 bg-[#070c16] p-4 text-sm text-gray-500 ${className}`}>
        Select a camera to view events.
      </div>
    );
  }

  return (
    <div
      className={`flex flex-col min-h-0 ${
        isCompact
          ? "bg-transparent"
          : isSidebar
            ? "bg-transparent"
            : isPage
              ? "rounded-none bg-[#0b1220]"
              : "rounded-xl border border-gray-800 bg-[#070c16]"
      } ${className}`}
    >
      {!isCompact ? (
      <div
        className={`shrink-0 space-y-2 ${
          isSidebar
            ? "px-0 py-2 border-b border-gray-800"
            : isPage
              ? ""
              : "px-3 py-2 border-b border-gray-800"
        }`}
      >
        {isPage ? (
          <MobilePageHeader
            title="Events"
            subtitle={`${cameraName || `Camera ${cameraId}`}${
              events.length > 0 ? ` · ${events.length}` : ""
            }${filterActive ? " · filtered" : ""}`}
            actions={
              <>
                <button
                  type="button"
                  onClick={() => setFiltersOpen((v) => !v)}
                  className="mobile-btn-secondary text-[11px] !px-2.5 !py-1.5"
                >
                  {filtersOpen ? "Hide" : "Filter"}
                </button>
                {events.length > 0 ? (
                  <button
                    type="button"
                    onClick={deleteAllEvents}
                    disabled={loading || deleting}
                    className="mobile-btn-danger text-[11px] !px-2.5 !py-1.5"
                  >
                    Clear
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={load}
                  disabled={loading || deleting}
                  className="mobile-btn-secondary text-[11px] !px-2.5 !py-1.5"
                >
                  {loading ? "…" : "Sync"}
                </button>
              </>
            }
          />
        ) : (
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <h2
              className={`font-semibold text-gray-100 ${
                isSidebar ? "text-xs" : "text-sm"
              }`}
            >
              Events
            </h2>
            <p className="text-gray-500 truncate text-[10px]">
              {cameraName || `Camera ${cameraId}`}
              {events.length > 0 ? ` · ${events.length}` : ""}
              {filterActive ? " · filtered" : ""}
            </p>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {events.length > 0 ? (
              <button
                type="button"
                onClick={deleteAllEvents}
                disabled={loading || deleting}
                className="text-[10px] px-2 py-1 rounded border border-red-900/60 text-red-300 hover:bg-red-950/40 disabled:opacity-40"
              >
                Clear all
              </button>
            ) : null}
            <button
              type="button"
              onClick={load}
              disabled={loading || deleting}
              className="text-[10px] px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-300 disabled:opacity-50"
            >
              {loading ? "…" : "Refresh"}
            </button>
          </div>
        </div>
        )}
        {isPage && !filtersOpen ? null : (
        <>
        <div
          className={`text-[10px] ${isSidebar ? "flex flex-col gap-1.5" : isPage ? "px-4 grid grid-cols-2 gap-x-2 gap-y-2" : "grid grid-cols-2 gap-x-2 gap-y-1"}`}
        >
          <label className={`text-gray-500 ${isSidebar ? "" : "col-span-2"}`}>From</label>
          <input
            type="date"
            value={filterFromDate}
            onChange={(e) => setFilterFromDate(e.target.value)}
            className={`${
              isPage ? "mobile-input !py-2 !text-xs" : "rounded bg-gray-900 border border-gray-700 px-1.5 py-1 text-gray-200"
            } ${isSidebar ? "w-full" : ""}`}
          />
          <input
            type="time"
            value={filterFromTime}
            onChange={(e) => setFilterFromTime(e.target.value)}
            className={`${
              isPage ? "mobile-input !py-2 !text-xs" : "rounded bg-gray-900 border border-gray-700 px-1.5 py-1 text-gray-200"
            } ${isSidebar ? "w-full" : ""}`}
          />
          <label className={`text-gray-500 ${isSidebar ? "mt-0.5" : "col-span-2"}`}>To</label>
          <input
            type="date"
            value={filterToDate}
            onChange={(e) => setFilterToDate(e.target.value)}
            className={`${
              isPage ? "mobile-input !py-2 !text-xs" : "rounded bg-gray-900 border border-gray-700 px-1.5 py-1 text-gray-200"
            } ${isSidebar ? "w-full" : ""}`}
          />
          <input
            type="time"
            value={filterToTime}
            onChange={(e) => setFilterToTime(e.target.value)}
            className={`${
              isPage ? "mobile-input !py-2 !text-xs" : "rounded bg-gray-900 border border-gray-700 px-1.5 py-1 text-gray-200"
            } ${isSidebar ? "w-full" : ""}`}
          />
        </div>
        <div className={`flex flex-wrap items-center gap-2 ${isPage ? "px-4 pb-2" : ""}`}>
          <button
            type="button"
            onClick={applyFilter}
            disabled={loading || deleting}
            className={
              isPage
                ? "mobile-btn-primary text-[11px] !px-3 !py-2"
                : "text-[10px] px-2 py-1 rounded bg-indigo-700 hover:bg-indigo-600 text-white disabled:opacity-50"
            }
          >
            Apply filter
          </button>
          <button
            type="button"
            onClick={clearFilter}
            disabled={
              loading ||
              deleting ||
              (!filterActive && !filterFromDate && !filterToDate)
            }
            className={
              isPage
                ? "mobile-btn-secondary text-[11px] !px-3 !py-2"
                : "text-[10px] px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-300 disabled:opacity-40"
            }
          >
            Clear filter
          </button>
        </div>
        {filterError ? (
          <p className={`text-[10px] text-red-400 ${isPage ? "px-4" : ""}`}>{filterError}</p>
        ) : null}
        </>
        )}
      </div>
      ) : null}
      <div
        className={`flex-1 min-h-0 overflow-y-auto space-y-2 ${
          isCompact ? "px-0 py-0 space-y-0" : isSidebar ? "px-0 py-2" : isPage ? "px-3 py-3" : "p-2"
        }`}
      >
        {events.length === 0 ? (
          <p className="text-xs text-gray-500 p-2">
            {filterActive ? "No events in this date/time range." : "No events yet for this camera."}
          </p>
        ) : (
          events.map((ev) => {
            const clip = resolveRecordingForEvent(ev, recordings, cameraId);
            const canPlay = Boolean(clip && typeof onPlayClip === "function");
            const isActive =
              canPlay &&
              playingClip &&
              cameraIdsMatch(playingClip.camId, clip.camId) &&
              playingClip.name === clip.name;
            if (isCompact) {
              return (
                <button
                  key={ev.id}
                  type="button"
                  disabled={!canPlay || deleting}
                  onClick={() => {
                    if (!clip) return;
                    onPlayClip({
                      ...clip,
                      camName: cameraName || clip.camName || "",
                    });
                  }}
                  className={`dashboard-event-row ${isActive ? "bg-indigo-500/10" : ""}`}
                >
                  <span className="dashboard-event-icon" aria-hidden>
                    🚶
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-xs font-medium text-gray-200">
                      {formatEventType(ev.event_type)}
                    </span>
                    <span className="block text-[10px] text-gray-500 truncate">
                      {cameraName || "Camera"} · {formatEventTime(ev.ts)}
                    </span>
                  </span>
                </button>
              );
            }

            return (
              <div
                key={ev.id}
                className={`flex gap-1 text-[11px] ${
                  isPage
                    ? `rounded-2xl border shadow-md ${
                        isActive
                          ? "border-indigo-500/60 bg-[#1a2332]"
                          : "border-white/10 bg-gradient-to-r from-[#141c2e] to-[#0c111c]"
                      }`
                    : `rounded-lg border ${
                        isActive
                          ? "border-blue-500/70 bg-[#1e293b]"
                          : "border-gray-800/80 bg-[#111827]"
                      }`
                }`}
              >
                <button
                  type="button"
                  disabled={!canPlay || deleting}
                  onClick={() => {
                    if (!clip) return;
                    onPlayClip({
                      ...clip,
                      camName: cameraName || clip.camName || "",
                    });
                  }}
                  className={`flex-1 min-w-0 text-left px-2 py-1.5 transition-colors ${
                    canPlay && !deleting
                      ? "hover:bg-[#1a2332] cursor-pointer"
                      : "opacity-70 cursor-default"
                  }`}
                  title={
                    canPlay
                      ? "Play recording"
                      : ev.recording_id
                        ? "Recording not ready yet"
                        : "No linked recording"
                  }
                >
                  <div
                    className={
                      isSidebar
                        ? "flex flex-col gap-0.5 items-start"
                        : "flex justify-between gap-2 items-start w-full"
                    }
                  >
                    <span className="font-medium text-indigo-300">{formatEventType(ev.event_type)}</span>
                    <span className={`text-gray-500 ${isSidebar ? "text-[9px]" : "shrink-0"}`}>
                      {formatEventTime(ev.ts)}
                    </span>
                  </div>
                  {clip ? (
                    <p className="text-gray-400 font-mono text-[10px] mt-0.5 truncate">{clip.name}</p>
                  ) : ev.recording_id ? (
                    <p className="text-gray-500 text-[10px] mt-0.5">Recording in progress…</p>
                  ) : null}
                  {typeof ev.person_count === "number" ? (
                    <p className="text-gray-500 text-[10px]">Person count: {ev.person_count}</p>
                  ) : null}
                </button>
                <button
                  type="button"
                  disabled={deleting}
                  onClick={() => deleteEvent(ev)}
                  className="shrink-0 px-2 text-red-400 hover:text-red-300 hover:bg-red-950/30 disabled:opacity-40 self-stretch rounded-r-lg"
                  title="Delete event"
                  aria-label="Delete event"
                >
                  <IconTrash className="w-3.5 h-3.5" />
                </button>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

function AppBottomNav({ tab, onTab, clipCount, eventCount }) {
  const tabs = [
    { id: "live", label: "Live", Icon: IconLive, badge: 0 },
    { id: "clips", label: "Clips", Icon: IconClips, badge: clipCount },
    { id: "events", label: "Events", Icon: IconEvents, badge: eventCount },
  ];
  return (
    <nav
      className="shrink-0 mobile-glass border-t border-white/5 flex justify-around px-2 lg:px-8 pt-2 pb-[max(0.65rem,env(safe-area-inset-bottom))]"
      aria-label="Main navigation"
    >
      {tabs.map((t) => {
        const active = tab === t.id;
        const Icon = t.Icon;
        return (
          <button
            key={t.id}
            type="button"
            onClick={() => onTab(t.id)}
            className={`relative flex flex-col items-center justify-center gap-1 min-w-[4.5rem] py-2 px-3 rounded-2xl transition-all active:scale-95 ${
              active
                ? "text-indigo-200 bg-indigo-500/20 shadow-[0_0_20px_rgba(99,102,241,0.25)]"
                : "text-gray-500"
            }`}
          >
            <span className="relative">
              <Icon className={active ? "w-6 h-6" : "w-5 h-5"} />
              {t.badge > 0 ? (
                <span className="absolute -top-1.5 -right-2 min-w-[1.1rem] h-[1.1rem] px-1 flex items-center justify-center rounded-full bg-indigo-500 text-[9px] font-bold text-white">
                  {t.badge > 99 ? "99+" : t.badge}
                </span>
              ) : null}
            </span>
            <span className={`text-[10px] font-semibold ${active ? "text-indigo-100" : ""}`}>
              {t.label}
            </span>
          </button>
        );
      })}
    </nav>
  );
}

function cameraIdsMatch(a, b) {
  if (a == null || b == null) return false;
  return String(a) === String(b);
}

/** Camera ids may be 0; never use truthiness on id alone. */
function isSetCameraId(id) {
  return id !== null && id !== undefined;
}

function recordingActiveForCam(recordingById, camId) {
  if (!recordingById || camId == null) return false;
  if (recordingById[camId] === true) return true;
  const n = Number(camId);
  if (!Number.isNaN(n) && recordingById[n] === true) return true;
  return recordingById[String(camId)] === true;
}

function recordingKey(r) {
  return `${r.camId}-${r.name}`;
}

/** Clip player — stable src (no remount loops); user taps play (no autoplay flicker). */
function ClipPlayer({ url, camId, filename, onRepaired }) {
  const videoRef = useRef(null);
  const autoRepairRef = useRef(false);
  const mobilePrepareRef = useRef(false);
  const [error, setError] = useState("");
  const [repairing, setRepairing] = useState(false);
  const [srcVersion, setSrcVersion] = useState(0);

  const videoSrc =
    url && srcVersion > 0
      ? `${url}${url.includes("?") ? "&" : "?"}v=${srcVersion}`
      : url || "";

  const repairClip = useCallback(async () => {
    if (!isSetCameraId(camId) || !filename) return false;
    setRepairing(true);
    setError("");
    try {
      const res = await fetch(
        `${API}/recordings/${encodeURIComponent(String(camId))}/files/${encodeURIComponent(filename)}/finalize-mobile`,
        { method: "POST" }
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const detail =
          typeof body.detail === "string"
            ? body.detail
            : res.status === 422
              ? "This clip is incomplete or corrupt and cannot be repaired. Delete it and record again."
              : "Repair failed";
        setError(detail);
        return false;
      }
      setSrcVersion((v) => v + 1);
      if (typeof onRepaired === "function") onRepaired();
      return true;
    } catch (e) {
      setError(String(e));
      return false;
    } finally {
      setRepairing(false);
    }
  }, [camId, filename, onRepaired]);

  useEffect(() => {
    mobilePrepareRef.current = false;
  }, [camId, filename]);

  useEffect(() => {
    if (!preferNativeHlsPlayback() || !isSetCameraId(camId) || !filename) return undefined;
    if (mobilePrepareRef.current) return undefined;
    mobilePrepareRef.current = true;
    let cancelled = false;
    (async () => {
      setRepairing(true);
      try {
        const res = await fetch(
          `${API}/recordings/${encodeURIComponent(String(camId))}/files/${encodeURIComponent(filename)}/finalize-mobile`,
          { method: "POST" }
        );
        if (!cancelled && res.ok) {
          setSrcVersion((v) => Math.max(1, v + 1));
          if (typeof onRepaired === "function") onRepaired();
        }
      } catch {
        /* fall through to normal load + error repair */
      } finally {
        if (!cancelled) setRepairing(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [camId, filename, onRepaired]);

  useEffect(() => {
    autoRepairRef.current = false;
    setError("");
    setSrcVersion(0);
  }, [url, camId, filename]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !videoSrc) return undefined;

    const onError = () => {
      if (!autoRepairRef.current && isSetCameraId(camId) && filename) {
        autoRepairRef.current = true;
        repairClip();
        return;
      }
      const code = video.error?.code;
      if (code === 4) {
        setError("This clip cannot be played. Try Convert clip or record again.");
      } else {
        setError("Could not play clip.");
      }
    };

    video.addEventListener("error", onError);
    return () => video.removeEventListener("error", onError);
  }, [videoSrc, camId, filename, repairClip]);

  return (
    <div>
      <video
        ref={videoRef}
        src={videoSrc}
        controls
        playsInline
        preload={preferNativeHlsPlayback() ? "metadata" : "auto"}
        className="w-full rounded bg-black max-h-[75vh]"
      />
      {repairing ? (
        <p className="text-[10px] text-gray-400 mt-1">Preparing clip for playback…</p>
      ) : null}
      {error ? (
        <div className="mt-2 space-y-2">
          <p className="text-xs text-red-400">{error}</p>
          <button
            type="button"
            disabled={repairing}
            onClick={repairClip}
            className="text-xs px-2 py-1 rounded bg-indigo-700 hover:bg-indigo-600 disabled:opacity-50 text-white"
          >
            {repairing ? "Converting clip…" : "Convert clip"}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function RecordingPlayModal({ playing, cameras, onClose, onRefresh }) {
  if (!playing) return null;
  const url = recordingFileUrl(
    playing.camId,
    playing.name,
    playing.edgeBaseUrl || edgeBaseUrlForCamera(cameras, playing.camId),
    cameras,
    { forPlayback: true, viaController: true }
  );
  return (
    <div
      className="fixed inset-0 z-[60] flex flex-col justify-end lg:justify-center items-stretch lg:items-center bg-black/90 lg:p-6"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        className="w-full lg:max-w-4xl shadow-xl border border-white/10 bg-[#0b1220] overflow-hidden rounded-t-3xl lg:rounded-2xl max-h-[92dvh] flex flex-col pb-[max(0.75rem,env(safe-area-inset-bottom))] lg:pb-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-between items-start gap-2 shrink-0 mobile-glass border-b border-white/5 px-4 py-3">
          <div className="min-w-0">
            <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-indigo-300/90">Playback</p>
            <p className="font-medium text-gray-100 truncate text-base">{playing.camName || "Clip"}</p>
            <p className="font-mono text-gray-500 truncate text-[11px] mt-0.5">{playing.name}</p>
          </div>
          <button type="button" onClick={onClose} className="p-2 rounded-full border border-white/10 text-gray-300 active:bg-white/10 shrink-0" aria-label="Close">
            <IconClose className="w-5 h-5" />
          </button>
        </div>
        <div className="flex-1 min-h-0 p-3 lg:p-4">
          <ClipPlayer url={url} camId={playing.camId} filename={playing.name} onRepaired={onRefresh} />
        </div>
      </div>
    </div>
  );
}

function IconDownload({ className = "w-3.5 h-3.5" }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M12 3v12M7 10l5 5 5-5M5 21h14" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconTrash({ className = "w-3.5 h-3.5" }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M4 7h16M9 7V5a1 1 0 011-1h4a1 1 0 011 1v2M10 11v6M14 11v6M6 7l1 12a1 1 0 001 1h8a1 1 0 001-1l1-12" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconLive({ className = "w-5 h-5" }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden>
      <rect x="3" y="5" width="14" height="12" rx="2" />
      <path d="M17 9l4-2v10l-4-2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconClips({ className = "w-5 h-5" }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M10 9l5 3-5 3V9z" fill="currentColor" stroke="none" />
    </svg>
  );
}

function IconEvents({ className = "w-5 h-5" }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden>
      <path d="M6 8h12M6 12h8M6 16h10" strokeLinecap="round" />
      <rect x="4" y="4" width="16" height="16" rx="2" />
    </svg>
  );
}

function IconSettings({ className = "w-5 h-5" }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden>
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 010 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.991a7.723 7.723 0 010-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28z"
      />
      <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
    </svg>
  );
}

function IconPlay({ className = "w-8 h-8" }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M9 6.5v11l9-5.5-9-5.5z" />
    </svg>
  );
}

function AppHeader({
  cameraName,
  streamLabel,
  personCount = 0,
  recording,
  onManage,
  onDetect,
  detecting,
}) {
  return (
    <header className="shrink-0 mobile-glass border-b border-white/5 pt-[max(0.5rem,env(safe-area-inset-top))] px-4 lg:px-6 pb-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-indigo-300/90">
            Vigilance
          </p>
          <h1 className="text-lg font-semibold text-white truncate leading-tight">
            {cameraName || "Live view"}
          </h1>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            disabled={detecting}
            onClick={onDetect}
            className="text-xs font-medium px-3 py-2 rounded-full bg-indigo-600 text-white active:bg-indigo-500 disabled:opacity-50"
          >
            {detecting ? "…" : "Find"}
          </button>
          <button
            type="button"
            onClick={onManage}
            className="flex items-center gap-1.5 text-xs font-medium px-3 py-2 rounded-full border border-white/10 text-gray-200 active:bg-white/10"
            aria-label="Manage cameras"
          >
            <IconSettings className="w-4 h-4 shrink-0" />
            <span>Manage</span>
          </button>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 mt-2.5">
        <span
          className={`inline-flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-1 rounded-full border ${
            streamLabel === "NO SIGNAL"
              ? "border-red-500/40 text-red-300 bg-red-500/10"
              : streamLabel === "LIVE"
                ? "border-emerald-500/40 text-emerald-300 bg-emerald-500/10"
                : "border-amber-500/40 text-amber-300 bg-amber-500/10"
          }`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              streamLabel === "NO SIGNAL"
                ? "bg-red-400"
                : streamLabel === "LIVE"
                  ? "bg-emerald-400 animate-pulse"
                  : "bg-amber-400"
            }`}
          />
          {streamLabel || "LIVE"}
        </span>
        {recording ? (
          <span className="inline-flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-1 rounded-full border border-red-500/50 text-red-200 bg-red-500/15">
            <span className="h-1.5 w-1.5 rounded-full bg-red-500 animate-pulse" />
            Recording
          </span>
        ) : null}
        {personCount > 0 ? (
          <span className="text-[11px] font-medium px-2.5 py-1 rounded-full border border-blue-500/40 text-blue-200 bg-blue-500/10">
            Person detected ({personCount})
          </span>
        ) : null}
      </div>
    </header>
  );
}

function RecordingsTimeline({
  recordings,
  cameras,
  activeCameraName,
  loading,
  listError = "",
  hasCameras,
  onRefresh,
  onDelete,
  onDeleteAll,
  playing,
  onPlayingChange,
  variant = "dock",
  className = "",
}) {
  const [playingLocal, setPlayingLocal] = useState(null);
  const clipPlaying = playing !== undefined ? playing : playingLocal;
  const setPlaying =
    typeof onPlayingChange === "function" ? onPlayingChange : setPlayingLocal;
  const [deleting, setDeleting] = useState(false);
  const isPage = variant === "page";
  const isDashboard = variant === "dashboard";
  const cardLayout = isPage || isDashboard;

  const clipUrl = (r, { forPlayback = false, viaController } = {}) =>
    recordingFileUrl(
      r.camId,
      r.name,
      r.edgeBaseUrl || edgeBaseUrlForCamera(cameras, r.camId),
      cameras,
      {
        forPlayback,
        viaController:
          viaController !== undefined
            ? viaController
            : forPlayback || preferNativeHlsPlayback(),
      }
    );

  return (
    <section
        className={`flex flex-col ${
          isPage || isDashboard
            ? "flex-1 min-h-0 bg-[#0b1220]"
            : "shrink-0 border-t border-gray-800 bg-[#070c16] max-h-[200px] min-h-[120px]"
        } ${className}`}
        aria-label="Recordings timeline"
      >
        {isPage && !isDashboard ? (
          <MobilePageHeader
            title="Clips"
            subtitle={`${activeCameraName || "No camera selected"}${
              recordings.length > 0 ? ` · ${recordings.length}` : ""
            }`}
            actions={
              <>
                {recordings.length > 0 && typeof onDeleteAll === "function" ? (
                  <button
                    type="button"
                    disabled={loading || deleting}
                    onClick={async () => {
                      setDeleting(true);
                      try {
                        const ok = await onDeleteAll();
                        if (ok) setPlaying(null);
                      } finally {
                        setDeleting(false);
                      }
                    }}
                    className="mobile-btn-danger text-[11px] !px-2.5 !py-1.5"
                  >
                    Clear
                  </button>
                ) : null}
                <button
                  type="button"
                  disabled={loading || deleting || !hasCameras}
                  onClick={onRefresh}
                  className="mobile-btn-secondary text-[11px] !px-2.5 !py-1.5"
                >
                  {loading ? "…" : "Sync"}
                </button>
              </>
            }
          />
        ) : isDashboard ? (
          <div className="shrink-0 flex flex-wrap items-center justify-between gap-3 px-4 lg:px-6 py-3 border-b border-white/[0.06] bg-[#0a0f18]/60">
            <p className="text-sm text-gray-300">
              <span className="font-semibold text-white">{recordings.length}</span> recording
              {recordings.length === 1 ? "" : "s"} found
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <select className="dashboard-select text-xs w-auto" defaultValue="all" aria-label="Recording type">
                <option value="all">All recordings</option>
                <option value="motion">Motion only</option>
              </select>
              <select className="dashboard-select text-xs w-auto" defaultValue="newest" aria-label="Sort order">
                <option value="newest">Newest first</option>
                <option value="oldest">Oldest first</option>
              </select>
              {recordings.length > 0 && typeof onDeleteAll === "function" ? (
                <button
                  type="button"
                  disabled={loading || deleting}
                  onClick={async () => {
                    setDeleting(true);
                    try {
                      const ok = await onDeleteAll();
                      if (ok) setPlaying(null);
                    } finally {
                      setDeleting(false);
                    }
                  }}
                  className="dashboard-btn-ghost text-xs text-red-300"
                >
                  Delete all
                </button>
              ) : null}
              <button
                type="button"
                disabled={loading || deleting || !hasCameras}
                onClick={onRefresh}
                className="dashboard-btn-secondary text-xs"
              >
                {loading ? "…" : "Refresh"}
              </button>
            </div>
          </div>
        ) : (
        <div
          className={`shrink-0 flex items-center justify-between gap-2 border-b border-white/5 ${
            isPage ? "px-4 py-3 mobile-glass" : "px-3 py-2 border-gray-800"
          }`}
        >
          <div className="min-w-0">
            <h2 className={`font-semibold text-gray-100 ${isPage ? "text-base" : "text-xs"}`}>
              Clips
            </h2>
            <p
              className={`text-gray-500 truncate ${isPage ? "text-xs mt-0.5" : "text-[10px]"}`}
              title={activeCameraName || ""}
            >
              {activeCameraName || "No camera selected"}
              {recordings.length > 0 ? ` · ${recordings.length}` : ""}
            </p>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {recordings.length > 0 && typeof onDeleteAll === "function" ? (
              <button
                type="button"
                disabled={loading || deleting}
                onClick={async () => {
                  setDeleting(true);
                  try {
                    const ok = await onDeleteAll();
                    if (ok) setPlaying(null);
                  } finally {
                    setDeleting(false);
                  }
                }}
                className="text-[10px] px-2 py-1 rounded border border-red-900/60 text-red-300 hover:bg-red-950/40 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Delete all
              </button>
            ) : null}
            <button
              type="button"
              disabled={loading || deleting || !hasCameras}
              onClick={onRefresh}
              className="text-[10px] px-2 py-1 rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed text-gray-200"
            >
              {loading ? "Loading…" : "Refresh"}
            </button>
          </div>
        </div>
        )}
        <div
          className={`flex-1 min-h-0 px-3 py-2 ${
            isPage || isDashboard ? "overflow-y-auto overflow-x-hidden" : "overflow-x-auto overflow-y-hidden"
          }`}
        >
          {!hasCameras ? (
            <p className="text-xs text-gray-500 py-2">Add a camera to see recordings.</p>
          ) : listError ? (
            <p className="text-xs text-amber-400/90 py-2" role="alert">
              {listError}
            </p>
          ) : loading && recordings.length === 0 ? (
            <p className="text-xs text-gray-500 py-2">Loading clips…</p>
          ) : recordings.length === 0 ? (
            <p className="text-xs text-gray-500 py-2">No clips for this camera yet. Tap Refresh to sync from cameras.</p>
          ) : (
            <ul
              className={
                isPage || isDashboard
                  ? "grid grid-cols-2 gap-3 pb-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 p-4 lg:p-6"
                  : "flex gap-3 pb-1"
              }
            >
              {recordings.map((r) => {
                const url = clipUrl(r, { forPlayback: true });
                const downloadUrl = clipUrl(r, { forPlayback: false, viaController: false });
                const key = recordingKey(r);
                const isPlaying =
                  clipPlaying != null &&
                  cameraIdsMatch(clipPlaying.camId, r.camId) &&
                  clipPlaying.name === r.name;
                return (
                  <li
                    key={key}
                    className={`flex flex-col overflow-hidden ${
                      cardLayout
                        ? "rounded-2xl border border-white/10 bg-gradient-to-b from-[#151d2e] to-[#0c111c] shadow-lg"
                        : "rounded-lg border p-1.5 gap-1 shrink-0 w-[10.5rem]"
                    } ${
                      isPlaying
                        ? cardLayout
                          ? "ring-2 ring-indigo-500/80 border-indigo-500/50"
                          : "border-blue-500/70 bg-[#111827]"
                        : cardLayout
                          ? ""
                          : "border-gray-800 bg-[#111827]/60"
                    }`}
                  >
                    <div
                      className={`relative w-full aspect-video overflow-hidden bg-black ${
                        cardLayout ? "" : "rounded border border-gray-700"
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() =>
                          setPlaying({
                            ...r,
                            camName: activeCameraName || r.camName || "",
                          })
                        }
                        className="absolute inset-0 w-full h-full group"
                        title="Play clip"
                      >
                        <RecordingThumbnail
                          camId={r.camId}
                          name={r.name}
                          videoFallbackSrc={url}
                          className="w-full h-full object-cover pointer-events-none"
                        />
                        {isDashboard ? (
                          <span className="absolute top-2 left-2 z-10 text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded bg-indigo-600/90 text-white">
                            Motion
                          </span>
                        ) : null}
                        {cardLayout ? (
                          <span className="absolute inset-0 flex items-center justify-center bg-black/25 group-active:bg-black/45 transition-colors">
                            <span className="flex h-11 w-11 items-center justify-center rounded-full bg-white/90 text-[#0b1220] shadow-lg">
                              <IconPlay className="w-5 h-5 ml-0.5" />
                            </span>
                          </span>
                        ) : null}
                      </button>
                      <div
                        className={`absolute flex gap-1 z-10 ${
                          cardLayout ? "top-2 right-2" : "top-1 right-1"
                        }`}
                      >
                        <a
                          href={downloadUrl}
                          download={r.name}
                          onClick={(e) => e.stopPropagation()}
                          className="p-1 rounded bg-black/75 text-gray-100 hover:bg-black/90 hover:text-white border border-gray-600/80"
                          title="Download"
                          aria-label="Download clip"
                        >
                          <IconDownload />
                        </a>
                        <button
                          type="button"
                          disabled={deleting}
                          onClick={async (e) => {
                            e.stopPropagation();
                            if (isPlaying) setPlaying(null);
                            setDeleting(true);
                            try {
                              const ok = await onDelete(r.camId, r.name);
                              if (!ok && isPlaying) setPlaying(r);
                            } finally {
                              setDeleting(false);
                            }
                          }}
                          className="p-1 rounded bg-black/75 text-red-300 hover:bg-red-950/90 hover:text-red-200 border border-red-900/50 disabled:opacity-40"
                          title="Delete"
                          aria-label="Delete clip"
                        >
                          <IconTrash />
                        </button>
                      </div>
                    </div>
                    <div className={`min-w-0 ${cardLayout ? "p-2.5 space-y-0.5" : "text-[10px] px-0.5"}`}>
                      <p className={cardLayout ? "text-xs text-gray-200 font-medium" : "text-gray-500 truncate"}>
                        {formatTime(r.mtime)}
                      </p>
                      <p className={cardLayout ? "text-[10px] text-gray-500" : "text-gray-600"}>
                        {formatBytes(r.size)}
                      </p>
                      {!cardLayout ? (
                        <p className="font-mono text-gray-600 truncate" title={r.name}>
                          {r.name}
                        </p>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </section>
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

function LiveTile({
  cam,
  recording,
  recordingMode,
  manualRecording,
  onManualToggle,
  faces,
  personCount,
  detectionSystem,
  motionClipCountdown,
  overlayDelayMs,
  layout = "default",
}) {
  const wrapRef = useRef(null);
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const hlsRef = useRef(null);
  const overlaySync = detectionOverlaySyncEnabled();
  const backendInferenceDelayMs =
    typeof detectionSystem?.inference_delay_ms === "number"
      ? detectionSystem.inference_delay_ms
      : 0;
  // Backend already runs inference on delayed RTSP frames; do not add the same delay again in UI.
  const baseOverlayDelay =
    backendInferenceDelayMs > 0
      ? 0
      : typeof overlayDelayMs === "number" && overlayDelayMs >= 0
        ? overlayDelayMs
        : detectionOverlayDelayMs();
  const [useWebRtc, setUseWebRtc] = useState(preferWebRtcLive);
  const synced = useOverlaySyncedDetections(faces, personCount, {
    videoRef,
    hlsRef,
    baseDelayMs: baseOverlayDelay,
    enabled: overlaySync && !useWebRtc,
  });
  const rawFaces = Array.isArray(faces) ? faces : [];
  const drawFaces = overlaySync && !useWebRtc ? synced.faces : rawFaces;
  const drawPersonCount =
    overlaySync && !useWebRtc
      ? synced.personCount
      : typeof personCount === "number"
        ? personCount
        : countPersonDetections(rawFaces);
  const [scale, setScale] = useState(1);
  const [streamError, setStreamError] = useState("");
  const [edgeHint, setEdgeHint] = useState("");
  const rtspSource = cameraRtspUrl(cam);
  const streamUrl = streamUrlForCamera(cam);
  const hlsProxyUrl = hlsPlaylistUrlForCamera(cam, true);
  const hlsDirectUrl = hlsPlaylistUrlForCamera(cam, false);
  const [hlsUrl, setHlsUrl] = useState(hlsProxyUrl);
  const showManual =
    recordingMode === "off" && cam.edge_base_url && typeof onManualToggle === "function";
  const isHero = layout === "hero";
  const isHeroShell = layout === "heroShell";
  const isThumb = layout === "thumb";
  const heroLayout = (isHero || isHeroShell) && !isThumb;

  const zoomIn = useCallback(() => setScale((s) => Math.min(4, s * 1.15)), []);
  const zoomOut = useCallback(() => setScale((s) => Math.max(0.5, s / 1.15)), []);

  const goFs = () => {
    const el = wrapRef.current;
    if (!el?.requestFullscreen) return;
    el.requestFullscreen().catch(() => {});
  };

  useEffect(() => {
    setUseWebRtc(preferWebRtcLive());
    setStreamError("");
    setEdgeHint("");
    setHlsUrl(hlsProxyUrl);
    setScale(1);
  }, [cam.id, rtspSource, hlsProxyUrl]);

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
    const rtsp = cameraRtspUrl(cam);
    if (rtsp) {
      try {
        // Edge is optional metadata (e.g. future Pi 4) while video is direct LAN RTSP (VIGI).
        // Do not block the tile when the edge is offline but the camera IP is different.
        if (new URL(String(edge).trim()).hostname !== new URL(rtsp).hostname) {
          return undefined;
        }
      } catch {
        /* invalid URL — still try edge health */
      }
    }
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
  }, [cam.edge_base_url, rtspSource]);

  useEffect(() => {
    if (useWebRtc) return undefined;
    const video = videoRef.current;
    if (!video) return undefined;
    let hls;
    let cancelled = false;
    let triedDirectHls = false;

    const failToWebRtc = () => {
      if (cancelled) return;
      if (!preferWebRtcLive()) {
        setStreamError("HLS playback failed.");
        return;
      }
      setStreamError("");
      setUseWebRtc(true);
      fetch(`${API}/cameras/${cam.id}/stream_health?probe_rtsp=false`)
        .then((r) => (r.ok ? r.json() : null))
        .then((h) => {
          if (!h || cancelled) return;
          if (Array.isArray(h.summary) && h.summary.length) {
            setEdgeHint(h.summary.join(" "));
          }
        })
        .catch(() => {});
    };

    const onVideoError = () => {
      if (cancelled) return;
      if (!triedDirectHls && hlsUrl === hlsProxyUrl && hlsDirectUrl !== hlsProxyUrl) {
        triedDirectHls = true;
        setHlsUrl(hlsDirectUrl);
        return;
      }
      failToWebRtc();
    };

    const onPlaying = () => {
      setStreamError("");
      setEdgeHint("");
    };

    video.addEventListener("error", onVideoError);
    video.addEventListener("playing", onPlaying);

    const useNative = preferNativeHlsPlayback() && canPlayNativeHls(video);
    const startPlay = () => {
      const p = video.play();
      if (p && typeof p.catch === "function") p.catch(() => {});
    };

    if (useNative) {
      hlsRef.current = null;
      video.playsInline = true;
      video.muted = true;
      video.autoplay = true;
      video.setAttribute("playsinline", "");
      video.setAttribute("webkit-playsinline", "");
      video.src = hlsUrl;
      video.load();
      video.addEventListener("loadedmetadata", startPlay);
      startPlay();
    } else if (Hls.isSupported()) {
      hls = new Hls({
        lowLatencyMode: false,
        maxLiveSyncPlaybackRate: 1.5,
        enableWorker: !preferNativeHlsPlayback(),
        manifestLoadingTimeOut: 20000,
        manifestLoadingMaxRetry: 6,
        fragLoadingTimeOut: 20000,
        fragLoadingMaxRetry: 6,
      });
      hlsRef.current = hls;
      hls.loadSource(hlsUrl);
      hls.attachMedia(video);
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (!data.fatal || cancelled) return;
        if (
          data.type === Hls.ErrorTypes.NETWORK_ERROR &&
          !triedDirectHls &&
          hlsUrl === hlsProxyUrl &&
          hlsDirectUrl !== hlsProxyUrl
        ) {
          triedDirectHls = true;
          setHlsUrl(hlsDirectUrl);
          return;
        }
        hls?.destroy();
        failToWebRtc();
      });
    } else if (canPlayNativeHls(video)) {
      hlsRef.current = null;
      video.src = hlsUrl;
    } else {
      setStreamError("HLS not supported in this browser — using WebRTC reader.");
      setUseWebRtc(true);
      return undefined;
    }

    return () => {
      cancelled = true;
      video.removeEventListener("error", onVideoError);
      video.removeEventListener("playing", onPlaying);
      if (useNative) {
        video.removeEventListener("loadedmetadata", startPlay);
      }
      hlsRef.current = null;
      if (hls) hls.destroy();
      video.removeAttribute("src");
      video.load();
    };
  }, [cam.id, rtspSource, hlsUrl, hlsProxyUrl, hlsDirectUrl, useWebRtc]);

  useEffect(() => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    const container = wrapRef.current;
    if (!video || !canvas || useWebRtc) return undefined;
    const ctx = canvas.getContext("2d");
    if (!ctx) return undefined;

    const paint = () => {
      const cw = container?.clientWidth || video.clientWidth;
      const ch = container?.clientHeight || video.clientHeight;
      if (cw < 2 || ch < 2) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 3);
      canvas.width = Math.round(cw * dpr);
      canvas.height = Math.round(ch * dpr);
      canvas.style.width = `${cw}px`;
      canvas.style.height = `${ch}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cw, ch);
      const vw = video.videoWidth;
      const vh = video.videoHeight;
      if (!vw || !vh) return;
      const layout = computeObjectContainLayout(cw, ch, vw, vh);
      if (!layout) return;
      const lineW = heroLayout ? 3 : 2;
      ctx.lineWidth = lineW;
      ctx.font = heroLayout
        ? "12px ui-monospace, system-ui, sans-serif"
        : "11px ui-monospace, system-ui, sans-serif";
      const faceList = personDetections(drawFaces);
      for (const f of faceList) {
        const x = layout.offsetX + Number(f.x) * layout.videoW * layout.scale;
        const y = layout.offsetY + Number(f.y) * layout.videoH * layout.scale;
        const w = Number(f.w) * layout.videoW * layout.scale;
        const h = Number(f.h) * layout.videoH * layout.scale;
        ctx.strokeStyle = "rgba(59, 130, 246, 0.95)";
        ctx.strokeRect(x, y, w, h);
        const label = formatDetectionLabel(f);
        if (!label) continue;
        const padX = 4;
        const padY = 2;
        const textY = Math.max(12, y - 4);
        const metrics = ctx.measureText(label);
        const boxW = metrics.width + padX * 2;
        const boxH = heroLayout ? 16 : 14;
        const labelX = Math.min(Math.max(0, x), cw - boxW);
        const labelTop = Math.max(0, textY - boxH + 2);
        ctx.fillStyle = "rgba(0, 0, 0, 0.72)";
        ctx.fillRect(labelX, labelTop, boxW, boxH);
        ctx.fillStyle = "rgba(96, 165, 250, 1)";
        ctx.fillText(label, labelX + padX, labelTop + boxH - padY - 2);
      }
    };

    paint();
    video.addEventListener("loadedmetadata", paint);
    video.addEventListener("loadeddata", paint);
    video.addEventListener("playing", paint);
    video.addEventListener("timeupdate", paint);
    const ro = new ResizeObserver(paint);
    if (container) ro.observe(container);
    ro.observe(video);
    window.addEventListener("orientationchange", paint);
    const overlayTick = window.setInterval(paint, 150);
    return () => {
      video.removeEventListener("loadedmetadata", paint);
      video.removeEventListener("loadeddata", paint);
      video.removeEventListener("playing", paint);
      video.removeEventListener("timeupdate", paint);
      ro.disconnect();
      window.removeEventListener("orientationchange", paint);
      window.clearInterval(overlayTick);
    };
  }, [drawFaces, useWebRtc, cam.id, heroLayout]);

  return (
    <div
      className={`flex flex-col min-h-0 h-full ${
        isThumb
          ? "rounded-none p-0 bg-transparent"
          : isHeroShell
            ? "rounded-none p-0 bg-transparent ring-0 shadow-none overflow-hidden h-full w-full"
            : heroLayout
              ? "rounded-2xl p-0 bg-[#0a0f18] ring-1 ring-white/5 shadow-2xl overflow-hidden"
              : "rounded-xl p-2 bg-[#111827]"
      }`}
    >
      {!isThumb && !heroLayout && !isHeroShell ? (
      <div className="flex justify-between items-center text-xs mb-1 gap-2">
        <span className="truncate font-medium">{cam.name}</span>
        <div className="flex items-center gap-1.5 shrink-0">
          <span
            className={
              edgeHint ? "text-red-400" : useWebRtc ? "text-green-400" : "text-amber-400"
            }
          >
            {edgeHint ? "NO SIGNAL" : useWebRtc ? "LIVE" : "HLS"}
          </span>
        </div>
      </div>
      ) : null}
      {!isThumb && !heroLayout && !isHeroShell && edgeHint ? (
        <p className="text-[10px] text-amber-400/95 mb-1 leading-snug">
          {edgeHint}
          {rtspSource ? (
            <>
              {" "}
              RTSP: <span className="font-mono text-gray-300">{rtspSource}</span>
            </>
          ) : null}
        </p>
      ) : null}
      <div
        ref={wrapRef}
        className={`relative flex-1 bg-black overflow-hidden touch-none ${
          isThumb
            ? "rounded-none min-h-0 h-full"
            : isHeroShell
              ? "min-h-0 flex-1 h-full rounded-none"
              : heroLayout
                ? "min-h-0 flex-1 rounded-none lg:min-h-[min(62vh,680px)]"
                : `rounded-lg min-h-[100px] max-h-[200px]`
        }`}
      >
        {heroLayout && !isHeroShell ? (
          <>
            <div className="absolute inset-x-0 top-0 h-14 mobile-video-gradient-top pointer-events-none z-[5]" />
            <div className="absolute inset-x-0 bottom-0 h-24 mobile-video-gradient-bottom pointer-events-none z-[5]" />
          </>
        ) : null}
        {recording && !isHeroShell ? (
          <div
            className={`absolute z-20 flex items-center gap-1.5 ${
              heroLayout ? "top-3 right-3" : "top-2 right-2"
            }`}
            title={
              motionClipCountdown
                ? `Recording — ${motionClipCountdown} remaining`
                : "Recording"
            }
            aria-label={
              motionClipCountdown
                ? `Recording, ${motionClipCountdown} remaining`
                : "Recording"
            }
          >
            <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-red-600 shadow-lg ring-2 ring-white/90 animate-pulse" />
            {motionClipCountdown ? (
              <span className="text-[11px] font-mono font-semibold tabular-nums text-white bg-black/55 backdrop-blur-sm px-1.5 py-0.5 rounded-md border border-white/10">
                {motionClipCountdown}
              </span>
            ) : null}
          </div>
        ) : null}
        <div
          className="w-full h-full origin-center transition-transform duration-75"
          style={{ transform: `scale(${scale})` }}
        >
          {useWebRtc ? (
            <div className={`relative w-full h-full ${isHeroShell ? "min-h-full" : "min-h-[140px]"}`}>
              <iframe
                title={cam.name}
                src={streamUrl}
                className={`w-full h-full border-0 bg-black pointer-events-none ${
                  isHeroShell ? "min-h-full" : "min-h-[140px]"
                }`}
                allow="autoplay; fullscreen"
                sandbox="allow-scripts allow-same-origin allow-autoplay allow-fullscreen"
              />
              <PersonBoxesOverlay
                faces={drawFaces}
                containerRef={wrapRef}
                assumedAspect={{ w: 16, h: 9 }}
              />
              {edgeHint && !personDetections(drawFaces).length ? (
                <div className="pointer-events-none absolute inset-0 z-30 flex flex-col items-center justify-center gap-2 p-3 text-center bg-black/35">
                  <p className="text-[11px] text-amber-200 max-w-[min(100%,24rem)] leading-snug">{edgeHint}</p>
                  <p className="text-[10px] text-gray-400 max-w-[min(100%,22rem)] leading-snug">
                    WebRTC reader — fix edge / MediaMTX (:8889), or set{" "}
                    <span className="font-mono text-gray-300">VITE_LIVE_WEBRTC=0</span> in{" "}
                    <span className="font-mono text-gray-300">.env</span> and restart Vite for HLS.
                  </p>
                </div>
              ) : null}
            </div>
          ) : (
            <div className="relative w-full h-full min-h-[140px] bg-black">
              <video
                ref={videoRef}
                className={`absolute inset-0 w-full h-full bg-black ${
                  isHeroShell ? "object-cover" : "object-contain"
                }`}
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
        {!isThumb && !isHeroShell ? (
        <div
          className={`absolute z-10 pointer-events-auto flex items-center gap-1 ${
            heroLayout
              ? "bottom-3 left-1/2 -translate-x-1/2 mobile-glass rounded-full border border-white/10 px-2 py-1.5 shadow-lg"
              : "bottom-1 left-1 right-1 flex-wrap"
          }`}
        >
          {showManual ? (
            <button
              type="button"
              onClick={onManualToggle}
              className={`rounded-full px-2.5 py-1 text-[10px] font-semibold shrink-0 ${
                manualRecording
                  ? "bg-red-600 text-white"
                  : heroLayout
                    ? "bg-white/10 text-gray-100"
                    : "bg-gray-700 text-gray-100 hover:bg-gray-600"
              }`}
              title={manualRecording ? "Stop manual recording" : "Start manual recording"}
            >
              {manualRecording ? "Stop" : "Rec"}
            </button>
          ) : null}
          <button
            type="button"
            onClick={zoomOut}
            className={`rounded-full text-white active:bg-white/20 ${
              heroLayout ? "w-8 h-8 text-sm" : "bg-black/70 px-2 py-0.5 text-[10px] hover:bg-black/90"
            }`}
          >
            −
          </button>
          <button
            type="button"
            onClick={zoomIn}
            className={`rounded-full text-white active:bg-white/20 ${
              heroLayout ? "w-8 h-8 text-sm" : "bg-black/70 px-2 py-0.5 text-[10px] hover:bg-black/90"
            }`}
          >
            +
          </button>
          <button
            type="button"
            onClick={() => setScale(1)}
            className={`rounded-full text-white active:bg-white/20 ${
              heroLayout ? "w-8 h-8 text-[10px] font-medium" : "bg-black/70 px-2 py-0.5 text-[10px] hover:bg-black/90"
            }`}
          >
            1×
          </button>
          <button
            type="button"
            onClick={goFs}
            className={`rounded-full text-white active:bg-white/20 ${
              heroLayout ? "w-8 h-8 text-[10px] font-medium" : "bg-black/70 px-2 py-0.5 text-[10px] hover:bg-black/90"
            }`}
          >
            ⛶
          </button>
        </div>
        ) : null}
      </div>
      {!isThumb && !heroLayout && !isHeroShell ? (
      <p
        className="hidden sm:block text-[10px] text-gray-400 mt-1 font-mono break-all leading-snug"
        title={useWebRtc ? "WebRTC reader (low latency)" : "HLS playlist for video + synced overlay"}
      >
        {useWebRtc ? streamUrl : hlsUrl}
      </p>
      ) : null}
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
  const [recordingsListError, setRecordingsListError] = useState("");
  const [form, setForm] = useState({
    recording_mode: "motion",
    pre_record_seconds: 10,
    post_record_seconds: 50,
    quality: "medium",
    flip_180: false,
  });
  const [connectionForm, setConnectionForm] = useState({
    name: "",
    url: "",
    edge_base_url: "",
  });
  const [saving, setSaving] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [showDebugPanel, setShowDebugPanel] = useState(false);
  /** Which camera drives the bottom clips timeline (click a live tile or sidebar entry). */
  const [activeCameraId, setActiveCameraId] = useState(null);
  /** Manual recording on edge cameras with recording_mode === "off" (cam id → active). */
  const [manualRecordingById, setManualRecordingById] = useState({});
  const [edgeRtspOverrides, setEdgeRtspOverrides] = useState({});
  /** Phase 1: controller `/ws/detections` → per-camera inference + person debug */
  const [detectionsById, setDetectionsById] = useState({});
  const [detectionWsOpen, setDetectionWsOpen] = useState(false);
  const [detectionSystem, setDetectionSystem] = useState(null);
  const [mainTab, setMainTab] = useState("live");
  const [devicesView, setDevicesView] = useState("grid");
  const [deviceDetailId, setDeviceDetailId] = useState(null);
  const [deviceDetailTab, setDeviceDetailTab] = useState("general");
  const [playingClip, setPlayingClip] = useState(null);
  /** Person-detected events today for Camera insights (active camera). */
  const [insightsPeopleToday, setInsightsPeopleToday] = useState(0);
  const [todayEvents, setTodayEvents] = useState([]);
  const [liveSessionStarted, setLiveSessionStarted] = useState(() => new Date().toISOString());
  const [camerasLoadError, setCamerasLoadError] = useState("");

  const load = useCallback(async () => {
    try {
      setCamerasLoadError("");
      const res = await fetch(`${API}/cameras`);
      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try {
          msg = await readApiError(res);
        } catch {
          /* keep msg */
        }
        setCamerasLoadError(msg);
        setCams([]);
        return;
      }
      const ct = res.headers.get("content-type") || "";
      if (!ct.includes("application/json")) {
        setCamerasLoadError(`Expected JSON from ${API}/cameras, got: ${ct || "unknown"}`);
        setCams([]);
        return;
      }
      const data = await res.json();
      const raw = Array.isArray(data) ? data : data?.cameras ?? data?.items ?? [];
      const list = Array.isArray(raw) ? raw : [];
      setCams(
        list.map((c) => {
          if (!c || typeof c !== "object") return c;
          const url = cameraRtspUrl(c);
          return url ? { ...c, url } : { ...c };
        }),
      );
    } catch (e) {
      console.error("[cameras] load failed:", e);
      setCamerasLoadError(e instanceof Error ? e.message : String(e));
      setCams([]);
    }
  }, [API]);

  const loadAllRecordings = useCallback(async (cameraList, { sync = false } = {}) => {
    if (!cameraList.length) {
      setAllRecordings([]);
      setRecordingsListError("");
      return;
    }
    setRecordingsLoading(true);
    setRecordingsListError("");
    try {
      if (sync) {
        try {
          await fetch(`${API}/recordings/sync`, { method: "POST" });
        } catch (e) {
          console.warn("[clips] catalog sync failed:", e);
        }
      }
      const res = await fetch(`${API}/recordings?limit=1000`);
      if (!res.ok) {
        const msg = await readApiError(res);
        setRecordingsListError(msg || "Could not load recordings list.");
        setAllRecordings([]);
        return;
      }
      const data = await res.json();
      const rows = Array.isArray(data.recordings) ? data.recordings : [];
      const flat = rows
        .filter((r) => r && isSetCameraId(r.camId))
        .map((r) => ({
          camId: r.camId,
          camName: r.camName || "",
          edgeBaseUrl: r.edgeBaseUrl || "",
          name: r.name,
          size: r.size ?? 0,
          mtime: r.mtime ?? 0,
          hasThumbnail: Boolean(r.hasThumbnail),
        }));
      flat.sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
      setAllRecordings(flat);
    } catch (e) {
      setRecordingsListError(String(e));
      setAllRecordings([]);
    } finally {
      setRecordingsLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!cams.length) {
      setActiveCameraId(null);
      return;
    }
    setActiveCameraId((prev) => {
      if (prev != null && cams.some((c) => cameraIdsMatch(c.id, prev))) return prev;
      return cams[0].id;
    });
  }, [cams]);

  useEffect(() => {
    loadAllRecordings(cams, { sync: true });
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

  const camsRef = useRef(cams);
  const loadAllRecordingsRef = useRef(loadAllRecordings);
  const recordingByIdRef = useRef(recordingById);
  const manualRecordingByIdRef = useRef(manualRecordingById);

  useEffect(() => {
    camsRef.current = cams;
  }, [cams]);
  useEffect(() => {
    loadAllRecordingsRef.current = loadAllRecordings;
  }, [loadAllRecordings]);
  useEffect(() => {
    recordingByIdRef.current = recordingById;
  }, [recordingById]);
  useEffect(() => {
    manualRecordingByIdRef.current = manualRecordingById;
  }, [manualRecordingById]);

  useEffect(() => {
    const camId = isSetCameraId(activeCameraId)
      ? activeCameraId
      : cams.length > 0
        ? cams[0].id
        : null;
    if (!isSetCameraId(camId)) {
      setInsightsPeopleToday(0);
      setTodayEvents([]);
      return undefined;
    }
    let cancelled = false;
    const load = async () => {
      try {
        const q = eventsApiQuery({ fromIso: todayStartIso(), toIso: null });
        const res = await fetch(
          `${API}/cameras/${encodeURIComponent(String(camId))}/events?${q}`
        );
        if (!res.ok || cancelled) return;
        const data = await res.json();
        const rows = Array.isArray(data.events) ? data.events : [];
        const personRows = rows.filter((ev) => ev?.event_type === "person_detected");
        if (!cancelled) {
          setInsightsPeopleToday(personRows.length);
          setTodayEvents(personRows);
        }
      } catch {
        if (!cancelled) {
          setInsightsPeopleToday(0);
          setTodayEvents([]);
        }
      }
    };
    load();
    const iv = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(iv);
    };
  }, [activeCameraId, cams]);

  useEffect(() => {
    setLiveSessionStarted(new Date().toISOString());
  }, [activeCameraId]);

  useEffect(() => {
    let ws;
    let alive = true;
    const prevRecordingRef = { current: {} };
    const scheduleRecordingsRefresh = () => {
      const list = camsRef.current;
      const load = loadAllRecordingsRef.current;
      if (!list?.length || typeof load !== "function") return;
      const opts = { sync: true };
      load(list, opts);
      window.setTimeout(() => load(camsRef.current, opts), 1500);
      window.setTimeout(() => load(camsRef.current, opts), 4000);
    };
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
          let anyStopped = false;
          Object.entries(camsMap).forEach(([id, row]) => {
            const key = Number(id);
            const now = Boolean(row.recording);
            const was = Boolean(
              prevRecordingRef.current[key] ??
                prevRecordingRef.current[id] ??
                prevRecordingRef.current[String(id)]
            );
            if (was && !now) anyStopped = true;
            next[key] = now;
          });
          prevRecordingRef.current = next;
          if (alive) setRecordingById(next);
          if (anyStopped) scheduleRecordingsRefresh();
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
            if (
              msg.backend != null ||
              typeof msg.hailo_ready === "boolean" ||
              msg.hailo_error != null
            ) {
              setDetectionSystem((prev) => ({
                ...(prev || {}),
                backend: msg.backend ?? prev?.backend,
                hailo_ready:
                  typeof msg.hailo_ready === "boolean" ? msg.hailo_ready : prev?.hailo_ready,
                hailo_error: msg.hailo_error ?? prev?.hailo_error,
              }));
            }
            const id = Number(msg.camera_id);
            const faces = Array.isArray(msg.faces) ? msg.faces : [];
            const personCount =
              typeof msg.person_count === "number"
                ? msg.person_count
                : countPersonDetections(faces);
            const personDetected = Boolean(msg.person_detected) || personCount > 0;
            const captureBusy = Boolean(msg.person_capture_busy);
            const recordEligible = Boolean(msg.person_record_eligible);
            setDetectionsById((prev) => ({
              ...prev,
              [id]: {
                faces,
                ts: msg.ts || "",
                personCount,
                personDetected,
                personMaxScore:
                  typeof msg.person_max_score === "number" ? msg.person_max_score : null,
                personDisplayThreshold:
                  typeof msg.person_display_threshold === "number"
                    ? msg.person_display_threshold
                    : null,
                personRecordThreshold:
                  typeof msg.person_record_threshold === "number"
                    ? msg.person_record_threshold
                    : null,
                personCaptureBusy: captureBusy,
                personRecordEligible: recordEligible,
                personTriggerStreak:
                  typeof msg.person_trigger_streak === "number"
                    ? msg.person_trigger_streak
                    : 0,
                personTriggerMinFrames:
                  typeof msg.person_trigger_min_frames === "number"
                    ? msg.person_trigger_min_frames
                    : 3,
                faceCount: typeof msg.face_count === "number" ? msg.face_count : faces.length,
                error: msg.error || null,
                hailoError: msg.hailo_error || null,
                personDetectionSource: msg.person_detection_source || null,
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
      cameraRtspUrl(cam) ||
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
      name: cam.name || "",
      url: cameraRtspUrl(cam) || "",
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
      const nameTrim = String(connectionForm.name || "").trim();
      const urlTrim = String(connectionForm.url || "").trim();
      const edgeTrim = String(connectionForm.edge_base_url || "").trim();
      if (!nameTrim) {
        alert("Camera name cannot be empty.");
        return;
      }
      if (nameTrim !== (settingsCam.name || "").trim()) {
        connBody.name = nameTrim;
      }
      if (urlTrim && urlTrim !== (cameraRtspUrl(settingsCam) || "")) {
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
          alert(err.detail || "Failed to update camera");
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
      const responseBody = await res.json().catch(() => ({}));
      if (!res.ok) {
        alert(
          typeof responseBody.detail === "string" ? responseBody.detail : `${path} failed`
        );
        return;
      }
      setActiveCameraId(cam.id);
      setManualRecordingById((prev) => ({ ...prev, [cam.id]: path === "start" }));
      const refreshClips = () => loadAllRecordings(cams, { sync: path === "stop" });
      await refreshClips();
      if (path === "stop") {
        const body =
          responseBody && typeof responseBody === "object" ? responseBody : {};
        const stoppedName =
          typeof body.filename === "string" && body.filename.trim()
            ? body.filename.trim()
            : null;
        if (stoppedName) {
          const stoppedSize =
            typeof body.size === "number" && body.size > 0 ? body.size : null;
          setAllRecordings((prev) => {
            const key = recordingKey({ camId: cam.id, name: stoppedName });
            if (prev.some((r) => recordingKey(r) === key)) return prev;
            const next = [
              {
                camId: cam.id,
                camName: cam.name,
                name: stoppedName,
                size: stoppedSize ?? 0,
                mtime: Date.now() / 1000,
              },
              ...prev,
            ];
            next.sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
            return next;
          });
        }
        window.setTimeout(refreshClips, 1500);
        window.setTimeout(refreshClips, 4000);
      }
    } catch (e) {
      alert(String(e));
    }
  };

  const deleteRecordingFor = async (camId, name) => {
    if (!isSetCameraId(camId) || !name) return false;
    if (!window.confirm(`Delete ${name}?`)) return false;
    try {
      const res = await fetch(
        `${API}/recordings/${encodeURIComponent(String(camId))}/files/${encodeURIComponent(name)}`,
        { method: "DELETE" }
      );
      if (!res.ok) {
        alert(await readApiError(res));
        return false;
      }
      setAllRecordings((prev) =>
        prev.filter((r) => !(cameraIdsMatch(r.camId, camId) && r.name === name))
      );
      await loadAllRecordings(cams, { sync: true });
      return true;
    } catch (e) {
      alert(String(e));
      return false;
    }
  };

  const liveCams = cams.slice(0, MAX_LIVE_TILES);
  const effectiveActiveCameraId =
    isSetCameraId(activeCameraId) ? activeCameraId : cams.length > 0 ? cams[0].id : null;
  const activeCamera =
    cams.find((c) => cameraIdsMatch(c.id, effectiveActiveCameraId)) ?? null;
  const recordingsForActiveCamera = isSetCameraId(effectiveActiveCameraId)
    ? allRecordings.filter((r) => cameraIdsMatch(r.camId, effectiveActiveCameraId))
    : [];

  const deleteAllRecordingsForActiveCamera = async () => {
    if (!isSetCameraId(effectiveActiveCameraId)) return false;
    const n = recordingsForActiveCamera.length;
    if (n === 0) return false;
    const label = activeCamera?.name || "this camera";
    if (!window.confirm(`Delete all ${n} clip${n === 1 ? "" : "s"} for ${label}?`)) return false;
    try {
      const res = await fetch(
        `${API}/recordings/${encodeURIComponent(String(effectiveActiveCameraId))}/all`,
        { method: "DELETE" }
      );
      if (!res.ok) {
        alert(await readApiError(res));
        return false;
      }
      let body = {};
      try {
        body = await res.json();
      } catch {
        body = {};
      }
      const deleted = typeof body.deleted === "number" ? body.deleted : 0;
      if (deleted === 0 && n > 0) {
        alert(
          "No clips were removed. Restart the edge agent and controller backend, then try again."
        );
        return false;
      }
      setAllRecordings((prev) =>
        prev.filter((r) => !cameraIdsMatch(r.camId, effectiveActiveCameraId))
      );
      await loadAllRecordings(cams, { sync: true });
      return true;
    } catch (e) {
      alert(String(e));
      return false;
    }
  };
  const mobileLiveCam = activeCamera ?? liveCams[0] ?? null;
  const mobilePersonCount = mobileLiveCam
    ? (detectionsById[mobileLiveCam.id]?.personCount ??
        countPersonDetections(detectionsById[mobileLiveCam.id]?.faces))
    : 0;
  const mobileRecording =
    mobileLiveCam &&
    ((mobileLiveCam.settings?.recording_mode || "motion") === "off"
      ? manualRecordingById[mobileLiveCam.id] === true
      : recordingActiveForCam(recordingById, mobileLiveCam.id));
  const showLivePanel = mainTab === "live";
  const showClipsPanel = mainTab === "clips";
  const showEventsPanel = mainTab === "events";
  const showDevicesPanel = mainTab === "devices";
  const deviceDetailCam =
    deviceDetailId != null ? cams.find((c) => cameraIdsMatch(c.id, deviceDetailId)) : null;

  const handleTabChange = (tab) => {
    setMainTab(tab);
    if (tab !== "devices") {
      setDevicesView("grid");
      setDeviceDetailId(null);
    }
  };

  const renderLiveTile = (c, layout) => (
    <LiveTile
      cam={c}
      layout={layout}
      recording={
        (c.settings?.recording_mode || "motion") === "off"
          ? manualRecordingById[c.id] === true
          : recordingActiveForCam(recordingById, c.id)
      }
      recordingMode={c.settings?.recording_mode || "motion"}
      manualRecording={manualRecordingById[c.id] === true}
      onManualToggle={() => toggleManualRecording(c)}
      faces={detectionsById[c.id]?.faces}
      personCount={detectionsById[c.id]?.personCount ?? 0}
      detectionSystem={detectionSystem}
      overlayDelayMs={
        typeof detectionSystem?.overlay_delay_ms === "number"
          ? detectionSystem.overlay_delay_ms
          : undefined
      }
      motionClipCountdown={null}
    />
  );

  const liveMeta = cameraDisplayMeta(mobileLiveCam);
  const isPrimaryCamera =
    mobileLiveCam && cams.length > 0 && cameraIdsMatch(cams[0].id, mobileLiveCam.id);
  const cameraInfoRows = mobileLiveCam
    ? [
        ["Camera Name", mobileLiveCam.name || "—"],
        ["Resolution", liveMeta.resolution],
        ["Frame Rate", `${liveMeta.fps} FPS`],
        ["Bitrate", liveMeta.bitrate],
        ["Connection", "Excellent"],
        ["Recording", mobileLiveCam.settings?.recording_mode || "motion"],
        [
          "Pre / Post",
          `${mobileLiveCam.settings?.pre_record_seconds ?? 10}s / ${mobileLiveCam.settings?.post_record_seconds ?? 50}s`,
        ],
      ]
    : [];
  const liveActivityItems = useMemo(() => {
    const nowIso = new Date().toISOString();
    const camLabel = mobileLiveCam?.name || activeCamera?.name || "Camera";
    const items = [];

    for (const ev of [...todayEvents].sort((a, b) => {
      const ta = new Date(a?.ts || 0).getTime();
      const tb = new Date(b?.ts || 0).getTime();
      return tb - ta;
    }).slice(0, 8)) {
      items.push({
        label: formatEventType(ev.event_type),
        time: formatTimelineClock(ev.ts),
        ts: ev.ts,
        detail: camLabel,
        kind: timelineActivityKind(ev.event_type),
      });
    }

    if (mobilePersonCount > 0) {
      items.unshift({
        label: "Person detected",
        time: formatTimelineClock(nowIso),
        ts: nowIso,
        detail: camLabel,
        kind: "person",
      });
    }

    if (mobileRecording) {
      items.unshift({
        label: "Recording clip",
        time: formatTimelineClock(nowIso),
        ts: nowIso,
        detail: camLabel,
        kind: "recording",
      });
    }

    items.push({
      label: "Live view started",
      time: formatTimelineClock(liveSessionStarted),
      ts: liveSessionStarted,
      live: true,
      detail: "Admin User",
    });

    return items.sort((a, b) => {
      const ta = new Date(a.ts || 0).getTime();
      const tb = new Date(b.ts || 0).getTime();
      return tb - ta;
    });
  }, [todayEvents, mobilePersonCount, mobileRecording, liveSessionStarted, mobileLiveCam?.name, activeCamera?.name]);
  const handleLiveFullscreen = () => {
    const el = document.querySelector(".dashboard-video-shell");
    if (el?.requestFullscreen) el.requestFullscreen().catch(() => {});
  };
  const handleLiveRecord = () => {
    if (!mobileLiveCam) return;
    if ((mobileLiveCam.settings?.recording_mode || "motion") === "off") {
      toggleManualRecording(mobileLiveCam);
      return;
    }
    if (mobileRecording) return;
    alert("Automatic motion recording is enabled for this camera.");
  };

  return (
    <>
      <VigilanceShell
        activeTab={mainTab}
        onTabChange={handleTabChange}
        clipCount={allRecordings.length}
        eventCount={0}
        cameraCount={cams.length}
        cameras={liveCams}
        activeCameraId={effectiveActiveCameraId}
        onSelectCamera={setActiveCameraId}
      >
        {showLivePanel ? (
          <LiveDashboardPage
            cameraName={mobileLiveCam?.name}
            isPrimary={Boolean(isPrimaryCamera)}
            streamLabel={liveCams.length > 0 ? "LIVE" : "NO SIGNAL"}
            personCount={mobilePersonCount}
            recording={Boolean(mobileRecording)}
            onFindCameras={detectCameras}
            detecting={detecting}
            onFullscreen={handleLiveFullscreen}
            cameras={liveCams}
            activeCameraId={effectiveActiveCameraId}
            onSelectCamera={setActiveCameraId}
            onRecord={mobileLiveCam ? handleLiveRecord : undefined}
            recordDisabled={!mobileLiveCam?.edge_base_url}
            activityItems={liveActivityItems}
            liveVideo={
              liveCams.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full min-h-[280px] p-8 text-center">
                  {camerasLoadError ? (
                    <>
                      <p className="text-amber-300 font-medium mb-1">Could not load cameras</p>
                      <p className="text-gray-400 text-sm mb-2 break-all max-w-md">{camerasLoadError}</p>
                      <p className="text-gray-500 text-xs font-mono">API: {API}</p>
                    </>
                  ) : (
                    <>
                      <p className="text-gray-300 font-medium mb-1">No cameras yet</p>
                      <p className="text-gray-500 text-sm">Add cameras from the Devices page.</p>
                    </>
                  )}
                </div>
              ) : mobileLiveCam ? (
                renderLiveTile(mobileLiveCam, "heroShell")
              ) : null
            }
            thumbStrip={
              liveCams.length > 1 ? (
                <LiveCameraThumbStrip
                  cameras={liveCams}
                  activeId={effectiveActiveCameraId}
                  onSelect={setActiveCameraId}
                  renderThumb={(c) => renderLiveTile(c, "thumb")}
                  mobile
                />
              ) : null
            }
            cameraInsights={
              <CameraInsights
                peopleDetected={insightsPeopleToday}
                vehiclesDetected={0}
                animalsDetected={0}
                recordingCount={recordingsForActiveCamera.length}
                onViewAll={() => setMainTab("events")}
              />
            }
            cameraInfo={<CameraInfoTable rows={cameraInfoRows} />}
          />
        ) : null}

        {showClipsPanel ? (
          <PlaybackDashboardPage
            cameraName={activeCamera?.name ?? "Select camera"}
            cameras={cams}
            activeCameraId={effectiveActiveCameraId}
            onSelectCamera={(id) => setActiveCameraId(id)}
            recordingsCount={recordingsForActiveCamera.length}
            onSync={() => loadAllRecordings(cams, { sync: true })}
            syncing={recordingsLoading}
            renderCameraThumb={(c) => (
              <div className="pointer-events-none h-full w-full">{renderLiveTile(c, "thumb")}</div>
            )}
            timeline={<PlaybackTimelineBar recordings={recordingsForActiveCamera} />}
            recordingsGrid={
              <RecordingsTimeline
                recordings={recordingsForActiveCamera}
                cameras={cams}
                activeCameraName={activeCamera?.name ?? ""}
                loading={recordingsLoading}
                listError={recordingsListError}
                hasCameras={cams.length > 0}
                onRefresh={() => loadAllRecordings(cams, { sync: true })}
                onDelete={deleteRecordingFor}
                onDeleteAll={deleteAllRecordingsForActiveCamera}
                playing={playingClip}
                onPlayingChange={setPlayingClip}
                variant="dashboard"
                className="flex-1 min-h-0"
              />
            }
          />
        ) : null}

        {showEventsPanel ? (
          <EventsPanel
            variant="page"
            cameraId={activeCameraId}
            cameraName={activeCamera?.name ?? ""}
            recordings={recordingsForActiveCamera}
            cameras={cams}
            playingClip={playingClip}
            onPlayClip={setPlayingClip}
            onClearPlay={() => setPlayingClip(null)}
            className="flex-1 min-h-0"
          />
        ) : null}

        {showDevicesPanel ? (
          <DevicesDashboardPage
            view={devicesView === "detail" && deviceDetailCam ? "detail" : devicesView}
            onViewChange={setDevicesView}
            onFindCameras={detectCameras}
            detecting={detecting}
            deviceCount={cams.length}
            gridContent={
              <div>
                {discoveredEdges.length > 0 ? (
                  <div className="mb-4 grid gap-2 sm:grid-cols-2">
                    {discoveredEdges.map((e, i) => (
                      <div key={`${e.edge_base_url}-${i}`} className="dashboard-card p-3 text-xs flex justify-between gap-2">
                        <span className="truncate font-medium">{e.name}</span>
                        <button type="button" onClick={() => addDiscovered(e)} className="dashboard-btn-primary text-[11px] !py-1">Add</button>
                      </div>
                    ))}
                  </div>
                ) : null}
                <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
                  {cams.map((c, idx) => {
                    const meta = cameraDisplayMeta(c);
                    return (
                      <DeviceCard
                        key={c.id}
                        name={c.name}
                        isPrimary={idx === 0}
                        online
                        resolution={meta.resolution}
                        fps={meta.fps}
                        ip={meta.ip}
                        preview={<div className="pointer-events-none h-full w-full">{renderLiveTile(c, "thumb")}</div>}
                        onClick={() => {
                          setDeviceDetailId(c.id);
                          setDevicesView("detail");
                          setActiveCameraId(c.id);
                          setDeviceDetailTab("general");
                        }}
                        onMenu={() => openSettings(c)}
                      />
                    );
                  })}
                </div>
              </div>
            }
            listContent={
              <div className="dashboard-card overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-white/10">
                    <tr>
                      <th className="text-left p-3">Camera name</th>
                      <th className="text-left p-3">Status</th>
                      <th className="text-left p-3">IP Address</th>
                      <th className="text-left p-3">Resolution</th>
                      <th className="text-left p-3">FPS</th>
                      <th className="text-left p-3">Last seen</th>
                      <th className="text-right p-3">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cams.map((c, idx) => {
                      const meta = cameraDisplayMeta(c);
                      return (
                        <tr key={c.id} className="border-b border-white/5 hover:bg-white/[0.02]">
                          <td className="p-3">
                            <span className="font-medium">{c.name}</span>
                            {idx === 0 ? (
                              <span className="ml-2 text-[9px] font-bold uppercase px-1.5 py-0.5 rounded bg-indigo-600/30 text-indigo-200">Primary</span>
                            ) : null}
                          </td>
                          <td className="p-3 text-emerald-400 text-xs">● Online</td>
                          <td className="p-3 font-mono text-xs text-gray-400">{meta.ip}</td>
                          <td className="p-3 text-xs text-gray-400">{meta.resolution}</td>
                          <td className="p-3 text-xs text-gray-400">{meta.fps}</td>
                          <td className="p-3 text-xs text-gray-500">Just now</td>
                          <td className="p-3 text-right space-x-1">
                            <button type="button" className="dashboard-btn-icon !h-8 !w-8" onClick={() => { setActiveCameraId(c.id); setMainTab("live"); }} title="Live">▶</button>
                            <button type="button" className="dashboard-btn-icon !h-8 !w-8" onClick={() => openSettings(c)} title="Settings">⚙</button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            }
            detailContent={
              deviceDetailCam ? (
                <div className="flex flex-col flex-1 min-h-0">
                  <DeviceDetailHeader
                    cameraName={deviceDetailCam.name}
                    isPrimary={cams.length > 0 && cameraIdsMatch(cams[0].id, deviceDetailCam.id)}
                    onBack={() => { setDevicesView("grid"); setDeviceDetailId(null); }}
                    onSettings={() => openSettings(deviceDetailCam)}
                  />
                  <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
                    <div className="shrink-0 p-4 lg:px-8 grid grid-cols-1 xl:grid-cols-3 gap-4 border-b border-white/[0.06]">
                      <div className="xl:col-span-2 dashboard-video-shell min-h-[280px] lg:min-h-[340px]">
                        {renderLiveTile(deviceDetailCam, "heroShell")}
                      </div>
                      <div className="dashboard-card p-4">
                        <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-3">Camera details</h3>
                        <CameraInfoTable
                          rows={(() => {
                            const m = cameraDisplayMeta(deviceDetailCam);
                            return [
                              ["IP Address", m.ip],
                              ["Model", "Generic IP Camera"],
                              ["Resolution", m.resolution],
                              ["FPS", m.fps],
                              ["Bitrate", m.bitrate],
                              ["Last seen", "Just now"],
                            ];
                          })()}
                        />
                      </div>
                    </div>
                    <DeviceConfigTabs activeTab={deviceDetailTab} onTabChange={setDeviceDetailTab}>
                      {deviceDetailTab === "general" ? (
                        <div className="max-w-2xl space-y-4">
                          <div className="dashboard-card p-4 space-y-3">
                            <h3 className="text-sm font-semibold">Camera information</h3>
                            <p className="text-xs text-gray-500">Name: {deviceDetailCam.name}</p>
                            <p className="text-xs text-gray-500 font-mono break-all">RTSP: {cameraRtspUrl(deviceDetailCam) || "—"}</p>
                          </div>
                          <div className="dashboard-card p-4">
                            <h3 className="text-sm font-semibold mb-3">Quick actions</h3>
                            <div className="flex flex-wrap gap-2">
                              <button type="button" className="dashboard-btn-secondary text-xs" onClick={() => { setActiveCameraId(deviceDetailCam.id); setMainTab("live"); }}>Open live view</button>
                              <button type="button" className="dashboard-btn-primary text-xs" onClick={() => openSettings(deviceDetailCam)}>Edit settings</button>
                            </div>
                          </div>
                          <p className="text-[11px] text-indigo-300/80 bg-indigo-950/30 border border-indigo-500/20 rounded-lg px-3 py-2">
                            All changes are saved from the settings dialog.
                          </p>
                        </div>
                      ) : (
                        <p className="text-sm text-gray-500">Open camera settings to configure {deviceDetailTab} options.</p>
                      )}
                    </DeviceConfigTabs>
                  </div>
                </div>
              ) : null
            }
          />
        ) : null}
      </VigilanceShell>

      <RecordingPlayModal
        playing={playingClip}
        cameras={cams}
        onClose={() => setPlayingClip(null)}
        onRefresh={() => loadAllRecordings(cams, { sync: true })}
      />

      {settingsCam && (
        <div
          className="fixed inset-0 z-50 flex flex-col justify-end lg:justify-center items-stretch lg:items-center bg-black/80 lg:p-6"
          role="dialog"
          aria-modal="true"
          onClick={closeSettings}
        >
          <div
            className="w-full lg:max-w-2xl overflow-y-auto shadow-xl border border-white/10 bg-[#0b1220] rounded-t-3xl lg:rounded-2xl max-h-[92dvh] p-4 pb-[max(1rem,env(safe-area-inset-bottom))] lg:pb-5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex justify-between items-start gap-3 pb-3 mb-3 border-b border-white/5">
              <div className="min-w-0">
                <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-indigo-300/90">Settings</p>
                <h2 className="font-semibold text-white text-lg">
                  Camera settings
                </h2>
                <p className="text-xs text-gray-400 mt-0.5">{settingsCam.location || "No location"}</p>
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
                className="p-2 rounded-full border border-white/10 text-gray-300 active:bg-white/10 shrink-0"
                aria-label="Close settings"
              >
                <IconClose className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-xs text-gray-400 mb-1">Camera name</label>
                <input
                  type="text"
                  className="mobile-input"
                  value={connectionForm.name}
                  onChange={(e) =>
                    setConnectionForm((f) => ({ ...f, name: e.target.value }))
                  }
                  placeholder="e.g. Front door"
                  maxLength={120}
                />
              </div>

              <div>
                <label className="block text-xs text-gray-400 mb-1">RTSP URL (MediaMTX pull source)</label>
                <input
                  type="text"
                  className="mobile-input font-mono"
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
                  className="mobile-input font-mono"
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
                  className="mobile-input"
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
                  className="mobile-input"
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
                    className="mobile-input"
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
                    className="mobile-input"
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
                className="mobile-btn-primary w-full lg:w-auto"
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
    </>
  );
}
