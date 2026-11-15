import { useState } from 'react';
import { Link } from 'react-router-dom';
import { errorMessage } from '@/lib/api';
import { approveMemory, deleteMemory, memoryChange, type MemoryChange } from '@/lib/memories';

/** "Memory saved: … Undo · Manage", shown under a Memory tool call in a chat. */
export function MemoryNotice({ toolName, result }: { toolName: string; result?: string | null }) {
  const change = memoryChange(toolName, result);
  const [state, setState] = useState<'idle' | 'busy' | 'undone'>('idle');
  const [error, setError] = useState<string | null>(null);
  if (!change) return null;
  if (change.kind === 'proposed') return <MemoryProposal change={change} />;

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

/** "Save to memory? … Save · No", for a memory the agent may only propose. */
function MemoryProposal({ change }: { change: MemoryChange }) {
  const [state, setState] = useState<'idle' | 'busy' | 'saved' | 'dismissed'>('idle');
  const [error, setError] = useState<string | null>(null);

  const settle = async (approve: boolean) => {
    setState('busy');
    setError(null);
    try {
      if (approve) await approveMemory(change.id);
      else await deleteMemory(change.id);
      setState(approve ? 'saved' : 'dismissed');
    } catch (e) {
      setState('idle');
      // Already settled in Settings or another tab: say so instead of failing.
      const status = (e as { response?: { status?: number } })?.response?.status;
      setError(status === 404 ? 'This was already settled.' : errorMessage(e, 'Could not do that.'));
    }
  };

  const done = state === 'saved' || state === 'dismissed';
  return (
    <div role="note" className="inline-flex max-w-2xl flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-line bg-panel px-3 py-2 text-[12.5px]">
      <span className="text-accent-ink" aria-hidden>◇</span>
      <span className="text-fg-dim">
        {state === 'saved' ? 'Memory saved:' : state === 'dismissed' ? 'Not saved:' : 'Save to memory?'}
      </span>
      <span className={state === 'dismissed' ? 'text-fg-dim line-through' : 'font-medium'}>{change.content}</span>
      {change.scope === 'agent' && !done && <span className="text-[11.5px] text-fg-dim">(this agent only)</span>}
      {!done && (
        <>
          <button type="button" onClick={() => void settle(true)} disabled={state === 'busy'}
            className="font-medium text-accent-ink hover:underline disabled:opacity-60">Save</button>
          <button type="button" onClick={() => void settle(false)} disabled={state === 'busy'}
            className="text-fg-dim hover:text-fg hover:underline disabled:opacity-60">No</button>
        </>
      )}
      <span className="text-fg-dim" aria-hidden>·</span>
      <Link to={`/settings/memory${state !== 'dismissed' ? `?memory=${encodeURIComponent(change.id)}` : ''}`}
        className="text-accent-ink hover:underline">
        Manage
      </Link>
      {error && <span role="alert" className="text-danger-ink">{error}</span>}
    </div>
  );
}
