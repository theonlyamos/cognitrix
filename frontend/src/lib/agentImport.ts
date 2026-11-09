/** Turn an exported agent file back into something POST /agents accepts.
 *  Things that only make sense on the computer or account it came from
 *  (folders, MCP servers, saved sign-ins, keys) are left behind. */
export function agentFromFile(text: string): Record<string, unknown> {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error('That file is not an agent file.');
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('That file is not an agent file.');
  const source = data as Record<string, unknown>;
  const llm = source.llm as Record<string, unknown> | undefined;
  if (typeof source.name !== 'string' || !source.name.trim() || !llm || typeof llm.provider !== 'string') {
    throw new Error('That file is not an agent file.');
  }
  const agent: Record<string, unknown> = { ...source };
  for (const key of ['id', '_id', 'owner_id', 'parent_id', 'folders', 'credentials', 'created_at', 'updated_at']) {
    delete agent[key];
  }
  agent.mcp_servers = [];
  agent.sub_agents = [];
  const cleanLlm = { ...llm };
  delete cleanLlm.api_key;
  delete cleanLlm.extra_headers;
  agent.llm = cleanLlm;
  agent.name = source.name.trim();
  agent.system_prompt = typeof source.system_prompt === 'string' ? source.system_prompt : '';
  return agent;
}
