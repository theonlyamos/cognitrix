import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { Input } from '@/lib/components/ui/input';
import {
  copyAgentSetup,
  createSnapshot,
  deleteSnapshot,
  desktopName,
  listSnapshots,
  restoreSnapshot,
  type SandboxSnapshot,
} from '@/lib/sandbox';
import { buttonClass, primaryButtonClass, quietButtonClass } from '@/components/settings/ui';

interface AgentLite {
  id: string;
  name: string;
}

function size(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function when(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

const selectClass = 'h-9 rounded-lg border border-line bg-bg px-2 text-[13px] md:h-8';

/**
 * Settings › Computer: save the agents' computer (or one agent's setup on it) and put it back,
 * or copy one agent's setup to another agent.
 */
export function SandboxSnapshotsCard() {
  const [snapshots, setSnapshots] = useState<SandboxSnapshot[] | null>(null);
  const [max, setMax] = useState(20);
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [name, setName] = useState('');
  const [what, setWhat] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [restoreTo, setRestoreTo] = useState<Record<string, string>>({});
  const [copyFrom, setCopyFrom] = useState('');
  const [copyTo, setCopyTo] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const load = useCallback(async () => {
    try {
      const data = await listSnapshots();
      setSnapshots(data.snapshots);
      setMax(data.max);
    } catch (err) {
      setError(errorMessage(err, "Couldn't load the snapshots."));
    }
  }, []);
  useEffect(() => {
    void load();
    api.get<AgentLite[]>('/agents').then((r) => setAgents((r.data ?? []).map((a) => ({ id: a.id, name: a.name })))).catch(() => {});
  }, [load]);

  const agentNames = useMemo(() => {
    const out: Record<string, string> = {};
    for (const a of agents) out[desktopName(a.id)] = a.name;
    return out;
  }, [agents]);
  const agentLabel = (desk: string | null) => (desk ? agentNames[desk] ?? desk : '');

  async function run(key: string, work: () => Promise<void>, done: string) {
    setBusy(key);
    setError('');
    setNotice('');
    try {
      await work();
      setNotice(done);
      await load();
    } catch (err) {
      setError(errorMessage(err, 'That didn’t work.'));
    } finally {
      setBusy(null);
      setConfirm(null);
    }
  }

  const save = (e: React.FormEvent) => {
    e.preventDefault();
    const agent = what ? desktopName(what) : null;
    const fallback = agent ? `${agentLabel(agent)} setup` : 'Whole computer';
    void run('save', async () => {
      await createSnapshot(name.trim() || fallback, agent);
      setName('');
    }, 'Snapshot saved.');
  };

  const copy = (e: React.FormEvent) => {
    e.preventDefault();
    if (!copyFrom || !copyTo || copyFrom === copyTo) {
      setError('Pick two different agents.');
      return;
    }
    if (confirm !== 'copy') {
      setConfirm('copy');
      return;
    }
    const from = agents.find((a) => a.id === copyFrom)?.name ?? 'the agent';
    const to = agents.find((a) => a.id === copyTo)?.name ?? 'the other agent';
    void run('copy', () => copyAgentSetup(desktopName(copyFrom), desktopName(copyTo)), `${to} now has a copy of ${from}’s setup.`);
  };

  return (
    <div className="space-y-3">
      <div>
        <p className="text-[13.5px] font-medium">Snapshots</p>
        <p className="text-[12.5px] text-fg-dim">
          Save the computer once it&rsquo;s set up, with installed tools, files and browser sign-ins, and put it back later. An agent&rsquo;s
          setup (its folder, browser and projects) can also be saved on its own and given to any agent.
        </p>
      </div>

      <form onSubmit={save} className="flex flex-wrap items-center gap-2">
        <select aria-label="What to save" value={what} onChange={(e) => setWhat(e.target.value)} className={selectClass}>
          <option value="">Whole computer</option>
          {agents.map((a) => <option key={a.id} value={a.id}>{a.name}&rsquo;s setup</option>)}
        </select>
        <Input aria-label="Snapshot name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80}
          placeholder="Name (optional)" className="h-9 min-w-[10rem] flex-1 text-[13px] md:h-8" />
        <button type="submit" disabled={busy !== null || (snapshots?.length ?? 0) >= max} className={primaryButtonClass}>
          {busy === 'save' ? 'Saving…' : 'Save snapshot'}
        </button>
      </form>

      {snapshots && snapshots.length > 0 && (
        <ul className="divide-y divide-line rounded-[10px] border border-line bg-bg">
          {snapshots.map((s) => {
            const restoring = busy === `restore:${s.id}`;
            const target = restoreTo[s.id] ?? s.agent ?? '';
            return (
              <li key={s.id} className="flex flex-wrap items-center gap-2 px-3 py-2">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13px] font-medium">{s.name}</p>
                  <p className="text-[11.5px] text-fg-dim">
                    {s.kind === 'agent' ? `${agentLabel(s.agent)}’s setup` : 'Whole computer'} · {size(s.size)} · {when(s.created_at)}
                  </p>
                </div>
                {confirm === `restore:${s.id}` ? (
                  <>
                    {s.kind === 'agent' && (
                      <select aria-label="Give this setup to" value={target} className={selectClass}
                        onChange={(e) => setRestoreTo({ ...restoreTo, [s.id]: e.target.value })}>
                        {s.agent && !agentNames[s.agent] && <option value={s.agent}>{s.agent}</option>}
                        {agents.map((a) => <option key={a.id} value={desktopName(a.id)}>{a.name}</option>)}
                      </select>
                    )}
                    <span className="text-[12px] text-fg-dim">
                      {s.kind === 'agent' ? 'Replaces that agent’s setup.' : 'Replaces everything on the computer.'}
                    </span>
                    <button type="button" disabled={busy !== null} className={primaryButtonClass}
                      onClick={() => void run(`restore:${s.id}`, () => restoreSnapshot(s.id, s.kind === 'agent' ? target : null),
                        s.kind === 'agent' ? `${agentLabel(target)} now has this setup.` : 'The computer is back to this snapshot.')}>
                      {restoring ? 'Restoring…' : 'Restore'}
                    </button>
                    <button type="button" className={quietButtonClass} onClick={() => setConfirm(null)}>Cancel</button>
                  </>
                ) : confirm === `delete:${s.id}` ? (
                  <>
                    <span className="text-[12px] text-fg-dim">Delete this snapshot?</span>
                    <button type="button" disabled={busy !== null} className={buttonClass + ' text-danger-ink'}
                      onClick={() => void run(`delete:${s.id}`, () => deleteSnapshot(s.id), 'Snapshot deleted.')}>
                      Delete
                    </button>
                    <button type="button" className={quietButtonClass} onClick={() => setConfirm(null)}>Cancel</button>
                  </>
                ) : (
                  <>
                    <button type="button" disabled={busy !== null} className={buttonClass} onClick={() => setConfirm(`restore:${s.id}`)}>
                      {s.kind === 'agent' ? 'Give to an agent' : 'Restore'}
                    </button>
                    <button type="button" disabled={busy !== null} className={quietButtonClass} onClick={() => setConfirm(`delete:${s.id}`)}>
                      Delete
                    </button>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {agents.length > 1 && (
        <form onSubmit={copy} className="flex flex-wrap items-center gap-2">
          <span className="text-[12.5px] text-fg-dim">Copy</span>
          <select aria-label="Copy from" value={copyFrom} onChange={(e) => { setCopyFrom(e.target.value); setConfirm(null); }} className={selectClass}>
            <option value="">an agent</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <span className="text-[12.5px] text-fg-dim">&rsquo;s setup to</span>
          <select aria-label="Copy to" value={copyTo} onChange={(e) => { setCopyTo(e.target.value); setConfirm(null); }} className={selectClass}>
            <option value="">another agent</option>
            {agents.filter((a) => a.id !== copyFrom).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <button type="submit" disabled={busy !== null || !copyFrom || !copyTo} className={buttonClass}>
            {busy === 'copy' ? 'Copying…' : confirm === 'copy' ? 'Replace its setup' : 'Copy'}
          </button>
          {confirm === 'copy' && (
            <button type="button" className={quietButtonClass} onClick={() => setConfirm(null)}>Cancel</button>
          )}
        </form>
      )}

      {notice && <p className="text-[12.5px] text-fg-dim">{notice}</p>}
      {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
    </div>
  );
}
