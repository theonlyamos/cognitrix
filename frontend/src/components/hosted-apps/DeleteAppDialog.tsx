import { useEffect, useRef, useState } from 'react';
import { Trash2, X } from 'lucide-react';
import { cn } from '@/lib/utils';

const buttonClass = 'inline-flex h-9 items-center gap-1.5 rounded-md border px-3 text-[13px] font-medium disabled:opacity-60 md:h-8';

/**
 * Asks before an app is deleted. An in-app dialog rather than window.confirm,
 * which the desktop app's window doesn't show.
 */
export function DeleteAppDialog({ title, folder, onCancel, onConfirm }: {
  title: string;
  folder?: string;
  onCancel: () => void;
  onConfirm: () => Promise<void> | void;
}) {
  const [busy, setBusy] = useState(false);
  const cancel = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    cancel.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy) onCancel(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [busy, onCancel]);

  const confirm = async () => {
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/45 md:items-center"
      onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onCancel(); }}>
      <div role="alertdialog" aria-modal="true" aria-labelledby="delete-app-title" aria-describedby="delete-app-body"
        className="w-full rounded-t-2xl border border-line bg-panel shadow-2xl md:w-[440px] md:rounded-2xl">
        <header className="flex items-center gap-2 border-b border-line px-4 py-3">
          <h2 id="delete-app-title" className="min-w-0 flex-1 truncate text-[15px] font-semibold">Delete “{title}”?</h2>
          <button type="button" onClick={onCancel} disabled={busy} aria-label="Close"
            className="grid h-9 w-9 place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg"><X size={16} /></button>
        </header>
        <div id="delete-app-body" className="space-y-2 px-4 py-4 text-[13px]">
          <p>The app stops, its link stops working for everyone it’s shared with, and all its versions, logs and saved data are deleted. This can’t be undone.</p>
          {folder && <p className="text-fg-dim">Its code stays in <span className="break-all font-mono text-[12px]">{folder}</span>.</p>}
        </div>
        <footer className="flex justify-end gap-2 border-t border-line px-4 py-3">
          <button ref={cancel} type="button" onClick={onCancel} disabled={busy}
            className={cn(buttonClass, 'border-line hover:bg-panel-2')}>Cancel</button>
          <button type="button" onClick={() => void confirm()} disabled={busy}
            className={cn(buttonClass, 'border-danger-ink/40 bg-danger-ink/10 text-danger-ink hover:bg-danger-ink/15')}>
            <Trash2 size={14} />{busy ? 'Deleting…' : 'Delete app'}
          </button>
        </footer>
      </div>
    </div>
  );
}
