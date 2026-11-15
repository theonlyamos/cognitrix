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
import { tr, dateLocale } from '@/lib/i18n';

interface AgentLite {
  id: string;
  name: string;
}

function size(bytes: number): string {
  if (bytes >= 1024 ** 3) return tr('ui.sandboxSnapshotsCard.valueGb', { value: (bytes / 1024 ** 3).toFixed(1) });
  if (bytes >= 1024 ** 2) return tr('ui.sandboxSnapshotsCard.roundMb', { round: Math.round(bytes / 1024 ** 2) });
  return tr('ui.sandboxSnapshotsCard.maxKb', { max: Math.max(1, Math.round(bytes / 1024)) });
}

function when(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString(dateLocale(), { dateStyle: 'medium', timeStyle: 'short' });
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
      setError(errorMessage(err, tr('ui.sandboxSnapshotsCard.couldnTLoadTheSnapshots')));
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
      setError(errorMessage(err, tr('ui.sandboxSnapshotsCard.thatDidnTWork')));
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
    }, tr('ui.sandboxSnapshotsCard.snapshotSaved'));
  };

  const copy = (e: React.FormEvent) => {
    e.preventDefault();
    if (!copyFrom || !copyTo || copyFrom === copyTo) {
      setError(tr('ui.sandboxSnapshotsCard.pickTwoDifferentAgents'));
      return;
    }
    if (confirm !== 'copy') {
      setConfirm('copy');
      return;
    }
    const from = agents.find((a) => a.id === copyFrom)?.name ?? 'the agent';
    const to = agents.find((a) => a.id === copyTo)?.name ?? 'the other agent';
    void run('copy', () => copyAgentSetup(desktopName(copyFrom), desktopName(copyTo)), tr('ui.sandboxSnapshotsCard.toNowHasACopy', { to, from }));
  };

  return (
    <div className="space-y-3">
      <div>
        <p className="text-[13.5px] font-medium">{tr('ui.sandboxSnapshotsCard.snapshots')}</p>
        <p className="text-[12.5px] text-fg-dim">
          {tr('ui.sandboxSnapshotsCard.saveTheComputerOnceIt')}
        </p>
      </div>

      <form onSubmit={save} className="flex flex-wrap items-center gap-2">
        <select aria-label={tr('ui.sandboxSnapshotsCard.whatToSave')} value={what} onChange={(e) => setWhat(e.target.value)} className={selectClass}>
          <option value="">{tr('ui.sandboxSnapshotsCard.wholeComputer')}</option>
          {agents.map((a) => <option key={a.id} value={a.id}>{tr('ui.sandboxSnapshotsCard.nameSSetup', { name: a.name })}</option>)}
        </select>
        <Input aria-label={tr('ui.sandboxSnapshotsCard.snapshotName')} value={name} onChange={(e) => setName(e.target.value)} maxLength={80}
          placeholder={tr('ui.sandboxSnapshotsCard.nameOptional')} className="h-9 min-w-[10rem] flex-1 text-[13px] md:h-8" />
        <button type="submit" disabled={busy !== null || (snapshots?.length ?? 0) >= max} className={primaryButtonClass}>
          {busy === 'save' ? tr('ui.sandboxSnapshotsCard.saving') : tr('ui.sandboxSnapshotsCard.saveSnapshot')}
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
                    {s.kind === 'agent' ? tr('ui.sandboxSnapshotsCard.agentlabelSSetup', { agentLabel: agentLabel(s.agent) }) : tr('ui.sandboxSnapshotsCard.wholeComputer')} · {size(s.size)} · {when(s.created_at)}
                  </p>
                </div>
                {confirm === `restore:${s.id}` ? (
                  <>
                    {s.kind === 'agent' && (
                      <select aria-label={tr('ui.sandboxSnapshotsCard.giveThisSetupTo')} value={target} className={selectClass}
                        onChange={(e) => setRestoreTo({ ...restoreTo, [s.id]: e.target.value })}>
                        {s.agent && !agentNames[s.agent] && <option value={s.agent}>{s.agent}</option>}
                        {agents.map((a) => <option key={a.id} value={desktopName(a.id)}>{a.name}</option>)}
                      </select>
                    )}
                    <span className="text-[12px] text-fg-dim">
                      {s.kind === 'agent' ? tr('ui.sandboxSnapshotsCard.replacesThatAgentSSetup') : tr('ui.sandboxSnapshotsCard.replacesEverythingOnTheComputer')}
                    </span>
                    <button type="button" disabled={busy !== null} className={primaryButtonClass}
                      onClick={() => void run(`restore:${s.id}`, () => restoreSnapshot(s.id, s.kind === 'agent' ? target : null),
                        s.kind === 'agent' ? tr('ui.sandboxSnapshotsCard.agentlabelNowHasThisSetup', { agentLabel: agentLabel(target) }) : tr('ui.sandboxSnapshotsCard.theComputerIsBackTo'))}>
                      {restoring ? tr('ui.sandboxSnapshotsCard.restoring') : tr('ui.sandboxSnapshotsCard.restore')}
                    </button>
                    <button type="button" className={quietButtonClass} onClick={() => setConfirm(null)}>{tr('ui.sandboxSnapshotsCard.cancel')}</button>
                  </>
                ) : confirm === `delete:${s.id}` ? (
                  <>
                    <span className="text-[12px] text-fg-dim">{tr('ui.sandboxSnapshotsCard.deleteThisSnapshot')}</span>
                    <button type="button" disabled={busy !== null} className={buttonClass + ' text-danger-ink'}
                      onClick={() => void run(`delete:${s.id}`, () => deleteSnapshot(s.id), tr('ui.sandboxSnapshotsCard.snapshotDeleted'))}>
                      {tr('ui.sandboxSnapshotsCard.delete')}
                    </button>
                    <button type="button" className={quietButtonClass} onClick={() => setConfirm(null)}>{tr('ui.sandboxSnapshotsCard.cancel')}</button>
                  </>
                ) : (
                  <>
                    <button type="button" disabled={busy !== null} className={buttonClass} onClick={() => setConfirm(`restore:${s.id}`)}>
                      {s.kind === 'agent' ? tr('ui.sandboxSnapshotsCard.giveToAnAgent') : tr('ui.sandboxSnapshotsCard.restore')}
                    </button>
                    <button type="button" disabled={busy !== null} className={quietButtonClass} onClick={() => setConfirm(`delete:${s.id}`)}>
                      {tr('ui.sandboxSnapshotsCard.delete')}
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
          <span className="text-[12.5px] text-fg-dim">{tr('ui.sandboxSnapshotsCard.copy')}</span>
          <select aria-label={tr('ui.sandboxSnapshotsCard.copyFrom')} value={copyFrom} onChange={(e) => { setCopyFrom(e.target.value); setConfirm(null); }} className={selectClass}>
            <option value="">{tr('ui.sandboxSnapshotsCard.anAgent')}</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <span className="text-[12.5px] text-fg-dim">{tr('ui.sandboxSnapshotsCard.sSetupTo')}</span>
          <select aria-label={tr('ui.sandboxSnapshotsCard.copyTo')} value={copyTo} onChange={(e) => { setCopyTo(e.target.value); setConfirm(null); }} className={selectClass}>
            <option value="">{tr('ui.sandboxSnapshotsCard.anotherAgent')}</option>
            {agents.filter((a) => a.id !== copyFrom).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <button type="submit" disabled={busy !== null || !copyFrom || !copyTo} className={buttonClass}>
            {busy === 'copy' ? tr('ui.sandboxSnapshotsCard.copying') : confirm === 'copy' ? tr('ui.sandboxSnapshotsCard.replaceItsSetup') : tr('ui.sandboxSnapshotsCard.copy')}
          </button>
          {confirm === 'copy' && (
            <button type="button" className={quietButtonClass} onClick={() => setConfirm(null)}>{tr('ui.sandboxSnapshotsCard.cancel')}</button>
          )}
        </form>
      )}

      {notice && <p className="text-[12.5px] text-fg-dim">{notice}</p>}
      {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
    </div>
  );
}
