import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Box as BoxIcon, Monitor as PcIcon } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { LoadingState, ErrorState } from '@/components/list-ui';
import { ChatRowMenu } from '@/components/ChatRowMenu';
import { ChangesPanel } from '@/components/changes/ChangesPanel';
import { ProjectFields, useProjectOptions } from '@/components/projects/ProjectForm';
import { timeAgo } from '@/lib/agentDraft';
import { chatHref } from '@/lib/chatNav';
import { useChatChanges } from '@/lib/fileChanges';
import { tr, trn } from '@/lib/i18n';
import {
  computerLabel, deleteProject, getProject, moveChatToProject, newProjectChatHref, projectChanges, projectSwatch, updateProject,
  type Project, type ProjectChanges, type ProjectChat, type ProjectInput,
} from '@/lib/projects';
import { cn } from '@/lib/utils';

const TABS = ['chats', 'changes', 'tasks', 'memory', 'settings'] as const;
type Tab = typeof TABS[number];
const TAB_LABEL: Record<Tab, () => string> = {
  chats: () => tr('ui.projects.tabChats'),
  changes: () => tr('ui.projects.tabChanges'),
  tasks: () => tr('ui.projects.tabTasks'),
  memory: () => tr('ui.projects.tabMemory'),
  settings: () => tr('ui.projects.tabSettings'),
};

interface MemoryRow { id: string; content: string; updated: string; pending: boolean }

/** One project: its chats, the files they changed, tasks, memories and settings. */
export default function ProjectDetail() {
  const { projectId = '', tab: rawTab } = useParams();
  const tab: Tab = (TABS as readonly string[]).includes(rawTab || '') ? rawTab as Tab : 'chats';
  const navigate = useNavigate();
  const options = useProjectOptions();
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState('');
  const [newAgent, setNewAgent] = useState('');
  const [changes, setChanges] = useState<ProjectChanges | null>(null);

  const load = useCallback(async () => {
    setError('');
    try {
      setProject(await getProject(projectId));
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [projectId]);
  useEffect(() => { setProject(null); void load(); }, [load]);

  const loadChanges = useCallback(async () => {
    try {
      setChanges(await projectChanges(projectId));
    } catch {
      setChanges({ chats: [], files: 0, added: 0, removed: 0, pending: 0 });
    }
  }, [projectId]);
  useEffect(() => { void loadChanges(); }, [loadChanges]);

  const agentName = useMemo(() => new Map(options.agents.map((a) => [a.id, a.name])), [options.agents]);
  if (error && !project) return <ErrorState message={error} onRetry={() => void load()} />;
  if (!project) return <LoadingState label={tr('ui.projects.loading')} />;

  const chats = Array.isArray(project.chats) ? project.chats : [];
  const where = computerLabel(project, options.devices);
  const go = (t: Tab) => navigate(t === 'chats' ? `/projects/${project.id}` : `/projects/${project.id}/${t}`);
  const startChat = () => navigate(newProjectChatHref(project, newAgent || undefined));

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-bg text-fg">
      <header className="app-page-header flex min-h-14 flex-none items-center gap-2 border-b border-line py-2 pl-16 pr-4 md:px-6">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 font-mono text-[11px] text-fg-dim">
            <Link to="/projects" className="hover:text-fg">{tr('ui.projects.projects')}</Link><span aria-hidden>/</span>
          </div>
          <h1 className="flex min-w-0 items-center gap-2 text-xl font-semibold tracking-tight">
            <span className="h-3 w-3 flex-none rounded-full" style={{ background: projectSwatch(project) }} />
            <span className="truncate">{project.name}</span>
          </h1>
        </div>
        <span className="hidden items-center gap-1 rounded-full border border-line px-2.5 py-1 font-mono text-[11px] text-fg-dim sm:inline-flex"
          title={tr('ui.projects.computer')}>
          {where.pc ? <PcIcon className="h-3 w-3" /> : <BoxIcon className="h-3 w-3" />}{where.label}
        </span>
        {project.agents.length > 1 && (
          <select value={newAgent || project.agents[0]} onChange={(e) => setNewAgent(e.target.value)} aria-label={tr('ui.projects.chatWith')}
            className="hidden h-8 max-w-[160px] rounded-lg border border-line bg-bg px-2 text-[12.5px] md:block">
            {project.agents.map((id) => <option key={id} value={id}>{agentName.get(id) || id}</option>)}
          </select>
        )}
        <Button size="sm" onClick={startChat}>{tr('ui.projects.newChat')}</Button>
      </header>
      <nav role="tablist" aria-label={tr('ui.projects.sections')} className="flex flex-none gap-1 overflow-x-auto border-b border-line px-3 md:px-5">
        {TABS.map((t) => (
          <button key={t} role="tab" type="button" aria-selected={tab === t} onClick={() => go(t)}
            className={cn('-mb-px min-h-11 whitespace-nowrap border-b-2 px-3 text-[13px] md:min-h-10',
              tab === t ? 'border-accent font-medium text-fg' : 'border-transparent text-fg-dim hover:text-fg')}>
            {TAB_LABEL[t]()}
            {t === 'chats' && chats.length > 0 && <span className="ml-1.5 font-mono text-[11px] text-fg-dim">{chats.length}</span>}
            {t === 'changes' && (changes?.pending ?? 0) > 0 && (
              <span className="ml-1.5 rounded-full bg-accent/15 px-1.5 font-mono text-[10.5px] text-accent-ink">{changes?.pending}</span>
            )}
          </button>
        ))}
      </nav>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === 'chats' && <ChatsTab project={project} chats={chats} agentName={agentName} onChanged={load} onNew={startChat} />}
        {tab === 'changes' && <ChangesTab changes={changes} onRefresh={loadChanges} />}
        {tab === 'tasks' && <TasksTab project={project} />}
        {tab === 'memory' && <MemoryTab projectId={project.id} />}
        {tab === 'settings' && (
          <SettingsTab project={project} options={options} onSaved={setProject}
            onDeleted={() => navigate('/projects')} />
        )}
      </div>
    </div>
  );
}

function ChatsTab({ project, chats, agentName, onChanged, onNew }: {
  project: Project; chats: ProjectChat[]; agentName: Map<string, string>; onChanged: () => void; onNew: () => void;
}) {
  if (chats.length === 0) {
    return (
      <div className="mx-auto max-w-md px-6 py-16 text-center">
        <p className="text-[15px] font-medium">{tr('ui.projects.noChatsYet')}</p>
        <p className="mt-1 text-[13px] text-fg-dim">{tr('ui.projects.noChatsDesc')}</p>
        <Button className="mt-4" onClick={onNew}>{tr('ui.projects.newChat')}</Button>
      </div>
    );
  }
  return (
    <ul>
      {chats.map((c) => (
        <li key={c.id} className="group flex items-center border-b border-line hover:bg-panel-2">
          <Link to={chatHref({ id: c.id, kind: c.kind === 'team' ? 'team' : 'agent', team_id: c.team_id })}
            className="flex min-w-0 flex-1 items-center gap-3 px-4 py-3 md:px-6">
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13.5px] font-medium group-hover:text-accent-ink">{c.title || tr('ui.projects.untitled')}</span>
              <span className="block truncate font-mono text-[11px] text-fg-dim">
                {(c.agent_id && agentName.get(c.agent_id)) || tr('ui.projects.team')} · {trn(c.message_count, 'ui.projects.messages_one', 'ui.projects.messages_other')}
              </span>
            </span>
            <span className="flex-none font-mono text-[11px] text-fg-dim">{timeAgo(c.updated_at)}</span>
          </Link>
          <ChatRowMenu compact label={tr('ui.projects.moreFor', { name: c.title })} actions={[
            { label: tr('ui.projects.removeFromProject'), onSelect: () => void moveChatToProject(c.id, null).then(onChanged) },
          ]} />
        </li>
      ))}
      <li className="px-4 py-3 font-mono text-[11px] text-fg-dim md:px-6">{tr('ui.projects.chatsFootnote', { name: project.name })}</li>
    </ul>
  );
}

/** Every chat's file changes in one place: pick a chat, then review its diffs. */
function ChangesTab({ changes, onRefresh }: { changes: ProjectChanges | null; onRefresh: () => void }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);
  const navigate = useNavigate();
  const chats = changes?.chats ?? [];
  const current = selected ?? chats[0]?.session_id ?? null;
  const state = useChatChanges(current, false);
  // An answer here changes the project's totals too.
  const act: typeof state.act = async (body) => {
    const ok = await state.act(body);
    onRefresh();
    return ok;
  };

  if (changes === null) return <LoadingState label={tr('ui.projects.loading')} />;
  if (chats.length === 0) {
    return (
      <div className="mx-auto max-w-md px-6 py-16 text-center">
        <p className="text-[15px] font-medium">{tr('ui.projects.noChanges')}</p>
        <p className="mt-1 text-[13px] text-fg-dim">{tr('ui.projects.noChangesDesc')}</p>
      </div>
    );
  }
  return (
    <div className="flex h-full min-h-[480px] flex-col md:flex-row">
      <aside className="flex-none border-b border-line md:w-72 md:border-b-0 md:border-r">
        <p className="px-4 py-2.5 font-mono text-[11px] text-fg-dim">
          {trn(changes.files, 'ui.projects.files_one', 'ui.projects.files_other')} · <span className="text-success-ink">+{changes.added}</span>{' '}
          <span className="text-danger-ink">−{changes.removed}</span> · {tr('ui.projects.pendingCount', { n: changes.pending })}
        </p>
        <ul className="max-h-56 overflow-y-auto md:max-h-none">
          {chats.map((c) => (
            <li key={c.session_id}>
              <button type="button" onClick={() => setSelected(c.session_id)} aria-current={c.session_id === current ? 'true' : undefined}
                className={cn('flex min-h-11 w-full flex-col items-start gap-0.5 px-4 py-2 text-left hover:bg-panel-2', c.session_id === current && 'bg-panel-2')}>
                <span className="w-full truncate text-[13px] font-medium">{c.title || tr('ui.projects.untitled')}</span>
                <span className="font-mono text-[11px] text-fg-dim">
                  {trn(c.files, 'ui.projects.files_one', 'ui.projects.files_other')} · +{c.added} −{c.removed}
                  {c.pending > 0 && <> · {tr('ui.projects.pendingCount', { n: c.pending })}</>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <div className="min-h-0 min-w-0 flex-1">
        {current && (
          <ChangesPanel
            key={current}
            state={{ ...state, act }}
            expanded={expanded}
            onToggleExpand={() => setExpanded((v) => !v)}
            onClose={() => navigate(chatHref({ id: current, kind: 'agent' }))}
            onCopyBack={() => navigate(chatHref({ id: current, kind: 'agent' }))}
          />
        )}
      </div>
    </div>
  );
}

function TasksTab({ project }: { project: Project }) {
  const tasks = project.tasks ?? [];
  return (
    <div>
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5 md:px-6">
        <p className="text-[12.5px] text-fg-dim">{tr('ui.projects.tasksHint')}</p>
        <Button asChild size="sm" variant="outline"><Link to={`/tasks/new?project=${encodeURIComponent(project.id)}`}>{tr('ui.projects.newTask')}</Link></Button>
      </div>
      {tasks.length === 0 ? (
        <p className="px-6 py-10 text-center text-[13px] text-fg-dim">{tr('ui.projects.noTasks')}</p>
      ) : (
        <ul>
          {tasks.map((t) => (
            <li key={t.id}>
              <Link to={`/tasks/${t.id}`} className="flex items-center gap-3 border-b border-line px-4 py-3 hover:bg-panel-2 md:px-6">
                <span className="min-w-0 flex-1 truncate text-[13.5px]">{t.title}</span>
                <span className="font-mono text-[11px] text-fg-dim">{t.status}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MemoryTab({ projectId }: { projectId: string }) {
  const [rows, setRows] = useState<MemoryRow[] | null>(null);
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    try {
      const { data } = await api.get<{ memories: MemoryRow[] }>(`/projects/${projectId}/memories`);
      setRows(data?.memories ?? []);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [projectId]);
  useEffect(() => { void load(); }, [load]);
  const add = async () => {
    if (!text.trim()) return;
    setError('');
    try {
      await api.post(`/projects/${projectId}/memories`, { content: text.trim() });
      setText('');
      await load();
    } catch (e) {
      setError(errorMessage(e));
    }
  };
  const forget = async (id: string) => {
    try {
      await api.delete(`/memories/${id}`);
      await load();
    } catch (e) {
      setError(errorMessage(e));
    }
  };
  return (
    <div className="mx-auto max-w-3xl px-4 py-5 md:px-6">
      <p className="text-[12.5px] text-fg-dim">{tr('ui.projects.memoryHint')}</p>
      <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); void add(); }}>
        <Input value={text} maxLength={300} onChange={(e) => setText(e.target.value)} placeholder={tr('ui.projects.memoryPlaceholder')}
          aria-label={tr('ui.projects.addMemory')} />
        <Button type="submit" size="sm" disabled={!text.trim()}>{tr('ui.projects.add')}</Button>
      </form>
      {error && <p role="alert" className="mt-2 text-[12px] text-danger-ink">{error}</p>}
      <ul className="mt-4 divide-y divide-line rounded-xl border border-line">
        {rows === null ? (
          <li className="px-4 py-3 text-[13px] text-fg-dim">{tr('ui.projects.loading')}</li>
        ) : rows.length === 0 ? (
          <li className="px-4 py-3 text-[13px] text-fg-dim">{tr('ui.projects.noMemories')}</li>
        ) : rows.map((m) => (
          <li key={m.id} className="flex items-start gap-3 px-4 py-2.5">
            <span className="min-w-0 flex-1 text-[13px]">{m.content}{m.pending && <span className="ml-2 font-mono text-[10.5px] text-fg-dim">{tr('ui.projects.waitingForOk')}</span>}</span>
            <button type="button" onClick={() => void forget(m.id)} className="flex-none text-[12px] text-fg-dim hover:text-danger-ink">{tr('ui.projects.forget')}</button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function SettingsTab({ project, options, onSaved, onDeleted }: {
  project: Project; options: ReturnType<typeof useProjectOptions>; onSaved: (p: Project) => void; onDeleted: () => void;
}) {
  const initial = (): ProjectInput => ({
    name: project.name, description: project.description, instructions: project.instructions, color: project.color ?? '',
    agents: project.agents, folders: project.folders, knowledge: project.knowledge, computer: project.computer ?? '',
    pc_folders: project.pc_folders, use_account_memory: project.use_account_memory,
  });
  const [value, setValue] = useState<ProjectInput>(initial);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const save = async () => {
    setBusy(true);
    setNote('');
    try {
      const saved = await updateProject(project.id, value);
      onSaved({ ...project, ...saved, chats: project.chats, tasks: project.tasks });
      setNote(tr('ui.projects.saved'));
    } catch (e) {
      setNote(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!window.confirm(tr('ui.projects.deleteConfirm', { name: project.name }))) return;
    await deleteProject(project.id);
    onDeleted();
  };
  return (
    <div className="mx-auto max-w-2xl px-4 py-5 md:px-6">
      <ProjectFields value={value} onChange={setValue} options={options} compact />
      <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-line pt-4">
        <Button size="sm" disabled={busy || !value.name?.trim()} onClick={() => void save()}>{busy ? tr('ui.projects.saving') : tr('ui.projects.save')}</Button>
        <Button size="sm" variant="outline" onClick={() => setValue(initial())}>{tr('ui.projects.reset')}</Button>
        {note && <span role="status" className="text-[12px] text-fg-dim">{note}</span>}
        <span className="grow" />
        <Button size="sm" variant="outline" onClick={() => void updateProject(project.id, { archived: !project.archived }).then((p) => onSaved({ ...project, ...p, chats: project.chats, tasks: project.tasks }))}>
          {project.archived ? tr('ui.projects.unarchive') : tr('ui.projects.archive')}
        </Button>
        <Button size="sm" variant="outline" className="text-danger-ink" onClick={() => void remove()}>{tr('ui.projects.delete')}</Button>
      </div>
      <p className="mt-3 text-[11.5px] text-fg-dim">{tr('ui.projects.settingsFootnote')}</p>
    </div>
  );
}
