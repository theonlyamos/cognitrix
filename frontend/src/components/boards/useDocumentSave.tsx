import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage } from '@/lib/api';

export const SAVE_DELAY_MS = 800;

export type SaveState = 'saved' | 'waiting' | 'saving' | 'stale' | 'error';

/** Saves a sheet or whiteboard a short moment after the last change.
 *
 *  Every save says which version the editor started from; when an agent or
 *  someone else saved in between, the server refuses (409) and the editor
 *  shows "Reload" instead of wiping their change. The editor is remounted
 *  (a new React key) whenever it loads a fresh copy, so this starts over. */
export function useDocumentSave(pageId: string, startVersion: number, onSaved?: (version: number, more: boolean) => void, onChange?: () => void) {
  const [state, setState] = useState<SaveState>('saved');
  const [message, setMessage] = useState('');
  const version = useRef(startVersion);
  const pending = useRef<string | null>(null);
  const timer = useRef<number | null>(null);
  const running = useRef(false);
  const onSavedRef = useRef(onSaved);
  onSavedRef.current = onSaved;
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  const flush = useCallback(async () => {
    if (timer.current !== null) { window.clearTimeout(timer.current); timer.current = null; }
    if (running.current || pending.current === null) return;
    const content = pending.current;
    pending.current = null;
    running.current = true;
    setState('saving');
    try {
      const { data } = await api.put<{ version: number }>(`/artifact-pages/${pageId}/content`, { content, base_version: version.current });
      version.current = data.version;
      onSavedRef.current?.(data.version, pending.current !== null);
      setState(pending.current === null ? 'saved' : 'waiting');
      setMessage('');
    } catch (e) {
      const status = (e as { response?: { status?: number } }).response?.status;
      if (status === 409) {
        pending.current = null;
        setState('stale');
        setMessage(errorMessage(e, 'This changed since you opened it. Reload to see the change.'));
      } else {
        // Keep the edit so the next change (or Retry) sends it again.
        if (pending.current === null) pending.current = content;
        setState('error');
        setMessage(errorMessage(e, 'Could not save. Your changes are kept here.'));
      }
    } finally {
      running.current = false;
    }
    if (pending.current !== null && timer.current === null) void flush();
  }, [pageId]);

  const save = useCallback((content: string) => {
    if (state === 'stale') return;
    onChangeRef.current?.();
    pending.current = content;
    setState((s) => (s === 'saving' ? s : 'waiting'));
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => { timer.current = null; void flush(); }, SAVE_DELAY_MS);
  }, [flush, state]);

  // Leaving the page or panel sends what is waiting.
  useEffect(() => () => { if (pending.current !== null) void flush(); }, [flush]);

  return {
    state, message, save,
    retry: () => void flush(),
    /** True while there are changes the server doesn't have yet. */
    dirty: state === 'waiting' || state === 'saving' || state === 'error',
    version: () => version.current,
  };
}

export function SaveStatus({ state, message, onRetry, onReload }: {
  state: SaveState; message: string; onRetry: () => void; onReload: () => void;
}) {
  if (state === 'stale') {
    return (
      <div role="alert" className="flex flex-none flex-wrap items-center gap-2 border-b border-line bg-warn/15 px-3 py-1.5 text-[12.5px] text-warn-ink">
        <span className="min-w-0 flex-1">{message || 'This changed since you opened it.'} Your last edit was not saved.</span>
        <button type="button" onClick={onReload} className="rounded-md border border-line bg-panel px-2 py-0.5 font-medium text-fg hover:bg-panel-2">Reload</button>
      </div>
    );
  }
  if (state === 'error') {
    return (
      <div role="alert" className="flex flex-none items-center gap-2 border-b border-line bg-danger/10 px-3 py-1.5 text-[12.5px] text-danger-ink">
        <span className="min-w-0 flex-1">{message}</span>
        <button type="button" onClick={onRetry} className="rounded-md border border-line bg-panel px-2 py-0.5 font-medium text-fg hover:bg-panel-2">Try again</button>
      </div>
    );
  }
  return null;
}

export function saveLabel(state: SaveState): string {
  return { saved: 'Saved', waiting: 'Editing…', saving: 'Saving…', stale: 'Not saved', error: 'Not saved' }[state];
}
