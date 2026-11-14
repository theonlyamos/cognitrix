import { useEffect, useRef, useState } from 'react';
import { Check, Globe, Link as LinkIcon, Lock, Users, X } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { appLink, type AppDetail } from '@/lib/hostedApps';
import { cn } from '@/lib/utils';

type Role = 'view' | 'edit';
const EVERYONE = '*';

const selectClass = 'h-9 rounded-md border border-line bg-bg px-2 text-[12.5px] text-fg md:h-8';
const buttonClass = 'inline-flex h-9 items-center gap-1.5 rounded-md border border-line px-3 text-[12.5px] hover:bg-panel-2 disabled:opacity-60 md:h-8';

function Option({ on, onPick, icon, title, detail, disabled }: {
  on: boolean; onPick: () => void; icon: JSX.Element; title: string; detail: string; disabled?: boolean;
}) {
  return (
    <button type="button" role="radio" aria-checked={on} disabled={disabled} onClick={onPick}
      className={cn('flex w-full items-start gap-3 rounded-[10px] border px-3 py-2.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50',
        on ? 'border-accent-ink/50 bg-accent/10' : 'border-line hover:bg-panel-2')}>
      <span className={cn('mt-0.5 grid h-4 w-4 flex-none place-items-center rounded-full border', on ? 'border-accent-ink bg-accent-ink' : 'border-fg-dim')}>
        {on && <span className="h-1.5 w-1.5 rounded-full bg-panel" />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5 text-[13.5px] font-medium">{icon}{title}</span>
        <span className="mt-0.5 block text-[12.5px] text-fg-dim">{detail}</span>
      </span>
    </button>
  );
}

/** Who can open an app: people you add, or everyone on this server. Never public. */
export function AppShareDialog({ app, onClose, onChanged }: { app: AppDetail; onClose: () => void; onChanged: (d: AppDetail) => void }) {
  const [pick, setPick] = useState('');
  const [pickRole, setPickRole] = useState<Role>('view');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDivElement>(null);

  useEffect(() => {
    dialog.current?.querySelector<HTMLElement>('select, button')?.focus();
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const directory = app.people || [];
  const nameOf = (id: string) => directory.find((p) => p.id === id)?.name || 'Someone';
  const people = Object.entries(app.shared_with || {}).filter(([id]) => id !== EVERYONE) as [string, Role][];
  const everyone = app.shared_with?.[EVERYONE] || '';
  const candidates = directory.filter((p) => !(p.id in (app.shared_with || {})));
  const link = appLink(app);

  const save = async (body: { people?: Record<string, Role>; everyone?: string }) => {
    setBusy(true);
    setError('');
    try {
      const { data } = await api.put<AppDetail>(`/hosted-apps/${app.id}/sharing`, body);
      onChanged(data);
    } catch (e) {
      setError(errorMessage(e, 'Could not change sharing.'));
    } finally {
      setBusy(false);
    }
  };
  const current = () => Object.fromEntries(people) as Record<string, Role>;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      setNotice('Link copied');
    } catch {
      setNotice(link);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/45 md:items-center" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={dialog} role="dialog" aria-modal="true" aria-label={`Share ${app.title}`}
        className="flex max-h-[92dvh] w-full flex-col overflow-hidden rounded-t-2xl border border-line bg-panel shadow-2xl md:w-[520px] md:rounded-2xl">
        <header className="flex flex-none items-center gap-2 border-b border-line px-4 py-3">
          <h2 className="min-w-0 flex-1 truncate text-[15px] font-semibold">Share “{app.title}”</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="grid h-9 w-9 place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg"><X size={16} /></button>
        </header>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
          <div>
            <div className="flex gap-2">
              <select aria-label="Person to add" value={pick} onChange={(e) => setPick(e.target.value)} className={cn(selectClass, 'min-w-0 flex-1')}>
                <option value="">{candidates.length ? 'Add someone from this server…' : 'Nobody else to add'}</option>
                {candidates.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              <select aria-label="Their role" value={pickRole} onChange={(e) => setPickRole(e.target.value as Role)} className={selectClass}>
                <option value="view">Can use</option>
                <option value="edit">Can edit</option>
              </select>
              <button type="button" className={buttonClass} disabled={!pick || busy}
                onClick={() => { void save({ people: { ...current(), [pick]: pickRole } }); setPick(''); }}>Add</button>
            </div>
            <ul className="mt-3 space-y-2.5">
              <li className="flex items-center gap-2.5">
                <span className="grid h-7 w-7 flex-none place-items-center rounded-full border border-line bg-panel-2 font-mono text-[11px]">{app.owner.name.slice(0, 1).toUpperCase()}</span>
                <span className="min-w-0 flex-1 truncate text-[13.5px]">{app.owner.name} <span className="text-fg-dim">(you)</span></span>
                <span className="font-mono text-[11px] text-fg-dim">owner</span>
              </li>
              {people.map(([id, role]) => (
                <li key={id} className="flex items-center gap-2.5">
                  <span className="grid h-7 w-7 flex-none place-items-center rounded-full border border-line bg-panel-2 font-mono text-[11px]">{nameOf(id).slice(0, 1).toUpperCase()}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13.5px]">{nameOf(id)}</span>
                    <span className="block font-mono text-[11px] text-fg-dim">{role === 'edit' ? 'can use it, see versions and logs, and redeploy' : 'can open and use it'}</span>
                  </span>
                  <select aria-label={`Role for ${nameOf(id)}`} value={role} className={selectClass} disabled={busy}
                    onChange={(e) => {
                      const value = e.target.value;
                      const next = current();
                      if (value === 'remove') delete next[id]; else next[id] = value as Role;
                      void save({ people: next });
                    }}>
                    <option value="view">Can use</option>
                    <option value="edit">Can edit</option>
                    <option value="remove">Remove</option>
                  </select>
                </li>
              ))}
            </ul>
          </div>

          <div className="space-y-2" role="radiogroup" aria-label="General access">
            <h3 className="font-mono text-[11px] uppercase tracking-wider text-fg-dim">General access</h3>
            <Option on={!everyone} disabled={busy} onPick={() => void save({ everyone: '' })}
              icon={<Lock size={14} aria-hidden />} title="Only people added" detail="You and the people listed above." />
            <Option on={!!everyone} disabled={busy} onPick={() => void save({ everyone: 'view' })}
              icon={<Users size={14} aria-hidden />} title="Everyone on this server" detail="Anyone who signs in to this Cognitrix can open and use it." />
            <Option on={false} disabled onPick={() => undefined}
              icon={<Globe size={14} aria-hidden />} title="Anyone with the link"
              detail="Not available for apps: they run code with your keys, so people always sign in first." />
          </div>

          {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
        </div>
        <footer className="flex flex-none items-center gap-2 border-t border-line px-4 py-3">
          <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-fg-dim" title={link}>{notice || link}</span>
          <button type="button" className={buttonClass} onClick={() => void copy()}>
            {notice === 'Link copied' ? <Check size={14} aria-hidden /> : <LinkIcon size={14} aria-hidden />}Copy link
          </button>
          <button type="button" onClick={onClose} className="inline-flex h-9 items-center rounded-md bg-fg px-3 text-[12.5px] font-medium text-bg md:h-8">Done</button>
        </footer>
      </div>
    </div>
  );
}
