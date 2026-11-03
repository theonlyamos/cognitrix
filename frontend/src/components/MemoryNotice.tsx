import { useState } from 'react';
import { Link } from 'react-router-dom';
import { errorMessage } from '@/lib/api';
import { deleteMemory, memoryChange } from '@/lib/memories';

/** "Memory saved: … Undo · Manage", shown under a Memory tool call in a chat. */
export function MemoryNotice({ toolName, result }: { toolName: string; result?: string | null }) {
  const change = memoryChange(toolName, result);
  const [state, setState] = useState<'idle' | 'busy' | 'undone'>('idle');
  const [error, setError] = useState<string | null>(null);
  if (!change) return null;

  const undo = async () => {
    setState('busy');
    setError(null);
    try {
      await deleteMemory(change.id);
      setState('undone');
    } catch (e) {
      setState('idle');
      setError(errorMessage(e, 'Could not undo.'));
    }
  };

  const verb = change.kind === 'saved' ? 'Memory saved' : change.kind === 'updated' ? 'Memory updated' : 'Memory forgotten';
  return (
    <div role="note" className="inline-flex max-w-2xl flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-line bg-panel px-3 py-2 text-[12.5px]">
      <span className="text-accent-ink" aria-hidden>◆</span>
      <span className="text-fg-dim">{state === 'undone' ? 'Memory removed:' : `${verb}:`}</span>
      <span className={state === 'undone' ? 'text-fg-dim line-through' : 'font-medium'}>{change.content}</span>
      {change.kind === 'saved' && change.scope === 'agent' && state !== 'undone' && (
        <span className="text-[11.5px] text-fg-dim">(this agent only)</span>
      )}
      {change.kind !== 'forgotten' && state !== 'undone' && (
        <button type="button" onClick={() => void undo()} disabled={state === 'busy'}
          className="text-accent-ink hover:underline disabled:opacity-60">
          {change.kind === 'saved' ? 'Undo' : 'Delete'}
        </button>
      )}
      <span className="text-fg-dim" aria-hidden>·</span>
      <Link to={`/settings/memory${change.kind !== 'forgotten' && state !== 'undone' ? `?memory=${encodeURIComponent(change.id)}` : ''}`}
        className="text-accent-ink hover:underline">
        Manage
      </Link>
      {error && <span role="alert" className="text-danger-ink">{error}</span>}
    </div>
  );
}
