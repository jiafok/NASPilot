import type { ReactNode } from 'react';

/** Shared mobile building blocks: icons, tab bar, skeletons, empty/error states. */

const stroke = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
};

export function IconHome() {
  return (
    <svg viewBox="0 0 24 24" {...stroke}>
      <path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1z" />
    </svg>
  );
}

export function IconCompass() {
  return (
    <svg viewBox="0 0 24 24" {...stroke}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="m15 9-2.2 4.8L8 16l2.2-4.8z" />
    </svg>
  );
}

export function IconRoute() {
  return (
    <svg viewBox="0 0 24 24" {...stroke}>
      <circle cx="6.5" cy="6.5" r="2.5" />
      <circle cx="17.5" cy="17.5" r="2.5" />
      <path d="M9 6.5h5a3.5 3.5 0 0 1 0 7H9a3.5 3.5 0 0 0 0 7h6" />
    </svg>
  );
}

export function IconSparkles() {
  return (
    <svg viewBox="0 0 24 24" {...stroke}>
      <path d="M12 4l1.6 4.4L18 10l-4.4 1.6L12 16l-1.6-4.4L6 10l4.4-1.6z" />
      <path d="M18.5 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z" />
    </svg>
  );
}

export function IconUser() {
  return (
    <svg viewBox="0 0 24 24" {...stroke}>
      <circle cx="12" cy="8.5" r="3.5" />
      <path d="M5 20c0-3.6 3.1-6 7-6s7 2.4 7 6" />
    </svg>
  );
}

export function IconBack() {
  return (
    <svg viewBox="0 0 24 24" {...stroke} width="24" height="24">
      <path d="M15 5l-7 7 7 7" />
    </svg>
  );
}

export function IconPlay() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" width="26" height="26">
      <path d="M8 5.5v13l11-6.5z" />
    </svg>
  );
}

export function IconPause() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" width="26" height="26">
      <rect x="7" y="5" width="3.6" height="14" rx="1.2" />
      <rect x="13.4" y="5" width="3.6" height="14" rx="1.2" />
    </svg>
  );
}

export function IconSearch() {
  return (
    <svg viewBox="0 0 24 24" {...stroke} width="20" height="20">
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4 4" />
    </svg>
  );
}

export function IconClose() {
  return (
    <svg viewBox="0 0 24 24" {...stroke} width="20" height="20">
      <path d="m6 6 12 12M18 6 6 18" />
    </svg>
  );
}

export function IconStar({ filled }: { filled?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" {...stroke} width="20" height="20" fill={filled ? 'currentColor' : 'none'}>
      <path d="m12 4 2.5 5.2 5.5.8-4 3.9 1 5.6L12 16.9 7 19.5l1-5.6-4-3.9 5.5-.8z" />
    </svg>
  );
}

/** Captions burned onto the video (a subtitle frame). */
export function IconCaptions() {
  return (
    <svg viewBox="0 0 24 24" {...stroke} width="22" height="22">
      <rect x="3" y="5" width="18" height="14" rx="3" />
      <path d="M8.5 12.5h3M14 12.5h2.5" />
    </svg>
  );
}

/** The transcript list under the media. */
export function IconList() {
  return (
    <svg viewBox="0 0 24 24" {...stroke} width="22" height="22">
      <path d="M4 7h16M4 12h16M4 17h10" />
    </svg>
  );
}

/** Playback speed. */
export function IconSpeed() {
  return (
    <svg viewBox="0 0 24 24" {...stroke} width="22" height="22">
      <path d="M4 18a8 8 0 1 1 16 0" />
      <path d="m12 18 4.5-5" />
    </svg>
  );
}

/** Expand the video to the whole screen. */
export function IconExpand() {
  return (
    <svg viewBox="0 0 24 24" {...stroke} width="22" height="22">
      <path d="M9 4H5v4M15 4h4v4M15 20h4v-4M9 20H5v-4" />
    </svg>
  );
}

/** Shrink back to the inline player. */
export function IconShrink() {
  return (
    <svg viewBox="0 0 24 24" {...stroke} width="22" height="22">
      <path d="M5 9h4V5M19 9h-4V5M19 15h-4v4M5 15h4v4" />
    </svg>
  );
}

/** Floating (corner) window. */
export function IconFloat() {
  return (
    <svg viewBox="0 0 24 24" {...stroke} width="22" height="22">
      <rect x="3" y="5" width="18" height="14" rx="3" />
      <rect x="11" y="11" width="8" height="6" rx="1.5" fill="currentColor" stroke="none" />
    </svg>
  );
}

/** Audio-only mode. */
export function IconHeadphones() {
  return (
    <svg viewBox="0 0 24 24" {...stroke} width="22" height="22">
      <path d="M4 14v-2a8 8 0 0 1 16 0v2" />
      <rect x="3" y="13.5" width="4" height="6" rx="2" />
      <rect x="17" y="13.5" width="4" height="6" rx="2" />
    </svg>
  );
}

/** Back 15 seconds. */
export function IconBack15() {
  return (
    <svg viewBox="0 0 24 24" {...stroke} width="24" height="24">
      <path d="M4 10a8 8 0 1 1 2 7" />
      <path d="M4 4v6h6" />
    </svg>
  );
}

/** Forward 15 seconds. */
export function IconForward15() {
  return (
    <svg viewBox="0 0 24 24" {...stroke} width="24" height="24">
      <path d="M20 10a8 8 0 1 0-2 7" />
      <path d="M20 4v6h-6" />
    </svg>
  );
}

export type TabKey = 'home' | 'discover' | 'learn' | 'ai' | 'me';

export const TABS: Array<{ key: TabKey; label: string; icon: ReactNode }> = [
  { key: 'home', label: '首页', icon: <IconHome /> },
  { key: 'discover', label: '发现', icon: <IconCompass /> },
  { key: 'learn', label: '学习', icon: <IconRoute /> },
  { key: 'ai', label: 'AI 老师', icon: <IconSparkles /> },
  { key: 'me', label: '我的', icon: <IconUser /> },
];

export function TabBar({ active, onChange }: { active: TabKey; onChange: (key: TabKey) => void }) {
  return (
    <nav className="ef-tabbar">
      {TABS.map((tab) => (
        <button
          key={tab.key}
          type="button"
          className="ef-tab"
          data-active={tab.key === active}
          aria-current={tab.key === active ? 'page' : undefined}
          onClick={() => onChange(tab.key)}
        >
          {tab.icon}
          <span>{tab.label}</span>
        </button>
      ))}
    </nav>
  );
}

export function SectionHead({
  title,
  action,
  onAction,
}: {
  title: string;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <div className="ef-section-head">
      <h2 className="ef-section-title">{title}</h2>
      {action && (
        <button type="button" className="ef-section-more" onClick={onAction}>
          {action}
        </button>
      )}
    </div>
  );
}

/** Skeleton rows: a spinner for a whole page reads as a stall on mobile. */
export function SkeletonList({ rows = 4 }: { rows?: number }) {
  return (
    <div className="ef-list" aria-hidden>
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="ef-skel" style={{ height: 72 }} />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  onAction,
}: {
  title: string;
  description?: string;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <div className="ef-empty">
      <svg viewBox="0 0 24 24" {...stroke} width="64" height="64" style={{ opacity: 0.35 }}>
        <rect x="4" y="5" width="16" height="14" rx="3" />
        <path d="M8 10h8M8 14h5" />
      </svg>
      <strong>{title}</strong>
      {description && <p>{description}</p>}
      {action && (
        <button type="button" className="ef-btn" data-kind="primary" onClick={onAction}>
          {action}
        </button>
      )}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="ef-empty">
      <svg viewBox="0 0 24 24" {...stroke} width="64" height="64" style={{ opacity: 0.35 }}>
        <circle cx="12" cy="12" r="8.5" />
        <path d="M12 8v5M12 16h.01" />
      </svg>
      <strong>加载失败</strong>
      {/* The reason is shown, but never a stack trace or status code. */}
      <p>{message}</p>
      <button type="button" className="ef-btn" onClick={onRetry}>
        重试
      </button>
    </div>
  );
}

export function LevelTag({ level }: { level?: string | null }) {
  if (!level) return null;
  return <span className="ef-tag">{level}</span>;
}
