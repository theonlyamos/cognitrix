import { useCallback, useEffect, useRef, useState } from 'react';
import { api, API_BASE, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { cn } from '@/lib/utils';
import { utcToLocal } from '@/lib/time';

const NOTES_MAX = 8000;

export interface AutomationTaskFields {
  notes?: string | null;
  wake_pending?: boolean;
  wake_at?: string | null;
  wake_reason?: string | null;
}

export interface TaskTriggerRow {
  id: string;
  name: string;
  enabled: boolean;
  token_hint: string;
  last_fired_at: string | null;
  fire_count: number;
}

interface CreatedTrigger extends TaskTriggerRow {
  token: string;
  path: string;
}

interface TriggerEventRow {
  id: string;
  status: string;
  received_at: string | null;
  payload: string;
  error: string | null;
}


/** Full URL for a webhook path. The API base can be relative (served by the engine). */
function hookUrl(path: string): string {
  const base = API_BASE.replace(/\/api\/v1$/, '');
  const origin = base.startsWith('http') ? base : window.location.origin + base;
  return origin.replace(/\/$/, '') + path;
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h3 className="font-mono text-[10px] tracking-[0.18em] text-fg-dim">{children}</h3>;
}

/** Notes, the agent's next check, and webhook triggers for one task. */
export function AutomationPanel({
  taskId,
  task,
  onChanged,
}: {
  taskId: string;
  task: AutomationTaskFields | null | undefined;
  onChanged: () => void | Promise<void>;
}) {
  const [notes, setNotes] = useState(task?.notes ?? '');
  const [savedNotes, setSavedNotes] = useState(task?.notes ?? '');
  const [savingNotes, setSavingNotes] = useState(false);
  const [triggers, setTriggers] = useState<TaskTriggerRow[]>([]);
  const [events, setEvents] = useState<TriggerEventRow[]>([]);
  const [newName, setNewName] = useState('');
  const [created, setCreated] = useState<CreatedTrigger | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');

  // Pick up notes an agent wrote while the panel was open, unless you're editing.
  const savedRef = useRef(task?.notes ?? '');
  useEffect(() => {
    const incoming = task?.notes ?? '';
    const prev = savedRef.current;
    if (incoming === prev) return;
    savedRef.current = incoming;
    setSavedNotes(incoming);
    setNotes((cur) => (cur === prev ? incoming : cur));
  }, [task?.notes]);

  const load = useCallback(async () => {
    try {
      const [t, e] = await Promise.all([
        api.get<TaskTriggerRow[]>(`/tasks/${taskId}/triggers`),
        api.get<TriggerEventRow[]>(`/tasks/${taskId}/events`),
      ]);
      setTriggers(t.data ?? []);
      setEvents(e.data ?? []);
    } catch (err) {
      setError(errorMessage(err, 'Could not load triggers.'));
    }
  }, [taskId]);

  useEffect(() => {
    void load();
  }, [load]);

  const act = async (key: string, fn: () => Promise<unknown>, fallback: string) => {
    setBusy(key);
    setError('');
    try {
      await fn();
    } catch (err) {
      setError(errorMessage(err, fallback));
    } finally {
      setBusy(null);
    }
  };

  const saveNotes = () =>
    act('notes', async () => {
      setSavingNotes(true);
      try {
        await api.put(`/tasks/${taskId}/notes`, { notes });
        savedRef.current = notes.trim();
        setSavedNotes(notes.trim());
        setNotes(notes.trim());
        await onChanged();
      } finally {
        setSavingNotes(false);
      }
    }, 'Could not save the notes.');

  const cancelWake = () =>
    act('wake', async () => {
      await api.delete(`/tasks/${taskId}/wake`);
      await onChanged();
    }, 'Could not cancel the check.');

  const addTrigger = () =>
    act('add', async () => {
      const { data } = await api.post<CreatedTrigger>(`/tasks/${taskId}/triggers`, { name: newName.trim() || 'Webhook' });
      setCreated(data);
      setCopied(false);
      setNewName('');
      await load();
    }, 'Could not create the webhook.');

  const rotate = (id: string) =>
    act(`rotate:${id}`, async () => {
      const { data } = await api.post<CreatedTrigger>(`/tasks/${taskId}/triggers/${id}/rotate`);
      setCreated(data);
      setCopied(false);
      await load();
    }, 'Could not make a new URL.');

  const toggle = (row: TaskTriggerRow) =>
    act(`toggle:${row.id}`, async () => {
      await api.patch(`/tasks/${taskId}/triggers/${row.id}`, { enabled: !row.enabled });
      await load();
    }, 'Could not update the webhook.');

  const remove = (id: string) =>
    act(`delete:${id}`, async () => {
      await api.delete(`/tasks/${taskId}/triggers/${id}`);
      if (created?.id === id) setCreated(null);
      await load();
    }, 'Could not delete the webhook.');

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  const notesDirty = notes !== savedNotes;

  return (
    <section aria-label="Automation" className="flex flex-col gap-5 border-b border-line bg-panel px-6 py-4 max-md:px-4">
      {error && <p role="alert" className="font-mono text-[12px] text-danger-ink">{error}</p>}

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <SectionTitle>NOTES · KEPT BETWEEN RUNS</SectionTitle>
          <span className={cn('font-mono text-[10.5px] tnum', notes.length > NOTES_MAX ? 'text-danger-ink' : 'text-fg-dim')}>
            {notes.length}/{NOTES_MAX}
          </span>
        </div>
        <label htmlFor="task-notes" className="sr-only">Task notes</label>
        <textarea
          id="task-notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={4}
          placeholder="Every run reads these. Agents update them too, e.g. the last version they saw."
          className="w-full resize-y rounded-[9px] border border-line bg-bg px-3 py-2 font-mono text-[12.5px] leading-relaxed outline-none focus:border-accent"
        />
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={saveNotes} disabled={!notesDirty || savingNotes || notes.length > NOTES_MAX}>
            {savingNotes ? 'Saving…' : 'Save notes'}
          </Button>
          {notesDirty && (
            <Button size="sm" variant="ghost" onClick={() => setNotes(savedNotes)}>
              Discard
            </Button>
          )}
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <SectionTitle>NEXT CHECK · SET BY THE AGENT</SectionTitle>
        {task?.wake_pending && task.wake_at ? (
          <div className="flex flex-wrap items-center gap-3 text-[13px]">
            <span>
              Checks again <span className="font-mono">{utcToLocal(task.wake_at)}</span>
              {task.wake_reason ? <span className="text-fg-dim"> · {task.wake_reason}</span> : null}
            </span>
            <Button size="sm" variant="outline" onClick={cancelWake} disabled={busy === 'wake'}>
              Cancel check
            </Button>
          </div>
        ) : (
          <p className="text-[13px] text-fg-dim">
            None. During a run the agent can call Schedule Next Check to pick when to look again.
          </p>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <SectionTitle>WEBHOOK TRIGGERS</SectionTitle>
        <p className="text-[12.5px] text-fg-dim">
          A POST to a trigger's secret URL starts a run, and the request body is passed to the agents. When a run is already going,
          events wait their turn.
        </p>

        {created && (
          <div role="status" className="rounded-[10px] border border-accent/50 bg-accent/5 p-3">
            <p className="text-[12.5px] font-medium">Copy this URL now. It won't be shown again.</p>
            <div className="mt-2 flex items-center gap-2">
              <code className="min-w-0 flex-1 break-all rounded bg-bg px-2 py-1.5 font-mono text-[11.5px]">{hookUrl(created.path)}</code>
              <Button size="sm" variant="outline" onClick={() => copy(hookUrl(created.path))}>
                {copied ? 'Copied' : 'Copy'}
              </Button>
            </div>
            <p className="mt-2 font-mono text-[11px] text-fg-dim">
              curl -X POST -H "Content-Type: application/json" -d '{'{"hello":"world"}'}' &lt;url&gt;
            </p>
            <Button size="sm" variant="ghost" className="mt-1" onClick={() => setCreated(null)}>
              Done
            </Button>
          </div>
        )}

        {triggers.length > 0 && (
          <ul className="flex flex-col divide-y divide-line rounded-[10px] border border-line">
            {triggers.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center gap-2 px-3 py-2">
                <span className={cn('h-1.5 w-1.5 rounded-full', row.enabled ? 'bg-accent' : 'bg-fg-dim')} aria-hidden />
                <span className="text-[13px] font-medium">{row.name}</span>
                <span className="font-mono text-[11px] text-fg-dim">…{row.token_hint}</span>
                <span className="font-mono text-[11px] text-fg-dim">
                  {row.fire_count} run{row.fire_count === 1 ? '' : 's'}
                  {row.last_fired_at ? ` · last ${utcToLocal(row.last_fired_at)}` : ''}
                </span>
                <span className="flex-1" />
                <Button size="sm" variant="ghost" onClick={() => toggle(row)} disabled={busy !== null}>
                  {row.enabled ? 'Turn off' : 'Turn on'}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => rotate(row.id)} disabled={busy !== null}>
                  New URL
                </Button>
                <Button size="sm" variant="ghost" className="hover:text-danger-ink" onClick={() => remove(row.id)} disabled={busy !== null}>
                  Delete
                </Button>
              </li>
            ))}
          </ul>
        )}

        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void addTrigger();
          }}
        >
          <label htmlFor="trigger-name" className="sr-only">Webhook name</label>
          <input
            id="trigger-name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Name, e.g. GitHub releases"
            maxLength={80}
            className="h-9 min-w-0 flex-1 rounded-[9px] border border-line bg-bg px-3 text-[13px] outline-none focus:border-accent"
          />
          <Button size="sm" type="submit" disabled={busy === 'add'}>
            {busy === 'add' ? 'Adding…' : '+ Add webhook'}
          </Button>
        </form>

        {events.length > 0 && (
          <div className="mt-1">
            <div className="mb-1 font-mono text-[10px] tracking-[0.14em] text-fg-dim">RECENT EVENTS</div>
            <ul className="flex flex-col gap-1">
              {events.slice(0, 5).map((ev) => (
                <li key={ev.id} className="flex items-center gap-2 font-mono text-[11px] text-fg-dim">
                  <span className={cn(ev.status === 'dispatched' && 'text-ok', ev.status === 'dropped' && 'text-danger-ink')}>
                    {ev.status === 'dispatched' ? 'ran' : ev.status === 'pending' ? 'waiting' : ev.status}
                  </span>
                  <span>{ev.received_at ? utcToLocal(ev.received_at) : ''}</span>
                  <span className="min-w-0 flex-1 truncate">{ev.payload}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}
