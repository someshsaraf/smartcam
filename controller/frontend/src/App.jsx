import { useCallback, useEffect, useRef, useState } from "react";

const API = (import.meta.env.VITE_API_URL || "http://192.168.2.104:8000").replace(
  /\/$/,
  ""
);
const MEDIAMTX_BASE = (
  import.meta.env.VITE_MEDIAMTX_BASE || "http://192.168.2.160:8889"
).replace(/\/$/, "");

const WS_RECORDING =
  (import.meta.env.VITE_WS_RECORDING_URL || "").replace(/\/$/, "") ||
  `${API.replace(/^http/, "ws").replace(/^https/, "wss")}/ws/recording`;

const MAX_LIVE_TILES = 6;

function streamPathForCamera(cam) {
  if (cam.mediamtx_path && String(cam.mediamtx_path).trim()) {
    return String(cam.mediamtx_path).trim().replace(/^\//, "");
  }
  const url = cam.url || "";
  return url.split("/").pop() || "camera";
}

function streamUrlForCamera(cam) {
  return `${MEDIAMTX_BASE}/${streamPathForCamera(cam)}`;
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

function LiveTile({ cam, recording }) {
  const wrapRef = useRef(null);
  const [scale, setScale] = useState(1);

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
          <iframe
            title={cam.name}
            src={streamUrlForCamera(cam)}
            className="w-full h-full min-h-[140px] border-0 bg-black"
            allow="autoplay; fullscreen"
          />
        </div>
        <div className="absolute bottom-1 left-1 right-1 flex flex-wrap gap-1 z-10 pointer-events-auto">
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
      <p className="text-[10px] text-gray-500 mt-1 truncate font-mono">
        {streamPathForCamera(cam)}
      </p>
    </div>
  );
}

export default function App() {
  const [cams, setCams] = useState([]);
  const [discovered, setDiscovered] = useState([]);
  const [discoveredEdges, setDiscoveredEdges] = useState([]);
  const [recordingById, setRecordingById] = useState({});
  const [settingsCam, setSettingsCam] = useState(null);
  const [recordings, setRecordings] = useState([]);
  const [form, setForm] = useState({
    recording_mode: "motion",
    pre_record_seconds: 10,
    post_record_seconds: 50,
    quality: "medium",
    flip_180: false,
  });
  const [saving, setSaving] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [manual, setManual] = useState({
    name: "",
    location: "",
    url: "",
    edge_base_url: "",
    mqtt_camera_id: "",
    mediamtx_path: "",
  });

  const load = useCallback(async () => {
    const res = await fetch(`${API}/cameras`);
    const data = await res.json();
    setCams(data);
  }, []);

  const loadRecordings = useCallback(async (camId) => {
    const res = await fetch(`${API}/recordings/${camId}`);
    if (!res.ok) {
      setRecordings([]);
      return;
    }
    setRecordings(await res.json());
  }, []);

  useEffect(() => {
    load();
  }, [load]);

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

  const detectCameras = async () => {
    setDetecting(true);
    setDiscoveredEdges([]);
    setDiscovered([]);
    try {
      const [edgesRes, legacyRes] = await Promise.all([
        fetch(`${API}/detect/edges`),
        fetch(`${API}/detect`),
      ]);
      if (edgesRes.ok) {
        setDiscoveredEdges(await edgesRes.json());
      } else {
        setDiscoveredEdges([]);
      }
      if (legacyRes.ok) {
        setDiscovered(await legacyRes.json());
      } else {
        setDiscovered([]);
      }
    } finally {
      setDetecting(false);
    }
  };

  const addDiscovered = async (cam) => {
    const { kind: _k, ...payload } = cam;
    const res = await fetch(`${API}/cameras`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      alert("Add failed");
      return;
    }
    await load();
  };

  const addManual = async () => {
    if (!manual.url.trim()) {
      alert("RTSP / stream URL is required");
      return;
    }
    const body = {
      name: manual.name.trim() || "Camera",
      location: manual.location.trim() || "",
      url: manual.url.trim(),
    };
    if (manual.edge_base_url.trim()) {
      body.edge_base_url = manual.edge_base_url.trim().replace(/\/$/, "");
    }
    if (manual.mqtt_camera_id.trim()) {
      body.mqtt_camera_id = manual.mqtt_camera_id.trim();
    }
    if (manual.mediamtx_path.trim()) {
      body.mediamtx_path = manual.mediamtx_path.trim();
    }
    const res = await fetch(`${API}/cameras`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(err.detail || "Add failed");
      return;
    }
    setManual({
      name: "",
      location: "",
      url: "",
      edge_base_url: "",
      mqtt_camera_id: "",
      mediamtx_path: "",
    });
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
    await loadRecordings(cam.id);
  };

  const closeSettings = () => {
    setSettingsCam(null);
    setRecordings([]);
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

  const deleteRecording = async (name) => {
    if (!settingsCam) return;
    if (!window.confirm(`Delete ${name}?`)) return;
    const res = await fetch(
      `${API}/recordings/${settingsCam.id}/files/${encodeURIComponent(name)}`,
      { method: "DELETE" }
    );
    if (!res.ok) {
      alert("Delete failed");
      return;
    }
    await loadRecordings(settingsCam.id);
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
        <p className="text-[10px] text-gray-600">
          Scans mDNS for Pi 4 edge agents and legacy LAN RTSP cameras. Nothing runs until you tap this.
        </p>

        <div>
          <h3 className="text-xs text-gray-400 mb-2">Edge agents</h3>
          {discoveredEdges.length === 0 ? (
            <p className="text-[10px] text-gray-600 mb-2">
              No matches — tap Detect cameras with edges online, or add manually below.
            </p>
          ) : (
            discoveredEdges.map((e, i) => (
              <div
                key={`${e.edge_base_url}-${e.mqtt_camera_id}-${i}`}
                className="bg-[#111827] p-2 rounded mb-2 text-xs flex flex-col gap-1"
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
              </div>
            ))
          )}
        </div>

        <div>
          <h3 className="text-xs text-gray-400 mb-2">Legacy RTSP (LAN)</h3>
          {discovered.map((c, i) => (
            <div
              key={i}
              className="bg-[#111827] p-2 rounded mb-2 text-xs flex justify-between items-center gap-1"
            >
              <span className="truncate">{c.name}</span>
              <button
                type="button"
                onClick={() => addDiscovered(c)}
                className="text-green-400 shrink-0"
              >
                Add
              </button>
            </div>
          ))}
        </div>

        <div className="border-t border-gray-800 pt-3 space-y-2">
          <h3 className="text-xs text-gray-400">Add Pi 4 edge camera</h3>
          <input
            className="w-full bg-[#0b1220] border border-gray-700 rounded px-2 py-1 text-xs"
            placeholder="Name"
            value={manual.name}
            onChange={(e) => setManual((m) => ({ ...m, name: e.target.value }))}
          />
          <input
            className="w-full bg-[#0b1220] border border-gray-700 rounded px-2 py-1 text-xs"
            placeholder="Location"
            value={manual.location}
            onChange={(e) => setManual((m) => ({ ...m, location: e.target.value }))}
          />
          <input
            className="w-full bg-[#0b1220] border border-gray-700 rounded px-2 py-1 text-xs font-mono"
            placeholder="Live RTSP URL (for MediaMTX)"
            value={manual.url}
            onChange={(e) => setManual((m) => ({ ...m, url: e.target.value }))}
          />
          <input
            className="w-full bg-[#0b1220] border border-gray-700 rounded px-2 py-1 text-xs font-mono"
            placeholder="Edge API http://pi4:8080"
            value={manual.edge_base_url}
            onChange={(e) =>
              setManual((m) => ({ ...m, edge_base_url: e.target.value }))
            }
          />
          <input
            className="w-full bg-[#0b1220] border border-gray-700 rounded px-2 py-1 text-xs font-mono"
            placeholder="MQTT camera id (topic segment)"
            value={manual.mqtt_camera_id}
            onChange={(e) =>
              setManual((m) => ({ ...m, mqtt_camera_id: e.target.value }))
            }
          />
          <input
            className="w-full bg-[#0b1220] border border-gray-700 rounded px-2 py-1 text-xs font-mono"
            placeholder="MediaMTX path (optional)"
            value={manual.mediamtx_path}
            onChange={(e) =>
              setManual((m) => ({ ...m, mediamtx_path: e.target.value }))
            }
          />
          <button
            type="button"
            onClick={addManual}
            className="w-full bg-emerald-700 hover:bg-emerald-600 p-2 rounded text-sm"
          >
            Add camera
          </button>
        </div>

        <div>
          <h3 className="text-xs text-gray-400 mb-2">Cameras</h3>
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
                  title="Settings & recordings"
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
          <code className="text-[10px] text-gray-500 truncate hidden sm:block">
            {WS_RECORDING}
          </code>
        </div>

        <div className="flex-1 p-3 overflow-auto min-h-0">
          {liveCams.length === 0 ? (
            <p className="text-gray-500 text-sm p-4">
              No cameras saved. Use Detect cameras or add manually in the sidebar — saved cameras persist across
              backend restarts until removed.
            </p>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 max-w-[1600px] mx-auto">
              {liveCams.map((c) => (
                <LiveTile
                  key={c.id}
                  cam={c}
                  recording={recordingById[c.id] === true}
                />
              ))}
            </div>
          )}
        </div>

        <div className="bg-[#111827] px-3 py-2 border-t border-gray-800 text-[10px] text-gray-500">
          Live views use MediaMTX ({MEDIAMTX_BASE}). Red dot = MQTT recording signal via{" "}
          {WS_RECORDING}.
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
                </select>
                {form.recording_mode === "continuous" ? (
                  <p className="text-xs text-amber-400 mt-2">
                    Continuous recording fills the SD card (or NAS) quickly. Use retention or
                    lower quality on the edge.
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

              <div className="border-t border-gray-700 pt-4">
                <h3 className="text-sm font-medium mb-2">Recordings</h3>
                {recordings.length === 0 ? (
                  <p className="text-xs text-gray-500">No clips yet.</p>
                ) : (
                  <ul className="space-y-3">
                    {recordings.map((r) => {
                      const url = `${API}/recordings/${settingsCam.id}/files/${encodeURIComponent(r.name)}`;
                      return (
                        <li
                          key={r.name}
                          className="bg-[#0b1220] rounded-lg p-2 text-xs"
                        >
                          <div className="flex justify-between gap-2 mb-2">
                            <span className="truncate font-mono">{r.name}</span>
                            <span className="text-gray-500 shrink-0">
                              {formatBytes(r.size)}
                            </span>
                          </div>
                          <p className="text-gray-500 mb-2">{formatTime(r.mtime)}</p>
                          <video
                            className="w-full rounded bg-black max-h-40"
                            src={url}
                            controls
                            preload="metadata"
                          />
                          <div className="flex gap-2 mt-2">
                            <a
                              href={url}
                              download={r.name}
                              className="text-blue-400 hover:underline"
                            >
                              Download
                            </a>
                            <button
                              type="button"
                              onClick={() => deleteRecording(r.name)}
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
          </div>
        </div>
      )}
    </div>
  );
}
