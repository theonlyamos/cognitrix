import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { tr } from '@/lib/i18n';

/** One line of a hunk: ' ' context, '-' removed, '+' added. */
export type DiffLine = [' ' | '-' | '+', string];

export interface Hunk {
  id: string;
  old_start: number;
  new_start: number;
  lines: DiffLine[];
}

export interface ChangedFile {
  /** Shown path (relative to the source's root). */
  path: string;
  /** What actions name the file by. */
  full_path: string;
  status: 'modified' | 'added' | 'deleted' | string;
  added: number;
  removed: number;
  binary: boolean;
  too_big: boolean;
  hunks: Hunk[];
  accepted: string[];
  state: 'accepted' | 'pending';
}

export interface ChangeSource {
  key: string;
  kind: 'sandbox' | 'pc' | 'local';
  label: string;
  /** Sandbox: the project name Copy back uses. */
  project?: string;
  /** Sandbox: the folder on the computer it was copied from. */
  source?: string;
  root: string;
  files: ChangedFile[];
  added: number;
  removed: number;
  pending: number;
}

export interface ChatChanges {
  sources: ChangeSource[];
}

export type ChangeAction =
  | { action: 'accept'; source: string; path?: string; hunk?: string }
  | { action: 'unaccept'; source: string; path: string; hunk?: string }
  | { action: 'reject'; source: string; path?: string; hunk?: string };

/** A chat's file changes, read again when a reply finishes. */
export function useChatChanges(sessionId: string | null | undefined, busy: boolean) {
  const [changes, setChanges] = useState<ChatChanges | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const current = useRef(sessionId);
  current.current = sessionId;

  useEffect(() => {
    setChanges(null);
    setError('');
  }, [sessionId]);

  const refresh = useCallback(async () => {
    const id = current.current;
    if (!id) return;
    try {
      const { data } = await api.get<ChatChanges>(`/sessions/${id}/changes`);
      if (current.current === id && data) setChanges(data);
    } catch {
      // Nothing to show this time.
    }
  }, []);

  useEffect(() => {
    if (!sessionId || busy) return;
    void refresh();
  }, [sessionId, busy, refresh]);

  const act = useCallback(async (body: ChangeAction): Promise<boolean> => {
    const id = current.current;
    if (!id) return false;
    setWorking(true);
    setError('');
    try {
      const { data } = await api.post<ChatChanges>(`/sessions/${id}/changes`, body);
      if (current.current === id && data) setChanges(data);
      return true;
    } catch (e) {
      setError(errorMessage(e));
      void refresh();
      return false;
    } finally {
      setWorking(false);
    }
  }, [refresh]);

  return { changes, working, error, clearError: () => setError(''), refresh, act };
}

export type ChatChangesState = ReturnType<typeof useChatChanges>;

export function totals(sources: ChangeSource[]) {
  return {
    files: sources.reduce((n, s) => n + s.files.length, 0),
    added: sources.reduce((n, s) => n + s.added, 0),
    removed: sources.reduce((n, s) => n + s.removed, 0),
    pending: sources.reduce((n, s) => n + s.pending, 0),
  };
}

/** What the agent is told when the person presses Copy back (the same words
 *  a press on the sandbox's review card sends). */
export function copyBackMessage(project: string): string {
  return `[Pressed "Copy back to my computer" on card "Changes in ${project}" (action: copy_back, value: ${project})]`;
}

/** Old and new line numbers for each line of a hunk. */
export function numbered(hunk: Hunk): { op: DiffLine[0]; text: string; old: number | null; new: number | null }[] {
  let o = hunk.old_start;
  let n = hunk.new_start;
  return hunk.lines.map(([op, text]) => {
    if (op === ' ') return { op, text, old: o++, new: n++ };
    if (op === '-') return { op, text, old: o++, new: null };
    return { op, text, old: null, new: n++ };
  });
}

/** Rows for a side by side view: removed lines next to the added lines that replaced them. */
export function sideBySide(hunk: Hunk): { left: { n: number; text: string; op: ' ' | '-' } | null; right: { n: number; text: string; op: ' ' | '+' } | null }[] {
  const rows: ReturnType<typeof sideBySide> = [];
  let o = hunk.old_start;
  let n = hunk.new_start;
  const lines = hunk.lines;
  let i = 0;
  while (i < lines.length) {
    const [op, text] = lines[i];
    if (op === ' ') {
      rows.push({ left: { n: o++, text, op: ' ' }, right: { n: n++, text, op: ' ' } });
      i++;
      continue;
    }
    const del: string[] = [];
    const add: string[] = [];
    while (i < lines.length && lines[i][0] === '-') del.push(lines[i++][1]);
    while (i < lines.length && lines[i][0] === '+') add.push(lines[i++][1]);
    for (let k = 0; k < Math.max(del.length, add.length); k++) {
      rows.push({
        left: k < del.length ? { n: o++, text: del[k], op: '-' } : null,
        right: k < add.length ? { n: n++, text: add[k], op: '+' } : null,
      });
    }
  }
  return rows;
}

/** "@@ -18,8 +18,10 @@": where the hunk sits in the old and new file. */
export function hunkHeader(hunk: Hunk): string {
  const oldCount = hunk.lines.filter(([op]) => op !== '+').length;
  const newCount = hunk.lines.filter(([op]) => op !== '-').length;
  return `@@ −${hunk.old_start},${oldCount} +${hunk.new_start},${newCount} @@`;
}

/** "Already saved on this computer" / "Already saved on Kofi's desktop". */
export function savedOn(source: ChangeSource): string {
  return source.kind === 'local' ? tr('ui.fileChanges.savedHere') : tr('ui.fileChanges.alreadySavedOn', { place: source.label });
}
