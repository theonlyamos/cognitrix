import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ArrowLeft, Clock, Code2, ExternalLink, Eye, Globe, LayoutPanelTop, Lock, Maximize2, MessageCircle, Minimize2,
  MoreHorizontal, Share, Users, X,
} from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import {
  ago, onArtifactUpdate, until,
  type CommentAnchor, type CommentThread, type PageDetail,
} from '@/lib/artifactPages';
import { ArtifactFrame, type FramePin } from '@/components/artifacts/ArtifactFrame';
import { CommentsRail } from '@/components/artifacts/CommentsRail';
import { ConsentDialog } from '@/components/artifacts/ConsentDialog';
import { ShareDialog } from '@/components/artifacts/ShareDialog';
import { cn } from '@/lib/utils';

const POLL_MS = 5000;
const iconButton = 'grid h-9 w-9 flex-none place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg md:h-8 md:w-8';
const menuItem = 'flex w-full items-center px-3 py-2 text-left text-[13px] hover:bg-panel-2 disabled:opacity-50';

export function AccessBadge({ access }: { access: PageDetail['access'] }) {
  if (access === 'public') {
    return <span className="inline-flex items-center gap-1 rounded-md bg-warn/15 px-1.5 py-0.5 font-mono text-[10.5px] text-warn-ink"><Globe size={11} />Public</span>;
  }
  if (access === 'workspace' || access === 'people') {
    return <span className="inline-flex items-center gap-1 rounded-md bg-info/15 px-1.5 py-0.5 font-mono text-[10.5px] text-info-ink"><Users size={11} />{access === 'workspace' ? 'Everyone' : 'Shared'}</span>;
  }
  return <span className="inline-flex items-center gap-1 rounded-md bg-panel-2 px-1.5 py-0.5 font-mono text-[10.5px] text-fg-dim"><Lock size={11} />Private</span>;
}

export function LiveBadge() {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md bg-accent/15 px-1.5 py-0.5 font-mono text-[10.5px] text-accent-ink">
      <span className="h-1.5 w-1.5 rounded-full bg-accent shadow-[0_0_0_3px_rgba(198,242,78,0.25)]" />Live
    </span>
  );
}

interface ArtifactViewerProps {
  pageId: string;
  /** 'panel': beside a chat (Close, Expand, open full page). 'page': the /artifacts/:id page (Back). */
  variant: 'panel' | 'page';
  onClose?: () => void;
  /** Panel beside a chat with its side panel open: go back to that panel. */
  onBack?: () => void;
  expanded?: boolean;
  onToggleExpand?: () => void;
  /** Open the comments on this thread (from an Inbox alert link). */
  initialThread?: string | null;
  onGone?: () => void;
}

/** One artifact: the page, its versions, source, comments and sharing. */
export function ArtifactViewer({ pageId, variant, onClose, onBack, expanded, onToggleExpand, initialThread, onGone }: ArtifactViewerProps) {
  const navigate = useNavigate();
  const [page, setPage] = useState<PageDetail | null>(null);
  const [error, setError] = useState('');
  const [view, setView] = useState<'preview' | 'code'>('preview');
  /** null follows the newest version the viewer may see. */
  const [pinnedVersion, setPinnedVersion] = useState<number | null>(null);
  const [frame, setFrame] = useState<{ url: string; version: number } | null>(null);
  const [source, setSource] = useState<{ version: number; text: string } | null>(null);
  const [threads, setThreads] = useState<CommentThread[]>([]);
  const [canSend, setCanSend] = useState(false);
  const [commentsOpen, setCommentsOpen] = useState(!!initialThread);
  const [activeThread, setActiveThread] = useState<string | null>(initialThread ?? null);
  const [picking, setPicking] = useState(false);
  const [draftAnchor, setDraftAnchor] = useState<CommentAnchor | null>(null);
  const [shareOpen, setShareOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [askConsent, setAskConsent] = useState(false);
  const [notice, setNotice] = useState('');
  const menuRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<PageDetail>(`/artifact-pages/${pageId}`);
      setPage(data);
      setError('');
    } catch (e) {
      setError(errorMessage(e, 'Could not open this artifact.'));
    }
  }, [pageId]);

  const loadComments = useCallback(async () => {
    try {
      const { data } = await api.get<{ threads: CommentThread[]; can_send: boolean }>(`/artifact-pages/${pageId}/comments`);
      setThreads(data.threads);
      setCanSend(data.can_send);
    } catch {
      // The page itself reports access problems.
    }
  }, [pageId]);

  useEffect(() => {
    setPage(null);
    setFrame(null);
    setSource(null);
    setPinnedVersion(null);
    setView('preview');
    setThreads([]);
    setDraftAnchor(null);
    setPicking(false);
    void load();
    void loadComments();
  }, [load, loadComments]);

  // New versions and comments show up without a reload: the agent's chat
  // says when it published; otherwise look again every few seconds.
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      void load();
      void loadComments();
    }, POLL_MS);
    const off = onArtifactUpdate((id) => { if (id === pageId) void load(); });
    return () => { window.clearInterval(timer); off(); };
  }, [load, loadComments, pageId]);

  const shownVersion = pinnedVersion ?? page?.viewer_version ?? 0;

  useEffect(() => {
    if (!page || !shownVersion) return;
    if (frame?.version === shownVersion) return;
    if (pinnedVersion === null) {
      setFrame({ url: page.frame_url, version: page.viewer_version });
      return;
    }
    let live = true;
    api.get<{ url: string; version: number }>(`/artifact-pages/${page.id}/frame`, { params: { v: shownVersion } })
      .then(({ data }) => { if (live) setFrame({ url: data.url, version: data.version }); })
      .catch((e) => { if (live) setNotice(errorMessage(e, 'That version is gone.')); });
    return () => { live = false; };
  }, [page, shownVersion, pinnedVersion, frame?.version]);

  useEffect(() => {
    if (view !== 'code' || !page || !shownVersion || source?.version === shownVersion) return;
    let live = true;
    api.get<{ source: string; version: number }>(`/artifact-pages/${page.id}/source`, { params: { v: shownVersion } })
      .then(({ data }) => { if (live) setSource({ version: data.version, text: data.source }); })
      .catch((e) => { if (live) setNotice(errorMessage(e, 'Could not load the source.')); });
    return () => { live = false; };
  }, [view, page, shownVersion, source?.version]);

  useEffect(() => {
    if (page && page.powers.length > 0 && !page.consent_asked) setAskConsent(true);
  }, [page?.id, page?.powers.length, page?.consent_asked]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: MouseEvent) => { if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false); };
    window.addEventListener('mousedown', close);
    return () => window.removeEventListener('mousedown', close);
  }, [menuOpen]);

  const pins: FramePin[] = useMemo(() => {
    if (!commentsOpen) return [];
    const list: FramePin[] = [];
    threads.forEach((thread, index) => {
      if (thread.resolved || thread.anchor.x === undefined || thread.anchor.y === undefined) return;
      list.push({ id: thread.id, n: index + 1, x: thread.anchor.x, y: thread.anchor.y, title: thread.body.slice(0, 80), active: thread.id === activeThread });
    });
    if (draftAnchor?.x !== undefined && draftAnchor.y !== undefined) {
      list.push({ id: 'draft', n: threads.length + 1, x: draftAnchor.x, y: draftAnchor.y, title: 'New comment', active: true });
    }
    return list;
  }, [threads, commentsOpen, activeThread, draftAnchor]);

  const submitComment = async (body: string, opts: { threadId?: string; sendToAgent: boolean }) => {
    try {
      const { data } = await api.post<{ threads: CommentThread[]; agent: string; id: string }>(`/artifact-pages/${pageId}/comments`, {
        body, thread_id: opts.threadId || '', anchor: opts.threadId ? null : draftAnchor, version: shownVersion, send_to_agent: opts.sendToAgent,
      });
      setThreads(data.threads);
      setDraftAnchor(null);
      if (!opts.threadId) setActiveThread(data.id);
      return data.agent;
    } catch (e) {
      throw new Error(errorMessage(e, 'Could not post the comment.'));
    }
  };

  const resolve = async (threadId: string, resolved: boolean) => {
    try {
      const { data } = await api.post<{ threads: CommentThread[] }>(`/artifact-pages/${pageId}/comments/${threadId}/resolve`, { resolved });
      setThreads(data.threads);
    } catch (e) {
      setNotice(errorMessage(e, 'Could not change the thread.'));
    }
  };

  const act = async (run: () => Promise<unknown>, done: string) => {
    setMenuOpen(false);
    try {
      await run();
      setNotice(done);
      void load();
    } catch (e) {
      setNotice(errorMessage(e, 'That did not work.'));
    }
  };

  if (error && !page) {
    return (
      <section className="flex h-full min-h-0 flex-col bg-panel">
        <header className="flex h-12 flex-none items-center gap-2 border-b border-line px-3">
          <span className="text-[13.5px] font-semibold">Artifact</span>
          {onClose && <button type="button" onClick={onClose} aria-label="Close" className={cn(iconButton, 'ml-auto')}><X size={16} /></button>}
        </header>
        <p role="alert" className="p-4 text-[13px] text-fg-dim">{error}</p>
      </section>
    );
  }
  if (!page) {
    return <section className="flex h-full items-center justify-center bg-panel font-mono text-[12px] text-fg-dim" role="status">opening artifact…</section>;
  }

  const editor = page.role !== 'view';
  const owner = page.role === 'owner';
  const behind = pinnedVersion !== null && pinnedVersion !== page.version;
  const openCount = threads.filter((t) => !t.resolved).length;
  const wide = variant === 'page' || !!expanded;

  return (
    <section aria-label={`Artifact: ${page.title}`} className="relative flex h-full min-h-0 flex-col bg-bg">
      <header className={cn('flex flex-none items-center gap-1.5 border-b border-line py-1.5 pr-2 md:px-3', variant === 'page' ? 'app-page-header min-h-14 pl-16 md:min-h-12' : 'min-h-12 pl-2')}>
        {variant === 'page' && (
          <Link to="/apps?tab=pages" aria-label="All pages" className={iconButton}><ArrowLeft size={16} /></Link>
        )}
        {variant === 'panel' && onBack && !expanded && (
          <button type="button" onClick={onBack} className="inline-flex h-9 flex-none items-center gap-1 rounded-lg px-2 font-mono text-[11.5px] text-fg-dim hover:bg-panel-2 hover:text-fg md:h-8">
            <span aria-hidden>‹</span> This chat
          </button>
        )}
        <LayoutPanelTop size={15} className="ml-1 hidden flex-none text-fg-dim sm:block" aria-hidden />
        <h2 className="min-w-0 flex-shrink truncate text-[14px] font-semibold" title={page.title}>{page.title}</h2>
        {page.live && <LiveBadge />}
        {page.versions.length > 1 ? (
          <select aria-label="Version" value={shownVersion}
            onChange={(e) => { const v = Number(e.target.value); setPinnedVersion(v === page.version ? null : v); }}
            className="h-8 w-[5.5rem] flex-none truncate rounded-md border border-line bg-panel px-1.5 font-mono text-[12px]" title="Versions">
            {page.versions.map((v) => (
              <option key={v.number} value={v.number}>v{v.number}{v.number === page.version ? ' · latest' : ''}{v.note ? ` · ${v.note.slice(0, 40)}` : ''}</option>
            ))}
          </select>
        ) : (
          <span className="flex-none rounded-md border border-line bg-panel px-1.5 py-1 font-mono text-[12px]">v{shownVersion}</span>
        )}
        <span className="min-w-1 flex-1" />
        <div role="tablist" aria-label="View" className="hidden flex-none rounded-lg border border-line bg-panel p-0.5 sm:inline-flex">
          {(['preview', 'code'] as const).map((mode) => (
            <button key={mode} type="button" role="tab" aria-selected={view === mode} onClick={() => setView(mode)}
              className={cn('rounded-md px-2.5 py-1 text-[12px] font-medium capitalize', view === mode ? 'bg-fg text-bg' : 'text-fg-dim hover:text-fg')}>
              {mode}
            </button>
          ))}
        </div>
        <button type="button" onClick={() => { setCommentsOpen((v) => !v); setPicking(false); }} aria-pressed={commentsOpen}
          aria-label={`Comments${openCount ? `, ${openCount} open` : ''}`} title="Comments"
          className={cn(iconButton, 'relative', commentsOpen && 'bg-panel-2 text-fg')}>
          <MessageCircle size={16} />
          {openCount > 0 && <span className="absolute -right-0.5 -top-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-accent px-1 font-mono text-[9.5px] font-semibold text-accent-foreground">{openCount}</span>}
        </button>
        {owner && (
          <button type="button" onClick={() => setShareOpen(true)}
            className="inline-flex h-9 flex-none items-center gap-1.5 rounded-md border border-line bg-panel px-2.5 text-[12.5px] font-medium hover:bg-panel-2 md:h-8">
            <Share size={14} /> <span className="hidden sm:inline">Share</span>
          </button>
        )}
        <div ref={menuRef} className="relative flex-none">
          <button type="button" onClick={() => setMenuOpen((v) => !v)} aria-haspopup="menu" aria-expanded={menuOpen} aria-label="More" className={iconButton}>
            <MoreHorizontal size={16} />
          </button>
          {menuOpen && (
            <div role="menu" className="absolute right-0 top-full z-40 mt-1 w-60 overflow-hidden rounded-[10px] border border-line bg-panel py-1 shadow-xl">
              <button type="button" role="menuitem" className={cn(menuItem, 'sm:hidden')} onClick={() => { setView(view === 'code' ? 'preview' : 'code'); setMenuOpen(false); }}>
                {view === 'code' ? 'Show preview' : 'Show code'}
              </button>
              {owner && (
                <button type="button" role="menuitem" className={menuItem}
                  onClick={() => void act(() => api.patch(`/artifact-pages/${page.id}`, { pinned: !page.pinned }), page.pinned ? 'Taken off the dashboard' : 'Pinned to the dashboard')}>
                  {page.pinned ? 'Unpin from dashboard' : 'Pin to dashboard'}
                </button>
              )}
              {editor && behind && (
                <button type="button" role="menuitem" className={menuItem}
                  onClick={() => void act(async () => { await api.post(`/artifact-pages/${page.id}/restore`, { version: shownVersion }); setPinnedVersion(null); }, `Version ${shownVersion} is the latest again`)}>
                  Restore version {shownVersion}
                </button>
              )}
              {owner && page.session_id && (
                <button type="button" role="menuitem" className={menuItem} onClick={() => navigate(`/home?chat=${encodeURIComponent(page.session_id)}`)}>
                  Open the chat it came from
                </button>
              )}
              {page.powers.length > 0 && (
                <button type="button" role="menuitem" className={menuItem} onClick={() => { setAskConsent(true); setMenuOpen(false); }}>
                  What this page may use…
                </button>
              )}
              <button type="button" role="menuitem" className={menuItem}
                onClick={() => {
                  setMenuOpen(false);
                  void api.get<{ source: string }>(`/artifact-pages/${page.id}/source`, { params: { v: shownVersion } }).then(({ data }) => {
                    const blob = new Blob([data.source], { type: page.format === 'markdown' ? 'text/markdown' : 'text/html' });
                    const href = URL.createObjectURL(blob);
                    const link = document.createElement('a');
                    link.href = href;
                    link.download = `${page.title.replace(/[\\/:*?"<>|]/g, '_')}.${page.format === 'markdown' ? 'md' : 'html'}`;
                    link.click();
                    window.setTimeout(() => URL.revokeObjectURL(href), 1000);
                  }).catch((e) => setNotice(errorMessage(e, 'Could not download it.')));
                }}>
                Download {page.format === 'markdown' ? 'Markdown' : 'HTML'}
              </button>
              {owner && (
                <button type="button" role="menuitem" className={cn(menuItem, 'text-danger-ink')}
                  onClick={() => {
                    setMenuOpen(false);
                    if (!window.confirm(`Delete “${page.title}” and all its versions and comments? Its links stop working.`)) return;
                    void api.delete(`/artifact-pages/${page.id}`).then(() => (onGone ? onGone() : onClose?.()), (e) => setNotice(errorMessage(e, 'Could not delete it.')));
                  }}>
                  Delete artifact
                </button>
              )}
            </div>
          )}
        </div>
        {variant === 'panel' && (
          <>
            <Link to={`/artifacts/${page.id}`} aria-label="Open as a full page" title="Open as a full page" className={cn(iconButton, 'hidden md:grid')}><ExternalLink size={15} /></Link>
            {onToggleExpand && (
              <button type="button" onClick={onToggleExpand} aria-label={expanded ? 'Shrink the panel' : 'Expand the panel'} title={expanded ? 'Shrink' : 'Expand'} className={cn(iconButton, 'hidden md:grid')}>
                {expanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
              </button>
            )}
            {onClose && <button type="button" onClick={onClose} aria-label="Close the artifact" className={iconButton}><X size={16} /></button>}
          </>
        )}
      </header>

      {page.task && (
        <div className="flex flex-none flex-wrap items-center gap-x-2 gap-y-1 border-b border-line bg-accent/10 px-3 py-2 text-[12.5px]">
          <Clock size={14} className="flex-none text-accent-ink" aria-hidden />
          <span className="min-w-0 flex-1">
            Kept fresh by the task <Link to={`/tasks/${page.task.id}`} className="font-medium underline-offset-2 hover:underline">{page.task.title}</Link>
            {page.task.schedule ? `, ${page.task.schedule}` : ''}.
            {page.task.next_run_at ? ` Next update ${until(page.task.next_run_at)}.` : ''}
          </span>
          {editor && (
            <button type="button" className="inline-flex h-7 flex-none items-center rounded-md border border-line bg-panel px-2 text-[12px] hover:bg-panel-2"
              onClick={() => void act(() => api.post(`/tasks/${page.task!.id}/run`, {}), 'Update started. The page changes when the task finishes.')}>
              Update now
            </button>
          )}
        </div>
      )}
      {behind && (
        <div className="flex flex-none items-center gap-2 border-b border-line bg-panel-2 px-3 py-1.5 text-[12.5px] text-fg-dim">
          You're looking at version {shownVersion}. The latest is {page.version}.
          <button type="button" className="font-medium text-fg underline-offset-2 hover:underline" onClick={() => setPinnedVersion(null)}>Show latest</button>
        </div>
      )}
      {notice && (
        <div role="status" className="flex flex-none items-center gap-2 border-b border-line px-3 py-1.5 text-[12.5px] text-fg-dim">
          <span className="min-w-0 flex-1">{notice}</span>
          <button type="button" aria-label="Dismiss" onClick={() => setNotice('')} className="hover:text-fg"><X size={13} /></button>
        </div>
      )}

      <div className="relative flex min-h-0 flex-1">
        <div className={cn('min-h-0 min-w-0 flex-1', variant === 'page' ? 'p-0 md:p-3' : 'p-0 md:p-2')}>
          <div className="h-full overflow-hidden border-line bg-panel md:rounded-[10px] md:border">
            {view === 'code' ? (
              <pre className="h-full overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-[12px] leading-relaxed text-fg-dim">
                {source?.version === shownVersion ? source.text : 'loading…'}
              </pre>
            ) : frame ? (
              <ArtifactFrame url={frame.url} title={page.title} pageId={page.id} allowed={page.allowed_powers} pins={pins}
                picking={picking} onPin={(id) => { if (id !== 'draft') { setActiveThread(id); setCommentsOpen(true); } }}
                onPicked={(anchor) => { setDraftAnchor(anchor); setPicking(false); setActiveThread(null); }} />
            ) : (
              <div role="status" className="flex h-full items-center justify-center font-mono text-[12px] text-fg-dim">loading…</div>
            )}
          </div>
        </div>
        {picking && (
          <div role="status" className="absolute left-1/2 top-3 z-30 flex -translate-x-1/2 items-center gap-2 whitespace-nowrap rounded-full border border-line bg-panel px-3 py-1.5 text-[12.5px] shadow-lg">
            Click the spot you want to comment on
            <button type="button" className="font-medium text-accent-ink hover:underline" onClick={() => setPicking(false)}>Cancel</button>
          </div>
        )}
        {commentsOpen && (
          <aside className={cn('absolute inset-x-0 bottom-0 z-20 h-[64%] overflow-hidden rounded-t-2xl border-t border-line shadow-2xl',
            // Beside the page when there is room; over its lower half in the narrow chat panel.
            // While placing a pin the sheet steps aside so the whole page can be clicked.
            picking && (wide ? 'max-md:hidden' : 'hidden'),
            wide ? 'md:static md:z-auto md:h-auto md:w-[340px] md:flex-none md:rounded-none md:border-l md:border-t-0 md:shadow-none' : 'md:h-[52%]')}>
            <CommentsRail page={page} threads={threads} canSend={canSend} activeThread={activeThread} onActive={setActiveThread}
              draftAnchor={draftAnchor} picking={picking}
              onPick={(on) => { setPicking(on); if (on) setView('preview'); }}
              onClearAnchor={() => setDraftAnchor(null)} onSubmit={submitComment}
              onResolve={(id, resolved) => void resolve(id, resolved)}
              onShowVersion={(v) => setPinnedVersion(v === page.version ? null : v)}
              onClose={() => { setCommentsOpen(false); setPicking(false); }} />
          </aside>
        )}
        {askConsent && <ConsentDialog page={page} onDone={() => { setAskConsent(false); void load(); }} />}
      </div>

      <footer className="hidden h-8 flex-none items-center gap-2 border-t border-line px-3 font-mono text-[11px] text-fg-dim md:flex">
        <AccessBadge access={page.access} />
        <span className="min-w-0 truncate">
          {page.agent?.name ? `made by ${page.agent.name}` : 'artifact'}{owner ? '' : ` · shared by ${page.owner.name}`}
        </span>
        <span className="ml-auto flex-none"><Eye size={11} className="mr-1 inline" aria-hidden />v{shownVersion} of {page.version} · {ago(page.updated)}</span>
        {view === 'code' && <Code2 size={12} aria-hidden />}
      </footer>

      {shareOpen && <ShareDialog page={page} onClose={() => setShareOpen(false)} onChanged={() => void load()} />}
    </section>
  );
}
