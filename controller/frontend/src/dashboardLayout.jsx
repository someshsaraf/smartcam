/**
 * Vigilance dashboard — laptop-first layouts aligned with design mockups.
 */

import { useEffect, useState } from "react";

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

function ShieldLogo({ className = "w-5 h-5" }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
      <path d="M12 3l7 4v6c0 4.5-3 7.5-7 8-4-.5-7-3.5-7-8V7l7-4z" />
    </svg>
  );
}

function LiveClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(t);
  }, []);
  return (
    <span className="font-mono text-[11px] text-white/90 tabular-nums">
      {now.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit" })}
    </span>
  );
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
            ? "bg-indigo-600/25 text-indigo-100 border border-indigo-500/50 shadow-[inset_0_0_0_1px_rgba(99,102,241,0.15)]"
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
    <div className="flex h-[100dvh] bg-[#0b0e14] text-white overflow-hidden">
      <aside className="hidden lg:flex flex-col w-[232px] shrink-0 border-r border-white/[0.06] bg-[#0a0f18]">
        <div className="px-5 py-5 border-b border-white/[0.06]">
          <div className="flex items-center gap-2.5">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-600/25 text-indigo-300 border border-indigo-500/20">
              <ShieldLogo />
            </span>
            <span className="font-bold tracking-[0.12em] text-sm">VIGILANCE</span>
          </div>
        </div>
        <nav className="flex-1 px-3 py-5 space-y-1 overflow-y-auto">{NAV_ITEMS.map((i) => navButton(i))}</nav>
        <div className="p-4 border-t border-white/[0.06] space-y-3">
          <div className="rounded-xl border border-emerald-500/20 bg-emerald-950/30 p-3">
            <div className="flex items-start gap-2">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-emerald-500/15 text-emerald-400">
                <ShieldLogo className="w-4 h-4" />
              </span>
              <div>
                <p className="text-[11px] font-semibold text-gray-200">System Status</p>
                <p className="text-[11px] text-emerald-400 mt-0.5">All systems operational</p>
              </div>
            </div>
          </div>
          <button type="button" className="w-full flex items-center justify-between text-xs text-gray-400 hover:text-gray-200 px-1">
            <span>{cameraCount} Camera{cameraCount === 1 ? "" : "s"} Online</span>
            <span className="text-gray-600">›</span>
          </button>
          <div className="flex items-center gap-2.5 pt-1 border-t border-white/[0.06]">
            <span className="h-9 w-9 rounded-full bg-indigo-600/40 flex items-center justify-center text-xs font-bold text-indigo-100">
              A
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium truncate">Admin User</p>
              <p className="text-[10px] text-gray-500 truncate">admin@vigilance.local</p>
            </div>
          </div>
        </div>
      </aside>

      {mobileNavOpen ? (
        <div className="lg:hidden fixed inset-0 z-50 flex" role="dialog" aria-modal="true">
          <button type="button" className="flex-1 bg-black/70" aria-label="Close menu" onClick={() => setMobileNavOpen(false)} />
          <aside className="w-[min(280px,85vw)] bg-[#0a0f18] border-l border-white/10 flex flex-col p-4">
            <p className="font-bold tracking-wide text-sm mb-4">VIGILANCE</p>
            <nav className="space-y-1 flex-1">{NAV_ITEMS.map((i) => navButton(i, true))}</nav>
          </aside>
        </div>
      ) : null}

      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        <header className="lg:hidden shrink-0 flex items-center justify-between gap-2 px-3 py-2.5 border-b border-white/[0.06] mobile-glass">
          <button type="button" onClick={() => setMobileNavOpen(true)} className="p-2 rounded-lg border border-white/10 text-gray-300" aria-label="Open menu">
            <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M4 7h16M4 12h16M4 17h16" strokeLinecap="round" />
            </svg>
          </button>
          <span className="text-sm font-semibold tracking-wide">VIGILANCE</span>
          <span className="w-9" />
        </header>

        <main className="flex-1 min-h-0 overflow-hidden flex flex-col bg-[#0b0e14]">{children}</main>

        <nav
          className="lg:hidden shrink-0 mobile-glass border-t border-white/[0.06] flex justify-around px-1 pt-1.5 pb-[max(0.5rem,env(safe-area-inset-bottom))]"
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
                className={`relative flex flex-col items-center gap-0.5 min-w-[4rem] py-1.5 px-2 rounded-xl ${active ? "text-indigo-300" : "text-gray-500"}`}
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

export function DashboardPageHeader({ eyebrow, title, badge, subtitle, actions, subActions, children }) {
  return (
    <header className="shrink-0 border-b border-white/[0.06] bg-[#0a0f18]/95 backdrop-blur-sm px-4 lg:px-8 py-3 lg:py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          {eyebrow ? (
            <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-indigo-400">{eyebrow}</p>
          ) : null}
          <div className="flex items-center gap-2 mt-1 flex-wrap">
            <h1 className="text-xl lg:text-2xl font-semibold text-white truncate">{title}</h1>
            {badge ? (
              <span className="text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-md bg-indigo-600/30 text-indigo-200 border border-indigo-500/40">
                {badge}
              </span>
            ) : null}
          </div>
          {subtitle ? <p className="text-xs text-gray-500 mt-1">{subtitle}</p> : null}
          {children}
        </div>
        {actions ? <div className="flex items-center gap-2 shrink-0 flex-wrap">{actions}</div> : null}
      </div>
      {subActions ? <div className="flex items-center justify-end gap-2 mt-3 pt-3 border-t border-white/[0.04]">{subActions}</div> : null}
    </header>
  );
}

function StatusPill({ variant, children }) {
  const styles =
    variant === "live"
      ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-300"
      : variant === "recording"
        ? "border-red-500/50 bg-red-950/40 text-red-200"
        : "border-white/10 bg-white/5 text-gray-400";
  return (
    <span className={`inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide px-3 py-1 rounded-full border ${styles}`}>
      {children}
    </span>
  );
}

function QuickActionIconWrap({ children, tone = "indigo" }) {
  const toneClass =
    tone === "amber"
      ? "dashboard-quick-action-icon-wrap dashboard-quick-action-icon-wrap-amber"
      : tone === "red"
        ? "dashboard-quick-action-icon-wrap dashboard-quick-action-icon-wrap-red"
        : "dashboard-quick-action-icon-wrap";
  return <span className={toneClass}>{children}</span>;
}

function QuickActionButton({ icon, label, hint, onClick, disabled, active, danger, iconTone = "indigo" }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`dashboard-quick-action ${active ? "dashboard-quick-action-active" : ""} ${danger ? "dashboard-quick-action-danger" : ""}`}
    >
      <QuickActionIconWrap tone={iconTone}>{icon}</QuickActionIconWrap>
      <span className="font-semibold text-sm text-gray-100">{label}</span>
      <span className="text-[11px] text-gray-500 leading-snug">{hint}</span>
    </button>
  );
}

export function LiveQuickActions({ onTalk, onSnapshot, onRecord, onSiren, onLight, recording, recordDisabled }) {
  const iconClass = "w-[18px] h-[18px]";
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 shrink-0">
      <QuickActionButton
        icon={
          <svg className={iconClass} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
            <path d="M12 14a3 3 0 003-3V6a3 3 0 10-6 0v5a3 3 0 003 3z" />
            <path d="M6 14h12v2a4 4 0 01-4 4h-4a4 4 0 01-4-4v-2z" strokeLinecap="round" />
          </svg>
        }
        label="Talk"
        hint="Start conversation"
        onClick={onTalk}
        disabled={!onTalk}
      />
      <QuickActionButton
        icon={
          <svg className={iconClass} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
            <path d="M4 8h4l2-3h4l2 3h4v11H4V8z" strokeLinejoin="round" />
            <circle cx="12" cy="13" r="3" />
          </svg>
        }
        label="Snapshot"
        hint="Capture image"
        onClick={onSnapshot}
        disabled={!onSnapshot}
      />
      <QuickActionButton
        icon={<span className={`h-2.5 w-2.5 rounded-full ${recording ? "bg-red-400 animate-pulse" : "bg-red-500"}`} />}
        label={recording ? "Recording" : "Record"}
        hint={recording ? "Stop clip" : "Start recording"}
        onClick={onRecord}
        disabled={recordDisabled}
        active={recording}
        danger
        iconTone="red"
      />
      <QuickActionButton
        icon={
          <svg className={iconClass} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
            <path d="M14 10V6a2 2 0 00-4 0v4M6 10h12l-1 10H7L6 10z" strokeLinejoin="round" />
          </svg>
        }
        label="Siren"
        hint="Trigger alarm"
        onClick={onSiren}
        disabled={!onSiren}
        iconTone="amber"
      />
      <QuickActionButton
        icon={
          <svg className={iconClass} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
            <path d="M9 18h6M10 22h4M12 2v2M5 8l1.5 1.5M19 8L17.5 9.5M4 14h16a2 2 0 002-2 8 8 0 00-8-8H10a8 8 0 00-8 8 2 2 0 002 2z" strokeLinecap="round" />
          </svg>
        }
        label="Light"
        hint="Toggle light"
        onClick={onLight}
        disabled={!onLight}
        iconTone="amber"
      />
    </div>
  );
}

function InsightIcon({ type }) {
  const c = "w-4 h-4";
  switch (type) {
    case "people":
      return (
        <svg className={c} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
          <circle cx="12" cy="7" r="3" />
          <path d="M6 20v-1a6 6 0 0112 0v1" strokeLinecap="round" />
        </svg>
      );
    case "vehicles":
      return (
        <svg className={c} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
          <path d="M6 16h12M6 16l-1-4h14l-1 4M8 16v2M16 16v2M7 12l1-4h8l1 4" strokeLinejoin="round" />
        </svg>
      );
    case "animals":
      return (
        <svg className={c} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
          <circle cx="8" cy="9" r="2" />
          <circle cx="16" cy="9" r="2" />
          <path d="M6 18c1-2 3-3 6-3s5 1 6 3" strokeLinecap="round" />
        </svg>
      );
    default:
      return (
        <svg className={c} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
          <circle cx="12" cy="12" r="8" />
          <circle cx="12" cy="12" r="2.5" fill="currentColor" stroke="none" />
        </svg>
      );
  }
}

function TimelineEventIcon({ live }) {
  const c = "w-3.5 h-3.5";
  if (live) {
    return (
      <svg className={c} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
        <rect x="3" y="6" width="12" height="10" rx="1.5" />
        <path d="M15 10l5-2v8l-5-2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  return (
    <svg className={c} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
      <circle cx="12" cy="7" r="3" />
      <path d="M6 20v-1a6 6 0 0112 0v1" strokeLinecap="round" />
    </svg>
  );
}

export function CameraInsights({
  peopleDetected = 0,
  vehiclesDetected = 0,
  animalsDetected = 0,
  recordingCount = 0,
  onViewAll,
}) {
  const rows = [
    { key: "people", label: "People detected", value: peopleDetected, icon: "people" },
    { key: "vehicles", label: "Vehicles detected", value: vehiclesDetected, icon: "vehicles" },
    { key: "animals", label: "Animal detected", value: animalsDetected, icon: "animals" },
    { key: "recordings", label: "Number of recordings", value: recordingCount, icon: "recordings" },
  ];

  return (
    <div className="flex flex-col min-h-0 h-full">
      <ul className="space-y-0.5 flex-1 min-h-0">
        {rows.map((row) => (
          <li
            key={row.key}
            className="flex items-center justify-between gap-3 py-2.5 border-b border-white/[0.05] last:border-0"
          >
            <div className="flex items-center gap-3 min-w-0">
              <span className="dashboard-insight-icon" aria-hidden>
                <InsightIcon type={row.icon} />
              </span>
              <span className="text-[13px] text-gray-300">{row.label}</span>
            </div>
            <span className="text-base font-semibold text-white tabular-nums shrink-0">{row.value}</span>
          </li>
        ))}
      </ul>
      {onViewAll ? (
        <button
          type="button"
          onClick={onViewAll}
          className="mt-3 w-full text-center text-[12px] font-medium text-indigo-300 hover:text-indigo-200 py-2 rounded-lg border border-indigo-500/20 bg-indigo-950/20"
        >
          View all insights →
        </button>
      ) : null}
    </div>
  );
}

export function ActivityTimeline({ items = [] }) {
  return (
    <ul className="space-y-0 relative pl-0.5">
      {items.length === 0 ? (
        <li className="text-xs text-gray-500 py-2">No activity yet.</li>
      ) : (
        items.map((item, i) => (
          <li
            key={`${item.label}-${item.time}-${i}`}
            className="grid grid-cols-[4.5rem_1.75rem_1fr] gap-x-2 pb-4 last:pb-0 items-start relative"
          >
            {i < items.length - 1 ? (
              <span
                className="absolute left-[5.6rem] top-7 bottom-0 w-px bg-indigo-500/35"
                aria-hidden
              />
            ) : null}
            <span className="text-[11px] text-gray-500 font-mono tabular-nums pt-1 shrink-0">
              {item.time}
            </span>
            <span
              className={`relative z-[1] flex h-7 w-7 items-center justify-center rounded-lg border ${
                item.live
                  ? "bg-indigo-600/25 border-indigo-500/35 text-indigo-200"
                  : "bg-indigo-600/15 border-indigo-500/25 text-indigo-300"
              }`}
            >
              <TimelineEventIcon live={item.live} />
            </span>
            <div className="min-w-0 pt-0.5">
              <p className="text-[13px] text-gray-100 font-medium leading-snug">{item.label}</p>
              {item.detail ? (
                <p className="text-[11px] text-gray-500 mt-0.5 leading-snug">{item.detail}</p>
              ) : null}
            </div>
          </li>
        ))
      )}
    </ul>
  );
}

export function LiveDashboardPage({
  cameraName,
  isPrimary,
  streamLabel,
  personCount,
  recording,
  onFindCameras,
  detecting,
  onFullscreen,
  cameras = [],
  activeCameraId,
  onSelectCamera,
  liveVideo,
  thumbStrip,
  cameraInsights,
  activityItems,
  cameraInfo,
  onTalk,
  onSnapshot,
  onRecord,
  onSiren,
  onLight,
  recordDisabled,
}) {
  const hasLive = streamLabel === "LIVE";

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <DashboardPageHeader
        eyebrow="Camera"
        title={cameraName || "Live view"}
        badge={isPrimary ? "Primary" : null}
        actions={
          <>
            <button type="button" disabled={detecting} onClick={onFindCameras} className="dashboard-btn-primary text-xs hidden sm:inline-flex">
              {detecting ? "…" : "+ Add Camera"}
            </button>
            <button type="button" className="dashboard-btn-icon" aria-label="Notifications">
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
                <path d="M15 17h5l-1.4-1.4A6 6 0 0118 11V8a6 6 0 00-12 0v3a6 6 0 01-.6 2.6L4 17h5" />
                <path d="M10 20a2 2 0 004 0" />
              </svg>
            </button>
            <span className="dashboard-btn-icon rounded-full bg-indigo-600/30 text-xs font-bold text-indigo-100">A</span>
          </>
        }
        subActions={
          <>
            <select className="dashboard-select text-xs" defaultValue="high" aria-label="Stream quality">
              <option value="high">High Quality 1080p</option>
              <option value="medium">Medium 720p</option>
              <option value="low">Low 480p</option>
            </select>
            <button type="button" onClick={onFullscreen} className="dashboard-btn-icon" aria-label="Fullscreen">
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
                <path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </>
        }
      />

      <div className="flex-1 min-h-0 overflow-y-auto flex flex-col px-4 lg:px-8 py-4 lg:py-5 gap-4">
        <div className="flex flex-wrap items-center gap-2 shrink-0">
          {hasLive ? (
            <StatusPill variant="live">
              <svg className="w-3 h-3" viewBox="0 0 24 24" fill="currentColor">
                <circle cx="12" cy="12" r="4" />
              </svg>
              Live
            </StatusPill>
          ) : (
            <StatusPill variant="offline">No signal</StatusPill>
          )}
          {recording ? (
            <StatusPill variant="recording">
              <span className="h-2 w-2 rounded-full bg-red-500 animate-pulse" />
              Recording
            </StatusPill>
          ) : null}
          {personCount > 0 ? (
            <span className="text-[11px] text-indigo-300 px-2.5 py-1 rounded-full border border-indigo-500/30 bg-indigo-500/10">
              {personCount} person{personCount === 1 ? "" : "s"}
            </span>
          ) : null}
        </div>

        <div className="flex flex-col shrink-0 gap-4">
          <div className="dashboard-video-shell dashboard-video-shell-hero dashboard-video-shell-live w-full shrink-0">
            <div className="absolute inset-x-0 top-0 z-20 flex items-center justify-between px-3 py-2.5 mobile-video-gradient-top pointer-events-none">
              {hasLive ? (
                <span className="pointer-events-auto inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-emerald-300 bg-emerald-500/20 border border-emerald-500/40 px-2 py-0.5 rounded">
                  Live
                </span>
              ) : null}
              <LiveClock />
              <span className="flex gap-1.5 pointer-events-none opacity-80">
                <span className="dashboard-video-chip">📷</span>
                <span className="dashboard-video-chip">🎤</span>
                <span className="dashboard-video-chip">🔊</span>
              </span>
            </div>

            <div className="absolute inset-0 z-10 [&>div]:h-full [&>div]:w-full">{liveVideo}</div>

            <div className="absolute inset-x-0 bottom-0 z-20 px-3 pb-3 pt-8 mobile-video-gradient-bottom pointer-events-none">
              <div className="flex items-end justify-between gap-2 pointer-events-auto">
                {cameras.length > 0 && onSelectCamera ? (
                  <select
                    className="dashboard-select text-xs max-w-[10rem] bg-black/60 backdrop-blur-sm"
                    value={activeCameraId ?? ""}
                    onChange={(e) => onSelectCamera(Number(e.target.value))}
                  >
                    {cameras.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                ) : null}
                <span className="ml-auto text-[10px] text-gray-300 flex items-center gap-2 font-mono">
                  <span>98%</span>
                  <svg className="w-4 h-4 text-emerald-400" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M2 20h20v2H2v-2zm2-4h2v4H4v-4zm4-6h2v10H8V10zm4-3h2v13h-2V7zm4 2h2v11h-2V9z" />
                  </svg>
                </span>
              </div>
            </div>
          </div>

          {thumbStrip ? <div className="shrink-0">{thumbStrip}</div> : null}

          <LiveQuickActions
            onTalk={onTalk}
            onSnapshot={onSnapshot}
            onRecord={onRecord}
            onSiren={onSiren}
            onLight={onLight}
            recording={recording}
            recordDisabled={recordDisabled}
          />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 shrink-0 pb-2">
          <div className="dashboard-panel">
            <div className="flex justify-between items-center mb-3 gap-2 shrink-0">
              <h2 className="dashboard-panel-title">Camera Insights</h2>
              <select className="dashboard-select text-[10px] py-1 px-2 w-auto" defaultValue="today" aria-label="Insights period">
                <option value="today">Today</option>
              </select>
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto">{cameraInsights}</div>
          </div>

          <div className="dashboard-panel">
            <div className="flex items-center justify-between mb-3 shrink-0">
              <h2 className="dashboard-panel-title">Activity Timeline</h2>
              {hasLive ? (
                <span className="text-[10px] font-medium text-emerald-400 flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/25">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  Live
                </span>
              ) : null}
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto pr-1">
              <ActivityTimeline items={activityItems} />
            </div>
          </div>

          <div className="dashboard-panel">
            <h2 className="dashboard-panel-title mb-3">Camera Info</h2>
            <div className="flex-1 min-h-0 overflow-y-auto">{cameraInfo}</div>
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
  recordingsCount = 0,
  onSync,
  syncing,
  renderCameraThumb,
  recordingsGrid,
}) {
  const today = new Date().toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <DashboardPageHeader
        eyebrow="Playback"
        title={cameraName || "Select camera"}
        actions={
          <>
            <input type="text" readOnly value={today} className="dashboard-input text-xs w-[9.5rem] text-center" aria-label="Date" />
            <button type="button" className="dashboard-btn-secondary text-xs">
              Filter
            </button>
            <button type="button" className="dashboard-btn-primary text-xs" onClick={onSync} disabled={syncing}>
              {syncing ? "…" : "Export"}
            </button>
          </>
        }
      />
      {timeline ? <div className="shrink-0 px-4 lg:px-8 py-3 border-b border-white/[0.06] bg-[#0a0f18]/50">{timeline}</div> : null}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        <aside className="hidden md:flex flex-col w-[220px] lg:w-[240px] shrink-0 border-r border-white/[0.06] bg-[#0a0f18] p-3 overflow-y-auto">
          <input type="search" placeholder="Search cameras…" className="dashboard-input text-xs mb-3" />
          <p className="text-[10px] font-semibold uppercase tracking-wider text-gray-500 mb-2 px-0.5">Cameras</p>
          <ul className="space-y-2">
            {cameras.map((c) => {
              const active = String(c.id) === String(activeCameraId);
              return (
                <li key={c.id}>
                  <button
                    type="button"
                    onClick={() => onSelectCamera(c.id)}
                    className={`w-full text-left rounded-xl border overflow-hidden transition-all ${
                      active
                        ? "border-indigo-500/60 ring-1 ring-indigo-500/30 bg-indigo-500/10"
                        : "border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.04]"
                    }`}
                  >
                    <div className="aspect-video bg-black/80 relative overflow-hidden">
                      {renderCameraThumb ? renderCameraThumb(c) : (
                        <span className="absolute inset-0 flex items-center justify-center text-[10px] text-gray-600">Preview</span>
                      )}
                    </div>
                    <div className="px-2.5 py-2">
                      <span className="block truncate text-sm font-medium">{c.name}</span>
                      <span className="text-[10px] text-emerald-400 flex items-center gap-1 mt-0.5">
                        <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                        Online
                      </span>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        </aside>

        <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
          <div className="md:hidden px-4 pt-3 shrink-0">
            <label className="text-[10px] text-gray-500 uppercase tracking-wider">Camera</label>
            <select className="dashboard-input mt-1 text-sm" value={activeCameraId ?? ""} onChange={(e) => onSelectCamera(Number(e.target.value))}>
              {cameras.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
          <div className="flex-1 min-h-0 overflow-hidden flex flex-col">{recordingsGrid}</div>
        </div>
      </div>
    </div>
  );
}

export function DevicesDashboardPage({
  view,
  onViewChange,
  onFindCameras,
  detecting,
  deviceCount = 0,
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
            subtitle="Manage cameras, streams, and recording settings"
            actions={
              <>
                <div className="hidden sm:flex rounded-lg border border-white/10 p-0.5 bg-black/20">
                  <button
                    type="button"
                    onClick={() => onViewChange("grid")}
                    className={`px-3 py-1.5 text-xs rounded-md font-medium ${view === "grid" ? "bg-indigo-600 text-white shadow" : "text-gray-400"}`}
                  >
                    Grid
                  </button>
                  <button
                    type="button"
                    onClick={() => onViewChange("list")}
                    className={`px-3 py-1.5 text-xs rounded-md font-medium ${view === "list" ? "bg-indigo-600 text-white shadow" : "text-gray-400"}`}
                  >
                    List
                  </button>
                </div>
                <button type="button" disabled={detecting} onClick={onFindCameras} className="dashboard-btn-primary text-xs">
                  {detecting ? "…" : "Find new cameras"}
                </button>
              </>
            }
          />
          <div className="flex-1 min-h-0 overflow-y-auto p-4 lg:px-8 lg:py-6">
            {view === "list" ? listContent : gridContent}
            {deviceCount > 0 && view !== "detail" ? (
              <p className="text-xs text-gray-500 mt-4 px-1">{deviceCount} device{deviceCount === 1 ? "" : "s"}</p>
            ) : null}
          </div>
        </>
      )}
    </div>
  );
}

export function DeviceCard({ name, isPrimary, online, resolution, fps, ip, preview, onClick, onMenu }) {
  return (
    <button type="button" onClick={onClick} className="dashboard-device-card group w-full text-left">
      <div className="relative aspect-[16/10] bg-black overflow-hidden">
        {preview}
        {online ? (
          <span className="absolute bottom-2 left-2 z-10 inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-emerald-300 bg-emerald-500/20 border border-emerald-500/40 px-2 py-0.5 rounded">
            Live
          </span>
        ) : null}
        {onMenu ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onMenu();
            }}
            className="absolute top-2 right-2 z-10 p-1.5 rounded-lg bg-black/50 text-gray-300 hover:text-white opacity-0 group-hover:opacity-100 transition-opacity"
            aria-label="More actions"
          >
            ⋯
          </button>
        ) : null}
      </div>
      <div className="p-3.5 space-y-1.5">
        <div className="flex items-center gap-2 min-w-0">
          <p className="font-semibold text-sm truncate">{name}</p>
          {isPrimary ? (
            <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded bg-indigo-600/30 text-indigo-200 border border-indigo-500/30 shrink-0">
              Primary
            </span>
          ) : null}
        </div>
        <p className={`text-xs flex items-center gap-1.5 ${online ? "text-emerald-400" : "text-gray-500"}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${online ? "bg-emerald-400" : "bg-gray-500"}`} />
          {online ? "Online" : "Offline"}
        </p>
        <p className="text-[11px] text-gray-500 font-mono">
          {resolution} · {fps} FPS
        </p>
        <p className="text-[11px] text-gray-600 font-mono truncate">{ip || "—"}</p>
      </div>
    </button>
  );
}

export function DeviceDetailHeader({ cameraName, onBack, onSettings, online = true, isPrimary }) {
  return (
    <header className="shrink-0 border-b border-white/[0.06] px-4 lg:px-8 py-3 flex flex-wrap items-center justify-between gap-3 bg-[#0a0f18]/80">
      <div className="flex items-center gap-3 min-w-0">
        <button type="button" onClick={onBack} className="text-xs text-indigo-400 hover:text-indigo-300 font-medium">
          ← Back to devices
        </button>
        <div className="min-w-0 hidden sm:block w-px h-6 bg-white/10" />
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-lg font-semibold truncate">{cameraName}</h1>
            {isPrimary ? (
              <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded bg-indigo-600/30 text-indigo-200 border border-indigo-500/30">
                Primary
              </span>
            ) : null}
          </div>
          <p className={`text-xs flex items-center gap-1.5 mt-0.5 ${online ? "text-emerald-400" : "text-gray-500"}`}>
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

export function DeviceConfigTabs({ activeTab = "general", onTabChange, children }) {
  const tabs = ["General", "Stream", "Detection", "Recording", "Advanced"];
  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="shrink-0 border-b border-white/[0.06] px-4 lg:px-8 overflow-x-auto">
        <div className="flex gap-1 min-w-max">
          {tabs.map((t) => {
            const id = t.toLowerCase();
            const active = activeTab === id;
            return (
              <button
                key={id}
                type="button"
                onClick={() => onTabChange?.(id)}
                className={`px-4 py-3 text-xs font-medium border-b-2 transition-colors ${
                  active
                    ? "border-indigo-500 text-indigo-200"
                    : "border-transparent text-gray-500 hover:text-gray-300"
                }`}
              >
                {t}
              </button>
            );
          })}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-4 lg:p-8">{children}</div>
    </div>
  );
}

export function CameraInfoTable({ rows }) {
  return (
    <dl className="space-y-0 text-sm">
      {rows.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-4 py-2.5 border-b border-white/[0.06] last:border-0">
          <dt className="text-gray-500 shrink-0 text-xs">{label}</dt>
          <dd className="text-gray-100 text-right truncate text-xs font-medium">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function PlaybackTimelineBar({ recordings = [] }) {
  const dayStart = new Date();
  dayStart.setHours(0, 0, 0, 0);
  const dayMs = 24 * 60 * 60 * 1000;
  const now = new Date();
  const playhead = Math.max(0, Math.min(100, ((now.getTime() - dayStart.getTime()) / dayMs) * 100));

  const segments = recordings.slice(0, 120).map((r) => {
    const t = (r.mtime || 0) * 1000;
    const left = Math.max(0, Math.min(99.2, ((t - dayStart.getTime()) / dayMs) * 100));
    const w = 0.35 + Math.min(2.5, (Number(r.size) || 0) / 8e6);
    return { left, w, key: `${r.camId}-${r.name}`, motion: true };
  });

  return (
    <div className="dashboard-card p-4">
      <div className="flex justify-between text-[10px] text-gray-500 mb-2 font-mono px-0.5">
        <span>12:00 AM</span>
        <span className="hidden sm:inline">6:00 AM</span>
        <span>12:00 PM</span>
        <span className="hidden sm:inline">6:00 PM</span>
        <span>12:00 AM</span>
      </div>
      <div className="relative h-10 rounded-lg bg-[#060a12] border border-white/[0.06] overflow-visible">
        {segments.map((s) => (
          <span
            key={s.key}
            className="absolute top-1.5 bottom-1.5 rounded-sm bg-indigo-500/75"
            style={{ left: `${s.left}%`, width: `${s.w}%` }}
            title="Motion clip"
          />
        ))}
        <span
          className="absolute top-0 bottom-0 w-0.5 bg-white shadow-[0_0_8px_rgba(255,255,255,0.6)] z-10"
          style={{ left: `${playhead}%` }}
        >
          <span className="absolute -top-7 left-1/2 -translate-x-1/2 whitespace-nowrap rounded bg-black/90 border border-white/10 px-2 py-0.5 text-[10px] font-mono text-gray-200">
            {now.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit" })}
          </span>
        </span>
      </div>
      <div className="flex flex-wrap gap-4 mt-3 text-[10px] text-gray-500">
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-2 rounded-sm bg-indigo-500/75" /> Motion
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-2 rounded-sm bg-sky-500/50" /> Continuous
        </span>
      </div>
    </div>
  );
}
