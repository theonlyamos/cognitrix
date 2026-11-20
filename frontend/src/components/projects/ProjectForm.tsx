import { useEffect, useId, useState, type ReactNode } from 'react';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import FolderChips from '@/components/FolderChips';
import { listDevices, type PairedDevice } from '@/lib/devices';
import { tr } from '@/lib/i18n';
import {
  COLOR_SWATCH, PROJECT_COLORS, createProject, listProjects, moveChatToProject, projectSwatch,
  type Project, type ProjectInput,
} from '@/lib/projects';
import { cn } from '@/lib/utils';

interface Named { id: string; name: string }

export function useProjectOptions() {
  const [agents, setAgents] = useState<Named[]>([]);
  const [collections, setCollections] = useState<Named[]>([]);
  const [devices, setDevices] = useState<PairedDevice[]>([]);
  useEffect(() => {
    api.get<Named[]>('/agents').then((r) => setAgents((r.data ?? []).map((a) => ({ id: a.id, name: a.name })))).catch(() => {});
    api.get<Named[] | { collections: Named[] }>('/knowledge').then((r) => {
      const data = r.data as Named[] | { collections: Named[] } | undefined;
      setCollections(Array.isArray(data) ? data : data?.collections ?? []);
    }).catch(() => {});
    listDevices().then(setDevices).catch(() => {});
  }, []);
  return { agents, collections, devices };
}

const textareaClass = 'w-full rounded-lg border border-line bg-bg px-3 py-2 text-[13px] leading-relaxed text-fg placeholder:text-fg-dim focus:border-accent focus:outline-none';
const selectClass = 'h-9 w-full rounded-lg border border-line bg-bg px-2 text-[13px] text-fg';

export function Field({ label, hint, children, htmlFor }: { label: string; hint?: string; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-[12.5px] font-medium text-fg">{label}</label>
      {children}
      {hint && <p className="text-[11.5px] leading-relaxed text-fg-dim">{hint}</p>}
    </div>
  );
}

/** The project's settings: used by New project and the project's Settings tab. */
export function ProjectFields({ value, onChange, options, compact = false }: {
  value: ProjectInput;
  onChange: (next: ProjectInput) => void;
  options: ReturnType<typeof useProjectOptions>;
  compact?: boolean;
}) {
  const ids = useId();
  const set = (patch: ProjectInput) => onChange({ ...value, ...patch });
  const agents = value.agents ?? [];
  const knowledge = value.knowledge ?? [];
  const computer = value.computer ?? '';
  const device = options.devices.find((d) => d.id === computer);
  const pcFolders = value.pc_folders ?? [];
  const toggle = (list: string[], id: string) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);

  return (
    <div className="space-y-4">
      <Field label={tr('ui.projects.name')} htmlFor={`${ids}-name`}>
        <Input id={`${ids}-name`} value={value.name ?? ''} maxLength={80} onChange={(e) => set({ name: e.target.value })}
          placeholder={tr('ui.projects.namePlaceholder')} />
      </Field>
      <Field label={tr('ui.projects.description')} htmlFor={`${ids}-desc`}>
        <Input id={`${ids}-desc`} value={value.description ?? ''} maxLength={300} onChange={(e) => set({ description: e.target.value })}
          placeholder={tr('ui.projects.descriptionPlaceholder')} />
      </Field>
      <div className="space-y-1.5">
        <span className="block text-[12.5px] font-medium text-fg">{tr('ui.projects.color')}</span>
        <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label={tr('ui.projects.color')}>
          {['', ...PROJECT_COLORS].map((c) => (
            <button key={c || 'default'} type="button" role="radio" aria-checked={(value.color ?? '') === c}
              aria-label={c || tr('ui.projects.defaultColor')}
              onClick={() => set({ color: c })}
              className={cn('grid h-7 w-7 place-items-center rounded-full border transition',
                (value.color ?? '') === c ? 'border-fg' : 'border-line hover:border-fg-dim')}>
              <span className="h-4 w-4 rounded-full" style={{ background: c ? COLOR_SWATCH[c] : projectSwatch({ color: null }) }} />
            </button>
          ))}
        </div>
      </div>
      <Field label={tr('ui.projects.instructions')} hint={tr('ui.projects.instructionsHint')} htmlFor={`${ids}-instr`}>
        <textarea id={`${ids}-instr`} rows={compact ? 4 : 6} value={value.instructions ?? ''} maxLength={20000}
          onChange={(e) => set({ instructions: e.target.value })} className={textareaClass}
          placeholder={tr('ui.projects.instructionsPlaceholder')} />
      </Field>
      <div className="space-y-1.5">
        <span className="block text-[12.5px] font-medium text-fg">{tr('ui.projects.agents')}</span>
        <p className="text-[11.5px] text-fg-dim">{tr('ui.projects.agentsHint')}</p>
        <div className="flex flex-wrap gap-1.5">
          {options.agents.map((a) => {
            const on = agents.includes(a.id);
            return (
              <button key={a.id} type="button" aria-pressed={on} onClick={() => set({ agents: toggle(agents, a.id) })}
                className={cn('min-h-9 rounded-full border px-3 text-[12.5px] transition md:min-h-7',
                  on ? 'border-accent bg-accent/10 text-accent-ink' : 'border-line text-fg-dim hover:border-fg-dim hover:text-fg')}>
                {on && agents[0] === a.id ? `${a.name} · ${tr('ui.projects.default')}` : a.name}
              </button>
            );
          })}
          {options.agents.length === 0 && <span className="text-[12px] text-fg-dim">{tr('ui.projects.noAgents')}</span>}
        </div>
      </div>
      <Field label={tr('ui.projects.computer')} hint={tr('ui.projects.computerHint')} htmlFor={`${ids}-pc`}>
        <select id={`${ids}-pc`} className={selectClass} value={computer}
          onChange={(e) => set({ computer: e.target.value, pc_folders: [] })}>
          <option value="sandbox">{tr('ui.projects.sharedSandbox')}</option>
          <option value="">{tr('ui.projects.eachAgentsOwn')}</option>
          {options.devices.map((d) => <option key={d.id} value={d.id}>{d.name}{d.online ? '' : ` (${tr('ui.projects.offline')})`}</option>)}
        </select>
      </Field>
      {device ? (
        <div className="space-y-1.5">
          <span className="block text-[12.5px] font-medium text-fg">{tr('ui.projects.foldersOnPc', { name: device.name })}</span>
          <p className="text-[11.5px] text-fg-dim">{tr('ui.projects.foldersOnPcHint')}</p>
          <div className="flex flex-col gap-1">
            {(device.folders ?? []).map((f) => (
              <label key={f.path} className="flex min-h-9 items-center gap-2 font-mono text-[12px] text-fg md:min-h-7">
                <input type="checkbox" checked={pcFolders.includes(f.path)} onChange={() => set({ pc_folders: toggle(pcFolders, f.path) })} />
                <span className="truncate">{f.path}</span>
              </label>
            ))}
            {(device.folders ?? []).length === 0 && <span className="text-[12px] text-fg-dim">{tr('ui.projects.pcSharesNoFolders')}</span>}
          </div>
        </div>
      ) : (
        <div className="space-y-1.5">
          <span className="block text-[12.5px] font-medium text-fg">{tr('ui.projects.folders')}</span>
          <p className="text-[11.5px] text-fg-dim">{computer === 'sandbox' ? tr('ui.projects.foldersSandboxHint') : tr('ui.projects.foldersHint')}</p>
          <FolderChips folders={value.folders ?? []} onChange={(folders) => set({ folders })} emptyLabel={tr('ui.projects.noFolders')} />
        </div>
      )}
      {options.collections.length > 0 && (
        <div className="space-y-1.5">
          <span className="block text-[12.5px] font-medium text-fg">{tr('ui.projects.knowledge')}</span>
          <div className="flex flex-wrap gap-1.5">
            {options.collections.map((c) => {
              const on = knowledge.includes(c.id);
              return (
                <button key={c.id} type="button" aria-pressed={on} onClick={() => set({ knowledge: toggle(knowledge, c.id) })}
                  className={cn('min-h-9 rounded-full border px-3 text-[12.5px] transition md:min-h-7',
                    on ? 'border-accent bg-accent/10 text-accent-ink' : 'border-line text-fg-dim hover:border-fg-dim hover:text-fg')}>
                  {c.name}
                </button>
              );
            })}
          </div>
        </div>
      )}
      <label className="flex items-start gap-2 text-[12.5px] text-fg">
        <input type="checkbox" className="mt-0.5" checked={value.use_account_memory ?? true}
          onChange={(e) => set({ use_account_memory: e.target.checked })} />
        <span>
          {tr('ui.projects.useAccountMemory')}
          <span className="block text-[11.5px] text-fg-dim">{tr('ui.projects.useAccountMemoryHint')}</span>
        </span>
      </label>
    </div>
  );
}

function Modal({ title, onClose, children, footer, wide = false }: { title: string; onClose: () => void; children: ReactNode; footer: ReactNode; wide?: boolean }) {
  const titleId = useId();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-[75] flex items-start justify-center overflow-y-auto bg-[rgba(5,6,7,.6)] p-4 sm:items-center"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-labelledby={titleId}
        className={cn('my-8 max-w-full overflow-hidden rounded-2xl border border-line bg-panel shadow-panel', wide ? 'w-[600px]' : 'w-[420px]')}>
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <h2 id={titleId} className="text-[17px] font-semibold">{title}</h2>
          <button type="button" onClick={onClose} aria-label={tr('ui.projects.close')} className="grid h-9 w-9 place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg md:h-7 md:w-7">✕</button>
        </div>
        <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
        <div className="flex items-center justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>
      </div>
    </div>
  );
}

export function NewProjectDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (project: Project) => void }) {
  const options = useProjectOptions();
  const [value, setValue] = useState<ProjectInput>({ computer: 'sandbox', use_account_memory: true });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const create = async () => {
    if (!value.name?.trim()) return;
    setBusy(true);
    setError('');
    try {
      onCreated(await createProject(value));
    } catch (e) {
      setError(errorMessage(e, tr('ui.projects.couldNotCreate')));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal wide title={tr('ui.projects.newProject')} onClose={onClose} footer={(
      <>
        {error && <p role="alert" className="mr-auto text-[12px] text-danger-ink">{error}</p>}
        <Button type="button" variant="outline" size="sm" onClick={onClose}>{tr('ui.projects.cancel')}</Button>
        <Button type="button" size="sm" disabled={busy || !value.name?.trim()} onClick={() => void create()}>
          {busy ? tr('ui.projects.creating') : tr('ui.projects.createProject')}
        </Button>
      </>
    )}>
      <ProjectFields value={value} onChange={setValue} options={options} />
      <p className="mt-3 text-[11.5px] text-fg-dim">{tr('ui.projects.readsAgentsMd')}</p>
    </Modal>
  );
}

/** "Move to project": pick one of the person's projects, or take the chat out. */
export function MoveToProjectDialog({ chatId, title, current, onClose, onMoved }: {
  chatId: string; title: string; current?: string | null; onClose: () => void; onMoved?: (projectId: string | null) => void;
}) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { listProjects().then(setProjects).catch((e) => setError(errorMessage(e))); }, []);
  const move = async (projectId: string | null) => {
    setBusy(true);
    setError('');
    try {
      await moveChatToProject(chatId, projectId);
      onMoved?.(projectId);
      onClose();
    } catch (e) {
      setError(errorMessage(e, tr('ui.projects.couldNotMove')));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={tr('ui.projects.moveTitle', { title })} onClose={onClose} footer={(
      <>
        {error && <p role="alert" className="mr-auto text-[12px] text-danger-ink">{error}</p>}
        {current && <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void move(null)}>{tr('ui.projects.removeFromProject')}</Button>}
        <Button type="button" variant="outline" size="sm" onClick={onClose}>{tr('ui.projects.cancel')}</Button>
      </>
    )}>
      {projects === null ? (
        <p className="text-[12.5px] text-fg-dim">{tr('ui.projects.loading')}</p>
      ) : projects.length === 0 ? (
        <p className="text-[12.5px] text-fg-dim">{tr('ui.projects.noProjectsToMove')}</p>
      ) : (
        <ul className="-mx-2">
          {projects.map((p) => (
            <li key={p.id}>
              <button type="button" disabled={busy || p.id === current} onClick={() => void move(p.id)}
                className="flex min-h-11 w-full items-center gap-2.5 rounded-lg px-2 text-left text-[13.5px] hover:bg-panel-2 disabled:opacity-60 md:min-h-9">
                <span className="h-2.5 w-2.5 flex-none rounded-full" style={{ background: projectSwatch(p) }} />
                <span className="min-w-0 flex-1 truncate">{p.name}</span>
                {p.id === current && <span className="font-mono text-[10.5px] text-fg-dim">{tr('ui.projects.here')}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}
