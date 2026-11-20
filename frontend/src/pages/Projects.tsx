import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Box as BoxIcon, FolderKanban as ProjectIcon, Monitor as PcIcon, Pin as PinIcon } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { PageHeader, LoadingState, ErrorState, EmptyState } from '@/components/list-ui';
import { ChatRowMenu } from '@/components/ChatRowMenu';
import { NewProjectDialog } from '@/components/projects/ProjectForm';
import { listDevices, type PairedDevice } from '@/lib/devices';
import { timeAgo } from '@/lib/agentDraft';
import { tr, trn } from '@/lib/i18n';
import { computerLabel, deleteProject, listProjects, newProjectChatHref, projectSwatch, updateProject, type Project } from '@/lib/projects';

interface Named { id: string; name: string }

/** Projects: cards for each project. Projects are not in the sidebar; this page manages them. */
export default function Projects() {
  const navigate = useNavigate();
  const [archived, setArchived] = useState(false);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [creating, setCreating] = useState(false);
  const [agents, setAgents] = useState<Named[]>([]);
  const [devices, setDevices] = useState<PairedDevice[]>([]);

  const load = async () => {
    setError('');
    try {
      setProjects(await listProjects(archived));
    } catch (e) {
      setError(errorMessage(e));
    }
  };
  useEffect(() => { setProjects(null); void load(); }, [archived]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    api.get<Named[]>('/agents').then((r) => setAgents(r.data ?? [])).catch(() => {});
    listDevices().then(setDevices).catch(() => {});
  }, []);

  const agentName = useMemo(() => new Map(agents.map((a) => [a.id, a.name])), [agents]);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (projects ?? []).filter((p) => !q || p.name.toLowerCase().includes(q) || p.description.toLowerCase().includes(q));
  }, [projects, query]);

  const change = async (p: Project, patch: Parameters<typeof updateProject>[1]) => {
    try {
      await updateProject(p.id, patch);
      await load();
    } catch (e) {
      setError(errorMessage(e));
    }
  };
  const remove = async (p: Project) => {
    if (!window.confirm(tr('ui.projects.deleteConfirm', { name: p.name }))) return;
    try {
      await deleteProject(p.id);
      await load();
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  const count = projects?.length ?? 0;
  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-bg text-fg">
      <PageHeader title={tr('ui.projects.projects')} subtitle={trn(count, 'ui.projects.count_one', 'ui.projects.count_other')}>
        <Button size="sm" onClick={() => setCreating(true)}>{tr('ui.projects.newProject')}</Button>
      </PageHeader>
      <div className="flex flex-none flex-wrap items-center gap-2 border-b border-line px-4 py-2.5 md:px-6">
        <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={tr('ui.projects.search')}
          aria-label={tr('ui.projects.search')} className="h-9 w-full max-w-[260px] md:h-8" />
        <div role="tablist" className="ml-auto flex rounded-lg border border-line p-0.5 text-[12px]">
          {[false, true].map((a) => (
            <button key={String(a)} role="tab" aria-selected={archived === a} type="button" onClick={() => setArchived(a)}
              className={`min-h-9 rounded-md px-3 md:min-h-7 ${archived === a ? 'bg-panel-2 font-medium text-fg' : 'text-fg-dim hover:text-fg'}`}>
              {a ? tr('ui.projects.archived') : tr('ui.projects.active')}
            </button>
          ))}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto">
        {error && <p role="alert" className="border-b border-line px-6 py-2 text-[12.5px] text-danger-ink">{error}</p>}
        {projects === null && !error ? (
          <LoadingState label={tr('ui.projects.loading')} />
        ) : projects === null ? (
          <ErrorState message={error} onRetry={() => void load()} />
        ) : projects.length === 0 ? (
          <EmptyState
            icon={<ProjectIcon className="h-5 w-5" />}
            title={archived ? tr('ui.projects.noArchived') : tr('ui.projects.noProjectsYet')}
            desc={tr('ui.projects.emptyDesc')}
            action={!archived ? <Button onClick={() => setCreating(true)}>{tr('ui.projects.createFirst')}</Button> : undefined}
          />
        ) : (
          <ul className="grid gap-3 p-4 sm:grid-cols-2 md:p-6 xl:grid-cols-3">
            {shown.map((p) => {
              const chats = typeof p.chats === 'number' ? p.chats : 0;
              const where = computerLabel(p, devices);
              const names = p.agents.map((id) => agentName.get(id)).filter(Boolean) as string[];
              return (
                <li key={p.id} className="group relative flex min-h-[150px] flex-col rounded-xl border border-line bg-panel transition-colors hover:border-fg-dim">
                  <Link to={`/projects/${p.id}`} className="flex flex-1 flex-col gap-2 p-4 pr-12">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="h-2.5 w-2.5 flex-none rounded-full" style={{ background: projectSwatch(p) }} />
                      <span className="truncate text-[15px] font-semibold group-hover:text-accent-ink">{p.name}</span>
                      {p.pinned && <PinIcon aria-label={tr('ui.projects.pinned')} className="h-3 w-3 flex-none text-fg-dim" />}
                    </div>
                    <p className="line-clamp-2 min-h-[2.6em] text-[12.5px] leading-snug text-fg-dim">
                      {p.description || p.instructions.split('\n')[0] || tr('ui.projects.noDescription')}
                    </p>
                    <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] text-fg-dim">
                      <span>{trn(chats, 'ui.projects.chats_one', 'ui.projects.chats_other')}</span>
                      <span className="inline-flex items-center gap-1">
                        {where.pc ? <PcIcon className="h-3 w-3" /> : <BoxIcon className="h-3 w-3" />}{where.label}
                      </span>
                      {names.length > 0 && <span className="truncate">{names.slice(0, 3).join(', ')}{names.length > 3 ? ` +${names.length - 3}` : ''}</span>}
                      {p.last_active && <span>{timeAgo(p.last_active)}</span>}
                    </div>
                  </Link>
                  <div className="absolute right-2 top-2.5">
                    <ChatRowMenu compact label={tr('ui.projects.moreFor', { name: p.name })} actions={[
                      { label: tr('ui.projects.newChat'), onSelect: () => navigate(newProjectChatHref(p)) },
                      { label: p.pinned ? tr('ui.projects.unpin') : tr('ui.projects.pin'), onSelect: () => void change(p, { pinned: !p.pinned }) },
                      { label: p.archived ? tr('ui.projects.unarchive') : tr('ui.projects.archive'), onSelect: () => void change(p, { archived: !p.archived }) },
                      { label: tr('ui.projects.delete'), danger: true, onSelect: () => void remove(p) },
                    ]} />
                  </div>
                </li>
              );
            })}
            {!archived && (
              <li>
                <button type="button" onClick={() => setCreating(true)}
                  className="flex h-full min-h-[150px] w-full flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-line text-[13px] text-fg-dim hover:border-fg-dim hover:text-fg">
                  <span className="text-xl leading-none">+</span>{tr('ui.projects.newProject')}
                </button>
              </li>
            )}
          </ul>
        )}
      </div>
      {creating && (
        <NewProjectDialog onClose={() => setCreating(false)} onCreated={(p) => { setCreating(false); navigate(`/projects/${p.id}`); }} />
      )}
    </div>
  );
}
