/** Shared layout components (glass header, sheets, manage). */

export function IconChevronLeft({ className = "w-5 h-5" }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M15 18l-6-6 6-6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function IconClose({ className = "w-5 h-5" }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
    </svg>
  );
}

export function MobilePageHeader({ title, subtitle, onBack, backLabel = "Back", actions = null }) {
  return (
    <header className="shrink-0 mobile-glass border-b border-white/5 pt-[max(0.5rem,env(safe-area-inset-top))] px-4 lg:px-6 pb-3">
      {onBack ? (
        <button
          type="button"
          onClick={onBack}
          className="flex items-center gap-1 text-sm font-medium text-indigo-300 mb-2 py-1 -ml-1 active:opacity-70"
        >
          <IconChevronLeft className="w-4 h-4" />
          {backLabel}
        </button>
      ) : null}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-indigo-300/90">Vigilance</p>
          <h1 className="text-lg font-semibold text-white leading-tight truncate">{title}</h1>
          {subtitle ? (
            <p className="text-xs text-gray-500 mt-0.5 truncate" title={subtitle}>
              {subtitle}
            </p>
          ) : null}
        </div>
        {actions ? <div className="flex items-center gap-1.5 shrink-0 flex-wrap justify-end">{actions}</div> : null}
      </div>
    </header>
  );
}

export function MobileField({ label, hint, children }) {
  return (
    <div className="space-y-1.5">
      {label ? <label className="block text-xs font-medium text-gray-400 px-0.5">{label}</label> : null}
      {children}
      {hint ? <p className="text-[10px] text-gray-500 leading-snug px-0.5">{hint}</p> : null}
    </div>
  );
}
