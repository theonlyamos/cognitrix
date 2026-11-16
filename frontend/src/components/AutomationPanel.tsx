import { useCallback, useEffect, useRef, useState } from 'react';
import { api, API_BASE, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { cn } from '@/lib/utils';
import { utcToLocal } from '@/lib/time';
import { tr, trn } from '@/lib/i18n';

const NOTES_MAX = 8000;

/** The agent that made the task with Create Task, and where it was working. */
export interface TaskCreator {
  agent_id?: string;
  agent_name?: string | null;
  kind?: 'chat' | 'task';
  session_id?: string;
  task_id?: string;
  depth?: number;
}

export interface AutomationTaskFields {
  created_by?: TaskCreator | null;
  report_to?: string | null;
  report_only_on?: string | null;
  report_to_effective?: string;
  notes?: string | null;
  wake_pending?: boolean;
  wake_at?: string | null;
  wake_reason?: string | null;
}

export interface TaskTriggerRow {
  id: string;
  name: string;
  /** "webhook" (the default) or "gmail" for new mail in the owner's Gmail. */
  kind?: string;
  enabled: boolean;
  token_hint: string;
  last_fired_at: string | null;
  fire_count: number;
  /** Mail triggers: only mail whose sender or subject contains this. */
  match?: string;
  checked_at?: string | null;
  /** Mail triggers: why the last check failed, in words for the owner. */
  error?: string | null;
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

const selectClass = 'h-9 rounded-lg border border-line bg-bg px-2 text-[12.5px] text-fg outline-none focus:border-accent disabled:opacity-60';

/** Who hears about a finished run of a task an agent made (tasks/reports.py). */
function ReportingSection({
  task,
  busy,
  onSave,
}: {
  task: AutomationTaskFields;
  busy: boolean;
  onSave: (reportTo: string, onlyOn: string) => void;
}) {
  const creator = task.created_by ?? {};
  const agent = creator.agent_name || tr('ui.automationPanel.theAgent');
  const reportTo = task.report_to ?? '';
  const onlyOn = task.report_only_on ?? 'always';
  const effective = task.report_to_effective ?? 'person';
  const origin = creator.kind === 'chat' && creator.session_id
    ? { href: `/home?chat=${encodeURIComponent(creator.session_id)}`, label: tr('ui.automationPanel.openTheChat') }
    : creator.kind === 'task' && creator.task_id
      ? { href: `/tasks/${encodeURIComponent(creator.task_id)}`, label: tr('ui.automationPanel.openTheTask') }
      : null;
  return (
    <div className="flex flex-col gap-2">
      <SectionTitle>{tr('ui.automationPanel.results')}</SectionTitle>
      <p className="text-[12.5px] text-fg-dim">
        {creator.kind === 'task'
          ? tr('ui.automationPanel.agentMadeThisTaskDuringATask', { agent })
          : tr('ui.automationPanel.agentMadeThisTaskInAChat', { agent })}
        {origin && (
          <>
            {' '}
            <a href={origin.href} className="text-accent-ink underline-offset-2 hover:underline">{origin.label}</a>
          </>
        )}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="task-report-to" className="text-[12.5px]">{tr('ui.automationPanel.resultsGoTo')}</label>
        <select
          id="task-report-to"
          className={selectClass}
          value={reportTo}
          disabled={busy}
          onChange={(e) => onSave(e.target.value, onlyOn)}
        >
          <option value="">
            {effective === 'creator' && !reportTo
              ? tr('ui.automationPanel.defaultAgent', { agent })
              : !reportTo ? tr('ui.automationPanel.defaultYou') : tr('ui.automationPanel.default')}
          </option>
          <option value="creator">{agent}</option>
          <option value="person">{tr('ui.automationPanel.you')}</option>
          <option value="both">{tr('ui.automationPanel.agentAndYou', { agent })}</option>
        </select>
        {effective !== 'person' && (
          <>
            <label htmlFor="task-report-only-on" className="text-[12.5px]">{tr('ui.automationPanel.tellTheAgent')}</label>
            <select
              id="task-report-only-on"
              className={selectClass}
              value={onlyOn}
              disabled={busy}
              onChange={(e) => onSave(reportTo, e.target.value)}
            >
              <option value="always">{tr('ui.automationPanel.afterEveryRun')}</option>
              <option value="failure">{tr('ui.automationPanel.onlyWhenARunFails')}</option>
              <option value="changed">{tr('ui.automationPanel.onlyWhenTheResultChanges')}</option>
            </select>
          </>
        )}
      </div>
      <p className="text-[12px] text-fg-dim">
        {effective === 'person'
          ? tr('ui.automationPanel.youAreToldWhenARunEnds')
          : effective === 'both'
            ? tr('ui.automationPanel.bothAreTold', { agent })
            : tr('ui.automationPanel.theAgentDecidesWhetherToTellYou', { agent })}
      </p>
    </div>
  );
}

/** Notes, the agent's next check, and webhook and new-mail triggers for one task. */
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
  const [mailMatch, setMailMatch] = useState('');
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
      setError(errorMessage(err, tr('ui.automationPanel.couldNotLoadTriggers')));
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
    }, tr('ui.automationPanel.couldNotSaveTheNotes'));

  const saveReporting = (reportTo: string, onlyOn: string) =>
    act('reporting', async () => {
      await api.put(`/tasks/${taskId}/reporting`, {
        report_to: reportTo || null,
        report_only_on: onlyOn && onlyOn !== 'always' ? onlyOn : null,
      });
      await onChanged();
    }, tr('ui.automationPanel.couldNotSaveWhoGets'));

  const cancelWake = () =>
    act('wake', async () => {
      await api.delete(`/tasks/${taskId}/wake`);
      await onChanged();
    }, tr('ui.automationPanel.couldNotCancelTheCheck'));

  const addTrigger = () =>
    act('add', async () => {
      const { data } = await api.post<CreatedTrigger>(`/tasks/${taskId}/triggers`, { name: newName.trim() || 'Webhook' });
      setCreated(data);
      setCopied(false);
      setNewName('');
      await load();
    }, tr('ui.automationPanel.couldNotCreateTheWebhook'));

  const addMailTrigger = () =>
    act('add-mail', async () => {
      await api.post(`/tasks/${taskId}/triggers`, { kind: 'gmail', match: mailMatch.trim() });
      setMailMatch('');
      await load();
    }, tr('ui.automationPanel.couldNotStartWatchingFor'));

  const rotate = (id: string) =>
    act(`rotate:${id}`, async () => {
      const { data } = await api.post<CreatedTrigger>(`/tasks/${taskId}/triggers/${id}/rotate`);
      setCreated(data);
      setCopied(false);
      await load();
    }, tr('ui.automationPanel.couldNotMakeANew'));

  const toggle = (row: TaskTriggerRow) =>
    act(`toggle:${row.id}`, async () => {
      await api.patch(`/tasks/${taskId}/triggers/${row.id}`, { enabled: !row.enabled });
      await load();
    }, tr('ui.automationPanel.couldNotUpdateTheTrigger'));

  const remove = (id: string) =>
    act(`delete:${id}`, async () => {
      await api.delete(`/tasks/${taskId}/triggers/${id}`);
      if (created?.id === id) setCreated(null);
      await load();
    }, tr('ui.automationPanel.couldNotDeleteTheTrigger'));

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
    <section aria-label={tr('ui.automationPanel.automation')} className="flex flex-col gap-5 border-b border-line bg-panel px-6 py-4 max-md:px-4">
      {error && <p role="alert" className="font-mono text-[12px] text-danger-ink">{error}</p>}

      {task?.created_by?.agent_id && (
        <ReportingSection task={task} busy={busy === 'reporting'} onSave={saveReporting} />
      )}

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <SectionTitle>{tr('ui.automationPanel.notesKeptBetweenRuns')}</SectionTitle>
          <span className={cn('font-mono text-[10.5px] tnum', notes.length > NOTES_MAX ? 'text-danger-ink' : 'text-fg-dim')}>
            {notes.length}/{NOTES_MAX}
          </span>
        </div>
        <label htmlFor="task-notes" className="sr-only">{tr('ui.automationPanel.taskNotes')}</label>
        <textarea
          id="task-notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={4}
          placeholder={tr('ui.automationPanel.everyRunReadsTheseAgents')}
          className="w-full resize-y rounded-[9px] border border-line bg-bg px-3 py-2 font-mono text-[12.5px] leading-relaxed outline-none focus:border-accent"
        />
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={saveNotes} disabled={!notesDirty || savingNotes || notes.length > NOTES_MAX}>
            {savingNotes ? tr('ui.automationPanel.saving') : tr('ui.automationPanel.saveNotes')}
          </Button>
          {notesDirty && (
            <Button size="sm" variant="ghost" onClick={() => setNotes(savedNotes)}>
              {tr('ui.automationPanel.discard')}
            </Button>
          )}
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <SectionTitle>{tr('ui.automationPanel.nextCheckSetByThe')}</SectionTitle>
        {task?.wake_pending && task.wake_at ? (
          <div className="flex flex-wrap items-center gap-3 text-[13px]">
            <span>
              {tr('ui.automationPanel.checksAgain')}{' '}<span className="font-mono">{utcToLocal(task.wake_at)}</span>
              {task.wake_reason ? <span className="text-fg-dim"> · {task.wake_reason}</span> : null}
            </span>
            <Button size="sm" variant="outline" onClick={cancelWake} disabled={busy === 'wake'}>
              {tr('ui.automationPanel.cancelCheck')}
            </Button>
          </div>
        ) : (
          <p className="text-[13px] text-fg-dim">
            {tr('ui.automationPanel.noneDuringARunThe')}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <SectionTitle>{tr('ui.automationPanel.triggers')}</SectionTitle>
        <p className="text-[12.5px] text-fg-dim">
          {tr('ui.automationPanel.aPostToAWebhook')}
        </p>

        {created && (
          <div role="status" className="rounded-[10px] border border-accent/50 bg-accent/5 p-3">
            <p className="text-[12.5px] font-medium">{tr('ui.automationPanel.copyThisUrlNowIt')}</p>
            <div className="mt-2 flex items-center gap-2">
              <code className="min-w-0 flex-1 break-all rounded bg-bg px-2 py-1.5 font-mono text-[11.5px]">{hookUrl(created.path)}</code>
              <Button size="sm" variant="outline" onClick={() => copy(hookUrl(created.path))}>
                {copied ? tr('ui.automationPanel.copied') : tr('ui.automationPanel.copy')}
              </Button>
            </div>
            <p className="mt-2 font-mono text-[11px] text-fg-dim">
              {tr('ui.automationPanel.curlXPostHContent')}{'{"hello":"world"}'}{tr('ui.automationPanel.url')}
            </p>
            <Button size="sm" variant="ghost" className="mt-1" onClick={() => setCreated(null)}>
              {tr('ui.automationPanel.done')}
            </Button>
          </div>
        )}

        {triggers.length > 0 && (
          <ul className="flex flex-col divide-y divide-line rounded-[10px] border border-line">
            {triggers.map((row) => {
              const mail = row.kind === 'gmail';
              return (
              <li key={row.id} className="flex flex-wrap items-center gap-2 px-3 py-2">
                <span className={cn('h-1.5 w-1.5 rounded-full', row.enabled ? 'bg-accent' : 'bg-fg-dim')} aria-hidden />
                <span className="text-[13px] font-medium">{row.name}</span>
                {mail ? (
                  <span className="font-mono text-[11px] text-fg-dim">
                    {row.match ? tr('ui.automationPanel.onlyMailMatchingMatch', { match: row.match }) : tr('ui.automationPanel.allNewMail')}
                  </span>
                ) : (
                  <span className="font-mono text-[11px] text-fg-dim">…{row.token_hint}</span>
                )}
                <span className="font-mono text-[11px] text-fg-dim">
                  {trn(row.fire_count, 'ui.count.runs_one', 'ui.count.runs_other')}
                  {row.last_fired_at ? tr('ui.automationPanel.lastUtctolocal', { utcToLocal: utcToLocal(row.last_fired_at) }) : ''}
                </span>
                <span className="flex-1" />
                <Button size="sm" variant="ghost" onClick={() => toggle(row)} disabled={busy !== null}>
                  {row.enabled ? tr('ui.automationPanel.turnOff') : tr('ui.automationPanel.turnOn')}
                </Button>
                {!mail && (
                  <Button size="sm" variant="ghost" onClick={() => rotate(row.id)} disabled={busy !== null}>
                    {tr('ui.automationPanel.newUrl')}
                  </Button>
                )}
                <Button size="sm" variant="ghost" className="hover:text-danger-ink" onClick={() => remove(row.id)} disabled={busy !== null}>
                  {tr('ui.automationPanel.delete')}
                </Button>
                {mail && row.error && (
                  <p role="alert" className="w-full pl-3.5 text-[12px] text-danger-ink">{row.error}</p>
                )}
              </li>
              );
            })}
          </ul>
        )}

        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void addTrigger();
          }}
        >
          <label htmlFor="trigger-name" className="sr-only">{tr('ui.automationPanel.webhookName')}</label>
          <input
            id="trigger-name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder={tr('ui.automationPanel.nameEGGithubReleases')}
            maxLength={80}
            className="h-9 min-w-0 flex-1 rounded-[9px] border border-line bg-bg px-3 text-[13px] outline-none focus:border-accent"
          />
          <Button size="sm" type="submit" disabled={busy === 'add'}>
            {busy === 'add' ? tr('ui.automationPanel.adding') : tr('ui.automationPanel.addWebhook')}
          </Button>
        </form>

        <form
          className="flex flex-wrap gap-2"
          aria-label={tr('ui.automationPanel.startOnNewMail')}
          onSubmit={(e) => {
            e.preventDefault();
            void addMailTrigger();
          }}
        >
          <label htmlFor="trigger-mail-match" className="sr-only">{tr('ui.automationPanel.onlyMailWhoseSenderOr')}</label>
          <input
            id="trigger-mail-match"
            value={mailMatch}
            onChange={(e) => setMailMatch(e.target.value)}
            placeholder={tr('ui.automationPanel.onlyMailFromOrAbout')}
            maxLength={200}
            className="h-9 min-w-0 flex-1 rounded-[9px] border border-line bg-bg px-3 text-[13px] outline-none focus:border-accent"
          />
          <Button size="sm" type="submit" variant="outline" disabled={busy === 'add-mail'}>
            {busy === 'add-mail' ? tr('ui.automationPanel.adding') : tr('ui.automationPanel.startOnNewGmail')}
          </Button>
        </form>

        {events.length > 0 && (
          <div className="mt-1">
            <div className="mb-1 font-mono text-[10px] tracking-[0.14em] text-fg-dim">{tr('ui.automationPanel.recentEvents')}</div>
            <ul className="flex flex-col gap-1">
              {events.slice(0, 5).map((ev) => (
                <li key={ev.id} className="flex items-center gap-2 font-mono text-[11px] text-fg-dim">
                  <span className={cn(ev.status === 'dispatched' && 'text-ok', ev.status === 'dropped' && 'text-danger-ink')}>
                    {ev.status === 'dispatched' ? tr('ui.automationPanel.ran') : ev.status === 'pending' ? tr('ui.automationPanel.waiting') : ev.status}
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
