import { useCallback, useEffect, useRef, useState } from "react";
import Hls from "hls.js";

const API = (import.meta.env.VITE_API_URL || "http://192.168.2.104:8000").replace(
  /\/$/,
  ""
);

/** Same host as the API, port 8889 — avoids hardcoding a stale LAN IP. Override with VITE_MEDIAMTX_BASE. */
function defaultMediaMtxBaseFromApi() {
  try {
    const u = new URL(API.startsWith("http") ? API : `http://${API}`);
    return `${u.protocol}//${u.hostname}:8889`;
  } catch {
    return "http://192.168.2.104:8889";
  }
}

const MEDIAMTX_BASE = (
  import.meta.env.VITE_MEDIAMTX_BASE || defaultMediaMtxBaseFromApi()
).replace(/\/$/, "");

const WS_RECORDING =
  (import.meta.env.VITE_WS_RECORDING_URL || "").replace(/\/$/, "") ||
  `${API.replace(/^http/, "ws").replace(/^https/, "wss")}/ws/recording`;

/** Low-latency HLS from embedded MediaMTX (Phase 1 overlays need a real video element). */
function defaultHlsBaseFromApi() {
  try {
    const u = new URL(API.startsWith("http") ? API : `http://${API}`);
    return `${u.protocol}//${u.hostname}:8888`;
  } catch {
    return "http://192.168.2.104:8888";
  }
}

const HLS_BASE = (import.meta.env.VITE_HLS_BASE || defaultHlsBaseFromApi()).replace(/\/$/, "");

const WS_DETECTIONS =
  (import.meta.env.VITE_WS_DETECTIONS_URL || "").replace(/\/$/, "") ||
  `${API.replace(/^http/, "ws").replace(/^https/, "wss")}/ws/detections`;

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

function hlsPlaylistUrlForCamera(cam) {
  const path = streamPathForCamera(cam).replace(/\/+$/, "");
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

function edgeDiscoveryKey(e) {
  return `${e.edge_base_url || ""}|${e.mqtt_camera_id || ""}`;
}

function LiveTile({ cam, recording, recordingMode, manualRecording, onManualToggle, faces }) {
  const wrapRef = useRef(null);
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const [scale, setScale] = useState(1);
  const [useIframeFallback, setUseIframeFallback] = useState(false);
  const streamUrl = streamUrlForCamera(cam);
  const hlsUrl = hlsPlaylistUrlForCamera(cam);
  const showManual =
    recordingMode === "off" && cam.edge_base_url && typeof onManualToggle === "function";

  const zoomIn = () => setScale((s) => Math.min(4, s * 1.15));
  const zoomOut = () => setScale((s) => Math.max(0.5, s / 1.15));

  const onWheel = (e) => {
    e.preventDefault();
    if (e.deltaY < 0) zoomIn();
    else zoomOut();
  };

  const goFs = () => {
    const el = wrapRef.current;
    if (!el?.requestFullscreen) return;
    el.requestFullscreen().catch(() => {});
  };

  useEffect(() => {
    if (useIframeFallback) return undefined;
    const video = videoRef.current;
    if (!video) return undefined;
    let hls;
    if (Hls.isSupported()) {
      hls = new Hls({
        lowLatencyMode: true,
        maxLiveSyncPlaybackRate: 1.5,
        enableWorker: true,
      });
      hls.loadSource(hlsUrl);
      hls.attachMedia(video);
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (data.fatal) {
          hls?.destroy();
          setUseIframeFallback(true);
        }
      });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = hlsUrl;
    } else {
      setUseIframeFallback(true);
      return undefined;
    }
    return () => {
      if (hls) hls.destroy();
      video.removeAttribute("src");
      video.load();
    };
  }, [cam.id, hlsUrl, useIframeFallback]);

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
      const faceList = Array.isArray(faces) ? faces : [];
      for (const f of faceList) {
        const x = ox + Number(f.x) * vw * scaleContain;
        const y = oy + Number(f.y) * vh * scaleContain;
        const w = Number(f.w) * vw * scaleContain;
        const h = Number(f.h) * vh * scaleContain;
        ctx.strokeRect(x, y, w, h);
      }
    };

    paint();
    video.addEventListener("loadeddata", paint);
    video.addEventListener("timeupdate", paint);
    const ro = new ResizeObserver(paint);
    ro.observe(video);
    return () => {
      video.removeEventListener("loadeddata", paint);
      video.removeEventListener("timeupdate", paint);
      ro.disconnect();
    };
  }, [faces, useIframeFallback, cam.id]);

  return (
    <div className="bg-[#111827] rounded-xl p-2 flex flex-col min-h-0">
      <div className="flex justify-between items-center text-xs mb-1 gap-2">
        <span className="truncate font-medium">{cam.name}</span>
        <span className="text-green-400 shrink-0">LIVE</span>
      </div>
      <div
        ref={wrapRef}
        className="relative flex-1 min-h-[140px] max-h-[280px] rounded-lg bg-black overflow-hidden"
        onWheel={onWheel}
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
          {useIframeFallback ? (
            <iframe
              title={cam.name}
              src={streamUrl}
              className="w-full h-full min-h-[140px] border-0 bg-black"
              allow="autoplay; fullscreen"
            />
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
                className="absolute inset-0 w-full h-full pointer-events-none"
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
  const [saving, setSaving] = useState(false);
  const [detecting, setDetecting] = useState(false);
  /** Manual recording on edge cameras with recording_mode === "off" (cam id → active). */
  const [manualRecordingById, setManualRecordingById] = useState({});
  const [edgeRtspOverrides, setEdgeRtspOverrides] = useState({});
  /** Phase 1: controller `/ws/detections` → `{ faces, ts }` per camera id */
  const [detectionsById, setDetectionsById] = useState({});

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
    let ws;
    let alive = true;
    const connect = () => {
      try {
        ws = new WebSocket(WS_DETECTIONS);
      } catch {
        return;
      }
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === "detections" && msg.camera_id != null && alive) {
            const id = Number(msg.camera_id);
            setDetectionsById((prev) => ({
              ...prev,
              [id]: { faces: Array.isArray(msg.faces) ? msg.faces : [], ts: msg.ts || "" },
            }));
          }
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
      <div className="w-72 bg-[#070c16] p-4 flex flex-col gap-4 overflow-y-auto shrink-0">
        <h1 className="text-xl font-bold">Vigilance</h1>
        <p className="text-[10px] text-gray-500">Controller UI · up to {MAX_LIVE_TILES} live tiles</p>

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

      <div className="flex-1 flex flex-col min-w-0">
        <div className="flex justify-between items-center px-4 py-2 border-b border-gray-800 gap-2">
          <code className="text-[10px] text-gray-500 truncate">{API}</code>
          <div className="hidden sm:flex flex-col items-end gap-0.5 min-w-0 max-w-[50%]">
            <code className="text-[10px] text-gray-500 truncate w-full text-right" title="Face detection WS">
              {WS_DETECTIONS}
            </code>
            <code className="text-[10px] text-gray-500 truncate w-full text-right" title="Recording state WS">
              {WS_RECORDING}
            </code>
          </div>
        </div>

        <div className="flex-1 p-3 overflow-auto min-h-0 flex flex-col gap-6">
          <div>
            {liveCams.length === 0 ? (
              <p className="text-gray-500 text-sm p-4">
                No cameras saved. Use Detect cameras to add Pi edges — saved cameras persist across
                backend restarts until removed.
              </p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 max-w-[1600px] mx-auto">
                {liveCams.map((c) => (
                  <LiveTile
                    key={c.id}
                    cam={c}
                    recording={recordingById[c.id] === true}
                    recordingMode={c.settings?.recording_mode || "motion"}
                    manualRecording={manualRecordingById[c.id] === true}
                    onManualToggle={() => toggleManualRecording(c)}
                    faces={detectionsById[c.id]?.faces}
                  />
                ))}
              </div>
            )}
          </div>

          <div className="max-w-[1600px] mx-auto w-full border-t border-gray-800 pt-4">
            <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
              <h2 className="text-sm font-semibold text-gray-200">Recordings</h2>
              <div className="flex items-center gap-2">
                {recordingsLoading ? (
                  <span className="text-[10px] text-gray-500">Loading…</span>
                ) : null}
                <button
                  type="button"
                  disabled={recordingsLoading || cams.length === 0}
                  onClick={() => loadAllRecordings(cams)}
                  className="text-[10px] px-2 py-1 rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  Refresh
                </button>
              </div>
            </div>
            {cams.length === 0 ? (
              <p className="text-xs text-gray-500">Add a camera to see recordings.</p>
            ) : allRecordings.length === 0 && !recordingsLoading ? (
              <p className="text-xs text-gray-500">No clips yet across saved cameras.</p>
            ) : (
              <ul className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                {allRecordings.map((r) => {
                  const url = `${API}/recordings/${r.camId}/files/${encodeURIComponent(r.name)}`;
                  return (
                    <li key={`${r.camId}-${r.name}`} className="bg-[#111827] rounded-lg p-3 text-xs border border-gray-800">
                      <div className="flex justify-between gap-2 mb-1">
                        <span className="truncate font-medium text-gray-200" title={r.camName}>
                          {r.camName}
                        </span>
                        <span className="text-gray-500 shrink-0">{formatBytes(r.size)}</span>
                      </div>
                      <p className="font-mono text-[10px] text-gray-400 truncate mb-1" title={r.name}>
                        {r.name}
                      </p>
                      <p className="text-gray-500 mb-2">{formatTime(r.mtime)}</p>
                      <video
                        className="w-full rounded bg-black max-h-40"
                        src={url}
                        controls
                        preload="metadata"
                      />
                      <div className="flex gap-3 mt-2">
                        <a href={url} download={r.name} className="text-blue-400 hover:underline">
                          Download
                        </a>
                        <button
                          type="button"
                          onClick={() => deleteRecordingFor(r.camId, r.name)}
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
        </div>

        <div className="bg-[#111827] px-3 py-2 border-t border-gray-800 text-[10px] text-gray-500 space-y-1">
          <p>
            Live tiles prefer HLS ({HLS_BASE}) for OpenCV/Hailo face boxes over{" "}
            <span className="font-mono text-gray-400">{WS_DETECTIONS}</span>. WebRTC reader (
            {MEDIAMTX_BASE}) is used if HLS fails. Red dot = recording via {WS_RECORDING}.
          </p>
          <p className="text-amber-500/90">
            “Refused to connect” → nothing listening on the MediaMTX URL (usually missing <span className="font-mono text-gray-400">mediamtx</span>{" "}
            binary on the Pi, wrong IP, or firewall). Check{" "}
            <span className="font-mono text-gray-400">{API}/system/mediamtx</span> for{" "}
            <span className="font-mono text-gray-400">process_running</span>. Set{" "}
            <span className="font-mono text-gray-400">VITE_API_URL</span> /{" "}
            <span className="font-mono text-gray-400">VITE_MEDIAMTX_BASE</span> in{" "}
            <span className="font-mono text-gray-400">.env.local</span> if defaults don’t match your network.
          </p>
        </div>
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
                Clips from all cameras are listed on the main page under <span className="text-gray-400">Recordings</span>.
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
