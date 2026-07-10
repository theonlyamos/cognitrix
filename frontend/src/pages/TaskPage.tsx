import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { Input } from '@/lib/components/ui/input';
import { Textarea } from '@/lib/components/ui/textarea';
import { Select } from '@/lib/components/ui/select';
import { LoadingState } from '@/components/list-ui';
import { Field, PageForm, CheckList } from '@/components/form';

interface Step { step: string; done: boolean }
interface Agent { id: string; name: string; llm?: { provider?: string; model?: string } }
interface Team { id: string; name: string; assigned_agents?: string[] }
interface TaskData {
  id?: string;
  title?: string;
  description?: string;
  step_instructions?: Record<string, { step: string; done: boolean }>;
  assigned_agents?: string[];
  team_id?: string | null;
  autostart?: boolean;
  status?: string;
  schedule_at?: string | null;
  schedule_interval?: number | null;
  schedule_cron?: string | null;
  schedule_enabled?: boolean;
}

type ScheduleMode = 'none' | 'once' | 'interval' | 'cron';

const UNIT_SECONDS: Record<string, number> = { seconds: 1, minutes: 60, hours: 3600, days: 86400 };

// "now" as a datetime-local string (local wall-clock) so the one-shot picker
// can't select a past instant.
function localNowMin(): string {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

// Stored naive-UTC 'YYYY-MM-DD HH:MM:SS' → local datetime-local value.
function utcToLocalInput(s: string): string {
  const d = new Date(s.replace(' ', 'T') + 'Z');
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

/** Task edit form. Runs, status and live monitoring live on TaskDetail
 *  (/tasks/:taskId); this page only creates and edits the task itself. */
export default function TaskPage() {
  const { taskId } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const editing = Boolean(taskId);
  // Where "done" leads: back to the details page when editing, the list when creating.
  const backTo = editing ? `/tasks/${taskId}` : '/tasks';

  const { data: existing, loading: loadingTask } = useResource<TaskData>(taskId ? `/tasks/${taskId}` : null);
  const { data: agentList, error: agentLoadError } = useResource<Agent[]>('/agents');
  const { data: teamList, error: teamLoadError } = useResource<Team[]>('/teams');

  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [steps, setSteps] = useState<Step[]>([]);
  const [assigned, setAssigned] = useState<Set<string>>(new Set());
  const [teamId, setTeamId] = useState('');
  const [autostart, setAutostart] = useState(false);
  const [scheduleMode, setScheduleMode] = useState<ScheduleMode>('none');
  const [scheduleAt, setScheduleAt] = useState('');
  const [intervalN, setIntervalN] = useState('30');
  const [intervalUnit, setIntervalUnit] = useState('minutes');
  const [cronExpr, setCronExpr] = useState('');
  const [scheduleEnabled, setScheduleEnabled] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const prefillApplied = useRef(false);

  useEffect(() => {
    if (!existing) return;
    setTitle(existing.title || '');
    setDescription(existing.description || '');
    const si = existing.step_instructions || {};
    setSteps(
      Object.keys(si)
        .sort((a, b) => Number(a) - Number(b))
        .map((k) => ({ step: si[k].step, done: !!si[k].done })),
    );
    setAssigned(new Set(existing.assigned_agents || []));
    setTeamId(existing.team_id || '');
    setAutostart(!!existing.autostart);
    if (existing.schedule_at) {
      setScheduleMode('once');
      setScheduleAt(utcToLocalInput(existing.schedule_at));
    } else if (existing.schedule_interval) {
      setScheduleMode('interval');
      const secs = existing.schedule_interval;
      // Pick the coarsest unit that represents the value exactly — no rounding
      // (a 90s interval hydrates as "90 seconds", not "2 minutes").
      const unit = secs % 86400 === 0 ? 'days' : secs % 3600 === 0 ? 'hours' : secs % 60 === 0 ? 'minutes' : 'seconds';
      setIntervalUnit(unit);
      setIntervalN(String(secs / UNIT_SECONDS[unit]));
    } else if (existing.schedule_cron) {
      setScheduleMode('cron');
      setCronExpr(existing.schedule_cron);
    } else {
      setScheduleMode('none');
    }
    if (existing.schedule_at || existing.schedule_interval || existing.schedule_cron) {
      setScheduleEnabled(!!existing.schedule_enabled);
    }
  }, [existing]);

  useEffect(() => {
    if (editing || prefillApplied.current) return;

    const requestedTeam = searchParams.get('teamId');
    if (requestedTeam) {
      if (!teamList && !teamLoadError) return;
      const team = teamList?.find((item) => item.id === requestedTeam);
      if (team) {
        prefillApplied.current = true;
        setTeamId(team.id);
        setAssigned(new Set(team.assigned_agents || []));
        return;
      }
    }

    const requestedAgent = searchParams.get('agentId');
    if (requestedAgent) {
      if (!agentList && !agentLoadError) return;
      if (agentList?.some((agent) => agent.id === requestedAgent)) {
        setAssigned(new Set([requestedAgent]));
      }
    }
    prefillApplied.current = true;
  }, [agentList, agentLoadError, editing, searchParams, teamList, teamLoadError]);

  const changeScheduleMode = (mode: ScheduleMode) => {
    // Picking a schedule on an unscheduled task should arm it by default.
    if (mode !== 'none' && scheduleMode === 'none') setScheduleEnabled(true);
    setScheduleMode(mode);
  };

  const toggleAgent = (id: string) =>
    setAssigned((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  // Assigning a team scopes the task to it and pre-fills its members as the
  // task's agents (still editable). Clearing the team leaves agents as-is.
  const changeTeam = (id: string) => {
    setTeamId(id);
    if (!id) return;
    const team = (teamList || []).find((t) => t.id === id);
    if (team) setAssigned(new Set(team.assigned_agents || []));
  };

  const save = async () => {
    if (!title.trim()) return setError('Give the task a title.');
    if (!description.trim()) return setError('Describe the task.');
    if (scheduleMode === 'once' && !scheduleAt) return setError('Pick a time for the one-shot schedule.');
    if (scheduleMode === 'interval' && (!intervalN || Number(intervalN) < 1)) return setError('Interval must be at least 1.');
    if (scheduleMode === 'cron' && !cronExpr.trim()) return setError('Enter a cron expression.');
    setError('');
    setSaving(true);
    try {
      const step_instructions: Record<string, Step> = {};
      steps.filter((s) => s.step.trim()).forEach((s, i) => (step_instructions[i] = { step: s.step.trim(), done: s.done }));
      const res = await api.post('/tasks', {
        ...(taskId ? { id: taskId } : {}),
        title: title.trim(),
        description: description.trim(),
        step_instructions,
        assigned_agents: [...assigned],
        team_id: teamId || null,
        autostart,
        // Always sent: the form is authoritative for schedule state (the
        // server treats their presence as a full respecification).
        schedule_at: scheduleMode === 'once' && scheduleAt ? new Date(scheduleAt).toISOString() : null,
        schedule_interval: scheduleMode === 'interval' ? Number(intervalN) * UNIT_SECONDS[intervalUnit] : null,
        schedule_cron: scheduleMode === 'cron' ? cronExpr.trim() : null,
        schedule_enabled: scheduleMode !== 'none' && scheduleEnabled,
        ...(existing?.status ? { status: existing.status } : {}),
      });
      // Land on the task's details page (new tasks included — the POST returns the id).
      const id = taskId || res.data?.id;
      navigate(id ? `/tasks/${id}` : '/tasks');
    } catch (e) {
      setError(errorMessage(e, 'Could not save the task.'));
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!taskId || !confirm('Delete this task?')) return;
    try {
      await api.delete(`/tasks/${taskId}`);
      navigate('/tasks');
    } catch (e) {
      setError(errorMessage(e, 'Could not delete the task.'));
    }
  };

  if (editing && loadingTask) {
    return <div className="flex-1 flex flex-col h-screen min-w-0 bg-bg"><LoadingState label="loading task…" /></div>;
  }

  return (
    <PageForm
      eyebrow={editing ? 'EDIT TASK' : 'NEW TASK'}
      title={editing ? title || 'Edit task' : 'New task'}
      backTo={backTo}
      error={error || agentLoadError || teamLoadError || ''}
      onSave={save}
      saving={saving}
      onDelete={editing ? remove : undefined}
    >
      <Field label="TITLE" required>
        <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Research competitor pricing" autoFocus />
      </Field>

      <Field label="DESCRIPTION" required>
        <Textarea rows={4} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What should be done, and what does 'done' look like?" />
      </Field>

      <Field label={`STEPS · ${steps.length}`} hint="optional — plan the task explicitly" composite>
        <div role="group" className="space-y-2">
          {steps.map((s, i) => (
            <div key={i} className="flex items-center gap-2">
              {s.done ? (
                <span className="w-6 flex-none text-right font-mono text-[11px] text-ok">✓</span>
              ) : (
                <span className="w-6 flex-none text-right font-mono text-[11px] text-fg-dim tnum">{i + 1}</span>
              )}
              <Input aria-label={`Step ${i + 1}`} value={s.step} onChange={(e) => setSteps((arr) => arr.map((x, j) => (j === i ? { ...x, step: e.target.value } : x)))} placeholder={`step ${i + 1}`} />
              <button type="button" onClick={() => setSteps((arr) => arr.filter((_, j) => j !== i))} className="grid h-11 w-11 flex-none place-items-center rounded border border-line text-fg-dim hover:border-danger hover:text-danger-ink md:h-8 md:w-8" aria-label={`Remove step ${i + 1}`}>✕</button>
            </div>
          ))}
          <button type="button" onClick={() => setSteps((arr) => [...arr, { step: '', done: false }])} className="min-h-11 rounded border border-line px-3 py-1.5 font-mono text-[11px] text-fg-dim transition-colors hover:border-fg-dim hover:text-fg md:min-h-0">+ add step</button>
        </div>
      </Field>

      <Field label="TEAM" hint="optional — assign the task to a team">
        <Select value={teamId} onChange={(e) => changeTeam(e.target.value)} disabled={(teamList || []).length === 0}>
          <option value="">— no team —</option>
          {(teamList || []).filter((t) => t.id).map((t) => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </Select>
      </Field>

      <Field label={`ASSIGNED AGENTS · ${assigned.size}`} hint={teamId ? 'from the selected team — editable' : undefined} composite>
        <CheckList
          options={(agentList || []).filter((a) => a.id).map((a) => ({ value: a.id, label: a.name, sub: `${a.llm?.provider || '—'} · ${a.llm?.model || '—'}` }))}
          selected={assigned}
          onToggle={toggleAgent}
          empty="no agents — create one first"
        />
      </Field>

      <Field label="SCHEDULE" hint="run this task automatically" composite>
        <div role="group" className="space-y-3">
          <Select aria-label="Schedule type" value={scheduleMode} onChange={(e) => changeScheduleMode(e.target.value as ScheduleMode)}>
            <option value="none">— not scheduled —</option>
            <option value="once">once, at a time</option>
            <option value="interval">repeat every…</option>
            <option value="cron">cron expression</option>
          </Select>
          {scheduleMode === 'once' && (
            <Input aria-label="Schedule time" type="datetime-local" min={localNowMin()} value={scheduleAt} onChange={(e) => setScheduleAt(e.target.value)} />
          )}
          {scheduleMode === 'interval' && (
            <div className="flex gap-2">
              <Input aria-label="Interval value" type="number" min={1} value={intervalN} onChange={(e) => setIntervalN(e.target.value)} className="w-28" />
              <Select aria-label="Interval unit" value={intervalUnit} onChange={(e) => setIntervalUnit(e.target.value)}>
                <option value="seconds">seconds</option>
                <option value="minutes">minutes</option>
                <option value="hours">hours</option>
                <option value="days">days</option>
              </Select>
            </div>
          )}
          {scheduleMode === 'cron' && (
            <div>
              <Input aria-label="Cron expression" value={cronExpr} onChange={(e) => setCronExpr(e.target.value)} placeholder="0 9 * * 1-5" className="font-mono" />
              <p className="mt-1 font-mono text-[11px] text-fg-dim">5-field cron, evaluated in the server's local time</p>
            </div>
          )}
          {scheduleMode !== 'none' && (
            <label className="flex min-h-11 cursor-pointer items-center gap-2.5 md:min-h-0">
              <input type="checkbox" checked={scheduleEnabled} onChange={(e) => setScheduleEnabled(e.target.checked)} className="accent-[var(--accent)]" />
              <span className="text-sm">Schedule enabled</span>
            </label>
          )}
        </div>
      </Field>

      <label className="flex min-h-11 cursor-pointer items-center gap-2.5 md:min-h-0">
        <input type="checkbox" checked={autostart} onChange={(e) => setAutostart(e.target.checked)} className="accent-[var(--accent)]" />
        <span className="text-sm">Auto-start when created</span>
      </label>
    </PageForm>
  );
}
