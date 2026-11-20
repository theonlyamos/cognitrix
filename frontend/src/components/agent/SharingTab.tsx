import { useEffect, useMemo, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import type { TabProps } from '@/lib/agentDraft';
import { Card, buttonClass, inputClass, primaryButtonClass } from '@/components/agent/ui';
import { cn } from '@/lib/utils';
import PublicLinkCard from '@/components/agent/PublicLinkCard';
import { tr } from '@/lib/i18n';

type Role = 'use' | 'edit';
interface Person { id: string; name: string; email: string }
interface Sharing {
  role: 'owner' | Role;
  owner: Person | null;
  can_manage: boolean;
  everyone?: Role | null;
  people?: (Person & { role: Role })[];
  directory?: Person[];
}

const ROLE_LABEL: Record<Role, string> = {
  get use() { return tr('ui.agent.sharingTab.canChat'); },
  get edit() { return tr('ui.agent.sharingTab.canEdit'); },
};

function RoleSelect({ value, onChange, label, allowNone }: {
  value: Role | '';
  onChange: (v: Role | '') => void;
  label: string;
  allowNone?: boolean;
}) {
  return (
    <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value as Role | '')}
      className={cn(inputClass, 'w-[150px] flex-none')}>
      {allowNone && <option value="">{tr('ui.agent.sharingTab.notShared')}</option>}
      <option value="use">{ROLE_LABEL.use}</option>
      <option value="edit">{ROLE_LABEL.edit}</option>
    </select>
  );
}

/** Agent page › Sharing: who else on this server can use the agent. Owner only. */
export default function SharingTab({ agent }: TabProps) {
  const [data, setData] = useState<Sharing | null>(null);
  const [everyone, setEveryone] = useState<Role | ''>('');
  const [people, setPeople] = useState<(Person & { role: Role })[]>([]);
  const [pick, setPick] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState('');

  const apply = (next: Sharing) => {
    setData(next);
    setEveryone(next.everyone || '');
    setPeople(next.people || []);
  };

  useEffect(() => {
    api.get<Sharing>(`/agents/${agent.id}/sharing`).then((r) => apply(r.data))
      .catch((e) => setError(errorMessage(e, tr('ui.agent.sharingTab.couldNotLoadSharing'))));
  }, [agent.id]);

  const candidates = useMemo(
    () => (data?.directory || []).filter((p) => !people.some((x) => x.id === p.id)),
    [data, people],
  );
  const dirty = useMemo(() => {
    if (!data) return false;
    const before = JSON.stringify([(data.everyone || ''), (data.people || []).map((p) => [p.id, p.role]).sort()]);
    const after = JSON.stringify([everyone, people.map((p) => [p.id, p.role]).sort()]);
    return before !== after;
  }, [data, everyone, people]);

  if (!data) {
    return error
      ? <p role="alert" className="text-[13px] text-danger-ink">{error}</p>
      : <p className="text-[13px] text-fg-dim">{tr('ui.agent.sharingTab.loading')}</p>;
  }
  if (!data.can_manage) {
    return (
      <Card title={tr('ui.agent.sharingTab.sharing')}>
        <p className="text-[13.5px] leading-relaxed text-fg-dim">
          {data.owner ? tr('ui.agent.sharingTab.nameOwnsThisAgentAnd', { name: data.owner.name }) : tr('ui.agent.sharingTab.thisAgentBelongsToEveryone')}
        </p>
      </Card>
    );
  }

  const add = () => {
    const person = candidates.find((p) => p.id === pick);
    if (!person) return;
    setPeople((cur) => [...cur, { ...person, role: 'use' }]);
    setPick('');
    setNotice('');
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.put<Sharing>(`/agents/${agent.id}/sharing`, {
        people: people.map((p) => ({ user_id: p.id, role: p.role })),
        everyone: everyone || null,
      });
      apply(res.data);
      setNotice(tr('ui.agent.sharingTab.sharingSaved'));
    } catch (e) {
      setError(errorMessage(e, tr('ui.agent.sharingTab.couldNotSaveSharing')));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <Card title={tr('ui.agent.sharingTab.shareThisAgent')} description={tr('ui.agent.sharingTab.letOtherPeopleOnThis')}>
        <p className="text-[13.5px] leading-relaxed text-fg-dim">
          <strong className="font-medium text-fg">{tr('ui.agent.sharingTab.canChat')}</strong>{' '}{tr('ui.agent.sharingTab.letsSomeoneSeeTheAgent')}{' '}
          <strong className="font-medium text-fg">{tr('ui.agent.sharingTab.canEdit')}</strong>{' '}{tr('ui.agent.sharingTab.alsoLetsThemChangeIts')}
        </p>
        <p className="mt-3 rounded-md border border-warn-ink/50 px-3 py-2.5 text-[13px] leading-relaxed">
          {tr('ui.agent.sharingTab.peopleYouShareWithUse')}
        </p>

        <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-line pt-4">
          <div className="min-w-[200px] flex-1">
            <div className="text-[14.5px] font-medium">{tr('ui.agent.sharingTab.everyoneOnThisServer')}</div>
            <div className="mt-0.5 text-[13px] text-fg-dim">{tr('ui.agent.sharingTab.includesPeopleWhoJoinLater')}</div>
          </div>
          <RoleSelect label={tr('ui.agent.sharingTab.everyoneOnThisServer')} value={everyone} allowNone onChange={(v) => { setEveryone(v); setNotice(''); }} />
        </div>

        <ul className="mt-2 divide-y divide-line border-t border-line">
          {people.map((p) => (
            <li key={p.id} className="flex flex-wrap items-center gap-3 py-3">
              <div className="min-w-[200px] flex-1">
                <div className="text-[14.5px] font-medium">{p.name}</div>
                {p.email && p.email !== p.name && <div className="mt-0.5 font-mono text-[12px] text-fg-dim">{p.email}</div>}
              </div>
              <RoleSelect label={tr('ui.agent.sharingTab.roleForName', { name: p.name })} value={p.role}
                onChange={(v) => { if (v) setPeople((cur) => cur.map((x) => (x.id === p.id ? { ...x, role: v } : x))); setNotice(''); }} />
              <button type="button" className={buttonClass} aria-label={tr('ui.agent.sharingTab.stopSharingWithName', { name: p.name })}
                onClick={() => { setPeople((cur) => cur.filter((x) => x.id !== p.id)); setNotice(''); }}>{tr('ui.agent.sharingTab.remove')}</button>
            </li>
          ))}
        </ul>

        {(data.directory || []).length === 0 ? (
          <p className="mt-3 text-[13px] text-fg-dim">{tr('ui.agent.sharingTab.nobodyElseHasAnAccount')}</p>
        ) : (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <select aria-label={tr('ui.agent.sharingTab.personToShareWith')} value={pick} onChange={(e) => setPick(e.target.value)}
              className={cn(inputClass, 'min-w-[220px] flex-1')} disabled={candidates.length === 0}>
              <option value="">{candidates.length ? tr('ui.agent.sharingTab.pickAPerson') : tr('ui.agent.sharingTab.everyoneIsAlreadyListed')}</option>
              {candidates.map((p) => <option key={p.id} value={p.id}>{p.name}{p.email && p.email !== p.name ? ` (${p.email})` : ''}</option>)}
            </select>
            <button type="button" className={buttonClass} disabled={!pick} onClick={add}>{tr('ui.agent.sharingTab.add')}</button>
          </div>
        )}

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button type="button" className={primaryButtonClass} disabled={busy || !dirty} onClick={() => void save()}>
            {busy ? tr('ui.agent.sharingTab.saving') : tr('ui.agent.sharingTab.saveSharing')}
          </button>
          {error && <p role="alert" className="text-[13px] text-danger-ink">{error}</p>}
          {notice && !dirty && <p role="status" className="text-[13px] text-fg-dim">{notice}</p>}
        </div>
      </Card>

      <PublicLinkCard agentId={agent.id} />
    </div>
  );
}
