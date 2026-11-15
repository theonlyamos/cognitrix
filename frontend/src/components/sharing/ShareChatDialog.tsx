import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, Link as LinkIcon, X } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { ROLE_LABEL, sharedChatHref, type ChatPeople, type ShareRole } from '@/lib/sharedChats';
import { cn } from '@/lib/utils';

const selectClass = 'h-9 rounded-md border border-line bg-bg px-2 text-[12.5px] text-fg md:h-8';
const buttonClass = 'inline-flex h-9 items-center gap-1.5 rounded-md border border-line px-3 text-[12.5px] hover:bg-panel-2 disabled:opacity-60 md:h-8';

/** Share one of my chats with people on this server: "Can read" or "Can write". */
export function ShareChatDialog({ chatId, title, onClose }: { chatId: string; title: string; onClose: () => void }) {
  const [data, setData] = useState<ChatPeople | null>(null);
  const [pick, setPick] = useState('');
  const [pickRole, setPickRole] = useState<ShareRole>('view');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDivElement>(null);
  const base = `/shared-chats/${encodeURIComponent(chatId)}/people`;

  useEffect(() => {
    api.get<ChatPeople>(base).then((r) => setData(r.data))
      .catch((e) => setError(errorMessage(e, 'Could not load sharing.')));
  }, [base]);

  useEffect(() => {
    dialog.current?.querySelector<HTMLElement>('select, button')?.focus();
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const candidates = useMemo(
    () => (data?.directory || []).filter((p) => !data?.people.some((x) => x.id === p.id)),
    [data],
  );
  const current = () => (data?.people || []).map((p) => ({ user_id: p.id, role: p.role }));

  const save = async (people: { user_id: string; role: ShareRole }[], done: string) => {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const { data: next } = await api.put<ChatPeople>(base, { people });
      setData(next);
      setNotice(done);
    } catch (e) {
      setError(errorMessage(e, 'Could not change sharing.'));
    } finally {
      setBusy(false);
    }
  };

  const link = `${window.location.origin}${sharedChatHref(chatId)}`;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      setNotice('Link copied');
    } catch {
      setNotice(link);
    }
  };

  // On document.body: the sidebar it opens from would otherwise trap a fixed overlay.
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/45 md:items-center" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={dialog} role="dialog" aria-modal="true" aria-label={`Share ${title}`}
        className="flex max-h-[92dvh] w-full flex-col overflow-hidden rounded-t-2xl border border-line bg-panel shadow-2xl md:w-[520px] md:rounded-2xl">
        <header className="flex flex-none items-center gap-2 border-b border-line px-4 py-3">
          <h2 className="min-w-0 flex-1 truncate text-[15px] font-semibold">Share “{title}”</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="grid h-9 w-9 place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg"><X size={16} /></button>
        </header>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
          <p className="text-[13px] leading-relaxed text-fg-dim">
            People you add see this whole chat and watch replies arrive live. <strong className="font-medium text-fg">Can write</strong> also lets them ask the agent things here:
            it answers them read-only (no changes, no approvals, none of your memories), and what it uses counts toward your usage.
          </p>
          {!data && !error && <p className="text-[13px] text-fg-dim">Loading…</p>}
          {data && (
            <>
              {data.directory.length === 0 ? (
                <p className="text-[13px] text-fg-dim">Nobody else has an account on this server yet. An admin can invite people in Settings › People.</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  <select aria-label="Person to add" value={pick} onChange={(e) => setPick(e.target.value)} className={cn(selectClass, 'min-w-0 flex-1')}>
                    <option value="">{candidates.length ? 'Add someone from this server…' : 'Everyone is already added'}</option>
                    {candidates.map((p) => <option key={p.id} value={p.id}>{p.name}{p.email && p.email !== p.name ? ` (${p.email})` : ''}</option>)}
                  </select>
                  <select aria-label="Their access" value={pickRole} onChange={(e) => setPickRole(e.target.value as ShareRole)} className={selectClass}>
                    <option value="view">{ROLE_LABEL.view}</option>
                    <option value="chat">{ROLE_LABEL.chat}</option>
                  </select>
                  <button type="button" className={buttonClass} disabled={!pick || busy}
                    onClick={() => { const who = candidates.find((p) => p.id === pick)?.name || 'them'; void save([...current(), { user_id: pick, role: pickRole }], `Shared with ${who}`); setPick(''); }}>Add</button>
                </div>
              )}
              {data.people.length === 0 ? (
                <p className="text-[13px] text-fg-dim">Only you can see this chat.</p>
              ) : (
                <ul className="space-y-2.5">
                  {data.people.map((p) => (
                    <li key={p.id} className="flex items-center gap-2.5">
                      <span className="grid h-7 w-7 flex-none place-items-center rounded-full border border-line bg-panel-2 font-mono text-[11px]">{p.name.slice(0, 1).toUpperCase()}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13.5px]">{p.name}</span>
                        {p.email && p.email !== p.name && <span className="block truncate font-mono text-[11px] text-fg-dim">{p.email}</span>}
                      </span>
                      <select aria-label={`Access for ${p.name}`} value={p.role} className={selectClass} disabled={busy}
                        onChange={(e) => {
                          const value = e.target.value;
                          void save(value === 'remove'
                            ? current().filter((x) => x.user_id !== p.id)
                            : current().map((x) => (x.user_id === p.id ? { ...x, role: value as ShareRole } : x)),
                          value === 'remove' ? `Stopped sharing with ${p.name}` : 'Saved');
                        }}>
                        <option value="view">{ROLE_LABEL.view}</option>
                        <option value="chat">{ROLE_LABEL.chat}</option>
                        <option value="remove">Remove</option>
                      </select>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
          {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
        </div>
        <footer className="flex flex-none flex-wrap items-center gap-2 border-t border-line px-4 py-3">
          <span aria-live="polite" className="min-w-0 flex-1 truncate font-mono text-[11px] text-fg-dim">
            {notice && <span className="inline-flex items-center gap-1"><Check size={12} />{notice}</span>}
          </span>
          <button type="button" onClick={() => void copy()} disabled={!data?.people.length}
            className="inline-flex h-9 items-center gap-1.5 rounded-md bg-accent px-3.5 text-[13px] font-semibold text-accent-foreground disabled:opacity-50 md:h-8">
            <LinkIcon size={14} /> Copy link
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
