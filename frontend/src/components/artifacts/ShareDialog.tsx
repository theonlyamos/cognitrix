import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, Globe, Link as LinkIcon, Lock, Users, X } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { pageLink, publicLink, type PageDetail, type Sharing } from '@/lib/artifactPages';
import { cn } from '@/lib/utils';

type Role = 'view' | 'edit';
type General = 'people' | 'workspace' | 'public';
interface Channel { id: string; kind: string; name: string }

const selectClass = 'h-9 rounded-md border border-line bg-bg px-2 text-[12.5px] text-fg md:h-8';
const buttonClass = 'inline-flex h-9 items-center gap-1.5 rounded-md border border-line px-3 text-[12.5px] hover:bg-panel-2 disabled:opacity-60 md:h-8';

function Option({ on, onPick, icon, title, detail, disabled }: {
  on: boolean; onPick: () => void; icon: JSX.Element; title: string; detail: string; disabled?: boolean;
}) {
  return (
    <button type="button" role="radio" aria-checked={on} disabled={disabled} onClick={onPick}
      className={cn('flex w-full items-start gap-3 rounded-[10px] border px-3 py-2.5 text-left transition-colors disabled:opacity-50',
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

/** Who can open an artifact: people, everyone on the server, or anyone with a link. */
export function ShareDialog({ page, onClose, onChanged }: { page: PageDetail; onClose: () => void; onChanged: () => void }) {
  const [data, setData] = useState<Sharing | null>(null);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [pick, setPick] = useState('');
  const [pickRole, setPickRole] = useState<Role>('view');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.get<Sharing>(`/artifact-pages/${page.id}/sharing`).then((r) => setData(r.data))
      .catch((e) => setError(errorMessage(e, 'Could not load sharing.')));
    api.get<{ channels: Channel[] }>(`/artifact-pages/${page.id}/channels`).then((r) => setChannels(r.data.channels))
      .catch(() => setChannels([]));
  }, [page.id]);

  useEffect(() => {
    dialog.current?.querySelector<HTMLElement>('select, button')?.focus();
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const save = async (body: Record<string, unknown>) => {
    setBusy(true);
    setError('');
    try {
      const { data: next } = await api.put<Sharing>(`/artifact-pages/${page.id}/sharing`, body);
      setData(next);
      onChanged();
    } catch (e) {
      setError(errorMessage(e, 'Could not change sharing.'));
    } finally {
      setBusy(false);
    }
  };

  const candidates = useMemo(
    () => (data?.directory || []).filter((p) => !data?.people.some((x) => x.id === p.id)),
    [data],
  );
  const general: General = data?.public ? 'public' : data?.everyone ? 'workspace' : 'people';
  const link = data?.public && data.public_url ? publicLink(data.public_url) : pageLink(page.id);

  const setPeople = (people: { user_id: string; role: Role }[]) => save({ people });
  const current = () => (data?.people || []).map((p) => ({ user_id: p.id, role: p.role }));

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      setNotice('Link copied');
    } catch {
      setNotice(link);
    }
  };

  const send = async (channel: Channel) => {
    setError('');
    try {
      await api.post(`/artifact-pages/${page.id}/send`, { channel_id: channel.id, url: link });
      setNotice(`Sent to ${channel.name}`);
    } catch (e) {
      setError(errorMessage(e, 'Could not send the link.'));
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/45 md:items-center" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={dialog} role="dialog" aria-modal="true" aria-label={`Share ${page.title}`}
        className="flex max-h-[92dvh] w-full flex-col overflow-hidden rounded-t-2xl border border-line bg-panel shadow-2xl md:w-[520px] md:rounded-2xl">
        <header className="flex flex-none items-center gap-2 border-b border-line px-4 py-3">
          <h2 className="min-w-0 flex-1 truncate text-[15px] font-semibold">Share “{page.title}”</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="grid h-9 w-9 place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg"><X size={16} /></button>
        </header>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
          {!data && !error && <p className="text-[13px] text-fg-dim">Loading…</p>}
          {data && (
            <>
              <div>
                <div className="flex gap-2">
                  <select aria-label="Person to add" value={pick} onChange={(e) => setPick(e.target.value)} className={cn(selectClass, 'min-w-0 flex-1')}>
                    <option value="">{candidates.length ? 'Add someone from this server…' : 'Nobody else to add'}</option>
                    {candidates.map((p) => <option key={p.id} value={p.id}>{p.name}{p.email && p.email !== p.name ? ` (${p.email})` : ''}</option>)}
                  </select>
                  <select aria-label="Their role" value={pickRole} onChange={(e) => setPickRole(e.target.value as Role)} className={selectClass}>
                    <option value="view">Viewer</option>
                    <option value="edit">Editor</option>
                  </select>
                  <button type="button" className={buttonClass} disabled={!pick || busy}
                    onClick={() => { void setPeople([...current(), { user_id: pick, role: pickRole }]); setPick(''); }}>Add</button>
                </div>
                <ul className="mt-3 space-y-2.5">
                  <li className="flex items-center gap-2.5">
                    <span className="grid h-7 w-7 flex-none place-items-center rounded-full border border-line bg-panel-2 font-mono text-[11px]">{page.owner.name.slice(0, 1).toUpperCase()}</span>
                    <span className="min-w-0 flex-1 truncate text-[13.5px]">{page.owner.name} <span className="text-fg-dim">(you)</span></span>
                    <span className="font-mono text-[11px] text-fg-dim">owner</span>
                  </li>
                  {data.people.map((p) => (
                    <li key={p.id} className="flex items-center gap-2.5">
                      <span className="grid h-7 w-7 flex-none place-items-center rounded-full border border-line bg-panel-2 font-mono text-[11px]">{p.name.slice(0, 1).toUpperCase()}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13.5px]">{p.name}</span>
                        <span className="block font-mono text-[11px] text-fg-dim">{p.role === 'edit' ? 'can comment, send comments to the agent and update it' : 'can open and comment'}</span>
                      </span>
                      <select aria-label={`Role for ${p.name}`} value={p.role} className={selectClass} disabled={busy}
                        onChange={(e) => {
                          const value = e.target.value;
                          void setPeople(value === 'remove'
                            ? current().filter((x) => x.user_id !== p.id)
                            : current().map((x) => (x.user_id === p.id ? { ...x, role: value as Role } : x)));
                        }}>
                        <option value="view">Viewer</option>
                        <option value="edit">Editor</option>
                        <option value="remove">Remove</option>
                      </select>
                    </li>
                  ))}
                </ul>
              </div>

              <div role="radiogroup" aria-label="General access" className="space-y-2">
                <div className="font-mono text-[10.5px] uppercase tracking-[0.12em] text-fg-dim">General access</div>
                <Option on={general === 'people'} disabled={busy} icon={<Lock size={13} />} title="Only people added"
                  detail="Nobody else can open the link." onPick={() => void save({ everyone: '', public: false })} />
                <Option on={general === 'workspace'} disabled={busy} icon={<Users size={13} />} title="Everyone on this server"
                  detail="Anyone signed in to this Cognitrix can open it and comment." onPick={() => void save({ everyone: data.everyone || 'view', public: false })} />
                <Option on={general === 'public'} disabled={busy || !data.public_links} icon={<Globe size={13} />} title="Anyone with the link"
                  detail={data.public_links ? 'No sign-in needed. They can only look: no comments, and the page gets no access to anything.' : 'Public links are turned off on this server.'}
                  onPick={() => void save({ public: true })} />
              </div>

              <label className="flex items-center gap-3">
                <span className="min-w-0 flex-1">
                  <span className="block text-[13.5px] font-medium">Always show the latest version</span>
                  <span className="block text-[12.5px] text-fg-dim">
                    {data.share_version ? `Off: viewers stay on version ${data.share_version} until you change this.` : 'Viewers get each new version as the agent publishes it.'}
                  </span>
                </span>
                <input type="checkbox" className="h-4 w-4 accent-accent-ink" checked={!data.share_version} disabled={busy}
                  onChange={(e) => void save({ share_version: e.target.checked ? 0 : data.version })} />
              </label>
            </>
          )}
          {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
        </div>
        <footer className="flex flex-none flex-wrap items-center gap-2 border-t border-line px-4 py-3">
          {channels.map((channel) => (
            <button key={channel.id} type="button" className={buttonClass} onClick={() => void send(channel)}>Send to {channel.name}</button>
          ))}
          <span aria-live="polite" className="min-w-0 flex-1 truncate font-mono text-[11px] text-fg-dim">
            {notice && <span className="inline-flex items-center gap-1"><Check size={12} />{notice}</span>}
          </span>
          <button type="button" onClick={() => void copy()}
            className="inline-flex h-9 items-center gap-1.5 rounded-md bg-accent px-3.5 text-[13px] font-semibold text-accent-foreground md:h-8">
            <LinkIcon size={14} /> Copy {data?.public ? 'public ' : ''}link
          </button>
        </footer>
      </div>
    </div>
  );
}
