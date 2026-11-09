import { describe, expect, it } from 'vitest';
import { agentFromFile } from './agentImport';

describe('agentFromFile', () => {
  it('keeps the agent and drops what belongs to the old computer or account', () => {
    const agent = agentFromFile(JSON.stringify({
      id: 'old', owner_id: 'someone', name: ' Researcher ', system_prompt: 'Be brief.',
      llm: { provider: 'openai', model: 'gpt-5.5', api_key: 'sk-secret', extra_headers: { Authorization: 'x' } },
      tools: [{ name: 'Web Search' }], folders: [{ path: 'C:/work' }], mcp_servers: ['abc'], credentials: ['github'],
      permission_level: 'ask',
    }));
    expect(agent).toMatchObject({ name: 'Researcher', system_prompt: 'Be brief.', permission_level: 'ask', mcp_servers: [] });
    expect(agent.llm).toEqual({ provider: 'openai', model: 'gpt-5.5' });
    for (const key of ['id', 'owner_id', 'folders', 'credentials']) expect(agent).not.toHaveProperty(key);
  });

  it('refuses files that are not agents', () => {
    for (const text of ['not json', '[]', '{"name":"x"}', '{"llm":{"provider":"openai"}}']) {
      expect(() => agentFromFile(text)).toThrow('not an agent file');
    }
  });
});
