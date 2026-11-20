import { api } from '@/lib/api';
import type { WorkFolder } from '@/lib/folders';
import type { ChangeSource } from '@/lib/fileChanges';
import type { PairedDevice } from '@/lib/devices';
import { tr } from '@/lib/i18n';

/** '' each agent's own computer, 'sandbox' one shared sandbox, else a paired PC's id. */
export type ProjectComputer = string | null;

export interface ProjectChat {
  id: string;
  title: string;
  agent_id: string | null;
  team_id: string | null;
  kind: string;
  pinned: boolean;
  archived: boolean;
  message_count: number;
  updated_at: string;
}

export interface ProjectTask {
  id: string;
  title: string;
  status: string;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  instructions: string;
  color: string | null;
  agents: string[];
  folders: WorkFolder[];
  knowledge: string[];
  computer: ProjectComputer;
  pc_folders: string[];
  use_account_memory: boolean;
  pinned: boolean;
  archived: boolean;
  created: string;
  updated: string;
  /** On the list: how many chats, and when one last changed. */
  chats?: number | ProjectChat[];
  last_active?: string;
  /** On one project. */
  tasks?: ProjectTask[];
}

export interface ProjectChanges {
  chats: {
    session_id: string;
    title: string;
    agent_id: string | null;
    updated_at: string;
    files: number;
    added: number;
    removed: number;
    pending: number;
    sources: ChangeSource[];
  }[];
  files: number;
  added: number;
  removed: number;
  pending: number;
}

export type ProjectInput = Partial<Pick<Project,
  'name' | 'description' | 'instructions' | 'color' | 'agents' | 'folders' | 'knowledge' | 'computer' |
  'pc_folders' | 'use_account_memory' | 'pinned' | 'archived'>>;

export const PROJECT_COLORS = ['blue', 'green', 'amber', 'red', 'violet', 'teal', 'pink', 'slate'] as const;

/** A swatch for each project colour (works on light and dark themes). */
export const COLOR_SWATCH: Record<string, string> = {
  blue: '#3b82f6', green: '#22a06b', amber: '#d97706', red: '#e5484d',
  violet: '#8b5cf6', teal: '#14b8a6', pink: '#e93d82', slate: '#64748b',
};

export function projectSwatch(project: Pick<Project, 'color'>): string {
  return COLOR_SWATCH[project.color || ''] || 'var(--accent, #3b82f6)';
}

export async function listProjects(archived = false): Promise<Project[]> {
  const { data } = await api.get<{ projects: Project[] }>(`/projects${archived ? '?archived=true' : ''}`);
  return data?.projects ?? [];
}

export async function getProject(id: string): Promise<Project> {
  const { data } = await api.get<Project>(`/projects/${id}`);
  return data;
}

export async function createProject(input: ProjectInput): Promise<Project> {
  const { data } = await api.post<Project>('/projects', input);
  return data;
}

export async function updateProject(id: string, input: ProjectInput): Promise<Project> {
  const { data } = await api.patch<Project>(`/projects/${id}`, input);
  return data;
}

export async function deleteProject(id: string, deleteChats = false): Promise<void> {
  await api.delete(`/projects/${id}${deleteChats ? '?delete_chats=true' : ''}`);
}

export async function moveChatToProject(sessionId: string, projectId: string | null): Promise<void> {
  await api.put(`/projects/chats/${sessionId}`, { project_id: projectId });
}

export async function projectChanges(id: string): Promise<ProjectChanges> {
  const { data } = await api.get<ProjectChanges>(`/projects/${id}/changes`);
  return data;
}

/** Where a new chat in the project goes. */
export function newProjectChatHref(project: Pick<Project, 'id' | 'agents'>, agentId?: string): string {
  const agent = agentId || project.agents[0];
  const start = agent ? `agent:${agent}` : '1';
  return `/home?new=${encodeURIComponent(start)}&project=${encodeURIComponent(project.id)}`;
}

/** Where the project's agents work, in a few words. */
export function computerLabel(project: Project, devices: PairedDevice[]): { label: string; pc: boolean } {
  if (project.computer === 'sandbox') return { label: tr('ui.projects.sharedSandbox'), pc: false };
  if (!project.computer) return { label: tr('ui.projects.eachAgentsOwn'), pc: false };
  const device = devices.find((d) => d.id === project.computer);
  return { label: device?.name || tr('ui.projects.pairedPc'), pc: true };
}

