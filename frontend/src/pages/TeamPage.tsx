import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { Textarea } from '@/lib/components/ui/textarea';
import { Select } from '@/lib/components/ui/select';
import { LoadingState } from '@/components/list-ui';
import { Field, PageForm, CheckList } from '@/components/form';

interface Agent { id: string; name: string; llm?: { provider?: string; model?: string } }
interface TeamData {
  id?: string;
  name?: string;
  description?: string;
  assigned_agents?: string[];
  leader_id?: string | null;
}

export default function TeamPage() {
  const { teamId } = useParams();
  const navigate = useNavigate();
  const editing = Boolean(teamId);

  const { data: existing, loading: loadingTeam } = useResource<TeamData>(teamId ? `/teams/${teamId}` : null);
  const { data: agentList } = useResource<Agent[]>('/agents');

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [assigned, setAssigned] = useState<Set<string>>(new Set());
  const [leader, setLeader] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!existing) return;
    setName(existing.name || '');
    setDescription(existing.description || '');
    setAssigned(new Set(existing.assigned_agents || []));
    setLeader(existing.leader_id || '');
  }, [existing]);

  const agents = useMemo(() => (agentList || []).filter((a) => a.id), [agentList]);
  const assignedAgents = useMemo(() => agents.filter((a) => assigned.has(a.id)), [agents, assigned]);

  const toggleAgent = (id: string) =>
    setAssigned((s) => {
      const n = new Set(s);
      if (n.has(id)) {
        n.delete(id);
        if (leader === id) setLeader('');
      } else n.add(id);
      return n;
    });

  const save = async () => {
    if (!name.trim()) return setError('Give the team a name.');
    if (!description.trim()) return setError('Describe the team.');
    setError('');
    setSaving(true);
    try {
      await api.post('/teams', {
        ...(teamId ? { id: teamId } : {}),
        name: name.trim(),
        description: description.trim(),
        assigned_agents: [...assigned],
        leader_id: leader || null,
      });
      navigate('/teams');
    } catch (e) {
      setError(errorMessage(e, 'Could not save the team.'));
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!teamId || !confirm('Delete this team?')) return;
    try {
      await api.delete(`/teams/${teamId}`);
      navigate('/teams');
    } catch (e) {
      setError(errorMessage(e, 'Could not delete the team.'));
    }
  };

  if (editing && loadingTeam) {
    return <div className="flex-1 flex flex-col h-screen min-w-0 bg-bg"><LoadingState label="loading team…" /></div>;
  }

  return (
    <PageForm
      eyebrow={editing ? 'EDIT TEAM' : 'NEW TEAM'}
      title={editing ? name || 'Edit team' : 'New team'}
      backTo="/teams"
      error={error}
      onSave={save}
      saving={saving}
      onDelete={editing ? remove : undefined}
      extraActions={
        editing ? (
          <Button asChild variant="outline" size="sm">
            <Link to={`/teams/${teamId}/interact`}>Interact →</Link>
          </Button>
        ) : undefined
      }
    >
      <Field label="NAME" required>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Research Squad" autoFocus />
      </Field>

      <Field label="DESCRIPTION" required>
        <Textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What does this team do together?" />
      </Field>

      <Field label={`MEMBERS · ${assigned.size}`}>
        <CheckList
          options={agents.map((a) => ({ value: a.id, label: a.name, sub: `${a.llm?.provider || '—'} · ${a.llm?.model || '—'}` }))}
          selected={assigned}
          onToggle={toggleAgent}
          empty="no agents — create one first"
        />
      </Field>

      <Field label="LEADER" hint="must be a member">
        <Select value={leader} onChange={(e) => setLeader(e.target.value)} disabled={assignedAgents.length === 0}>
          <option value="">— none —</option>
          {assignedAgents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </Select>
      </Field>
    </PageForm>
  );
}
