import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { Textarea } from '@/lib/components/ui/textarea';
import { Select } from '@/lib/components/ui/select';
import { LoadingState } from '@/components/list-ui';
import { Field, PageForm, CheckList } from '@/components/form';
import { cn } from '@/lib/utils';

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
  results?: string[];
}

export default function TaskPage() {
  const { taskId } = useParams();
  const navigate = useNavigate();
  const editing = Boolean(taskId);

  const { data: existing, loading: loadingTask, refetch } = useResource<TaskData>(taskId ? `/tasks/${taskId}` : null);
  const { data: agentList } = useResource<Agent[]>('/agents');
  const { data: teamList } = useResource<Team[]>('/teams');

  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [steps, setSteps] = useState<Step[]>([]);
  const [assigned, setAssigned] = useState<Set<string>>(new Set());
  const [teamId, setTeamId] = useState('');
  const [autostart, setAutostart] = useState(false);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(false);

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
  }, [existing]);

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
    setError('');
    setSaving(true);
    try {
      const step_instructions: Record<string, Step> = {};
      steps.filter((s) => s.step.trim()).forEach((s, i) => (step_instructions[i] = { step: s.step.trim(), done: s.done }));
      await api.post('/tasks', {
        ...(taskId ? { id: taskId } : {}),
        title: title.trim(),
        description: description.trim(),
        step_instructions,
        assigned_agents: [...assigned],
        team_id: teamId || null,
        autostart,
        ...(existing?.status ? { status: existing.status } : {}),
      });
      navigate('/tasks');
    } catch (e) {
      setError(errorMessage(e, 'Could not save the task.'));
    } finally {
      setSaving(false);
    }
  };

  const start = async () => {
    if (!taskId) return;
    setStarting(true);
    try {
      await api.get(`/tasks/start/${taskId}`);
      await refetch();
    } catch (e) {
      setError(errorMessage(e, 'Could not start the task.'));
    } finally {
      setStarting(false);
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

  const status = existing?.status;
  const results = existing?.results || [];

  return (
    <PageForm
      eyebrow={editing ? 'EDIT TASK' : 'NEW TASK'}
      title={editing ? title || 'Edit task' : 'New task'}
      backTo="/tasks"
      error={error}
      onSave={save}
      saving={saving}
      onDelete={editing ? remove : undefined}
      extraActions={
        editing ? (
          <Button variant="outline" size="sm" onClick={start} disabled={starting}>
            {starting ? 'Starting…' : '▶ Run'}
          </Button>
        ) : undefined
      }
    >
      {editing && (status || results.length > 0) && (
        <div className="rounded border border-line bg-panel-2 px-3 py-2.5">
          <div className="flex items-center gap-2 font-mono text-[11px] text-fg-dim">
            <span>status</span>
            <span className={cn('rounded border px-2 py-0.5', status === 'completed' ? 'border-ok/40 text-ok' : status === 'in_progress' ? 'border-accent/40 text-accent-ink' : status === 'failed' ? 'border-danger/40 text-danger-ink' : 'border-line')}>{status || 'pending'}</span>
          </div>
          {results.length > 0 && (
            <div className="mt-2 space-y-1">
              {results.map((r, i) => <div key={i} className="whitespace-pre-wrap text-[13px] text-fg-dim">{r}</div>)}
            </div>
          )}
        </div>
      )}

      <Field label="TITLE" required>
        <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Research competitor pricing" autoFocus />
      </Field>

      <Field label="DESCRIPTION" required>
        <Textarea rows={4} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What should be done, and what does 'done' look like?" />
      </Field>

      <Field label={`STEPS · ${steps.length}`} hint="optional — plan the task explicitly">
        <div className="space-y-2">
          {steps.map((s, i) => (
            <div key={i} className="flex items-center gap-2">
              <span className="w-6 flex-none text-right font-mono text-[11px] text-fg-dim tnum">{i + 1}</span>
              <Input value={s.step} onChange={(e) => setSteps((arr) => arr.map((x, j) => (j === i ? { ...x, step: e.target.value } : x)))} placeholder={`step ${i + 1}`} />
              <button type="button" onClick={() => setSteps((arr) => arr.filter((_, j) => j !== i))} className="grid h-8 w-8 flex-none place-items-center rounded border border-line text-fg-dim hover:border-danger hover:text-danger-ink" aria-label="Remove step">✕</button>
            </div>
          ))}
          <button type="button" onClick={() => setSteps((arr) => [...arr, { step: '', done: false }])} className="rounded border border-line px-3 py-1.5 font-mono text-[11px] text-fg-dim transition-colors hover:border-fg-dim hover:text-fg">+ add step</button>
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

      <Field label={`ASSIGNED AGENTS · ${assigned.size}`} hint={teamId ? 'from the selected team — editable' : undefined}>
        <CheckList
          options={(agentList || []).filter((a) => a.id).map((a) => ({ value: a.id, label: a.name, sub: `${a.llm?.provider || '—'} · ${a.llm?.model || '—'}` }))}
          selected={assigned}
          onToggle={toggleAgent}
          empty="no agents — create one first"
        />
      </Field>

      <label className="flex cursor-pointer items-center gap-2.5">
        <input type="checkbox" checked={autostart} onChange={(e) => setAutostart(e.target.checked)} className="accent-[var(--accent)]" />
        <span className="text-sm">Auto-start when created</span>
      </label>
    </PageForm>
  );
}
