import { AppWindow } from 'lucide-react';
import { appViewFrom, requestOpenAppView } from '@/lib/appViews';

/** The card under an app tool call that opened the app's screen: opens it beside the chat. */
export function AppViewCard({ result }: { result?: string | null }) {
  const shown = appViewFrom(result);
  if (!shown) return null;
  return (
    <button type="button" onClick={() => requestOpenAppView(shown.id)}
      className="flex w-full max-w-md items-center gap-3 rounded-[10px] border border-line bg-panel px-3 py-2.5 text-left transition-colors hover:border-fg-dim">
      <span className="grid h-9 w-9 flex-none place-items-center rounded-lg bg-info/15 text-info-ink"><AppWindow size={17} aria-hidden /></span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13.5px] font-semibold">{shown.app}</span>
        <span className="block truncate font-mono text-[11px] text-fg-dim">Connected app · update {shown.version}</span>
      </span>
      <span className="flex-none rounded-md border border-line px-2 py-1 text-[12px] font-medium">Open</span>
    </button>
  );
}
