import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { Input } from '@/lib/components/ui/input';
import { Textarea } from '@/lib/components/ui/textarea';
import { Select } from '@/lib/components/ui/select';
import { LoadingState } from '@/components/list-ui';
import { Field, PageForm, CheckList } from '@/components/form';
import { listDevices, type PairedDevice } from '@/lib/devices';
import { tr } from '@/lib/i18n';

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
  swarm?: boolean;
  read_memory?: boolean;
  computer?: string | null;
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
  const [swarm, setSwarm] = useState(false);
  const [readMemory, setReadMemory] = useState(false);
  const [computer, setComputer] = useState('');
  const [pcs, setPcs] = useState<PairedDevice[]>([]);
  useEffect(() => { listDevices().then(setPcs).catch(() => {}); }, []);
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
    setSwarm(!!existing.swarm);
    setReadMemory(!!existing.read_memory);
    setComputer(existing.computer || '');
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
    if (!title.trim()) return setError(tr('ui.taskPage.giveTheTaskATitle'));
    if (!description.trim()) return setError(tr('ui.taskPage.describeTheTask'));
    if (scheduleMode === 'once' && !scheduleAt) return setError(tr('ui.taskPage.pickATimeForThe'));
    if (scheduleMode === 'interval' && (!intervalN || Number(intervalN) < 1)) return setError(tr('ui.taskPage.intervalMustBeAtLeast'));
    if (scheduleMode === 'cron' && !cronExpr.trim()) return setError(tr('ui.taskPage.enterACronExpression'));
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
        swarm,
        read_memory: readMemory,
        computer: computer || null,
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
      setError(errorMessage(e, tr('ui.taskPage.couldNotSaveTheTask')));
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!taskId || !confirm(tr('ui.taskPage.deleteThisTask'))) return;
    try {
      await api.delete(`/tasks/${taskId}`);
      navigate('/tasks');
    } catch (e) {
      setError(errorMessage(e, tr('ui.taskPage.couldNotDeleteTheTask')));
    }
  };

  if (editing && loadingTask) {
    return <div className="flex-1 flex flex-col h-full min-w-0 bg-bg"><LoadingState label={tr('ui.taskPage.loadingTask')} /></div>;
  }

  return (
    <PageForm
      eyebrow={editing ? 'EDIT TASK' : 'NEW TASK'}
      title={editing ? title || tr('ui.taskPage.editTask') : tr('ui.taskPage.newTask')}
      backTo={backTo}
      error={error || agentLoadError || teamLoadError || ''}
      onSave={save}
      saving={saving}
      onDelete={editing ? remove : undefined}
    >
      <Field label={tr('ui.taskPage.title')} required>
        <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={tr('ui.taskPage.researchCompetitorPricing')} autoFocus />
      </Field>

      <Field label={tr('ui.taskPage.description')} required>
        <Textarea rows={4} value={description} onChange={(e) => setDescription(e.target.value)} placeholder={tr('ui.taskPage.whatShouldBeDoneAnd')} />
      </Field>

      <Field label={tr('ui.taskPage.stepsLength', { length: steps.length })} hint={tr('ui.taskPage.optionalPlanTheTaskExplicitly')} composite>
        <div role="group" className="space-y-2">
          {steps.map((s, i) => (
            <div key={i} className="flex items-center gap-2">
              {s.done ? (
                <span className="w-6 flex-none text-right font-mono text-[11px] text-ok">✓</span>
              ) : (
                <span className="w-6 flex-none text-right font-mono text-[11px] text-fg-dim tnum">{i + 1}</span>
              )}
              <Input aria-label={tr('ui.taskPage.stepValue', { value: i + 1 })} value={s.step} onChange={(e) => setSteps((arr) => arr.map((x, j) => (j === i ? { ...x, step: e.target.value } : x)))} placeholder={tr('ui.taskPage.stepValue_543f', { value: i + 1 })} />
              <button type="button" onClick={() => setSteps((arr) => arr.filter((_, j) => j !== i))} className="grid h-11 w-11 flex-none place-items-center rounded border border-line text-fg-dim hover:border-danger hover:text-danger-ink md:h-8 md:w-8" aria-label={tr('ui.taskPage.removeStepValue', { value: i + 1 })}>✕</button>
            </div>
          ))}
          <button type="button" onClick={() => setSteps((arr) => [...arr, { step: '', done: false }])} className="min-h-11 rounded border border-line px-3 py-1.5 font-mono text-[11px] text-fg-dim transition-colors hover:border-fg-dim hover:text-fg md:min-h-0">{tr('ui.taskPage.addStep')}</button>
        </div>
      </Field>

      <Field label={tr('ui.taskPage.team')} hint={tr('ui.taskPage.optionalAssignTheTaskTo')}>
        <Select value={teamId} onChange={(e) => changeTeam(e.target.value)} disabled={(teamList || []).length === 0}>
          <option value="">{tr('ui.taskPage.noTeam')}</option>
          {(teamList || []).filter((t) => t.id).map((t) => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </Select>
      </Field>

      {(pcs.length > 0 || computer) && (
        <Field label={tr('ui.taskPage.computer')} hint={tr('ui.taskPage.computerHint')}>
          <Select value={computer} onChange={(e) => setComputer(e.target.value)}>
            <option value="">{tr('ui.taskPage.agentsUsualComputer')}</option>
            {pcs.map((d) => (
              <option key={d.id} value={d.id}>{d.online ? d.name : tr('ui.taskPage.nameOffline', { name: d.name })}</option>
            ))}
          </Select>
        </Field>
      )}

      <Field label={tr('ui.taskPage.assignedAgentsSize', { size: assigned.size })} hint={teamId ? tr('ui.taskPage.fromTheSelectedTeamEditable') : undefined} composite>
        <CheckList
          options={(agentList || []).filter((a) => a.id).map((a) => ({ value: a.id, label: a.name, sub: `${a.llm?.provider || '—'} · ${a.llm?.model || '—'}` }))}
          selected={assigned}
          onToggle={toggleAgent}
          empty={tr('ui.taskPage.noAgentsCreateOneFirst')}
        />
      </Field>

      <Field label={tr('ui.taskPage.schedule')} hint={tr('ui.taskPage.runThisTaskAutomatically')} composite>
        <div role="group" className="space-y-3">
          <Select aria-label={tr('ui.taskPage.scheduleType')} value={scheduleMode} onChange={(e) => changeScheduleMode(e.target.value as ScheduleMode)}>
            <option value="none">{tr('ui.taskPage.notScheduled')}</option>
            <option value="once">{tr('ui.taskPage.onceAtATime')}</option>
            <option value="interval">{tr('ui.taskPage.repeatEvery')}</option>
            <option value="cron">{tr('ui.taskPage.cronExpression_2348')}</option>
          </Select>
          {scheduleMode === 'once' && (
            <Input aria-label={tr('ui.taskPage.scheduleTime')} type="datetime-local" min={localNowMin()} value={scheduleAt} onChange={(e) => setScheduleAt(e.target.value)} />
          )}
          {scheduleMode === 'interval' && (
            <div className="flex gap-2">
              <Input aria-label={tr('ui.taskPage.intervalValue')} type="number" min={1} value={intervalN} onChange={(e) => setIntervalN(e.target.value)} className="w-28" />
              <Select aria-label={tr('ui.taskPage.intervalUnit')} value={intervalUnit} onChange={(e) => setIntervalUnit(e.target.value)}>
                <option value="seconds">{tr('ui.taskPage.seconds')}</option>
                <option value="minutes">{tr('ui.taskPage.minutes')}</option>
                <option value="hours">{tr('ui.taskPage.hours')}</option>
                <option value="days">{tr('ui.taskPage.days')}</option>
              </Select>
            </div>
          )}
          {scheduleMode === 'cron' && (
            <div>
              <Input aria-label={tr('ui.taskPage.cronExpression')} value={cronExpr} onChange={(e) => setCronExpr(e.target.value)} placeholder="0 9 * * 1-5" className="font-mono" />
              <p className="mt-1 font-mono text-[11px] text-fg-dim">5-field cron, evaluated in the server's local time</p>
            </div>
          )}
          {scheduleMode !== 'none' && (
            <label className="flex min-h-11 cursor-pointer items-center gap-2.5 md:min-h-0">
              <input type="checkbox" checked={scheduleEnabled} onChange={(e) => setScheduleEnabled(e.target.checked)} className="accent-[var(--accent)]" />
              <span className="text-sm">{tr('ui.taskPage.scheduleEnabled')}</span>
            </label>
          )}
        </div>
      </Field>

      <label className="flex min-h-11 cursor-pointer items-start gap-2.5 md:min-h-0">
        <input type="checkbox" checked={swarm} onChange={(e) => setSwarm(e.target.checked)} className="mt-1 accent-[var(--accent)]" />
        <span className="text-sm">
          {tr('ui.taskPage.runAsASwarm')}
          <span className="block text-[12px] text-fg-dim">{tr('ui.taskPage.eachStepMaySplitIts')}</span>
        </span>
      </label>

      <label className="flex min-h-11 cursor-pointer items-start gap-2.5 md:min-h-0">
        <input type="checkbox" checked={readMemory} onChange={(e) => setReadMemory(e.target.checked)} className="mt-1 accent-[var(--accent)]" />
        <span className="text-sm">
          {tr('ui.taskPage.readMySavedMemories')}
          <span className="block text-[12px] text-fg-dim">{tr('ui.taskPage.runsSeeWhatYourAgents')}</span>
        </span>
      </label>

      <label className="flex min-h-11 cursor-pointer items-center gap-2.5 md:min-h-0">
        <input type="checkbox" checked={autostart} onChange={(e) => setAutostart(e.target.checked)} className="accent-[var(--accent)]" />
        <span className="text-sm">{tr('ui.taskPage.autoStartWhenCreated')}</span>
      </label>
    </PageForm>
  );
}
