/** Shapes and labels shared by the chat start screen and chat list. */

export interface PickerAgent {
  id: string;
  name: string;
  description?: string | null;
  llm?: { provider?: string | null; model?: string | null } | null;
}

export interface PickerTeam {
  id: string;
  name: string;
  description?: string | null;
  assigned_agents?: string[];
}

export type PickTarget = { kind: 'agent'; id: string } | { kind: 'team'; id: string };

export function agentModel(agent: PickerAgent | undefined): string {
  const provider = agent?.llm?.provider || '';
  const model = agent?.llm?.model || '';
  return [provider, model].filter(Boolean).join(' · ');
}

export function teamLine(team: PickerTeam): string {
  const n = team.assigned_agents?.length ?? 0;
  return `team · ${n} agent${n === 1 ? '' : 's'}`;
}
