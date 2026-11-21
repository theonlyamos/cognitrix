import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { Input } from '@/lib/components/ui/input';
import { Textarea } from '@/lib/components/ui/textarea';
import { Select } from '@/lib/components/ui/select';
import { LoadingState, FilterChips } from '@/components/list-ui';
import { Field, PageForm, CheckList } from '@/components/form';
import { getPcLink, listDevices, remoteAgents, sendTaskToWeb, type PairedDevice } from '@/lib/devices';
import { isDesktop } from '@/lib/desktop';
import { modelName } from '@/lib/plain';
import { tr, dateLocale } from '@/lib/i18n';

interface Step { step: string; done: boolean }
interface Agent { id: string; name: string; description?: string; llm?: { provider?: string; model?: string }; automation?: { standing?: { primary?: boolean } } }
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
  schedule_tz?: string | null;
  swarm?: boolean;
  read_memory?: boolean;
  computer?: string | null;
}

type ScheduleMode = 'none' | 'once' | 'interval' | 'cron';
/** When the task runs, as the person picks it. */
type When = 'now' | 'later' | 'repeat' | 'manual';
type Repeat = 'daily' | 'weekdays' | 'weekly' | 'hours' | 'custom';

const browserZone = () => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
};

/** "0 9 * * 1-5" -> weekdays at 09:00, when the line is one of the simple shapes. */
function readCron(cron: string): { repeat: Repeat; time: string; weekday: string } | null {
  const m = /^(\d{1,2}) (\d{1,2}) \* \* (\*|1-5|[0-7])$/.exec(cron.trim());
  if (!m) return null;
  const time = `${m[2].padStart(2, '0')}:${m[1].padStart(2, '0')}`;
  if (m[3] === '*') return { repeat: 'daily', time, weekday: '1' };
  if (m[3] === '1-5') return { repeat: 'weekdays', time, weekday: '1' };
  return { repeat: 'weekly', time, weekday: m[3] === '7' ? '0' : m[3] };
}

/** A title from the first line of what was asked, for a task saved without one. */
function titleFrom(text: string): string {
  const line = text.trim().split(/\n|(?<=[.!?])\s/)[0].trim().replace(/[.!?]+$/, '');
  return line.length > 70 ? `${line.slice(0, 67).trimEnd()}…` : line;
}

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
  const [swarm, setSwarm] = useState(false);
  const [readMemory, setReadMemory] = useState(false);
  const [computer, setComputer] = useState('');
  const [pcs, setPcs] = useState<PairedDevice[]>([]);
  useEffect(() => { listDevices().then(setPcs).catch(() => {}); }, []);
  // Desktop app linked to a web account that takes tasks from this PC: a new
  // task can be sent there instead of running here.
  const [webAccount, setWebAccount] = useState('');
  const [webAgents, setWebAgents] = useState<{ id: string; name: string }[]>([]);
  const [onWeb, setOnWeb] = useState(false);
  const [webAgent, setWebAgent] = useState('');
  useEffect(() => {
    if (!isDesktop() || taskId) return;
    getPcLink()
      .then((l) => {
        if (!l.linked || !l.powers.includes('tasks')) return;
        setWebAccount(l.account);
        remoteAgents().then((a) => { setWebAgents(a); setWebAgent((v) => v || a[0]?.id || ''); }).catch(() => {});
      })
      .catch(() => {});
  }, [taskId]);
  const [scheduleAt, setScheduleAt] = useState('');
  const [intervalN, setIntervalN] = useState('30');
  const [intervalUnit, setIntervalUnit] = useState('minutes');
  const [cronExpr, setCronExpr] = useState('');
  const [scheduleEnabled, setScheduleEnabled] = useState(true);
  const [when, setWhen] = useState<When>(searchParams.get('repeat') ? 'repeat' : 'now');
  const [repeat, setRepeat] = useState<Repeat>('daily');
  const [atTime, setAtTime] = useState('09:00');
  const [weekday, setWeekday] = useState('1');
  // The zone a stored cron line is read in; a schedule picked here uses this browser's.
  const [cronZone, setCronZone] = useState<string | null>(null);
  const [showMore, setShowMore] = useState(editing);
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
    setCronZone(existing.schedule_tz ?? null);
    if (existing.schedule_at) {
      setWhen('later');
      setScheduleAt(utcToLocalInput(existing.schedule_at));
    } else if (existing.schedule_interval) {
      setWhen('repeat');
      setRepeat('hours');
      const secs = existing.schedule_interval;
      // Pick the coarsest unit that represents the value exactly — no rounding
      // (a 90s interval hydrates as "90 seconds", not "2 minutes").
      const unit = secs % 86400 === 0 ? 'days' : secs % 3600 === 0 ? 'hours' : secs % 60 === 0 ? 'minutes' : 'seconds';
      setIntervalUnit(unit);
      setIntervalN(String(secs / UNIT_SECONDS[unit]));
    } else if (existing.schedule_cron) {
      setWhen('repeat');
      setCronExpr(existing.schedule_cron);
      // Only a line written for this time zone can be shown as a clock time here.
      const simple = (existing.schedule_tz ?? null) === browserZone() ? readCron(existing.schedule_cron) : null;
      if (simple) {
        setRepeat(simple.repeat);
        setAtTime(simple.time);
        setWeekday(simple.weekday);
      } else {
        setRepeat('custom');
      }
    } else {
      setWhen('manual');
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

    if (!agentList && !agentLoadError) return;
    const requestedAgent = searchParams.get('agentId');
    if (requestedAgent && agentList?.some((agent) => agent.id === requestedAgent)) {
      setAssigned(new Set([requestedAgent]));
    } else {
      // Nobody asked for: the main assistant, which hands parts on when it needs to.
      const usual = agentList?.find((a) => a.automation?.standing?.primary) ?? agentList?.[0];
      if (usual?.id) setAssigned(new Set([usual.id]));
    }
    prefillApplied.current = true;
  }, [agentList, agentLoadError, editing, searchParams, teamList, teamLoadError]);

  // What the server stores for the picked "when".
  const scheduleMode: ScheduleMode = when === 'later' ? 'once'
    : when !== 'repeat' ? 'none'
    : repeat === 'hours' ? 'interval' : 'cron';
  const [hour, minute] = atTime.split(':').map((part) => Number(part) || 0);
  const cronLine = repeat === 'custom' ? cronExpr.trim()
    : repeat === 'daily' ? `${minute} ${hour} * * *`
    : repeat === 'weekdays' ? `${minute} ${hour} * * 1-5`
    : `${minute} ${hour} * * ${weekday}`;

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
    if (!description.trim()) return setError(tr('ui.taskPage.describeTheTask'));
    const finalTitle = title.trim() || titleFrom(description);
    if (!finalTitle) return setError(tr('ui.taskPage.giveTheTaskATitle'));
    if (scheduleMode === 'once' && !scheduleAt) return setError(tr('ui.taskPage.pickATimeForThe'));
    if (scheduleMode === 'interval' && (!intervalN || Number(intervalN) < 1)) return setError(tr('ui.taskPage.intervalMustBeAtLeast'));
    if (scheduleMode === 'cron' && !cronLine) return setError(tr('ui.taskPage.enterACronExpression'));
    setError('');
    setSaving(true);
    if (onWeb) {
      try {
        await sendTaskToWeb({ title: finalTitle, description: description.trim(), steps: steps.map((s) => s.step.trim()).filter(Boolean), agent_id: webAgent || null });
        navigate('/settings/computers');
      } catch (err) {
        setError(errorMessage(err, tr('ui.taskPage.couldNotSendTheTask')));
      } finally {
        setSaving(false);
      }
      return;
    }
    try {
      const step_instructions: Record<string, Step> = {};
      steps.filter((s) => s.step.trim()).forEach((s, i) => (step_instructions[i] = { step: s.step.trim(), done: s.done }));
      const res = await api.post('/tasks', {
        ...(taskId ? { id: taskId } : {}),
        title: finalTitle,
        description: description.trim(),
        step_instructions,
        assigned_agents: [...assigned],
        team_id: teamId || null,
        autostart: !editing && when === 'now',
        swarm,
        read_memory: readMemory,
        computer: computer || null,
        // A new task made from a project's Tasks tab belongs to that project.
        ...(!taskId && searchParams.get('project') ? { project_id: searchParams.get('project') } : {}),
        // Always sent: the form is authoritative for schedule state (the
        // server treats their presence as a full respecification).
        schedule_at: scheduleMode === 'once' && scheduleAt ? new Date(scheduleAt).toISOString() : null,
        schedule_interval: scheduleMode === 'interval' ? Number(intervalN) * UNIT_SECONDS[intervalUnit] : null,
        schedule_cron: scheduleMode === 'cron' ? cronLine : null,
        // A clock time picked here is in this person's time zone; a custom line keeps the zone it had.
        schedule_tz: scheduleMode === 'cron' ? (repeat === 'custom' ? cronZone : browserZone()) : null,
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

  const agents = (agentList || []).filter((a) => a.id);
  const teams = (teamList || []).filter((t) => t.id);
  // The one-line "who": a team, a single agent, or several picked under More options.
  const who = teamId ? `team:${teamId}` : assigned.size === 1 ? `agent:${[...assigned][0]}` : assigned.size > 1 ? 'several' : '';
  const changeWho = (value: string) => {
    if (value.startsWith('team:')) return changeTeam(value.slice(5));
    setTeamId('');
    setAssigned(value.startsWith('agent:') ? new Set([value.slice(6)]) : new Set());
  };
  const weekdays = [1, 2, 3, 4, 5, 6, 0].map((day) => ({
    value: String(day),
    // 2023-01-01 was a Sunday.
    label: new Date(2023, 0, 1 + day).toLocaleDateString(dateLocale(), { weekday: 'long' }),
  }));
  const whenWords: Record<When, string> = {
    now: tr('ui.taskPage.whenNowHint'),
    later: tr('ui.taskPage.whenLaterHint'),
    repeat: tr('ui.taskPage.whenRepeatHint'),
    manual: tr('ui.taskPage.whenManualHint'),
  };

  return (
    <PageForm
      eyebrow={editing ? 'EDIT TASK' : 'NEW TASK'}
      trail={editing
        ? [{ label: tr('ui.tasks.tasks'), to: '/tasks' }, { label: title || tr('ui.taskPage.editTask'), to: backTo }]
        : [{ label: tr('ui.tasks.tasks'), to: '/tasks' }]}
      title={editing ? tr('ui.taskPage.editTask') : tr('ui.taskPage.newTask')}
      backTo={backTo}
      error={error || agentLoadError || teamLoadError || ''}
      onSave={save}
      saving={saving}
      saveLabel={editing ? undefined : when === 'now' ? tr('ui.taskPage.startTask') : tr('ui.taskPage.createTask')}
      onDelete={editing ? remove : undefined}
    >
      {webAccount && (
        <Field label={tr('ui.taskPage.runOn')} hint={onWeb ? tr('ui.taskPage.runOnWebHint', { account: webAccount }) : undefined}>
          <Select value={onWeb ? 'web' : 'here'} onChange={(e) => setOnWeb(e.target.value === 'web')}>
            <option value="here">{tr('ui.taskPage.thisComputer')}</option>
            <option value="web">{tr('ui.taskPage.accountOnTheWeb', { account: webAccount })}</option>
          </Select>
        </Field>
      )}

      <Field label={tr('ui.taskPage.whatShouldGetDone')} required>
        <Textarea rows={5} value={description} onChange={(e) => setDescription(e.target.value)} placeholder={tr('ui.taskPage.whatShouldGetDonePlaceholder')} autoFocus />
      </Field>

      {onWeb && (
        <Field label={tr('ui.taskPage.agentOnTheWeb')}>
          <Select value={webAgent} onChange={(e) => setWebAgent(e.target.value)}>
            {webAgents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </Select>
        </Field>
      )}

      {!onWeb && (<>
      <Field label={tr('ui.taskPage.whoDoesIt')} hint={who === 'several' ? tr('ui.taskPage.pickedBelow') : undefined}>
        <Select value={who} onChange={(e) => changeWho(e.target.value)} disabled={agents.length === 0}>
          {who === '' && <option value="">{agents.length ? tr('ui.taskPage.chooseAnAgent') : tr('ui.taskPage.noAgentsCreateOneFirst')}</option>}
          {who === 'several' && <option value="several">{tr('ui.taskPage.severalAgents', { count: assigned.size })}</option>}
          <optgroup label={tr('ui.taskPage.agents')}>
            {agents.map((a) => <option key={a.id} value={`agent:${a.id}`}>{a.name}</option>)}
          </optgroup>
          {teams.length > 0 && (
            <optgroup label={tr('ui.taskPage.teams')}>
              {teams.map((t) => <option key={t.id} value={`team:${t.id}`}>{t.name}</option>)}
            </optgroup>
          )}
        </Select>
      </Field>

      <Field label={tr('ui.taskPage.when')} composite>
        <div role="group" className="space-y-3">
          <FilterChips<When>
            label={tr('ui.taskPage.when')}
            value={when}
            onChange={(next) => { if (next !== 'manual' && when === 'manual') setScheduleEnabled(true); setWhen(next); }}
            options={[
              ...(editing ? [] : [{ id: 'now' as const, label: tr('ui.taskPage.whenNow') }]),
              { id: 'later', label: tr('ui.taskPage.whenLater') },
              { id: 'repeat', label: tr('ui.taskPage.whenRepeat') },
              { id: 'manual', label: editing ? tr('ui.taskPage.notScheduled') : tr('ui.taskPage.whenManual') },
            ]}
          />
          <p className="text-[12.5px] text-fg-dim">{whenWords[when]}</p>
          {when === 'later' && (
            <Input aria-label={tr('ui.taskPage.scheduleTime')} type="datetime-local" min={localNowMin()} value={scheduleAt} onChange={(e) => setScheduleAt(e.target.value)} />
          )}
          {when === 'repeat' && (
            <div className="flex flex-wrap items-center gap-2">
              <Select aria-label={tr('ui.taskPage.howOften')} value={repeat} onChange={(e) => setRepeat(e.target.value as Repeat)} className="w-auto min-w-[11rem]">
                <option value="daily">{tr('ui.taskPage.everyDay')}</option>
                <option value="weekdays">{tr('ui.taskPage.everyWeekday')}</option>
                <option value="weekly">{tr('ui.taskPage.everyWeek')}</option>
                <option value="hours">{tr('ui.taskPage.everyFew')}</option>
                <option value="custom">{tr('ui.taskPage.customSchedule')}</option>
              </Select>
              {repeat === 'weekly' && (
                <Select aria-label={tr('ui.taskPage.dayOfTheWeek')} value={weekday} onChange={(e) => setWeekday(e.target.value)} className="w-auto">
                  {weekdays.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
                </Select>
              )}
              {(repeat === 'daily' || repeat === 'weekdays' || repeat === 'weekly') && (
                <>
                  <span className="text-[13px] text-fg-dim">{tr('ui.taskPage.at')}</span>
                  <Input aria-label={tr('ui.taskPage.timeOfDay')} type="time" value={atTime} onChange={(e) => setAtTime(e.target.value || '09:00')} className="w-32" />
                </>
              )}
              {repeat === 'hours' && (
                <>
                  <Input aria-label={tr('ui.taskPage.intervalValue')} type="number" min={1} value={intervalN} onChange={(e) => setIntervalN(e.target.value)} className="w-24" />
                  <Select aria-label={tr('ui.taskPage.intervalUnit')} value={intervalUnit} onChange={(e) => setIntervalUnit(e.target.value)} className="w-auto">
                    {intervalUnit === 'seconds' && <option value="seconds">{tr('ui.taskPage.seconds')}</option>}
                    <option value="minutes">{tr('ui.taskPage.minutes')}</option>
                    <option value="hours">{tr('ui.taskPage.hours')}</option>
                    <option value="days">{tr('ui.taskPage.days')}</option>
                  </Select>
                </>
              )}
              {repeat === 'custom' && (
                <div className="w-full">
                  <Input aria-label={tr('ui.taskPage.cronExpression')} value={cronExpr} onChange={(e) => setCronExpr(e.target.value)} placeholder="0 9 * * 1-5" className="font-mono" />
                  <p className="mt-1 text-[12px] text-fg-dim">
                    {tr('ui.taskPage.customScheduleHint', { zone: cronZone || tr('ui.taskPage.theServersTimeZone') })}
                  </p>
                </div>
              )}
            </div>
          )}
          {editing && scheduleMode !== 'none' && (
            <label className="flex min-h-11 cursor-pointer items-center gap-2.5 md:min-h-0">
              <input type="checkbox" checked={scheduleEnabled} onChange={(e) => setScheduleEnabled(e.target.checked)} className="accent-[var(--accent)]" />
              <span className="text-sm">{tr('ui.taskPage.scheduleEnabled')}</span>
            </label>
          )}
        </div>
      </Field>
      </>)}

      <button
        type="button"
        aria-expanded={showMore}
        onClick={() => setShowMore((v) => !v)}
        className="flex min-h-11 items-center gap-2 text-[13px] text-fg-dim transition-colors hover:text-fg md:min-h-0"
      >
        <span aria-hidden className={showMore ? 'rotate-90 transition-transform' : 'transition-transform'}>▸</span>{' '}{tr('ui.taskPage.moreOptions')}
      </button>

      {showMore && (<>
      <Field label={tr('ui.taskPage.title')} hint={tr('ui.taskPage.titleHint')}>
        <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={titleFrom(description) || tr('ui.taskPage.researchCompetitorPricing')} />
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
          <button type="button" onClick={() => setSteps((arr) => [...arr, { step: '', done: false }])} className="min-h-11 rounded border border-line px-3 py-1.5 text-[12.5px] text-fg-dim transition-colors hover:border-fg-dim hover:text-fg md:min-h-0">{tr('ui.taskPage.addStep')}</button>
        </div>
      </Field>

      {!onWeb && (<>
      <Field label={tr('ui.taskPage.assignedAgentsSize', { size: assigned.size })} hint={teamId ? tr('ui.taskPage.fromTheSelectedTeamEditable') : undefined} composite>
        <CheckList
          options={agents.map((a) => ({ value: a.id, label: a.name, sub: a.description || modelName(a.llm?.model) }))}
          selected={assigned}
          onToggle={toggleAgent}
          empty={tr('ui.taskPage.noAgentsCreateOneFirst')}
        />
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
      </>)}
      </>)}
    </PageForm>
  );
}
