import { useCallback, useEffect, useRef, useState } from "react";
import Hls from "hls.js";
import {
  API,
  detectionOverlayDelayMs,
  detectionOverlaySyncEnabled,
  HLS_BASE,
  MEDIAMTX_BASE,
  preferWebRtcLive,
  WS_DETECTIONS,
  WS_RECORDING,
} from "./envConfig";
import { useOverlaySyncedDetections } from "./useOverlaySyncedDetections";

const MAX_LIVE_TILES = 6;
const MOBILE_BREAKPOINT_PX = 768;

function useIsMobile(breakpoint = MOBILE_BREAKPOINT_PX) {
  const [isMobile, setIsMobile] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.matchMedia(`(max-width: ${breakpoint}px)`).matches;
  });
  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${breakpoint}px)`);
    const onChange = () => setIsMobile(mq.matches);
    onChange();
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [breakpoint]);
  return isMobile;
}

function canPlayNativeHls(video) {
  if (!video) return false;
  const types = ["application/vnd.apple.mpegurl", "application/x-mpegURL"];
  return types.some((t) => {
    const v = video.canPlayType(t);
    return v === "probably" || v === "maybe";
  });
}

function isIosDevice() {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent || "";
  return (
    /iPad|iPhone|iPod/i.test(ua) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
  );
}

function preferNativeHlsPlayback() {
  if (typeof navigator === "undefined") return false;
  const android = /Android/i.test(navigator.userAgent || "");
  const narrow = typeof window !== "undefined" && window.innerWidth < 1024;
  return isIosDevice() || (android && narrow);
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
          />
        );
      })}
    </div>
  );
}

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

function formatCountdown(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m > 0) return `${m}:${String(r).padStart(2, "0")}`;
  return `${r}s`;
}

function motionClipRemainingSec(status) {
  if (!status?.active) return null;
  const endsAt = Number(status.ends_at);
  if (Number.isFinite(endsAt) && endsAt > 1e9) {
    return Math.max(0, Math.floor(endsAt - Date.now() / 1000));
  }
  if (typeof status.remaining_seconds === "number") {
    return Math.max(0, Math.floor(status.remaining_seconds));
  }
  return null;
}

function motionClipIsActive(status) {
  if (!status || typeof status !== "object") return false;
  if (status.active) return true;
  const phase = status.phase;
  return phase === "starting" || phase === "post_roll" || phase === "materializing";
}

function formatMotionClipLine(status, settings) {
  if (!motionClipIsActive(status)) return null;
  const pre = settings?.pre_record_seconds ?? status.pre_seconds ?? 10;
  const post = settings?.post_record_seconds ?? status.post_seconds ?? 50;
  if (status.phase === "materializing") return "Motion clip: saving…";
  if (status.phase === "starting") {
    return `Recording: preparing clip (${pre}s pre + ${post}s post)…`;
  }
  if (status.phase === "post_roll") {
    const rem = motionClipRemainingSec(status);
    if (rem == null) return "Recording…";
    return `Recording: ${formatCountdown(rem)} left (${pre}s pre + ${post}s post)`;
  }
  return "Recording motion clip…";
}

/** Re-render live tiles every second while a motion clip countdown is visible. */
function useMotionClipCountdownTicker(motionClipById) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const hasCountdown = Object.values(motionClipById || {}).some(
      (st) =>
        motionClipIsActive(st) &&
        (st.phase === "post_roll" || st.phase === "starting" || st.phase === "materializing")
    );
    if (!hasCountdown) return undefined;
    const iv = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(iv);
  }, [motionClipById]);
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

function LiveCameraThumbStrip({ cameras, activeId, onSelect, renderThumb }) {
  const others = cameras.filter((c) => !cameraIdsMatch(c.id, activeId));
  if (others.length === 0) return null;
  return (
    <div
      className="flex gap-2 overflow-x-auto pb-1 shrink-0 snap-x snap-mandatory"
      aria-label="Other camera feeds"
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
          className="shrink-0 w-[7.5rem] snap-start cursor-pointer group"
          title={`Switch to ${c.name}`}
        >
          <div className="rounded-lg overflow-hidden ring-1 ring-gray-700 group-hover:ring-indigo-500/70 aspect-video bg-black pointer-events-none">
            {renderThumb(c)}
          </div>
          <p className="text-[10px] text-gray-400 truncate mt-1 px-0.5 group-hover:text-gray-200">
            {c.name}
          </p>
        </div>
      ))}
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

function EventsPanel({
  cameraId,
  cameraName,
  recordings = [],
  cameras = [],
  playingClip,
  onPlayClip,
  onClearPlay,
  className = "",
}) {
  const [events, setEvents] = useState([]);
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
      className={`rounded-xl border border-gray-800 bg-[#070c16] flex flex-col min-h-0 ${className}`}
    >
      <div className="shrink-0 px-3 py-2 border-b border-gray-800 space-y-2">
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-gray-100">Events</h2>
            <p className="text-[10px] text-gray-500 truncate">
              {cameraName || `Camera ${cameraId}`}
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
        <div className="grid grid-cols-2 gap-x-2 gap-y-1 text-[10px]">
          <label className="text-gray-500 col-span-2">From</label>
          <input
            type="date"
            value={filterFromDate}
            onChange={(e) => setFilterFromDate(e.target.value)}
            className="rounded bg-gray-900 border border-gray-700 px-1.5 py-1 text-gray-200"
          />
          <input
            type="time"
            value={filterFromTime}
            onChange={(e) => setFilterFromTime(e.target.value)}
            className="rounded bg-gray-900 border border-gray-700 px-1.5 py-1 text-gray-200"
          />
          <label className="text-gray-500 col-span-2">To</label>
          <input
            type="date"
            value={filterToDate}
            onChange={(e) => setFilterToDate(e.target.value)}
            className="rounded bg-gray-900 border border-gray-700 px-1.5 py-1 text-gray-200"
          />
          <input
            type="time"
            value={filterToTime}
            onChange={(e) => setFilterToTime(e.target.value)}
            className="rounded bg-gray-900 border border-gray-700 px-1.5 py-1 text-gray-200"
          />
        </div>
        <div className="flex flex-wrap items-center gap-1">
          <button
            type="button"
            onClick={applyFilter}
            disabled={loading || deleting}
            className="text-[10px] px-2 py-1 rounded bg-indigo-700 hover:bg-indigo-600 text-white disabled:opacity-50"
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
            className="text-[10px] px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-300 disabled:opacity-40"
          >
            Clear filter
          </button>
        </div>
        {filterError ? <p className="text-[10px] text-red-400">{filterError}</p> : null}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-2 space-y-1">
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
            return (
              <div
                key={ev.id}
                className={`flex gap-1 rounded-lg border text-[11px] ${
                  isActive
                    ? "border-blue-500/70 bg-[#1e293b]"
                    : "border-gray-800/80 bg-[#111827]"
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
                  <div className="flex justify-between gap-2 items-start">
                    <span className="font-medium text-indigo-300">{formatEventType(ev.event_type)}</span>
                    <span className="text-gray-500 shrink-0">{formatEventTime(ev.ts)}</span>
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

function MobileBottomNav({ tab, onTab, clipCount, eventCount }) {
  const tabs = [
    { id: "live", label: "Live" },
    { id: "clips", label: clipCount > 0 ? `Clips (${clipCount})` : "Clips" },
    { id: "events", label: eventCount > 0 ? `Events (${eventCount})` : "Events" },
  ];
  return (
    <nav
      className="md:hidden shrink-0 border-t border-gray-800 bg-[#070c16] flex pb-[max(0.75rem,env(safe-area-inset-bottom))]"
      aria-label="Main navigation"
    >
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => onTab(t.id)}
          className={`flex-1 py-3 text-xs font-medium transition-colors ${
            tab === t.id
              ? "text-indigo-300 border-t-2 border-indigo-500 bg-indigo-500/10"
              : "text-gray-400 border-t-2 border-transparent"
          }`}
        >
          {t.label}
        </button>
      ))}
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
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 p-4"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        className="max-w-4xl w-full bg-[#111827] rounded-xl border border-gray-700 p-3 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-between items-start gap-2 mb-2 text-xs">
          <div className="min-w-0">
            <p className="font-medium text-gray-200 truncate">{playing.camName || "Clip"}</p>
            <p className="font-mono text-gray-500 truncate">{playing.name}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-400 hover:text-white px-2 shrink-0"
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <ClipPlayer
          url={url}
          camId={playing.camId}
          filename={playing.name}
          onRepaired={onRefresh}
        />
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
        className={`bg-[#070c16] flex flex-col ${
          isPage
            ? "flex-1 min-h-0 border-t border-gray-800"
            : "shrink-0 border-t border-gray-800 max-h-[200px] min-h-[120px]"
        } ${className}`}
        aria-label="Recordings timeline"
      >
        <div className="px-3 py-2 border-b border-gray-800 flex items-center justify-between gap-2 shrink-0">
          <div className="min-w-0">
            <h2 className="text-xs font-semibold text-gray-200">Clips</h2>
            <p className="text-[10px] text-gray-500 truncate" title={activeCameraName || ""}>
              {activeCameraName || "No camera selected"}
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
        <div
          className={`flex-1 min-h-0 px-3 py-2 ${
            isPage ? "overflow-y-auto overflow-x-hidden" : "overflow-x-auto overflow-y-hidden"
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
            <ul className={isPage ? "flex flex-col gap-3 pb-4" : "flex gap-3 pb-1"}>
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
                    className={`rounded-lg border p-1.5 flex flex-col gap-1 ${
                      isPage ? "w-full max-w-xl" : "shrink-0 w-[10.5rem]"
                    } ${
                      isPlaying
                        ? "border-blue-500/70 bg-[#111827]"
                        : "border-gray-800 bg-[#111827]/60"
                    }`}
                  >
                    <div className="relative w-full aspect-video rounded overflow-hidden bg-black border border-gray-700">
                      <button
                        type="button"
                        onClick={() =>
                          setPlaying({
                            ...r,
                            camName: activeCameraName || r.camName || "",
                          })
                        }
                        className="absolute inset-0 w-full h-full"
                        title="Play clip"
                      >
                        <RecordingThumbnail
                          src={url}
                          className="w-full h-full object-cover pointer-events-none"
                        />
                      </button>
                      <div className="absolute top-1 right-1 flex gap-1 z-10">
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
                    <div className="min-w-0 text-[10px] px-0.5">
                      <p className="text-gray-500 truncate">{formatTime(r.mtime)}</p>
                      <p className="text-gray-600">{formatBytes(r.size)}</p>
                      <p className="font-mono text-gray-600 truncate" title={r.name}>
                        {r.name}
                      </p>
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

/** Always-on debug line for Hailo YOLOv8n person test (yolov8n.hef only). */
function formatPersonDebugLine(info, wsOpen, system) {
  if (!wsOpen) return "Person test: WS disconnected";
  if (!info?.ts && !info?.error) return "Person test: waiting for frames…";
  if (info.status === "buffering") {
    const age = typeof info.bufferAgeMs === "number" ? info.bufferAgeMs : 0;
    const need =
      typeof info.inferenceDelayMs === "number" ? info.inferenceDelayMs : 4500;
    return `Person test: syncing video (${age} / ${need} ms)…`;
  }
  if (info.error) return `Person test: — (${info.error})`;

  const hailoErr = info.hailoError || system?.hailo_error || null;
  const n =
    typeof info.personCount === "number" ? info.personCount : countPersonDetections(info.faces);
  if (n > 0) return `Person test: YES — ${n} person(s) (≥90%)`;
  if (hailoErr) return `Person test: — (Hailo YOLOv8n: ${hailoErr})`;
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
  detectionSystem,
  motionClipLine,
  overlayDelayMs,
  layout = "default",
  isMobile = false,
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
  const persons =
    typeof drawPersonCount === "number" ? drawPersonCount : countPersonDetections(drawFaces);
  const personDebugLine = formatPersonDebugLine(
    { ...detectionInfo, faces: rawFaces, personCount: detectionInfo?.personCount ?? drawPersonCount },
    detectionWsOpen,
    detectionSystem
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
    setUseWebRtc(preferWebRtcLive());
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

    if (useNative) {
      hlsRef.current = null;
      video.playsInline = true;
      video.setAttribute("playsinline", "");
      video.setAttribute("webkit-playsinline", "");
      video.src = hlsUrl;
      video.load();
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
      hlsRef.current = null;
      if (hls) hls.destroy();
      video.removeAttribute("src");
      video.load();
    };
  }, [cam.id, cam.url, hlsUrl, hlsProxyUrl, hlsDirectUrl, useWebRtc]);

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
      const lineW = isMobile ? 3 : 2;
      ctx.lineWidth = lineW;
      ctx.font = isMobile
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
        const boxH = isMobile ? 16 : 14;
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
  }, [drawFaces, useWebRtc, cam.id, isMobile]);

  const isHero = layout === "hero";
  const isThumb = layout === "thumb";

  return (
    <div
      className={`bg-[#111827] flex flex-col min-h-0 h-full ${
        isThumb ? "rounded-none p-0" : "rounded-xl p-2"
      }`}
    >
      {!isThumb ? (
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
      {!isThumb ? (
      <div className="mb-1 space-y-0.5">
        <p
          className={`text-[10px] font-mono leading-snug hidden sm:block ${
            personDebugPositive
              ? "text-blue-300 font-semibold"
              : personDebugLine.includes("—") ||
                  personDebugLine.includes("waiting") ||
                  personDebugLine.includes("disconnected")
                ? "text-amber-400/90"
                : "text-gray-500"
          }`}
          title="Hailo YOLOv8n on controller RTSP — triggers motion clip when recording mode is Motion"
        >
          {personDebugLine}
        </p>
        {motionClipLine ? (
          <p className="text-[10px] font-mono text-rose-300 font-semibold" title="Motion recording in progress">
            {motionClipLine}
          </p>
        ) : null}
        {isMobile && persons > 0 ? (
          <p className="sm:hidden text-[10px] text-blue-300 font-semibold">
            Person detected ({persons})
          </p>
        ) : null}
      </div>
      ) : null}
      {!isThumb && edgeHint ? (
        <p className="text-[10px] text-amber-400/95 mb-1 leading-snug">
          {edgeHint}
          {cam.url ? (
            <>
              {" "}
              RTSP: <span className="font-mono text-gray-300">{cam.url}</span>
            </>
          ) : null}
        </p>
      ) : null}
      <div
        ref={wrapRef}
        className={`relative flex-1 bg-black overflow-hidden touch-none ${
          isThumb
            ? "rounded-none min-h-0 h-full"
            : `rounded-lg ${
                isHero
                  ? isMobile
                    ? "min-h-[42dvh] max-h-[68dvh]"
                    : "min-h-[200px] max-h-none"
                  : isMobile
                    ? "min-h-[28dvh] max-h-[40dvh]"
                    : "min-h-[100px] max-h-[200px]"
              }`
        }`}
      >
        {recording ? (
          <div
            className="absolute top-2 right-2 z-20 h-5 w-5 rounded-full bg-red-600 shadow-lg ring-2 ring-white/90"
            title="Recording"
            aria-label="Recording"
          />
        ) : null}
        <div
          className="w-full h-full origin-center transition-transform duration-75"
          style={{ transform: `scale(${scale})` }}
        >
          {useWebRtc ? (
            <div className="relative w-full h-full min-h-[140px]">
              <iframe
                title={cam.name}
                src={streamUrl}
                className="w-full h-full min-h-[140px] border-0 bg-black pointer-events-none"
                allow="autoplay; fullscreen"
                sandbox="allow-scripts allow-same-origin allow-autoplay allow-fullscreen"
              />
              <PersonBoxesOverlay
                faces={drawFaces}
                containerRef={wrapRef}
                assumedAspect={{ w: 16, h: 9 }}
              />
              {edgeHint && !personDetections(drawFaces).length ? (
                <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-2 text-center text-[10px] text-gray-400/90">
                  WebRTC reader — see message above.
                </div>
              ) : null}
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
        {!isThumb ? (
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
        ) : null}
      </div>
      {!isThumb ? (
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
  /** Motion clip in progress on Pi edge (cam id → status from edge). */
  const [motionClipById, setMotionClipById] = useState({});
  const [edgeRtspOverrides, setEdgeRtspOverrides] = useState({});
  /** Phase 1: controller `/ws/detections` → per-camera inference + person debug */
  const [detectionsById, setDetectionsById] = useState({});
  const [detectionWsOpen, setDetectionWsOpen] = useState(false);
  const [detectionSystem, setDetectionSystem] = useState(null);
  const isMobile = useIsMobile();
  const [mobileTab, setMobileTab] = useState("live");
  const [mobileManageOpen, setMobileManageOpen] = useState(false);
  const [playingClip, setPlayingClip] = useState(null);

  const load = useCallback(async () => {
    const res = await fetch(`${API}/cameras`);
    const data = await res.json();
    setCams(data);
  }, []);

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
    const edgeCams = cams.filter(
      (c) =>
        c.edge_base_url &&
        isSetCameraId(c.id) &&
        (c.settings?.recording_mode || "motion") === "motion"
    );
    if (!edgeCams.length) return undefined;
    let cancelled = false;
    const poll = async () => {
      for (const c of edgeCams) {
        try {
          const res = await fetch(`${API}/cameras/${c.id}/recordings/motion/status`);
          if (!res.ok || cancelled) continue;
          const st = await res.json();
          if (cancelled || !st || typeof st !== "object") continue;
          setMotionClipById((prev) => {
            const prevSt = prev[c.id];
            const wasActive = motionClipIsActive(prevSt);
            const nowActive = motionClipIsActive(st);
            const next = { ...prev, [c.id]: st };
            if (wasActive && !nowActive) {
              loadAllRecordingsRef.current(camsRef.current, { sync: true });
            } else if (st.filename && st.filename !== prevSt?.filename) {
              loadAllRecordingsRef.current(camsRef.current, { sync: false });
            }
            return next;
          });
        } catch {
          /* ignore */
        }
      }
    };
    poll();
    const iv = window.setInterval(poll, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(iv);
    };
  }, [cams]);

  const camsRef = useRef(cams);
  const loadAllRecordingsRef = useRef(loadAllRecordings);
  const motionClipByIdRef = useRef(motionClipById);
  const recordingByIdRef = useRef(recordingById);
  const manualRecordingByIdRef = useRef(manualRecordingById);

  useEffect(() => {
    camsRef.current = cams;
  }, [cams]);
  useEffect(() => {
    loadAllRecordingsRef.current = loadAllRecordings;
  }, [loadAllRecordings]);
  useEffect(() => {
    motionClipByIdRef.current = motionClipById;
  }, [motionClipById]);
  useEffect(() => {
    recordingByIdRef.current = recordingById;
  }, [recordingById]);
  useEffect(() => {
    manualRecordingByIdRef.current = manualRecordingById;
  }, [manualRecordingById]);

  useEffect(() => {
    let ws;
    let alive = true;
    const prevRecordingRef = { current: {} };
    const scheduleRecordingsRefresh = () => {
      const list = camsRef.current;
      const load = loadAllRecordingsRef.current;
      if (!list?.length || typeof load !== "function") return;
      load(list);
      window.setTimeout(() => load(camsRef.current), 1500);
      window.setTimeout(() => load(camsRef.current), 4000);
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
            const personDetected = Boolean(msg.person_detected) || personCount > 0;
            setDetectionsById((prev) => ({
              ...prev,
              [id]: {
                faces,
                ts: msg.ts || "",
                personCount,
                personDetected,
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
      name: cam.name || "",
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
          setAllRecordings((prev) => {
            const key = recordingKey({ camId: cam.id, name: stoppedName });
            if (prev.some((r) => recordingKey(r) === key)) return prev;
            const next = [
              {
                camId: cam.id,
                camName: cam.name,
                name: stoppedName,
                size: 0,
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
  useMotionClipCountdownTicker(motionClipById);
  const showLivePanel = !isMobile || mobileTab === "live";
  const showClipsPanel = isMobile && mobileTab === "clips";
  const showEventsPanel = isMobile && mobileTab === "events";

  useEffect(() => {
    if (mobileTab === "cameras") setMobileTab("live");
  }, [mobileTab]);

  const renderLiveTile = (c, layout) => (
    <LiveTile
      cam={c}
      layout={layout}
      isMobile={isMobile}
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
      detectionInfo={detectionsById[c.id]}
      detectionWsOpen={detectionWsOpen}
      detectionSystem={detectionSystem}
      overlayDelayMs={
        typeof detectionSystem?.overlay_delay_ms === "number"
          ? detectionSystem.overlay_delay_ms
          : undefined
      }
      motionClipLine={
        (c.settings?.recording_mode || "motion") === "motion"
          ? formatMotionClipLine(motionClipById[c.id], c.settings)
          : null
      }
    />
  );

  return (
    <div className="flex flex-col md:flex-row h-[100dvh] bg-[#0b1220] text-white overflow-hidden">
      <aside
        className={`bg-[#070c16] p-3 flex flex-col gap-3 overflow-y-auto border-r border-gray-800 min-h-0 ${
          isMobile
            ? mobileManageOpen
              ? "flex flex-1 w-full fixed inset-0 z-40"
              : "hidden"
            : "hidden md:flex w-56 lg:w-60 shrink-0"
        }`}
      >
        {isMobile && mobileManageOpen ? (
          <button
            type="button"
            onClick={() => setMobileManageOpen(false)}
            className="text-sm text-indigo-300 hover:text-indigo-100 text-left mb-1"
          >
            ← Back to live
          </button>
        ) : null}
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
            Hailo YOLOv8n:{" "}
            <span className="text-gray-200">
              {detectionSystem?.hailo_ready
                ? "ready"
                : detectionSystem?.hailo_error || "not ready"}
            </span>
          </p>
          <p className="text-gray-400">
            HEF:{" "}
            <span className="text-gray-200 font-mono">
              {detectionSystem?.hef_model || "yolov8n.hef"}
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
                  {c.name}:{" "}
                  {formatPersonDebugLine(detectionsById[c.id], detectionWsOpen, detectionSystem)}
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
              role="button"
              tabIndex={0}
              onClick={() => setActiveCameraId(c.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setActiveCameraId(c.id);
                }
              }}
              className={`bg-[#111827] p-2 rounded mb-2 text-sm flex justify-between items-center gap-1 cursor-pointer border ${
                cameraIdsMatch(activeCameraId, c.id)
                  ? "border-indigo-500 ring-1 ring-indigo-500/40"
                  : "border-transparent hover:border-gray-600"
              }`}
              title="Show this camera's clips in the timeline"
            >
              <span className="truncate flex-1">{c.name}</span>
              <div className="flex items-center gap-1 shrink-0">
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    openSettings(c);
                  }}
                  className="text-gray-300 hover:text-white px-1"
                  title="Camera settings"
                >
                  ⚙
                </button>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    deleteCamera(c);
                  }}
                  className="text-red-400 hover:text-red-300 px-1 text-xs"
                  title="Remove camera"
                >
                  ✕
                </button>
              </div>
            </div>
          ))}
        </div>
      </aside>

      <div
        className={`flex-1 flex flex-col min-w-0 min-h-0 ${
          isMobile && mobileManageOpen ? "hidden" : ""
        }`}
      >
        {showLivePanel ? (
        <>
        {isMobile ? (
          <div className="flex gap-2 px-3 py-2 border-b border-gray-800 bg-[#070c16]/80 shrink-0">
            <button
              type="button"
              disabled={detecting}
              onClick={detectCameras}
              className="flex-1 text-xs py-2 rounded bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50"
            >
              {detecting ? "Detecting…" : "Detect cameras"}
            </button>
            <button
              type="button"
              onClick={() => setMobileManageOpen(true)}
              className="flex-1 text-xs py-2 rounded border border-gray-600 text-gray-200 hover:bg-gray-800"
            >
              Manage
            </button>
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-2 px-3 md:px-4 py-2 border-b border-gray-800 bg-[#070c16]/80 shrink-0">
          <span className="text-xs font-medium text-gray-200">
            {liveCams.length} camera{liveCams.length === 1 ? "" : "s"} live
          </span>
          {activeCamera ? (
            <span className="text-[10px] px-2 py-0.5 rounded-full border border-indigo-500/50 text-indigo-200 bg-indigo-500/10">
              Active: {activeCamera.name}
            </span>
          ) : null}
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

        <div className="flex-1 p-2 md:p-3 min-h-0 overflow-hidden flex flex-col">
          {liveCams.length === 0 ? (
            <p className="text-gray-500 text-sm p-4">
              No cameras saved. Use Detect cameras to add Pi edges — saved cameras persist across
              backend restarts until removed.
            </p>
          ) : isMobile ? (
            <div className="flex flex-col flex-1 min-h-0 gap-2">
              {mobileLiveCam ? (
                <div className="flex-1 min-h-0 rounded-xl ring-1 ring-gray-800">
                  {renderLiveTile(mobileLiveCam, "hero")}
                </div>
              ) : null}
              <LiveCameraThumbStrip
                cameras={liveCams}
                activeId={effectiveActiveCameraId}
                onSelect={setActiveCameraId}
                renderThumb={(c) => renderLiveTile(c, "thumb")}
              />
            </div>
          ) : (
            <div
              className={`grid flex-1 min-h-0 gap-3 grid-cols-1 md:grid-cols-3 ${
                liveCams.length > 4 ? "md:grid-rows-3" : "md:grid-rows-2"
              }`}
            >
              {liveCams.map((c, i) => (
                <div
                  key={c.id}
                  role="button"
                  tabIndex={0}
                  onClick={(e) => {
                    if (e.target.closest("button, a")) return;
                    setActiveCameraId(c.id);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setActiveCameraId(c.id);
                    }
                  }}
                  className={`min-h-0 min-w-0 rounded-xl transition-shadow cursor-pointer ${bentoTileClass(i, liveCams.length)} ${
                    cameraIdsMatch(activeCameraId, c.id)
                      ? "ring-2 ring-indigo-500 ring-offset-2 ring-offset-[#0b1220]"
                      : "hover:ring-1 hover:ring-gray-600"
                  }`}
                  title={
                    cameraIdsMatch(activeCameraId, c.id)
                      ? "Active camera (clips below)"
                      : "Click to activate — show this camera's clips"
                  }
                >
                  {renderLiveTile(c, i === 0 ? "hero" : "default")}
                </div>
              ))}
            </div>
          )}
        </div>
        </>
        ) : null}

        {(!isMobile || showClipsPanel) && (
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
          variant={isMobile ? "page" : "dock"}
        />
        )}

        {(!isMobile || showEventsPanel) && (
          <EventsPanel
            cameraId={activeCameraId}
            cameraName={activeCamera?.name ?? ""}
            recordings={recordingsForActiveCamera}
            cameras={cams}
            playingClip={playingClip}
            onPlayClip={setPlayingClip}
            onClearPlay={() => setPlayingClip(null)}
            className={
              isMobile ? "flex-1 min-h-0 m-2" : "shrink-0 h-64 mx-2 mb-2"
            }
          />
        )}

        {isMobile ? (
          <MobileBottomNav
            tab={mobileTab}
            onTab={setMobileTab}
            clipCount={recordingsForActiveCamera.length}
          />
        ) : null}
      </div>

      <RecordingPlayModal
        playing={playingClip}
        cameras={cams}
        onClose={() => setPlayingClip(null)}
        onRefresh={() => loadAllRecordings(cams, { sync: true })}
      />

      {settingsCam && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          role="dialog"
          aria-modal="true"
        >
          <div className="bg-[#111827] rounded-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto p-5 shadow-xl border border-gray-800">
            <div className="flex justify-between items-start mb-4">
              <div>
                <h2 className="text-lg font-semibold">Camera settings</h2>
                <p className="text-xs text-gray-400">{settingsCam.location || "No location"}</p>
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
                <label className="block text-xs text-gray-400 mb-1">Camera name</label>
                <input
                  type="text"
                  className="w-full bg-[#0b1220] border border-gray-700 rounded px-3 py-2 text-sm"
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
