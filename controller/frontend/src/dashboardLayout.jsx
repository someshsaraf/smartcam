/**
 * Vigilance dashboard shell — laptop-first layout with responsive mobile fallbacks.
 */

import { useState } from "react";

const NAV_ITEMS = [
  { id: "live", label: "Live View", icon: "live" },
  { id: "clips", label: "Playback", icon: "playback" },
  { id: "events", label: "Events", icon: "events" },
  { id: "devices", label: "Devices", icon: "devices" },
];

function NavIcon({ name, className = "w-5 h-5" }) {
  const c = className;
  switch (name) {
    case "live":
      return (
        <svg className={c} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
          <rect x="3" y="5" width="14" height="12" rx="2" />
          <path d="M17 9l4-2v10l-4-2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      );
    case "playback":
      return (
        <svg className={c} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
          <circle cx="12" cy="12" r="9" />
          <path d="M10 8.5v7l5.5-3.5-5.5-3.5z" fill="currentColor" stroke="none" />
        </svg>
      );
    case "events":
      return (
        <svg className={c} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
          <path d="M6 8h12M6 12h8M6 16h10" strokeLinecap="round" />
          <rect x="4" y="4" width="16" height="16" rx="2" />
        </svg>
      );
    case "devices":
      return (
        <svg className={c} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
          <rect x="3" y="4" width="7" height="6" rx="1" />
          <rect x="14" y="4" width="7" height="6" rx="1" />
          <rect x="3" y="14" width="7" height="6" rx="1" />
          <rect x="14" y="14" width="7" height="6" rx="1" />
        </svg>
      );
    default:
      return null;
  }
}

export function VigilanceShell({
  activeTab,
  onTabChange,
  clipCount = 0,
  eventCount = 0,
  cameraCount = 0,
  children,
}) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  const badgeFor = (id) => {
    if (id === "clips" && clipCount > 0) return clipCount > 99 ? "99+" : clipCount;
    if (id === "events" && eventCount > 0) return eventCount > 99 ? "99+" : eventCount;
    return null;
  };

  const navButton = (item, mobile = false) => {
    const active = activeTab === item.id;
    return (
      <button
        key={item.id}
        type="button"
        onClick={() => {
          onTabChange(item.id);
          if (mobile) setMobileNavOpen(false);
        }}
        className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors ${
          active
            ? "bg-indigo-600/20 text-indigo-200 border border-indigo-500/40"
            : "text-gray-400 hover:text-gray-200 hover:bg-white/5 border border-transparent"
        }`}
      >
        <NavIcon name={item.icon} className="w-5 h-5 shrink-0" />
        <span className="flex-1 text-left">{item.label}</span>
        {badgeFor(item.id) ? (
          <span className="min-w-[1.25rem] h-5 px-1.5 flex items-center justify-center rounded-full bg-indigo-500 text-[10px] font-bold text-white">
            {badgeFor(item.id)}
          </span>
        ) : null}
      </button>
    );
  };

  return (
    <div className="flex h-[100dvh] bg-[#080c14] text-white overflow-hidden">
      {/* Desktop sidebar */}
      <aside className="hidden lg:flex flex-col w-[220px] shrink-0 border-r border-white/5 bg-[#0a0f18]">
        <div className="px-4 py-5 border-b border-white/5">
          <div className="flex items-center gap-2">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-600/30 text-indigo-300">
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
                <path d="M12 3l7 4v10l-7 4-7-4V7l7-4z" />
              </svg>
            </span>
            <span className="font-bold tracking-wide text-sm">VIGILANCE</span>
          </div>
        </div>
        <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">{NAV_ITEMS.map((i) => navButton(i))}</nav>
        <div className="p-3 border-t border-white/5 space-y-2">
          <div className="dashboard-card p-3">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-gray-500">System</p>
            <p className="text-xs text-emerald-400 mt-1 flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              Operational
            </p>
            <p className="text-[11px] text-gray-500 mt-0.5">{cameraCount} camera{cameraCount === 1 ? "" : "s"} online</p>
          </div>
        </div>
      </aside>

      {/* Mobile drawer */}
      {mobileNavOpen ? (
        <div
          className="lg:hidden fixed inset-0 z-50 flex"
          role="dialog"
          aria-modal="true"
        >
          <button
            type="button"
            className="flex-1 bg-black/60"
            aria-label="Close menu"
            onClick={() => setMobileNavOpen(false)}
          />
          <aside className="w-[min(280px,85vw)] bg-[#0a0f18] border-l border-white/10 flex flex-col p-4">
            <p className="font-bold text-sm mb-4">VIGILANCE</p>
            <nav className="space-y-1 flex-1">{NAV_ITEMS.map((i) => navButton(i, true))}</nav>
          </aside>
        </div>
      ) : null}

      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        {/* Mobile top bar */}
        <header className="lg:hidden shrink-0 flex items-center justify-between gap-2 px-3 py-2.5 border-b border-white/5 mobile-glass">
          <button
            type="button"
            onClick={() => setMobileNavOpen(true)}
            className="p-2 rounded-lg border border-white/10 text-gray-300"
            aria-label="Open menu"
          >
            <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M4 7h16M4 12h16M4 17h16" strokeLinecap="round" />
            </svg>
          </button>
          <span className="text-sm font-semibold">Vigilance</span>
          <span className="w-9" />
        </header>

        <main className="flex-1 min-h-0 overflow-hidden flex flex-col">{children}</main>

        {/* Mobile bottom nav */}
        <nav
          className="lg:hidden shrink-0 mobile-glass border-t border-white/5 flex justify-around px-1 pt-1.5 pb-[max(0.5rem,env(safe-area-inset-bottom))]"
          aria-label="Main navigation"
        >
          {NAV_ITEMS.map((item) => {
            const active = activeTab === item.id;
            const badge = badgeFor(item.id);
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onTabChange(item.id)}
                className={`relative flex flex-col items-center gap-0.5 min-w-[4rem] py-1.5 px-2 rounded-xl ${
                  active ? "text-indigo-300" : "text-gray-500"
                }`}
              >
                <NavIcon name={item.icon} className={active ? "w-6 h-6" : "w-5 h-5"} />
                <span className="text-[9px] font-medium">{item.label.split(" ")[0]}</span>
                {badge ? (
                  <span className="absolute top-0 right-1 min-w-[1rem] h-4 px-1 rounded-full bg-indigo-500 text-[8px] font-bold flex items-center justify-center">
                    {badge}
                  </span>
                ) : null}
              </button>
            );
          })}
        </nav>
      </div>
    </div>
  );
}

export function DashboardPageHeader({
  eyebrow,
  title,
  badge,
  actions,
  children,
}) {
  return (
    <header className="shrink-0 border-b border-white/5 bg-[#0a0f18]/80 px-4 lg:px-6 py-3 lg:py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          {eyebrow ? (
            <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-indigo-400/90">
              {eyebrow}
            </p>
          ) : null}
          <div className="flex items-center gap-2 mt-0.5 flex-wrap">
            <h1 className="text-lg lg:text-xl font-semibold text-white truncate">{title}</h1>
            {badge ? (
              <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                {badge}
              </span>
            ) : null}
          </div>
          {children}
        </div>
        {actions ? <div className="flex items-center gap-2 shrink-0 flex-wrap">{actions}</div> : null}
      </div>
    </header>
  );
}

export function LiveDashboardPage({
  cameraName,
  streamLabel,
  personCount,
  recording,
  onFindCameras,
  detecting,
  liveVideo,
  thumbStrip,
  recentEvents,
  cameraInfo,
}) {
  return (
    <div className="flex flex-col flex-1 min-h-0">
      <DashboardPageHeader
        eyebrow="Camera"
        title={cameraName || "Live view"}
        actions={
          <>
            <span
              className={`text-[11px] font-medium px-2.5 py-1 rounded-full border ${
                streamLabel === "NO SIGNAL"
                  ? "border-red-500/40 text-red-300 bg-red-500/10"
                  : streamLabel === "LIVE"
                    ? "border-emerald-500/40 text-emerald-300 bg-emerald-500/10"
                    : "border-white/10 text-gray-400"
              }`}
            >
              {streamLabel}
            </span>
            {recording ? (
              <span className="text-[11px] font-medium px-2.5 py-1 rounded-full border border-red-500/40 text-red-200 bg-red-500/10 flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-red-500 animate-pulse" />
                Recording
              </span>
            ) : null}
            {personCount > 0 ? (
              <span className="text-[11px] font-medium px-2.5 py-1 rounded-full border border-indigo-500/40 text-indigo-200 bg-indigo-500/10">
                Person ({personCount})
              </span>
            ) : null}
            <button
              type="button"
              disabled={detecting}
              onClick={onFindCameras}
              className="dashboard-btn-primary text-xs"
            >
              {detecting ? "…" : "+ Find cameras"}
            </button>
          </>
        }
      />

      <div className="flex-1 min-h-0 overflow-y-auto lg:overflow-hidden flex flex-col p-4 lg:p-6 gap-4">
        <div className="flex flex-col lg:flex-row gap-4 flex-1 min-h-0">
          <div className="flex-1 min-h-[min(50vh,420px)] lg:min-h-0 flex flex-col gap-3">
            <div className="dashboard-card flex-1 min-h-0 overflow-hidden p-0">{liveVideo}</div>
            {thumbStrip ? <div className="shrink-0">{thumbStrip}</div> : null}
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 shrink-0">
          <div className="dashboard-card p-4 min-h-[160px] max-h-[240px] overflow-hidden flex flex-col">
            <div className="flex justify-between items-center mb-2">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                Recent events
              </h2>
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto">{recentEvents}</div>
          </div>
          <div className="dashboard-card p-4 min-h-[160px] max-h-[240px] overflow-hidden flex flex-col md:col-span-1 xl:col-span-1">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
              Activity
            </h2>
            <p className="text-xs text-gray-500">Live detection and recording events appear in Events.</p>
          </div>
          <div className="dashboard-card p-4 min-h-[160px] md:col-span-2 xl:col-span-1">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-3">
              Camera info
            </h2>
            {cameraInfo}
          </div>
        </div>
      </div>
    </div>
  );
}

export function PlaybackDashboardPage({
  cameraName,
  cameras,
  activeCameraId,
  onSelectCamera,
  timeline,
  toolbar,
  recordingsGrid,
}) {
  return (
    <div className="flex flex-col flex-1 min-h-0">
      <DashboardPageHeader eyebrow="Playback" title={cameraName || "Select camera"} actions={toolbar} />
      {timeline ? <div className="shrink-0 px-4 lg:px-6 py-3 border-b border-white/5">{timeline}</div> : null}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        <aside className="hidden md:flex flex-col w-[200px] lg:w-[220px] shrink-0 border-r border-white/5 bg-[#0a0f18] p-3 overflow-y-auto">
          <input
            type="search"
            placeholder="Search cameras…"
            className="dashboard-input text-xs mb-3"
            disabled
          />
          <p className="text-[10px] font-semibold uppercase tracking-wider text-gray-500 mb-2">
            Cameras
          </p>
          <ul className="space-y-1.5">
            {cameras.map((c) => {
              const active = String(c.id) === String(activeCameraId);
              return (
                <li key={c.id}>
                  <button
                    type="button"
                    onClick={() => onSelectCamera(c.id)}
                    className={`w-full text-left px-2.5 py-2 rounded-lg border text-sm transition-colors ${
                      active
                        ? "border-indigo-500/50 bg-indigo-500/10 text-indigo-100"
                        : "border-white/5 bg-white/[0.02] text-gray-300 hover:bg-white/5"
                    }`}
                  >
                    <span className="block truncate font-medium">{c.name}</span>
                    <span className="text-[10px] text-emerald-400 flex items-center gap-1 mt-0.5">
                      <span className="h-1 w-1 rounded-full bg-emerald-400" />
                      Online
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </aside>
        <div className="flex-1 min-h-0 flex flex-col md:hidden px-3 pt-2 shrink-0">
          <label className="text-[10px] text-gray-500 uppercase tracking-wider">Camera</label>
          <select
            className="dashboard-input mt-1 text-sm"
            value={activeCameraId ?? ""}
            onChange={(e) => onSelectCamera(Number(e.target.value))}
          >
            {cameras.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div className="flex-1 min-h-0 overflow-hidden">{recordingsGrid}</div>
      </div>
    </div>
  );
}

export function DevicesDashboardPage({
  view,
  onViewChange,
  onFindCameras,
  detecting,
  gridContent,
  listContent,
  detailContent,
}) {
  return (
    <div className="flex flex-col flex-1 min-h-0">
      {view === "detail" ? (
        detailContent
      ) : (
        <>
          <DashboardPageHeader
            eyebrow="Devices"
            title="All devices"
            actions={
              <>
                <div className="hidden sm:flex rounded-lg border border-white/10 p-0.5">
                  <button
                    type="button"
                    onClick={() => onViewChange("grid")}
                    className={`px-3 py-1.5 text-xs rounded-md ${
                      view === "grid" ? "bg-indigo-600 text-white" : "text-gray-400"
                    }`}
                  >
                    Grid
                  </button>
                  <button
                    type="button"
                    onClick={() => onViewChange("list")}
                    className={`px-3 py-1.5 text-xs rounded-md ${
                      view === "list" ? "bg-indigo-600 text-white" : "text-gray-400"
                    }`}
                  >
                    List
                  </button>
                </div>
                <button
                  type="button"
                  disabled={detecting}
                  onClick={onFindCameras}
                  className="dashboard-btn-primary text-xs"
                >
                  {detecting ? "…" : "Find new cameras"}
                </button>
              </>
            }
          />
          <div className="flex-1 min-h-0 overflow-y-auto p-4 lg:p-6">
            {view === "list" ? listContent : gridContent}
          </div>
        </>
      )}
    </div>
  );
}

export function DeviceDetailHeader({ cameraName, onBack, onSettings, online = true }) {
  return (
    <header className="shrink-0 border-b border-white/5 px-4 lg:px-6 py-3 flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-3 min-w-0">
        <button type="button" onClick={onBack} className="dashboard-btn-ghost text-xs">
          ← Back
        </button>
        <div className="min-w-0">
          <h1 className="text-lg font-semibold truncate">{cameraName}</h1>
          <p className={`text-xs flex items-center gap-1.5 ${online ? "text-emerald-400" : "text-gray-500"}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${online ? "bg-emerald-400" : "bg-gray-500"}`} />
            {online ? "Online" : "Offline"}
          </p>
        </div>
      </div>
      <button type="button" onClick={onSettings} className="dashboard-btn-secondary text-xs">
        Camera settings
      </button>
    </header>
  );
}

export function CameraInfoTable({ rows }) {
  return (
    <dl className="space-y-2 text-sm">
      {rows.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-4 py-1 border-b border-white/5 last:border-0">
          <dt className="text-gray-500 shrink-0">{label}</dt>
          <dd className="text-gray-200 text-right truncate font-mono text-xs">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Simple 24h timeline bar (visual; clips drive real navigation). */
export function PlaybackTimelineBar({ recordings = [] }) {
  const dayStart = new Date();
  dayStart.setHours(0, 0, 0, 0);
  const dayMs = 24 * 60 * 60 * 1000;
  const segments = recordings.slice(0, 80).map((r) => {
    const t = (r.mtime || 0) * 1000;
    const left = Math.max(0, Math.min(100, ((t - dayStart.getTime()) / dayMs) * 100));
    return { left, key: `${r.camId}-${r.name}` };
  });

  return (
    <div className="dashboard-card p-3">
      <div className="flex justify-between text-[10px] text-gray-500 mb-2 font-mono">
        <span>12 AM</span>
        <span>6 AM</span>
        <span>12 PM</span>
        <span>6 PM</span>
        <span>12 AM</span>
      </div>
      <div className="relative h-8 rounded-lg bg-[#0b1220] border border-white/5 overflow-hidden">
        {segments.map((s) => (
          <span
            key={s.key}
            className="absolute top-1 bottom-1 w-1 min-w-[3px] rounded-sm bg-indigo-500/70"
            style={{ left: `${s.left}%` }}
          />
        ))}
      </div>
      <div className="flex gap-4 mt-2 text-[10px] text-gray-500">
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-sm bg-indigo-500/70" /> Motion clip
        </span>
      </div>
    </div>
  );
}
