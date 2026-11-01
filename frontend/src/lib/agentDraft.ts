import { folderName, type WorkFolder } from '@/lib/folders';

export type AgentLevel = 'auto' | 'ask' | 'allow';
export type ToolLevel = 'auto' | 'allow' | 'ask' | 'deny';
export type ReviewerMode = '' | 'off' | 'unattended' | 'always';
export type CodingMode = '' | 'read_only' | 'edit' | 'full';

export interface ToolInfo {
  name: string;
  description?: string;
  category?: string;
  default_permission?: 'asks' | 'runs' | 'depends';
  [key: string]: unknown;
}

/** The agent as the API returns it. */
export interface AgentRecord {
  id: string;
  name: string;
  description?: string;
  system_prompt?: string;
  llm?: {
    provider?: string;
    model?: string;
    temperature?: number;
    max_tokens?: number | null;
    base_url?: string | null;
    reasoning_effort?: string | null;
  };
  tools?: ToolInfo[];
  mcp_servers?: string[];
  folders?: WorkFolder[] | null;
  can_request_folders?: boolean;
  permission_level?: AgentLevel;
  tool_permissions?: Record<string, ToolLevel>;
  reviewer?: { mode?: string; provider?: string; model?: string; max_allow?: string };
  coding_agents?: { clis?: string[]; max_mode?: string };
  credentials?: string[] | null;
  can_create_sub_agents?: boolean;
  sub_agent_model?: string;
  automation?: { self_checkins?: boolean; max_runs_per_day?: number | null };
}

/** Everything the tabs can edit. Saved together with one button. */
export interface AgentDraft {
  name: string;
  description: string;
  provider: string;
  model: string;
  reasoning: string;
  temperature: number;
  maxTokens: string;
  baseUrl: string;
  systemPrompt: string;
  tools: string[];
  toolPermissions: Record<string, ToolLevel>;
  permissionLevel: AgentLevel;
  reviewerMode: ReviewerMode;
  reviewerProvider: string;
  reviewerModel: string;
  reviewerMaxAllow: '' | 'medium' | 'high';
  codingClis: string[] | null;
  codingMaxMode: CodingMode;
  folders: WorkFolder[] | null;
  canRequestFolders: boolean;
  mcpServers: string[];
  credentials: string[] | null;
  canCreateSubAgents: boolean;
  subAgentModel: string;
  selfCheckins: boolean;
  maxRunsPerDay: string;
}

export function draftFrom(agent: AgentRecord): AgentDraft {
  const reviewer = agent.reviewer || {};
  const coding = agent.coding_agents || {};
  const auto = agent.automation || {};
  return {
    name: agent.name || '',
    description: agent.description || '',
    provider: agent.llm?.provider || 'openai',
    model: agent.llm?.model || '',
    reasoning: agent.llm?.reasoning_effort || '',
    temperature: agent.llm?.temperature ?? 0.4,
    maxTokens: agent.llm?.max_tokens ? String(agent.llm.max_tokens) : '',
    baseUrl: agent.llm?.base_url || '',
    systemPrompt: agent.system_prompt || '',
    tools: (agent.tools || []).map((t) => t.name),
    toolPermissions: { ...(agent.tool_permissions || {}) },
    permissionLevel: agent.permission_level || 'auto',
    reviewerMode: (['off', 'unattended', 'always'].includes(reviewer.mode || '') ? reviewer.mode : '') as ReviewerMode,
    reviewerProvider: reviewer.provider || '',
    reviewerModel: reviewer.model || '',
    reviewerMaxAllow: (reviewer.max_allow === 'medium' || reviewer.max_allow === 'high') ? reviewer.max_allow : '',
    codingClis: Array.isArray(coding.clis) ? [...coding.clis] : null,
    codingMaxMode: (['read_only', 'edit', 'full'].includes(coding.max_mode || '') ? coding.max_mode : '') as CodingMode,
    folders: agent.folders ?? null,
    canRequestFolders: agent.can_request_folders ?? true,
    mcpServers: [...(agent.mcp_servers || [])],
    credentials: Array.isArray(agent.credentials) ? [...agent.credentials] : null,
    canCreateSubAgents: agent.can_create_sub_agents ?? true,
    subAgentModel: agent.sub_agent_model || '',
    selfCheckins: auto.self_checkins ?? true,
    maxRunsPerDay: auto.max_runs_per_day ? String(auto.max_runs_per_day) : '',
  };
}

/** The save request for POST /agents. */
export function payloadFrom(id: string, draft: AgentDraft, catalog: ToolInfo[], current: ToolInfo[]): Record<string, unknown> {
  const byName = new Map<string, ToolInfo>();
  for (const t of current) byName.set(t.name, t);
  for (const t of catalog) byName.set(t.name, t);
  const tools = draft.tools
    .map((name) => byName.get(name))
    .filter((t): t is ToolInfo => Boolean(t))
    .map((t) => {
      const rest = { ...t };
      delete rest.default_permission;
      return rest;
    });
  const reviewer: Record<string, string> = {};
  if (draft.reviewerMode) reviewer.mode = draft.reviewerMode;
  if (draft.reviewerProvider) {
    reviewer.provider = draft.reviewerProvider;
    if (draft.reviewerModel.trim()) reviewer.model = draft.reviewerModel.trim();
  }
  if (draft.reviewerMaxAllow) reviewer.max_allow = draft.reviewerMaxAllow;
  const coding: Record<string, unknown> = {};
  if (draft.codingClis !== null) coding.clis = draft.codingClis;
  if (draft.codingMaxMode) coding.max_mode = draft.codingMaxMode;
  const automation: Record<string, unknown> = {};
  if (!draft.selfCheckins) automation.self_checkins = false;
  if (draft.maxRunsPerDay.trim()) automation.max_runs_per_day = Number(draft.maxRunsPerDay);
  return {
    id,
    name: draft.name.trim(),
    description: draft.description.trim(),
    system_prompt: draft.systemPrompt,
    // api_key is left out: the server keeps the stored one or uses the engine's.
    llm: {
      provider: draft.provider,
      model: draft.model.trim(),
      temperature: draft.temperature,
      reasoning_effort: draft.reasoning,
      ...(draft.baseUrl ? { base_url: draft.baseUrl } : {}),
      ...(draft.maxTokens ? { max_tokens: Number(draft.maxTokens) } : {}),
    },
    tools,
    // Levels for tools the agent no longer has are dropped, except MCP tools,
    // which aren't in the catalog but can still be set by name.
    tool_permissions: Object.fromEntries(
      Object.entries(draft.toolPermissions).filter(([n]) => draft.tools.includes(n) || !catalog.some((t) => t.name === n)),
    ),
    permission_level: draft.permissionLevel,
    reviewer,
    coding_agents: coding,
    folders: draft.folders,
    can_request_folders: draft.canRequestFolders,
    mcp_servers: draft.mcpServers,
    credentials: draft.credentials,
    can_create_sub_agents: draft.canCreateSubAgents,
    sub_agent_model: draft.subAgentModel.trim(),
    automation,
  };
}

/** What the draft would fail to save with, or ''. */
export function draftProblem(draft: AgentDraft): string {
  if (!draft.name.trim()) return 'Give the agent a name (General tab).';
  if (!draft.model.trim()) return 'Choose a model (General tab).';
  if (draft.maxTokens && !(Number(draft.maxTokens) > 0)) return 'Max tokens must be a positive number (General tab).';
  if (draft.maxRunsPerDay.trim()) {
    const n = Number(draft.maxRunsPerDay);
    if (!Number.isInteger(n) || n < 1 || n > 1440) return 'Most runs per day must be a whole number from 1 to 1440 (Tasks tab).';
  }
  return '';
}

/** A readable summary of what this agent's settings add to its prompt. */
export function promptSummary(draft: AgentDraft, teamLine: string): string {
  const lines = [`You are ${draft.name || 'this agent'} (${handleFor(draft.name || 'agent')}).`];
  if (draft.description.trim()) lines.push(draft.description.trim());
  lines.push('', '[your instructions]', '');
  if (draft.folders === null) lines.push("Folders: the app's working folder (read & write).");
  else if (draft.folders.length === 0) lines.push('Folders: a private scratch folder for each chat.');
  else lines.push(`Folders: ${draft.folders.map((f) => `${folderName(f)} (${f.access === 'write' ? 'read & write' : 'read only'}${f.primary ? ', primary' : ''})`).join(', ')}.`);
  const asks = draft.tools.filter((t) => draft.toolPermissions[t] === 'ask');
  const never = draft.tools.filter((t) => draft.toolPermissions[t] === 'deny');
  let tools = `Tools: ${draft.tools.length - never.length} available.`;
  if (asks.length) tools += ` Ask first: ${asks.join(', ')}.`;
  if (never.length) tools += ` Never: ${never.join(', ')} (hidden from the model).`;
  lines.push(tools);
  if (draft.mcpServers.length) lines.push(`MCP servers: ${draft.mcpServers.length} connected.`);
  if (teamLine) lines.push(teamLine);
  return lines.join('\n');
}

/** @handle used for mentions in team chats (spaces become dashes). */
export const handleFor = (name: string) => `@${name.trim().replace(/\s+/g, '-')}`;

export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return '?';
  return (words.length === 1 ? words[0].slice(0, 2) : words[0][0] + words[1][0]).toUpperCase();
}

/** "2 h ago" for a UTC 'YYYY-MM-DD HH:MM:SS' (or ISO) time. */
export function timeAgo(value: string | null | undefined, now = Date.now()): string {
  const t = parseUtc(value);
  if (t === null) return '';
  const diff = Math.round((now - t) / 1000);
  if (diff < 0) return timeUntil(value, now);
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  if (diff < 86400 * 2) return 'yesterday';
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} days ago`;
  return new Date(t).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function timeUntil(value: string | null | undefined, now = Date.now()): string {
  const t = parseUtc(value);
  if (t === null) return '';
  const diff = Math.round((t - now) / 1000);
  if (diff <= 60) return 'any moment';
  if (diff < 3600) return `in ${Math.round(diff / 60)} min`;
  if (diff < 86400) {
    const minutes = Math.round(diff / 60);
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    return m ? `in ${h} h ${m} min` : `in ${h} h`;
  }
  return new Date(t).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' });
}

export function parseUtc(value: string | null | undefined): number | null {
  if (!value) return null;
  let text = String(value).trim();
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(text)) text = text.replace(' ', 'T');
  if (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(text)) text += 'Z';
  const t = Date.parse(text);
  return Number.isNaN(t) ? null : t;
}

/** A plain description of a task's schedule. */
export function scheduleText(task: {
  schedule_enabled?: boolean; schedule_at?: string | null; schedule_interval?: number | null; schedule_cron?: string | null;
}): string {
  if (!task.schedule_enabled) return 'when you run it';
  if (task.schedule_cron) return `cron ${task.schedule_cron}`;
  if (task.schedule_interval) {
    const s = task.schedule_interval;
    if (s === 604800) return 'every week';
    if (s % 86400 === 0) return s === 86400 ? 'every day' : `every ${s / 86400} days`;
    if (s % 3600 === 0) return s === 3600 ? 'every hour' : `every ${s / 3600} hours`;
    return `every ${Math.round(s / 60)} min`;
  }
  if (task.schedule_at) return 'once';
  return 'scheduled';
}

export interface Overview {
  stats: { chats_week: number; running_chats: number; task_runs_week: number; failed_runs_week: number; pending_approvals: number };
  recent_chats: { id: string; title: string; last_active: string; running: boolean }[];
  tasks: OverviewTask[];
  approvals: OverviewApproval[];
  teams: { id: string; name: string; description?: string; leader_id?: string | null; members: { id: string; name: string }[] }[];
}

export interface OverviewTask {
  id: string;
  title: string;
  status: string;
  schedule_enabled: boolean;
  schedule_at?: string | null;
  schedule_interval?: number | null;
  schedule_cron?: string | null;
  next_run_at?: string | null;
  wake_at?: string | null;
  wake_reason?: string | null;
  max_runs_per_day?: number | null;
  last_run_at?: string | null;
  last_run_status?: string | null;
}

export interface OverviewApproval {
  id: string;
  run_id: string;
  task_id: string;
  task_title: string;
  tool_name: string;
  params: Record<string, unknown>;
  risk_level: string;
  details: string;
}

export interface AgentOptions {
  reviewer: { mode: string; provider: string; model: string; max_allow: string };
  coding_clis: { key: string; label: string; command: string; installed: boolean }[];
  default_max_runs_per_day: number;
}

export type TabId = 'overview' | 'general' | 'tools' | 'folders' | 'connections' | 'tasks' | 'teams';

export const TABS: { id: TabId; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'general', label: 'General' },
  { id: 'tools', label: 'Tools & approvals' },
  { id: 'folders', label: 'Working folders' },
  { id: 'connections', label: 'Connections' },
  { id: 'tasks', label: 'Tasks' },
  { id: 'teams', label: 'Teams' },
];

export interface TabProps {
  agent: AgentRecord;
  draft: AgentDraft;
  update: (patch: Partial<AgentDraft>) => void;
  overview: Overview | null;
  overviewError: string | null;
  refreshOverview: () => void;
  options: AgentOptions | null;
  catalog: ToolInfo[];
  goTo: (tab: TabId) => void;
  /** Open the chat page on this agent, optionally on one chat. */
  openChat: (sessionId?: string) => void;
  remove: () => void;
}
