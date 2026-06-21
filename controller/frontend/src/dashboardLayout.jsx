/**
 * Vigilance dashboard — laptop-first layouts aligned with design mockups.
 */

import { useEffect, useState } from "react";
import {
  Camera,
  Car,
  Cd,
  Element4,
  Flash,
  HambergerMenu,
  Lamp,
  Maximize3,
  Microphone2,
  MonitorRecorder,
  Notification,
  People,
  Pet,
  Profile,
  RecordCircle,
  Refresh,
  SearchZoomIn,
  SearchZoomOut,
  Setting2,
  ShieldTick,
  VideoPlay,
  VideoSquare,
  VolumeHigh,
  Wifi,
} from "iconsax-react";

const NAV_ITEMS = [
  { id: "live", label: "Live View", icon: "live" },
  { id: "clips", label: "Playback", icon: "playback" },
  { id: "events", label: "Events", icon: "events" },
  { id: "devices", label: "Devices", icon: "devices" },
  { id: "people", label: "People", icon: "people" },
  { id: "vehicles", label: "Vehicles", icon: "vehicles" },
  { id: "settings", label: "Settings", icon: "settings" },
];

const CAMERA_TAB_DOTS = ["bg-amber-400", "bg-indigo-400", "bg-emerald-400", "bg-rose-400", "bg-sky-400", "bg-violet-400"];

function NavIcon({ name, className = "w-5 h-5" }) {
  const sizeMatch = /w-(\d+)/.exec(className);
  const size = sizeMatch ? Number(sizeMatch[1]) * 4 : 20;
  const common = { size, variant: "Bulk", className };
  switch (name) {
    case "live":
      return <VideoSquare {...common} color="#a5b4fc" aria-hidden />;
    case "playback":
      return <VideoPlay {...common} color="#a5b4fc" aria-hidden />;
    case "events":
      return <Flash {...common} color="#a5b4fc" aria-hidden />;
    case "devices":
      return <Element4 {...common} color="#a5b4fc" aria-hidden />;
    case "people":
      return <People {...common} color="#a5b4fc" aria-hidden />;
    case "vehicles":
      return <Car {...common} color="#a5b4fc" aria-hidden />;
    case "settings":
      return <Setting2 {...common} color="#a5b4fc" aria-hidden />;
    default:
      return null;
  }
}

function ShieldLogo({ className = "w-5 h-5" }) {
  return <ShieldTick className={className} size={20} variant="Bulk" color="#a5b4fc" aria-hidden />;
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
  cameras = [],
  activeCameraId,
  onSelectCamera,
  children,
}) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const hasCameraSelector = Array.isArray(cameras) && cameras.length > 0 && typeof onSelectCamera === "function";

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
            ? "bg-indigo-600/20 text-indigo-100 border border-indigo-500/40 nav-item-active-bar"
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
          <div className="rounded-xl border border-emerald-500/20 bg-emerald-950/20 p-3">
            <div className="flex items-center gap-2 mb-2">
              <ShieldTick size={16} variant="Bulk" color="#34d399" aria-hidden />
              <p className="text-[12px] font-semibold text-gray-100">System Status</p>
            </div>
            <p className="text-[11px] text-emerald-400 mb-2.5">All systems operational</p>
            {hasCameraSelector ? (
              <ul className="space-y-0.5 border-t border-white/[0.06] pt-2">
                {cameras.map((c) => {
                  const selected = String(c.id) === String(activeCameraId);
                  return (
                    <li key={c.id}>
                      <button
                        type="button"
                        onClick={() => onSelectCamera(c.id)}
                        className={`system-status-camera-row w-full text-left rounded-lg px-2 -mx-2 transition-colors ${
                          selected ? "bg-indigo-500/15 text-indigo-100" : "text-gray-400 hover:text-gray-200 hover:bg-white/5"
                        }`}
                      >
                        <span className="truncate">{c.name}</span>
                        <span className="shrink-0 text-[10px] font-semibold text-emerald-400 bg-emerald-500/15 border border-emerald-500/30 px-1.5 py-0.5 rounded">
                          Live
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="text-[11px] text-gray-500">No cameras configured</p>
            )}
          </div>
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
            <HambergerMenu className="w-5 h-5" size={20} variant="Outline" color="#e5e7eb" aria-hidden />
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

export function DashboardPageHeader({ eyebrow, title, badge, badges, subtitle, actions, subActions, children, compact = false }) {
  const toolbar =
    actions || subActions ? (
      <div className={`flex items-center shrink-0 flex-nowrap justify-end ${compact ? "gap-1" : "gap-2"}`}>
        {actions}
        {subActions}
      </div>
    ) : null;

  return (
    <header
      className={`shrink-0 border-b border-white/[0.06] bg-[#0a0f18]/95 backdrop-blur-sm px-4 lg:px-8 ${
        compact ? "py-2 lg:py-2.5" : "py-3 lg:py-4"
      }`}
    >
      <div
        className={`flex ${compact ? "items-center justify-between gap-2 w-full flex-nowrap" : "flex-wrap justify-between items-start gap-3"}`}
      >
        <div className={compact ? "min-w-0 flex-1" : "min-w-0"}>
          {eyebrow ? (
            <p
              className={`font-semibold uppercase text-indigo-400 ${
                compact ? "hidden lg:block text-[9px] tracking-[0.18em]" : "text-[10px] tracking-[0.22em]"
              }`}
            >
              {eyebrow}
            </p>
          ) : null}
          <div className={`flex items-center gap-2 min-w-0 ${compact ? "lg:mt-0.5" : "mt-1 flex-wrap"}`}>
            <h1
              className={`font-semibold text-white truncate ${compact ? "text-base sm:text-lg lg:text-xl" : "text-xl lg:text-2xl"}`}
            >
              {title}
            </h1>
            {badge ? (
              <span
                className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-md bg-indigo-600/30 text-indigo-200 border border-indigo-500/40 ${
                  compact ? "hidden lg:inline-flex" : ""
                }`}
              >
                {badge}
              </span>
            ) : null}
            {badges ? (
              <span
                className={`items-center gap-2 flex-wrap ${
                  compact ? "hidden lg:inline-flex" : "inline-flex"
                }`}
              >
                {badges}
              </span>
            ) : null}
          </div>
          {subtitle ? <p className={`text-gray-500 ${compact ? "text-[11px] mt-0.5" : "text-xs mt-1"}`}>{subtitle}</p> : null}
          {children}
        </div>
        {compact ? toolbar : actions ? <div className="flex items-center gap-2 shrink-0 flex-wrap">{actions}</div> : null}
      </div>
      {!compact && subActions ? (
        <div className="flex items-center justify-end gap-2 mt-3 pt-3 border-t border-white/[0.04]">{subActions}</div>
      ) : null}
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
    <span className={`inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide px-2.5 py-0.5 rounded-full border ${styles}`}>
      {children}
    </span>
  );
}

function QuickActionIconWrap({ children, tone = "indigo" }) {
  const toneSuffix =
    tone === "amber" || tone === "red" || tone === "sky" || tone === "violet" || tone === "rose"
      ? ` dashboard-quick-action-icon-wrap-${tone}`
      : "";
  return <span className={`dashboard-quick-action-icon-wrap${toneSuffix}`}>{children}</span>;
}

function QuickActionButton({ icon, label, hint, onClick, disabled, active, danger, iconTone = "indigo", overlay = false }) {
  const overlayClass = overlay ? " dashboard-quick-action-overlay" : "";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`dashboard-quick-action${overlayClass} ${active ? "dashboard-quick-action-active" : ""} ${danger ? "dashboard-quick-action-danger" : ""}`}
    >
      <QuickActionIconWrap tone={iconTone}>{icon}</QuickActionIconWrap>
      <span className="dashboard-quick-action-text">
        <span className="dashboard-quick-action-label">{label}</span>
        <span className="dashboard-quick-action-hint">{hint}</span>
      </span>
    </button>
  );
}

function QuickActionIconButton({ icon, label, onClick, disabled, active, danger, iconTone = "indigo" }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      className={`dashboard-quick-action-icon-only ${active ? "dashboard-quick-action-active" : ""} ${danger ? "dashboard-quick-action-danger" : ""}`}
    >
      <QuickActionIconWrap tone={iconTone}>{icon}</QuickActionIconWrap>
    </button>
  );
}

function recordActionLabels(recordingMode, recording) {
  if (recordingMode === "motion") {
    return {
      label: recording ? "Recording" : "Auto rec",
      hint: recording ? "Motion clip active" : "Starts when person detected",
      disabled: true,
    };
  }
  return {
    label: recording ? "Stop recording" : "Record",
    hint: recording ? "Stop clip" : "Start recording",
    disabled: false,
  };
}

export function LiveQuickActions({
  onTalk,
  onSnapshot,
  onRecord,
  onSiren,
  onLight,
  recording,
  recordDisabled,
  recordingMode = "motion",
}) {
  const size = 20;
  const rec = recordActionLabels(recordingMode, recording);
  return (
    <div className="live-quick-actions-icons" role="toolbar" aria-label="Quick actions">
      <QuickActionIconButton
        icon={<Microphone2 size={size} variant="Bulk" color="#a78bfa" aria-hidden />}
        label="Talk"
        onClick={onTalk}
        disabled={!onTalk}
        iconTone="violet"
      />
      <QuickActionIconButton
        icon={<Camera size={size} variant="Bulk" color="#38bdf8" aria-hidden />}
        label="Snapshot"
        onClick={onSnapshot}
        disabled={!onSnapshot}
        iconTone="sky"
      />
      <QuickActionIconButton
        icon={
          <RecordCircle
            size={size}
            variant="Bulk"
            color={recording ? "#f87171" : "#ef4444"}
            className={recording ? "animate-pulse" : undefined}
            aria-hidden
          />
        }
        label={rec.label}
        onClick={onRecord}
        disabled={rec.disabled || recordDisabled}
        active={recording}
        danger
        iconTone="red"
      />
      <QuickActionIconButton
        icon={<Notification size={size} variant="Bulk" color="#fbbf24" aria-hidden />}
        label="Siren"
        onClick={onSiren}
        disabled={!onSiren}
        iconTone="amber"
      />
      <QuickActionIconButton
        icon={<Lamp size={size} variant="Bulk" color="#facc15" aria-hidden />}
        label="Light"
        onClick={onLight}
        disabled={!onLight}
        iconTone="amber"
      />
    </div>
  );
}

export function VideoOverlayActions({
  onTalk,
  onSnapshot,
  onRecord,
  onSiren,
  onLight,
  recording,
  recordDisabled,
  recordingMode = "motion",
}) {
  const size = 18;
  const rec = recordActionLabels(recordingMode, recording);
  return (
    <div className="video-overlay-actions pointer-events-none">
      <div className="video-overlay-cards pointer-events-auto">
        <QuickActionButton
          overlay
          icon={<Microphone2 size={size} variant="Bulk" color="#a78bfa" aria-hidden />}
          label="Talk"
          hint="Start conversation"
          onClick={onTalk}
          disabled={!onTalk}
          iconTone="violet"
        />
        <QuickActionButton
          overlay
          icon={<Camera size={size} variant="Bulk" color="#38bdf8" aria-hidden />}
          label="Snapshot"
          hint="Capture image"
          onClick={onSnapshot}
          disabled={!onSnapshot}
          iconTone="sky"
        />
        <QuickActionButton
          overlay
          icon={
            <RecordCircle
              size={size}
              variant="Bulk"
              color={recording ? "#f87171" : "#ef4444"}
              className={recording ? "animate-pulse" : undefined}
              aria-hidden
            />
          }
          label={rec.label}
          hint={rec.hint}
          onClick={onRecord}
          disabled={rec.disabled || recordDisabled}
          active={recording}
          danger
          iconTone="red"
        />
        <QuickActionButton
          overlay
          icon={<Notification size={size} variant="Bulk" color="#fbbf24" aria-hidden />}
          label="Siren"
          hint="Trigger alarm"
          onClick={onSiren}
          disabled={!onSiren}
          iconTone="amber"
        />
        <QuickActionButton
          overlay
          icon={<Lamp size={size} variant="Bulk" color="#facc15" aria-hidden />}
          label="Light"
          hint="Toggle light"
          onClick={onLight}
          disabled={!onLight}
          iconTone="amber"
        />
      </div>
    </div>
  );
}

function InsightIcon({ type }) {
  switch (type) {
    case "people":
      return <People size={18} variant="Bulk" color="#a78bfa" aria-hidden />;
    case "vehicles":
      return <Car size={18} variant="Bulk" color="#38bdf8" aria-hidden />;
    case "animals":
      return <Pet size={18} variant="Bulk" color="#fbbf24" aria-hidden />;
    default:
      return <MonitorRecorder size={18} variant="Bulk" color="#fb7185" aria-hidden />;
  }
}

function TimelineEventIcon({ live, kind }) {
  const size = 14;
  if (live || kind === "live") {
    return <VideoSquare size={size} variant="Bulk" color="#a5b4fc" aria-hidden />;
  }
  if (kind === "vehicle") {
    return <Car size={size} variant="Bulk" color="#38bdf8" aria-hidden />;
  }
  if (kind === "motion") {
    return <Flash size={size} variant="Bulk" color="#fbbf24" aria-hidden />;
  }
  if (kind === "recording") {
    return <Cd size={size} variant="Bulk" color="#fb7185" aria-hidden />;
  }
  return <Profile size={size} variant="Bulk" color="#a5b4fc" aria-hidden />;
}

export function CameraInsights({
  peopleDetected = 0,
  vehiclesDetected = 0,
  animalsDetected = 0,
  recordingCount = 0,
  onViewAll,
}) {
  const cards = [
    {
      key: "people",
      label: "People Detected",
      value: peopleDetected,
      trend: "+12%",
      trendUp: true,
      icon: "people",
      iconBg: "bg-violet-500/15 border-violet-500/25",
    },
    {
      key: "vehicles",
      label: "Vehicles Detected",
      value: vehiclesDetected,
      trend: "-5%",
      trendUp: false,
      icon: "vehicles",
      iconBg: "bg-sky-500/15 border-sky-500/25",
    },
    {
      key: "animals",
      label: "Animals Detected",
      value: animalsDetected,
      trend: "+8%",
      trendUp: true,
      icon: "animals",
      iconBg: "bg-amber-500/15 border-amber-500/25",
    },
    {
      key: "recordings",
      label: "Total Recordings",
      value: recordingCount,
      trend: "+7%",
      trendUp: true,
      icon: "recordings",
      iconBg: "bg-rose-500/15 border-rose-500/25",
    },
  ];

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
        {cards.map((card) => (
          <div key={card.key} className="insight-stat-card">
            <div className="flex items-start justify-between gap-2">
              <span className={`flex h-9 w-9 items-center justify-center rounded-lg border ${card.iconBg}`}>
                <InsightIcon type={card.icon} />
              </span>
              <span
                className={`text-[11px] font-semibold tabular-nums ${
                  card.trendUp ? "text-emerald-400" : "text-rose-400"
                }`}
              >
                {card.trend}
              </span>
            </div>
            <div>
              <p className="text-2xl font-bold text-white tabular-nums leading-none">{card.value}</p>
              <p className="text-[11px] text-gray-500 mt-1">Today</p>
            </div>
            <p className="text-[12px] text-gray-400 leading-snug">{card.label}</p>
          </div>
        ))}
      </div>
      {onViewAll ? (
        <button
          type="button"
          onClick={onViewAll}
          className="self-start text-[12px] font-medium text-indigo-300 hover:text-indigo-200"
        >
          View all insights →
        </button>
      ) : null}
    </div>
  );
}

export function LiveCameraTabBar({ cameras = [], activeId, onSelect, onAdd, detecting }) {
  if (!cameras.length) return null;
  return (
    <div className="shrink-0 px-4 lg:px-6 py-3 border-b border-white/[0.06] bg-[#0a0f18]/60">
      <div className="flex items-center gap-2 mobile-scroll-x pb-0.5">
        {cameras.map((c, idx) => {
          const active = String(c.id) === String(activeId);
          const dot = CAMERA_TAB_DOTS[idx % CAMERA_TAB_DOTS.length];
          return (
            <button
              key={c.id}
              type="button"
              onClick={() => onSelect(c.id)}
              className={`live-camera-tab ${active ? "live-camera-tab-active" : "live-camera-tab-idle"}`}
            >
              <span className={`h-2 w-2 rounded-full shrink-0 ${dot}`} aria-hidden />
              {c.name}
            </button>
          );
        })}
        {typeof onAdd === "function" ? (
          <button
            type="button"
            disabled={detecting}
            onClick={onAdd}
            className="live-camera-tab live-camera-tab-idle text-indigo-300 border-indigo-500/30 bg-indigo-600/10 hover:bg-indigo-600/20"
          >
            {detecting ? "…" : "+ Add Camera"}
          </button>
        ) : null}
      </div>
    </div>
  );
}

export function QuickCamerasGrid({ cameras = [], activeId, onSelect, renderThumb }) {
  if (!cameras.length || typeof renderThumb !== "function") return null;
  return (
    <div>
      <div className="flex items-center justify-between mb-3 gap-2">
        <h2 className="text-sm font-semibold text-gray-100">Quick Cameras</h2>
        <button type="button" className="text-[12px] text-indigo-300 hover:text-indigo-200">
          View All
        </button>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {cameras.slice(0, 4).map((c) => {
          const active = String(c.id) === String(activeId);
          return (
            <button
              key={c.id}
              type="button"
              onClick={() => onSelect(c.id)}
              className={`quick-camera-card group ${active ? "ring-1 ring-indigo-500/50" : ""}`}
            >
              <div className="aspect-video bg-black relative overflow-hidden pointer-events-none">
                {renderThumb(c)}
                {active ? (
                  <span className="absolute top-2 left-2 text-[9px] font-bold uppercase tracking-wide text-red-300 bg-red-500/20 border border-red-500/40 px-1.5 py-0.5 rounded">
                    Live
                  </span>
                ) : null}
              </div>
              <div className="px-2.5 py-2 flex items-center justify-between gap-1">
                <span className="text-[12px] font-medium text-gray-200 truncate">{c.name}</span>
                <span className="text-gray-600 text-xs">⋯</span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function RecentEventsStrip({ events = [], onViewAll }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-3 gap-2">
        <h2 className="text-sm font-semibold text-gray-100">Recent Events</h2>
        {onViewAll ? (
          <button type="button" onClick={onViewAll} className="text-[12px] text-indigo-300 hover:text-indigo-200">
            View All
          </button>
        ) : null}
      </div>
      {events.length === 0 ? (
        <p className="text-xs text-gray-500">No events yet today.</p>
      ) : (
        <div className="flex gap-3 mobile-scroll-x pb-1">
          {events.map((ev, i) => (
            <div key={`${ev.ts}-${i}`} className="recent-event-card">
              <div className="aspect-video bg-[#161d2c] flex items-center justify-center text-gray-600 text-[10px]">
                {ev.thumb || "Event"}
              </div>
              <div className="p-2.5">
                <p className="text-[10px] text-gray-500 font-mono">{ev.time}</p>
                <p className="text-[12px] font-medium text-gray-100 mt-0.5 leading-snug">{ev.label}</p>
                <p className="text-[10px] text-gray-500 truncate mt-0.5">{ev.detail}</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function AiProtectionCard() {
  return (
    <div className="ai-protection-card relative overflow-hidden">
      <div className="relative z-[1] max-w-[70%]">
        <p className="text-[13px] font-semibold text-white leading-snug">AI-Powered Protection</p>
        <p className="text-[11px] text-gray-400 mt-1.5 leading-relaxed">
          Smart detection, real-time alerts, and complete peace of mind.
        </p>
        <button type="button" className="mt-3 dashboard-btn-primary dashboard-btn-sm">
          Learn More
        </button>
      </div>
      <span
        className="absolute right-2 bottom-2 flex h-20 w-20 items-center justify-center rounded-2xl bg-indigo-500/10 border border-indigo-500/20 opacity-80 select-none pointer-events-none"
        aria-hidden
      >
        <MonitorRecorder size={40} variant="Bulk" color="#6366f1" />
      </span>
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
            className="grid grid-cols-[4.5rem_1.75rem_1fr] gap-x-2 pb-3 last:pb-0 items-start relative"
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
              <TimelineEventIcon live={item.live} kind={item.kind} />
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

/** Camera / mic / speaker / fullscreen chips overlaid on the live video (hero tile). */
export function LiveVideoOverlayControls({
  muted = true,
  onToggleMute,
  onFullscreen,
  onSnapshot,
  onTalk,
  className = "",
}) {
  return (
    <div
      className={`absolute top-12 right-3 z-30 flex items-center gap-1.5 pointer-events-auto ${className}`.trim()}
      role="toolbar"
      aria-label="Live video controls"
    >
      <button
        type="button"
        className="dashboard-video-chip"
        aria-label="Snapshot"
        title="Snapshot"
        onClick={onSnapshot}
        disabled={!onSnapshot}
      >
        <Camera size={14} variant="Outline" color="#f1f5f9" aria-hidden />
      </button>
      <button
        type="button"
        className="dashboard-video-chip"
        aria-label="Talk"
        title="Talk"
        onClick={onTalk}
        disabled={!onTalk}
      >
        <Microphone2 size={14} variant="Outline" color="#f1f5f9" aria-hidden />
      </button>
      <button
        type="button"
        className={`dashboard-video-chip ${muted ? "" : "dashboard-video-chip-active"}`}
        aria-label={muted ? "Unmute live audio" : "Mute live audio"}
        title={muted ? "Unmute" : "Mute"}
        onClick={onToggleMute}
        disabled={!onToggleMute}
      >
        {muted ? (
          <VolumeHigh size={14} variant="Outline" color="#f1f5f9" aria-hidden />
        ) : (
          <VolumeHigh size={14} variant="Bold" color="#34d399" aria-hidden />
        )}
      </button>
      <button
        type="button"
        className="dashboard-video-chip"
        aria-label="Fullscreen"
        title="Fullscreen"
        onClick={onFullscreen}
        disabled={!onFullscreen}
      >
        <Maximize3 size={14} variant="Outline" color="#f1f5f9" aria-hidden />
      </button>
    </div>
  );
}

export function LiveDashboardPage({
  cameraName,
  streamLabel,
  personCount,
  animalCount = 0,
  recording,
  onFindCameras,
  detecting,
  onFullscreen,
  cameras = [],
  activeCameraId,
  onSelectCamera,
  liveVideo,
  renderCameraThumb,
  cameraInsights,
  activityItems,
  recentEvents = [],
  onViewAllEvents,
  onTalk,
  onSnapshot,
  onRecord,
  onSiren,
  onLight,
  recordDisabled,
  recordingMode = "motion",
}) {
  const hasLive = streamLabel === "LIVE";

  return (
    <div className="live-dashboard-page flex flex-col flex-1 min-h-0">
      <header className="live-page-header">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-bold text-white tracking-tight">Live View</h1>
            <p className="text-sm text-gray-500 mt-0.5">Monitor your cameras in real-time.</p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button type="button" className="dashboard-btn-icon dashboard-btn-icon-sm relative" aria-label="Notifications">
              <Notification size={16} variant="Outline" color="#cbd5e1" aria-hidden />
              <span className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-red-500 ring-2 ring-[#0a0f18]" />
            </button>
            <button type="button" className="dashboard-btn-icon dashboard-btn-icon-sm" aria-label="Settings">
              <Setting2 size={16} variant="Outline" color="#cbd5e1" aria-hidden />
            </button>
            <select className="dashboard-select dashboard-select-sm hidden sm:inline-flex" defaultValue="high" aria-label="Stream quality">
              <option value="high">High Quality 1080p</option>
              <option value="medium">Medium 720p</option>
              <option value="low">Low 480p</option>
            </select>
            <button type="button" onClick={onFullscreen} className="dashboard-btn-icon dashboard-btn-icon-sm" aria-label="Fullscreen">
              <Maximize3 size={16} variant="Outline" color="#cbd5e1" aria-hidden />
            </button>
          </div>
        </div>
      </header>

      <LiveCameraTabBar
        cameras={cameras}
        activeId={activeCameraId}
        onSelect={onSelectCamera}
        onAdd={onFindCameras}
        detecting={detecting}
      />

      <div className="live-view-scroll flex-1 min-h-0">
        <div className="live-view-inner max-w-none">
          <div className="live-view-main-grid">
            <div className="live-view-center flex flex-col gap-5 min-w-0">
              <div className="dashboard-video-shell dashboard-video-shell-live w-full">
                <div className="absolute inset-x-0 top-0 z-20 flex items-center justify-between px-3 py-2.5 mobile-video-gradient-top pointer-events-none">
                  <div className="pointer-events-auto flex items-center gap-2 flex-wrap">
                    {hasLive ? (
                      <span className="inline-flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-emerald-300 bg-emerald-500/20 border border-emerald-500/40 px-2 py-0.5 rounded">
                        Live
                        {recording ? (
                          <span className="h-2 w-2 rounded-full bg-red-500 animate-pulse" aria-hidden />
                        ) : null}
                      </span>
                    ) : null}
                    {cameraName ? (
                      <span className="text-[11px] font-medium text-white/90">{cameraName}</span>
                    ) : null}
                    {personCount > 0 ? (
                      <span className="text-[10px] text-indigo-200 px-2 py-0.5 rounded-full border border-indigo-500/30 bg-indigo-500/10">
                        {personCount} person{personCount === 1 ? "" : "s"}
                      </span>
                    ) : null}
                    {animalCount > 0 ? (
                      <span className="text-[10px] text-amber-200 px-2 py-0.5 rounded-full border border-amber-500/30 bg-amber-500/10">
                        {animalCount} animal{animalCount === 1 ? "" : "s"}
                      </span>
                    ) : null}
                  </div>
                  <LiveClock />
                </div>

                <div className="absolute inset-0 z-10 [&>div]:h-full [&>div]:w-full">
                  {liveVideo}
                </div>

                <div className="absolute inset-x-0 bottom-0 z-20 px-3 pb-3 pt-12 mobile-video-gradient-bottom pointer-events-none">
                  <div className="hidden lg:flex justify-center pointer-events-auto">
                    <VideoOverlayActions
                      onTalk={onTalk}
                      onSnapshot={onSnapshot}
                      onRecord={onRecord}
                      onSiren={onSiren}
                      onLight={onLight}
                      recording={recording}
                      recordDisabled={recordDisabled}
                      recordingMode={recordingMode}
                    />
                  </div>
                  <div className="lg:hidden mt-2 pointer-events-auto">
                    <LiveQuickActions
                      onTalk={onTalk}
                      onSnapshot={onSnapshot}
                      onRecord={onRecord}
                      onSiren={onSiren}
                      onLight={onLight}
                      recording={recording}
                      recordDisabled={recordDisabled}
                      recordingMode={recordingMode}
                    />
                  </div>
                </div>
              </div>

              {typeof renderCameraThumb === "function" ? (
                <QuickCamerasGrid
                  cameras={cameras}
                  activeId={activeCameraId}
                  onSelect={onSelectCamera}
                  renderThumb={renderCameraThumb}
                />
              ) : null}

              <div>
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-sm font-semibold text-gray-100">Camera Insights</h2>
                  <select className="dashboard-select text-[10px] py-1 px-2 w-auto" defaultValue="today" aria-label="Insights period">
                    <option value="today">Today</option>
                  </select>
                </div>
                {cameraInsights}
              </div>

              <RecentEventsStrip events={recentEvents} onViewAll={onViewAllEvents} />
            </div>

            <aside className="live-view-right-rail flex flex-col gap-4 min-w-0">
              <div className="dashboard-panel">
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-sm font-semibold text-gray-100">Activity Feed</h2>
                  {hasLive ? (
                    <span className="text-[10px] font-medium text-emerald-400 flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/25">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                      Live
                    </span>
                  ) : null}
                </div>
                <ActivityTimeline items={activityItems} />
                {onViewAllEvents ? (
                  <button
                    type="button"
                    onClick={onViewAllEvents}
                    className="mt-3 w-full text-center text-[12px] font-medium text-indigo-300 hover:text-indigo-200 py-2"
                  >
                    View All Events
                  </button>
                ) : null}
              </div>
              <AiProtectionCard />
            </aside>
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
            subtitle="Commercial cameras from backend/.env; use Add Camera for Pi 4 edge agents"
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
                  {detecting ? "…" : "+ Add Camera"}
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

export function DeviceCard({ name, isPrimary, online, resolution, fps, ip, preview, onClick, onMenu, onRemove }) {
  return (
    <button type="button" onClick={onClick} className="dashboard-device-card group w-full text-left">
      <div className="relative aspect-[16/10] bg-black overflow-hidden">
        {preview}
        {online ? (
          <span className="absolute bottom-2 left-2 z-10 inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-emerald-300 bg-emerald-500/20 border border-emerald-500/40 px-2 py-0.5 rounded">
            Live
          </span>
        ) : null}
        {onMenu || onRemove ? (
          <div className="absolute top-2 right-2 z-10 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            {onRemove ? (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onRemove();
                }}
                className="p-1.5 rounded-lg bg-black/50 text-rose-300 hover:text-rose-200 hover:bg-rose-950/40"
                aria-label="Remove camera"
                title="Remove camera"
              >
                ✕
              </button>
            ) : null}
            {onMenu ? (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onMenu();
                }}
                className="p-1.5 rounded-lg bg-black/50 text-gray-300 hover:text-white"
                aria-label="More actions"
              >
                ⋯
              </button>
            ) : null}
          </div>
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
