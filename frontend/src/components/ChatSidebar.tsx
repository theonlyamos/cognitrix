import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import { ago, sizeLabel, type FileItem, type PageSummary } from '@/lib/artifactPages';
import type { ToolArtifact } from '@/context/SessionContext';
import type { AgentAvatar } from '@/lib/agentAvatar';
import type { WorkFolder } from '@/lib/folders';
import { ArtifactPreview } from '@/components/ArtifactPreview';
import { ComputerPreview } from '@/components/ComputerPreview';
import FolderChips from '@/components/FolderChips';
import { AgentBadge } from '@/components/StartChat';

export type SidebarSection = 'computer' | 'waiting' | 'artifacts' | 'files' | 'activity' | 'folders' | 'about';

export const SIDEBAR_SECTIONS: { id: SidebarSection; label: string }[] = [
  { id: 'computer', label: 'Computer' },
  { id: 'waiting', label: 'Waiting on you' },
  { id: 'artifacts', label: 'Artifacts' },
  { id: 'files', label: 'Files' },
  { id: 'activity', label: 'Activity' },
  { id: 'folders', label: 'Folders' },
  { id: 'about', label: 'About this chat' },
];

const CLOSED_STORE = 'chatSidebar.closed';
const HIDDEN_STORE = 'chatSidebar.hidden';
const OPEN_STORE = 'chatSidebar.open';

function loadSet(key: string): Set<SidebarSection> {
  try {
    const value = JSON.parse(localStorage.getItem(key) || '[]');
    if (Array.isArray(value)) return new Set(value.filter((v) => SIDEBAR_SECTIONS.some((s) => s.id === v)));
  } catch {
    // Storage blocked or bad JSON: every section shows, open.
  }
  return new Set();
}

function saveSet(key: string, value: Set<SidebarSection>) {
  try {
    localStorage.setItem(key, JSON.stringify([...value]));
  } catch {
    // Not remembered this time.
  }
}

/** Whether the person left the sidebar open or closed; null before they ever chose. */
export function loadSidebarOpen(): boolean | null {
  try {
    const value = localStorage.getItem(OPEN_STORE);
    return value === '1' ? true : value === '0' ? false : null;
  } catch {
    return null;
  }
}

export function saveSidebarOpen(open: boolean) {
  try {
    localStorage.setItem(OPEN_STORE, open ? '1' : '0');
  } catch {
    // Not remembered this time.
  }
}

export interface SidebarWaiting {
  id: string;
  kind: 'approval' | 'question';
  title: string;
  detail?: string;
}

export interface SidebarActivity {
  key: string;
  name: string;
  status: 'running' | 'done' | 'error' | string;
}

export interface ChatSidebarProps {
  onClose: () => void;
  /** The agent's desktop name when its sandboxed computer is running, else null. */
  desktop: string | null;
  /** The sandboxed computer is switched on for this server. */
  sandboxOn: boolean;
  desktopHeldBy?: string | null;
  onOpenComputer: () => void;
  waiting: SidebarWaiting[];
  onApprove: (id: string) => void;
  onDeny: (id: string) => void;
  /** Scroll the chat to the request so its full details and options show. */
  onShowWaiting: () => void;
  pages: PageSummary[];
  /** Pages published since the sidebar was last looked at. */
  newPageIds?: Set<string>;
  onOpenArtifact: (id: string) => void;
  images: ToolArtifact[];
  files: FileItem[];
  activity: SidebarActivity[];
  busy: boolean;
  folders: WorkFolder[];
  onChangeFolders: (folders: WorkFolder[]) => void | Promise<void>;
  foldersDisabled?: boolean;
  about: {
    agentId: string;
    agentName: string;
    avatar?: AgentAvatar | null;
    model: string;
    reasoning?: string;
    messages: number;
    started?: string;
    incognito?: boolean;
  };
}

/** The chat's side panel: what this chat's agent is doing and what it made. */
export function ChatSidebar(props: ChatSidebarProps) {
  const { onClose, desktop, sandboxOn, waiting, pages, images, files, activity, about } = props;
  const [closed, setClosed] = useState(() => loadSet(CLOSED_STORE));
  const [hidden, setHidden] = useState(() => loadSet(HIDDEN_STORE));
  const [customizing, setCustomizing] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!customizing) return;
    const onDown = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setCustomizing(false);
    };
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setCustomizing(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [customizing]);

  const toggle = (id: SidebarSection) => setClosed((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    saveSet(CLOSED_STORE, next);
    return next;
  });
  const toggleHidden = (id: SidebarSection) => setHidden((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    saveSet(HIDDEN_STORE, next);
    return next;
  });

  const section = (id: SidebarSection, aside: ReactNode, body: ReactNode) => {
    if (hidden.has(id)) return null;
    const label = SIDEBAR_SECTIONS.find((s) => s.id === id)!.label;
    const open = !closed.has(id);
    return (
      <section key={id} aria-label={label} className="border-b border-line px-3.5 py-3">
        <button
          type="button"
          aria-expanded={open}
          onClick={() => toggle(id)}
          className="flex min-h-11 w-full items-center gap-2 text-left md:min-h-0"
        >
          <span aria-hidden className={cn('w-3 flex-none text-center text-[8px] text-fg-dim transition-transform', !open && '-rotate-90')}>▼</span>
          <span className="flex-1 font-mono text-[10px] uppercase tracking-[0.16em] text-fg-dim">{label}</span>
          <span className="font-mono text-[10.5px] text-fg-dim">{aside}</span>
        </button>
        {open && <div className="mt-2.5">{body}</div>}
      </section>
    );
  };

  const empty = (text: string) => <p className="text-[12.5px] leading-relaxed text-fg-dim">{text}</p>;

  return (
    <section aria-label="This chat" className="flex h-full min-h-0 flex-col bg-panel">
      <header className="flex min-h-14 flex-none items-center gap-1 border-b border-line pl-3.5 pr-2 md:min-h-12">
        <h2 className="mr-auto text-[13.5px] font-semibold">This chat</h2>
        <div ref={menuRef} className="relative">
          <button
            type="button"
            aria-haspopup="menu"
            aria-expanded={customizing}
            aria-label="Choose sections"
            title="Choose sections"
            onClick={() => setCustomizing((v) => !v)}
            className={iconButton}
          >
            <svg aria-hidden width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="19" cy="12" r="1.6" /></svg>
          </button>
          {customizing && (
            <div role="menu" aria-label="Sections" className="absolute right-0 top-full z-30 mt-1 w-52 rounded-lg border border-line bg-panel p-1.5 shadow-lg">
              <div className="px-2 pb-1 pt-1 font-mono text-[10px] uppercase tracking-[0.16em] text-fg-dim">Show sections</div>
              {SIDEBAR_SECTIONS.map((s) => (
                <label key={s.id} className="flex min-h-11 cursor-pointer items-center gap-2 rounded px-2 text-[13px] hover:bg-panel-2 md:min-h-8">
                  <input type="checkbox" checked={!hidden.has(s.id)} onChange={() => toggleHidden(s.id)} className="accent-[var(--accent)]" />
                  {s.label}
                </label>
              ))}
            </div>
          )}
        </div>
        <button type="button" onClick={onClose} aria-label="Close the chat sidebar" title="Close (Ctrl+.)" className={iconButton}>
          <svg aria-hidden width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6 6 18" /></svg>
        </button>
      </header>

      <div className="min-h-0 flex-1 overscroll-contain overflow-y-auto">
        {section('computer', desktop ? 'live' : sandboxOn ? 'idle' : 'off', desktop ? (
          <>
            <ComputerPreview desktop={desktop} label={about.agentName} onOpen={props.onOpenComputer} />
            <div className="mt-2 flex items-center gap-2 text-[12.5px]">
              <AgentBadge name={about.agentName} avatar={about.avatar} size="sm" />
              <span className="min-w-0 flex-1 truncate">
                {props.desktopHeldBy ? `${props.desktopHeldBy} has control` : `${about.agentName}'s desktop`}
              </span>
              <button type="button" onClick={props.onOpenComputer} className={smallButton}>Open</button>
            </div>
          </>
        ) : sandboxOn ? (
          <>
            {empty(`${about.agentName} isn't using its computer right now. A live picture shows here when it does.`)}
            <button type="button" onClick={props.onOpenComputer} className={cn(smallButton, 'mt-2')}>Open the computer panel</button>
          </>
        ) : empty('The sandboxed computer is off. Turn it on in Settings to give agents their own desktop you can watch.'))}

        {section('waiting', waiting.length || '', waiting.length === 0 ? empty('Nothing is waiting on you.') : (
          <ul className="flex flex-col gap-2">
            {waiting.map((item) => (
              <li key={item.id} className="rounded-lg border border-[#ffb86b]/50 bg-[#ffb86b]/10 px-2.5 py-2 text-[12.5px]">
                <div className="font-medium [overflow-wrap:anywhere]">{item.title}</div>
                {item.detail && <div className="mt-0.5 line-clamp-3 text-fg-dim [overflow-wrap:anywhere]">{item.detail}</div>}
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {item.kind === 'approval' && (
                    <>
                      <button type="button" onClick={() => props.onApprove(item.id)} className={cn(smallButton, 'border-transparent bg-accent text-accent-foreground hover:bg-accent')}>Approve</button>
                      <button type="button" onClick={() => props.onDeny(item.id)} className={smallButton}>Deny</button>
                    </>
                  )}
                  <button type="button" onClick={props.onShowWaiting} className={smallButton}>{item.kind === 'question' ? 'Answer in chat' : 'Show'}</button>
                </div>
              </li>
            ))}
          </ul>
        ))}

        {section('artifacts', pages.length || '', pages.length === 0 ? empty('Pages this chat publishes show here.') : (
          <ul className="-mx-1.5 flex flex-col">
            {pages.map((page) => (
              <li key={page.id}>
                <button
                  type="button"
                  onClick={() => props.onOpenArtifact(page.id)}
                  className="flex min-h-11 w-full items-center gap-2.5 rounded-md px-1.5 py-1.5 text-left hover:bg-panel-2"
                >
                  <span aria-hidden className="grid h-8 w-10 flex-none place-items-center rounded border border-line bg-bg text-accent-ink">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="9" rx="1" /><rect x="14" y="3" width="7" height="5" rx="1" /><rect x="14" y="12" width="7" height="9" rx="1" /><rect x="3" y="16" width="7" height="5" rx="1" /></svg>
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-medium">{page.title}</span>
                    <span className="block truncate font-mono text-[10.5px] text-fg-dim">
                      v{page.version} · {ago(page.updated)}
                      {page.live && ' · live'}
                      {props.newPageIds?.has(page.id) && <span className="ml-1.5 rounded-[3px] bg-accent px-1 text-[9.5px] text-accent-foreground">NEW</span>}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ))}

        {section('files', images.length + files.length || '', images.length + files.length === 0 ? empty('Images and documents from this chat show here.') : (
          <div className="flex flex-col gap-2.5">
            {images.length > 0 && (
              <div className="grid grid-cols-3 gap-1.5 [&_img]:aspect-square [&_img]:w-full [&_img]:object-cover">
                {images.map((image) => (
                  <div key={image.id} className="min-w-0 overflow-hidden rounded-md border border-line bg-panel-2">
                    <ArtifactPreview artifact={image} />
                  </div>
                ))}
              </div>
            )}
            {files.length > 0 && (
              <ul className="-mx-1.5 flex flex-col">
                {files.map((file) => <FileRow key={file.id} file={file} />)}
              </ul>
            )}
          </div>
        ))}

        {section('activity', props.busy ? 'working' : activity.length ? `${activity.length} steps` : '', activity.length === 0
          ? empty(props.busy ? 'Thinking…' : 'Steps the agent takes in its latest reply show here.')
          : (
            <ol className="flex flex-col gap-1.5 text-[12.5px]">
              {activity.map((step) => (
                <li key={step.key} className={cn('flex items-start gap-2', step.status === 'done' && 'text-fg-dim')}>
                  <span aria-hidden className={cn('w-3 flex-none pt-px text-center font-mono text-[11px]', step.status === 'error' ? 'text-danger-ink' : 'text-accent-ink')}>
                    {step.status === 'running' ? '✱' : step.status === 'done' ? '✓' : step.status === 'error' ? '×' : '·'}
                  </span>
                  <span className="min-w-0 flex-1 truncate">{step.name}</span>
                  <span className="sr-only">{step.status}</span>
                </li>
              ))}
            </ol>
          ))}

        {section('folders', props.folders.length || '', (
          <FolderChips folders={props.folders} onChange={props.onChangeFolders} emptyLabel="private scratch folder" disabled={props.foldersDisabled} />
        ))}

        {section('about', '', (
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-[12.5px]">
            <dt className={term}>agent</dt>
            <dd className="min-w-0 truncate">
              <Link to={`/agents/${about.agentId}`} className="hover:underline">{about.agentName}</Link>
            </dd>
            {about.model && (<><dt className={term}>model</dt><dd className="min-w-0 truncate" title={about.model}>{about.model}</dd></>)}
            {about.reasoning && (<><dt className={term}>reasoning</dt><dd className="min-w-0 truncate">{about.reasoning}</dd></>)}
            <dt className={term}>messages</dt><dd>{about.messages}</dd>
            {about.started && (<><dt className={term}>started</dt><dd className="min-w-0 truncate">{about.started}</dd></>)}
            {about.incognito && (<><dt className={term}>mode</dt><dd>Incognito</dd></>)}
          </dl>
        ))}
      </div>
    </section>
  );
}

function FileRow({ file }: { file: FileItem }) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const download = async () => {
    setBusy(true);
    setFailed(false);
    try {
      const { data } = await api.get(`/artifact-pages/files/${file.id}`, { params: { download: true }, responseType: 'blob' });
      const url = URL.createObjectURL(data as Blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = file.title;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <li>
      <button
        type="button"
        disabled={busy}
        onClick={() => void download()}
        title={`Download ${file.title}`}
        className="flex min-h-11 w-full items-center gap-2.5 rounded-md px-1.5 py-1.5 text-left hover:bg-panel-2 disabled:opacity-60"
      >
        <svg aria-hidden className="h-4 w-4 flex-none text-fg-dim" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></svg>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px]">{file.title}</span>
          <span className={cn('block truncate font-mono text-[10.5px]', failed ? 'text-danger-ink' : 'text-fg-dim')}>
            {failed ? "Couldn't download it. Try again." : `${sizeLabel(file.size_bytes)} · ${ago(file.updated)}`}
          </span>
        </span>
      </button>
    </li>
  );
}

const iconButton = 'inline-flex h-11 w-11 flex-none items-center justify-center rounded-lg text-fg-dim hover:bg-panel-2 hover:text-fg md:h-8 md:w-8';
const smallButton = 'inline-flex min-h-11 items-center rounded-md border border-line px-2.5 text-[12px] hover:bg-panel-2 md:min-h-7';
const term = 'pt-px font-mono text-[10.5px] text-fg-dim';
