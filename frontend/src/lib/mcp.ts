import { api } from '@/lib/api';

export type McpTransport = 'http' | 'sse' | 'stdio';

export interface McpServer {
  /** Database id; null for servers from the CLI's mcp.json. */
  id: string | null;
  /** What agents store in mcp_servers: the id, or the name for mcp.json servers. */
  ref: string;
  name: string;
  description: string;
  transport: McpTransport;
  url: string | null;
  command: string | null;
  args: string[];
  /** Names only: header and env values never come back from the server. */
  header_names: string[];
  env_names: string[];
  timeout: number;
  enabled: boolean;
  shared: boolean;
  source: 'database' | 'config_file';
  can_edit: boolean;
  connected: boolean;
  /** Set for connected apps (Settings › Connected apps). */
  catalog_id?: string | null;
  auth_type?: 'none' | 'header' | 'oauth';
  /** 'connected', 'needs_reauth', or '' (never signed in / signed out). */
  status?: string;
  account?: string;
}

export interface McpServerList {
  servers: McpServer[];
  can_add_local: boolean;
  can_add_shared: boolean;
}

export interface McpServerInput {
  name: string;
  transport: McpTransport;
  description?: string;
  url?: string | null;
  /** A value of KEEP_SECRET keeps the stored value for that name. */
  headers?: Record<string, string>;
  command?: string | null;
  args?: string[];
  env?: Record<string, string>;
  enabled?: boolean;
  shared?: boolean;
}

export interface McpToolInfo {
  name: string;
  /** The tool's name inside Cognitrix, as agents' permission settings use it. */
  tool?: string;
  title?: string;
  description: string;
  read_only: boolean;
  asks_first?: boolean;
  /** What happens with no per-agent setting: read-only tools run, others ask. */
  default?: 'allow' | 'ask';
}

export interface McpTestResult {
  connected: boolean;
  tools: McpToolInfo[];
  error: string | null;
}

/** A server's tools, connecting first if needed (for per-agent permissions). */
export async function listMcpServerTools(ref: string): Promise<McpTestResult> {
  return (await api.get<McpTestResult>(`/mcp/servers/${encodeURIComponent(ref)}/tools`)).data;
}

/** Connected apps are managed in Settings › Connected apps, not as raw servers. */
export function isConnectedApp(server: McpServer): boolean {
  return Boolean(server.catalog_id) || server.auth_type === 'oauth';
}

export const KEEP_SECRET = '***';

export async function listMcpServers(): Promise<McpServerList> {
  return (await api.get<McpServerList>('/mcp/servers')).data;
}

export async function addMcpServer(input: McpServerInput): Promise<McpServer> {
  return (await api.post<McpServer>('/mcp/servers', input)).data;
}

export async function updateMcpServer(id: string, input: Partial<McpServerInput>): Promise<McpServer> {
  return (await api.patch<McpServer>(`/mcp/servers/${encodeURIComponent(id)}`, input)).data;
}

export async function deleteMcpServer(id: string): Promise<void> {
  await api.delete(`/mcp/servers/${encodeURIComponent(id)}`);
}

export async function testMcpServer(ref: string): Promise<McpTestResult> {
  return (await api.post<McpTestResult>(`/mcp/servers/${encodeURIComponent(ref)}/test`)).data;
}
