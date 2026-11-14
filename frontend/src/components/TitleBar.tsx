import { isDesktop, windowAction } from '@/lib/desktop';
import { cn } from '@/lib/utils';

export function BellIcon({ className = 'h-[17px] w-[17px]' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <path d="M10.3 21a1.9 1.9 0 0 0 3.4 0" />
    </svg>
  );
}

/** Minimize, maximize and close for the frameless desktop window. */
export function WindowControls({ height = 42 }: { height?: number }) {
  const cell = 'grid w-[46px] place-items-center text-fg-dim hover:text-fg';
  return (
    <div className="flex items-center" style={{ height }}>
      <button type="button" aria-label="Minimize" onClick={() => windowAction('minimize')} className={cn(cell, 'h-full hover:bg-panel-2')}>
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M0 5h10" stroke="currentColor" /></svg>
      </button>
      <button type="button" aria-label="Maximize" onClick={() => windowAction('toggleMaximize')} className={cn(cell, 'h-full hover:bg-panel-2')}>
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><rect x=".5" y=".5" width="9" height="9" fill="none" stroke="currentColor" /></svg>
      </button>
      <button type="button" aria-label="Close" onClick={() => windowAction('close')} className={cn(cell, 'h-full hover:bg-[#c42b1c] hover:text-white')}>
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M0 0l10 10M10 0 0 10" stroke="currentColor" /></svg>
      </button>
    </div>
  );
}

/**
 * The desktop app's window frame: a slim drag handle with the window
 * controls. The web app has no top bar; search, notifications and settings
 * live in the sidebar.
 */
export default function TitleBar() {
  if (!isDesktop()) return null;
  return (
    <header data-tauri-drag-region className="relative z-30 flex h-8 flex-none select-none items-center justify-end border-b border-line bg-panel">
      <WindowControls height={32} />
    </header>
  );
}
