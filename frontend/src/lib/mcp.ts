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

export interface McpTestResult {
  connected: boolean;
  tools: { name: string; description: string; read_only: boolean }[];
  error: string | null;
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
