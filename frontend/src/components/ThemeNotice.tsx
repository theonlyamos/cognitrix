import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTheme, type ThemeUndo } from '@/context/ThemeContext';
import { errorMessage } from '@/lib/api';

export interface ThemeChange {
  name: string;
  previous: ThemeUndo;
}

/** A Set Theme tool result: "Theme changed: Nord (light: …). Previous: {...}". */
export function themeChange(toolName: string, result?: string | null): ThemeChange | null {
  if (!result || toolName.replace(/_/g, ' ').trim().toLowerCase() !== 'set theme') return null;
  const m = /^Theme changed: ([\s\S]+?) \([^)]*\)\. Previous: (\{.*\})\s*$/.exec(result.trim());
  if (!m) return null;
  try {
    const previous = JSON.parse(m[2]) as ThemeChange['previous'];
    return { name: m[1], previous };
  } catch {
    return null;
  }
}

/** "Theme changed to Nord · Undo · Customize", under a Set Theme call; it also shows the change at once. */
export function ThemeNotice({ toolName, result }: { toolName: string; result?: string | null }) {
  const change = themeChange(toolName, result);
  return change ? <ThemeChangeNote change={change} /> : null;
}

function ThemeChangeNote({ change }: { change: ThemeChange }) {
  const { refresh, restore } = useTheme();
  const [state, setState] = useState<'idle' | 'busy' | 'undone'>('idle');
  const [error, setError] = useState<string | null>(null);
  const fetched = useRef(false);

  // The agent changed the account's theme: take it now rather than on the next focus.
  useEffect(() => {
    if (fetched.current) return;
    fetched.current = true;
    void refresh();
  }, [refresh]);

  const undo = async () => {
    setState('busy');
    setError(null);
    try {
      await restore(change.previous);
      setState('undone');
    } catch (e) {
      setState('idle');
      setError(errorMessage(e, 'Could not undo.'));
    }
  };

  return (
    <div role="note" className="inline-flex max-w-2xl flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-line bg-panel px-3 py-2 text-[12.5px]">
      <span className="text-accent-ink" aria-hidden>◐</span>
      <span className="text-fg-dim">{state === 'undone' ? 'Theme change undone:' : 'Theme changed:'}</span>
      <span className={state === 'undone' ? 'text-fg-dim line-through' : 'font-medium'}>{change.name}</span>
      {state !== 'undone' && (
        <button type="button" onClick={() => void undo()} disabled={state === 'busy'}
          className="text-accent-ink hover:underline disabled:opacity-60">Undo</button>
      )}
      <span className="text-fg-dim" aria-hidden>·</span>
      <Link to="/settings/appearance" className="text-accent-ink hover:underline">Customize</Link>
      {error && <span role="alert" className="text-danger-ink">{error}</span>}
    </div>
  );
}
