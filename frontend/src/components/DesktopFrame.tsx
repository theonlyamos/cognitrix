import type { ReactNode } from 'react';
import { isDesktop } from '@/lib/desktop';
import { WindowControls } from '@/components/TitleBar';

/**
 * Pages outside the app shell (sign in, sign up, loading) still need a way to
 * move, minimize, maximize and close the frameless desktop window. In the
 * browser this renders the page unchanged.
 */
export default function DesktopFrame({ children }: { children: ReactNode }) {
  if (!isDesktop()) return <>{children}</>;
  return (
    <div className="flex h-screen flex-col bg-bg text-fg">
      <header data-tauri-drag-region className="flex h-9 flex-none select-none items-center justify-end border-b border-line bg-panel">
        <WindowControls height={36} />
      </header>
      {/* The pages size themselves to the screen; here they fill what's left under the bar. */}
      <div className="min-h-0 flex-1 overflow-y-auto [&>*]:!min-h-full">{children}</div>
    </div>
  );
}
