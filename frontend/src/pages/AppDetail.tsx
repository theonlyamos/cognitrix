import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Check, Copy, ExternalLink, Maximize2, Pause, Pencil, Pin, Play, RefreshCw, Share2, Trash2 } from 'lucide-react';
import { AccessLabel, AppStatusBadge, Sparkline } from '@/components/hosted-apps/parts';
import { AppShareDialog } from '@/components/hosted-apps/AppShareDialog';
import { DeleteAppDialog } from '@/components/hosted-apps/DeleteAppDialog';
import { ErrorState, LoadingState } from '@/components/list-ui';
import { api, errorMessage } from '@/lib/api';
import { ago } from '@/lib/artifactPages';
import { CHAT_DRAFT_KEY } from '@/lib/chatDraft';
import { APP_FRAME_SANDBOX, appLink, openAppWindow, openUrl, type AppDetail as Detail } from '@/lib/hostedApps';
import { cn } from '@/lib/utils';

type SideTab = 'overview' | 'versions' | 'logs';
type PhoneTab = 'app' | SideTab;
const TAB_LABEL: Record<PhoneTab, string> = { app: 'App', overview: 'Overview', versions: 'Versions', logs: 'Logs' };

const button = 'inline-flex h-9 flex-none items-center gap-1.5 rounded-md border border-line bg-panel px-3 text-[13px] font-medium hover:bg-panel-2 disabled:opacity-50 md:h-8';
const SLEEP_CHOICES = [[0, 'Never'], [15, '15 min'], [60, '1 hour'], [240, '4 hours']] as const;

function useApp(appId: string) {
  const [app, setApp] = useState<Detail | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    try {
      const { data } = await api.get<Detail>(`/hosted-apps/${encodeURIComponent(appId)}`);
      setApp(data);
      setError('');
    } catch (e) {
      setError(errorMessage(e, 'Could not load this app.'));
    }
  }, [appId]);
  useEffect(() => { void load(); }, [load]);
  return { app, setApp, error, load };
}

function Versions({ app, onChanged }: { app: Detail; onChanged: (d: Detail) => void }) {
  const [busy, setBusy] = useState(0);
  const [error, setError] = useState('');
  const [open, setOpen] = useState<number | null>(null);
  const canEdit = app.role !== 'view';
  const promote = async (number: number) => {
    setBusy(number);
    setError('');
    try {
      const { data } = await api.post<Detail>(`/hosted-apps/${app.id}/promote`, { version: number });
      onChanged(data);
    } catch (e) {
      setError(errorMessage(e, 'That version did not start.'));
    } finally {
      setBusy(0);
    }
  };
  if (!app.versions?.length) return <p className="text-[13px] text-fg-dim">No versions yet.</p>;
  return (
    <div className="flex flex-col">
      {error && <p role="alert" className="mb-2 text-[12.5px] text-danger-ink">{error}</p>}
      {app.versions.map((v) => (
        <div key={v.number} className="border-b border-line py-2.5">
          <div className="flex items-start gap-3">
            <span className="w-8 flex-none pt-0.5 font-mono text-[11.5px] font-medium">v{v.number}</span>
            <button type="button" className="min-w-0 flex-1 text-left" onClick={() => setOpen(open === v.number ? null : v.number)}
              aria-expanded={open === v.number}>
              <span className={cn('block text-[13px] font-medium', v.status === 'failed' && 'text-danger-ink')}>
                {v.status === 'failed' ? `Failed to start${v.error ? `: ${v.error}` : ''}` : v.note || (v.status === 'building' ? 'Building…' : 'New version')}
              </span>
              <span className="block font-mono text-[10.5px] text-fg-dim">
                {ago(v.created)}{v.agent?.name ? ` · ${v.agent.name}` : ''}{v.branch ? ` · ${v.branch}` : ''}
                {v.status === 'failed' && app.version ? ` · v${app.version} kept serving` : ''}
              </span>
            </button>
            {v.status === 'live' ? (
              <span className="flex-none rounded bg-accent px-1.5 py-0.5 font-mono text-[9.5px] tracking-wider text-accent-foreground">LIVE</span>
            ) : v.status === 'ok' && canEdit ? (
              <button type="button" className="flex-none rounded-md border border-line px-2 py-1 text-[11.5px] hover:bg-panel-2 disabled:opacity-50"
                disabled={busy > 0} onClick={() => void promote(v.number)}>{busy === v.number ? 'Starting…' : 'Make live'}</button>
            ) : null}
          </div>
          {open === v.number && v.log && (
            <pre className="mt-2 max-h-56 overflow-auto rounded-md bg-[#0b0c0e] p-2.5 font-mono text-[11px] leading-relaxed text-[#c9cdc9]">{v.log}</pre>
          )}
        </div>
      ))}
    </div>
  );
}

function Logs({ app }: { app: Detail }) {
  const [log, setLog] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const { data } = await api.get<{ log: string }>(`/hosted-apps/${app.id}/logs`, { params: { lines: 300 } });
      setLog(data.log || '(nothing printed yet)');
    } catch (e) {
      setLog(errorMessage(e, 'Could not read the log.'));
    }
  }, [app.id]);
  useEffect(() => { void load(); }, [load]);
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex items-center"><span className="font-mono text-[10px] tracking-[0.16em] text-fg-dim">OUTPUT OF v{app.version || app.latest}</span>
        <button type="button" onClick={() => void load()} className="ml-auto inline-flex items-center gap-1 text-[12px] text-fg-dim hover:text-fg"><RefreshCw size={12} />Refresh</button></div>
      <pre className="min-h-40 flex-1 overflow-auto whitespace-pre-wrap break-words rounded-md bg-[#0b0c0e] p-3 font-mono text-[11px] leading-relaxed text-[#c9cdc9]">{log ?? 'loading…'}</pre>
    </div>
  );
}

function Overview({ app, onChanged, onDeleted }: { app: Detail; onChanged: (d: Detail) => void; onDeleted: () => void }) {
  const owner = app.role === 'owner';
  const canEdit = app.role !== 'view';
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);
  const act = async (name: string, work: () => Promise<Detail | void>) => {
    setBusy(name);
    setError('');
    try {
      const out = await work();
      if (out) onChanged(out);
    } catch (e) {
      setError(errorMessage(e, 'That did not work.'));
    } finally {
      setBusy('');
    }
  };
  const patch = (body: Record<string, unknown>) => act('patch', async () => (await api.patch<Detail>(`/hosted-apps/${app.id}`, body)).data);
  const total = app.visits_7d.reduce((a, b) => a + b, 0);
  return (
    <div className="flex flex-col gap-5">
      <div>
        <div className="mb-2 font-mono text-[10px] tracking-[0.16em] text-fg-dim">VISITS · LAST 7 DAYS</div>
        <div className="flex items-end gap-3"><b className="text-[24px] font-semibold leading-none">{total}</b><Sparkline values={app.visits_7d} width={200} height={34} /></div>
      </div>
      {app.description && <p className="text-[13px] text-fg-dim">{app.description}</p>}
      <dl className="grid grid-cols-[100px_1fr] gap-x-3 gap-y-2 text-[12.5px]">
        <dt className="font-mono text-[10.5px] text-fg-dim">who can open</dt><dd><AccessLabel access={app.access} /></dd>
        <dt className="font-mono text-[10.5px] text-fg-dim">built by</dt>
        <dd>{app.agent?.name || 'an agent'}{owner && app.session_id ? <> · <Link className="underline" to="/home" onClick={() => openChat(app)}>open chat</Link></> : ''}</dd>
        <dt className="font-mono text-[10.5px] text-fg-dim">runs on</dt><dd>{app.runtime === 'sandbox' ? 'The agents’ sandboxed computer' : 'This machine'}</dd>
        {canEdit && app.command && <><dt className="font-mono text-[10.5px] text-fg-dim">start</dt><dd className="break-all font-mono text-[11.5px]">{app.command}</dd></>}
        {canEdit && app.install && <><dt className="font-mono text-[10.5px] text-fg-dim">install</dt><dd className="break-all font-mono text-[11.5px]">{app.install}</dd></>}
        {owner && app.folder && <><dt className="font-mono text-[10.5px] text-fg-dim">from folder</dt><dd className="break-all font-mono text-[11.5px]">{app.folder}</dd></>}
        {app.branch && <><dt className="font-mono text-[10.5px] text-fg-dim">branch</dt><dd className="font-mono text-[11.5px]">{app.branch}</dd></>}
        {app.task && <><dt className="font-mono text-[10.5px] text-fg-dim">kept fresh by</dt><dd><Link className="underline" to={`/tasks/${app.task.id}`}>{app.task.title}</Link>{app.task.schedule ? ` · ${app.task.schedule}` : ''}</dd></>}
        {app.uses_api && <><dt className="font-mono text-[10.5px] text-fg-dim">Cognitrix API</dt><dd>Has its own key (read, chat, run)</dd></>}
        <dt className="font-mono text-[10.5px] text-fg-dim">sleeps after</dt>
        <dd>{owner ? (
          <select aria-label="Sleep after" value={app.sleep_after} disabled={busy !== ''} onChange={(e) => void patch({ sleep_after: Number(e.target.value) })}
            className="h-8 rounded-md border border-line bg-bg px-2 text-[12.5px]">
            {SLEEP_CHOICES.map(([v, label]) => <option key={v} value={v}>{v ? `${label} with no visitors` : label}</option>)}
            {!SLEEP_CHOICES.some(([v]) => v === app.sleep_after) && <option value={app.sleep_after}>{app.sleep_after} min</option>}
          </select>
        ) : app.sleep_after ? `${app.sleep_after} min with no visitors` : 'never'}</dd>
      </dl>
      {app.last_error && <p className="rounded-md border border-danger-ink/30 bg-danger-ink/5 p-2.5 text-[12.5px] text-danger-ink">{app.last_error}</p>}
      {canEdit && (
        <div className="flex flex-wrap gap-2">
          {app.status === 'online'
            ? <button type="button" className={button} disabled={busy !== ''} onClick={() => void act('stop', async () => (await api.post<Detail>(`/hosted-apps/${app.id}/stop`)).data)}><Pause size={14} />{busy === 'stop' ? 'Stopping…' : 'Sleep now'}</button>
            : app.version > 0 && <button type="button" className={button} disabled={busy !== ''} onClick={() => void act('start', async () => (await api.post<Detail>(`/hosted-apps/${app.id}/start`)).data)}><Play size={14} />{busy === 'start' ? 'Starting…' : 'Start'}</button>}
          {app.folder && <button type="button" className={button} disabled={busy !== ''} title="Publish its folder again as a new version"
            onClick={() => void act('redeploy', async () => (await api.post<Detail>(`/hosted-apps/${app.id}/redeploy`, {})).data)}><RefreshCw size={14} />{busy === 'redeploy' ? 'Publishing…' : 'Redeploy'}</button>}
          {owner && <button type="button" className={button} disabled={busy !== ''} onClick={() => void patch({ pinned: !app.pinned })}><Pin size={14} />{app.pinned ? 'Unpin' : 'Pin'}</button>}
          {owner && <button type="button" className={cn(button, 'text-danger-ink')} disabled={busy !== ''}
            onClick={() => setConfirmDelete(true)}>
            <Trash2 size={14} />Delete</button>}
        </div>
      )}
      {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
      {confirmDelete && (
        <DeleteAppDialog title={app.title} folder={app.folder} onCancel={() => setConfirmDelete(false)}
          onConfirm={async () => {
            // A failure closes the dialog too, so its message shows under the buttons.
            await act('delete', async () => { await api.delete(`/hosted-apps/${app.id}`); onDeleted(); });
            setConfirmDelete(false);
          }} />
      )}
    </div>
  );
}

/** Open the chat that built the app, or a new chat with its agent asking for a change. */
function openChat(app: Detail): void {
  try {
    if (app.agent?.id) localStorage.setItem('selectedAgentId', app.agent.id);
    if (app.agent?.id) localStorage.setItem(`chatSession:${app.agent.id}`, app.session_id || '');
    if (!app.session_id) sessionStorage.setItem(CHAT_DRAFT_KEY, `Change the app "${app.title}" (app_id ${app.id}): `);
  } catch {
    /* storage off: the chat still opens */
  }
}

/** /apps/:appId: the running app with its versions, log and sharing. */
export default function AppDetail() {
  const { appId = '' } = useParams();
  const navigate = useNavigate();
  const { app, setApp, error, load } = useApp(appId);
  const [frame, setFrame] = useState('');
  const [side, setSide] = useState<SideTab>('overview');
  const [phoneTab, setPhoneTab] = useState<PhoneTab>('app');
  const [sharing, setSharing] = useState(false);
  const [copied, setCopied] = useState(false);

  const live = app ? app.version > 0 : false;
  useEffect(() => {
    if (!app || !live) return;
    let on = true;
    openUrl(app.id).then((url) => { if (on) setFrame(url); }).catch(() => undefined);
    return () => { on = false; };
  }, [app?.id, app?.version, live]); // eslint-disable-line react-hooks/exhaustive-deps

  // Building apps finish on their own.
  useEffect(() => {
    if (app?.status !== 'building') return;
    const timer = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(timer);
  }, [app?.status, load]);

  if (error && !app) return <ErrorState message={error} onRetry={() => void load()} />;
  if (!app) return <LoadingState label="loading app…" />;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(appLink(app));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard off */
    }
  };
  // Full screen inside Cognitrix, with a bar to come back (a new tab is one click from there).
  const openFull = () => navigate(`/apps/open/${app.slug}`);
  const editInChat = () => { openChat(app); navigate('/home'); };

  const appFrame = live && frame ? (
    <iframe key={frame} src={frame} title={app.title} sandbox={APP_FRAME_SANDBOX} className="h-full w-full border-0 bg-white" />
  ) : (
    <div className="grid h-full place-items-center p-6 text-center text-[13px] text-fg-dim">
      {app.status === 'building' ? `Building version ${Math.max(1, app.latest)}…` : app.last_error || 'This app has no running version yet.'}
    </div>
  );
  const sideBody = side === 'overview' ? <Overview app={app} onChanged={setApp} onDeleted={() => navigate('/apps', { replace: true })} />
    : side === 'versions' ? <Versions app={app} onChanged={setApp} /> : <Logs app={app} />;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="app-page-header flex flex-none items-center gap-2 border-b border-line bg-panel py-2 pl-16 pr-3 md:gap-3 md:px-4">
        <Link to="/apps" aria-label="All apps" className="grid h-9 w-9 flex-none place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg"><ArrowLeft size={16} /></Link>
        <h1 className="min-w-0 truncate text-[15px] font-semibold">{app.title}</h1>
        <AppStatusBadge app={app} className="hidden sm:inline-flex" />
        <button type="button" onClick={() => void copy()} title="Copy the link"
          className="hidden min-w-0 items-center gap-1.5 truncate rounded-md border border-line bg-bg px-2 py-1 font-mono text-[11.5px] text-fg-dim hover:text-fg lg:inline-flex">
          {copied ? <Check size={12} /> : <Copy size={12} />}{appLink(app).replace(/^https?:\/\//, '')}
        </button>
        <div className="ml-auto flex flex-none items-center gap-2">
          {app.role !== 'view' && <button type="button" className={cn(button, 'hidden md:inline-flex')} onClick={editInChat}><Pencil size={14} />Edit in chat</button>}
          {live && <button type="button" className={cn(button, 'w-9 justify-center px-0')} onClick={openFull} aria-label="Full screen" title="Full screen (Esc to come back)"><Maximize2 size={14} /></button>}
          {live && <button type="button" className={cn(button, 'hidden md:inline-flex')} onClick={() => void openAppWindow(app).catch(() => openFull())} title="Open in its own window"><ExternalLink size={14} />Open</button>}
          {app.role === 'owner' ? (
            <button type="button" className={cn(button, 'border-transparent bg-accent text-accent-foreground hover:bg-accent/90')} onClick={() => setSharing(true)} aria-label="Share">
              <Share2 size={14} /><span className="hidden md:inline">Share</span>
            </button>
          ) : (
            <button type="button" className={button} onClick={() => void copy()} aria-label="Copy link">{copied ? <Check size={14} /> : <Copy size={14} />}</button>
          )}
        </div>
      </header>

      {/* Phone: one thing at a time. */}
      <nav role="tablist" aria-label="App sections" className="flex flex-none gap-1 border-b border-line px-2 md:hidden">
        {(['app', 'versions', 'logs', 'overview'] as PhoneTab[]).filter((t) => app.role !== 'view' || t === 'app' || t === 'overview').map((t) => (
          <button key={t} type="button" role="tab" aria-selected={phoneTab === t} onClick={() => { setPhoneTab(t); if (t !== 'app') setSide(t); }}
            className={cn('-mb-px border-b-2 px-3 py-2.5 text-[13px]', phoneTab === t ? 'border-accent font-medium' : 'border-transparent text-fg-dim')}>
            {t === 'overview' ? 'Details' : TAB_LABEL[t]}
          </button>
        ))}
      </nav>
      <div className="flex min-h-0 flex-1 flex-col md:hidden">
        {phoneTab === 'app' ? <div className="min-h-0 flex-1 p-2"><div className="h-full overflow-hidden rounded-[10px] border border-line">{appFrame}</div></div>
          : <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4">{sideBody}</div>}
      </div>

      <div className="hidden min-h-0 flex-1 md:flex">
        <div className="min-w-0 flex-1 p-4"><div className="h-full overflow-hidden rounded-[10px] border border-line bg-panel">{appFrame}</div></div>
        <aside className="flex w-[360px] flex-none flex-col border-l border-line bg-panel">
          <nav role="tablist" aria-label="App details" className="flex flex-none gap-1 border-b border-line px-3">
            {(['overview', 'versions', 'logs'] as SideTab[]).filter((t) => app.role !== 'view' || t === 'overview').map((t) => (
              <button key={t} type="button" role="tab" aria-selected={side === t} onClick={() => setSide(t)}
                className={cn('-mb-px border-b-2 px-3 py-2.5 text-[13px]', side === t ? 'border-accent font-medium' : 'border-transparent text-fg-dim hover:text-fg')}>
                {TAB_LABEL[t]}{t === 'versions' && app.versions?.length ? <span className="ml-1 font-mono text-[10.5px] text-fg-dim" aria-label={`, ${app.versions.length}`}>{app.versions.length}</span> : null}
              </button>
            ))}
          </nav>
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4">{sideBody}</div>
        </aside>
      </div>
      {sharing && <AppShareDialog app={app} onClose={() => setSharing(false)} onChanged={setApp} />}
    </div>
  );
}
