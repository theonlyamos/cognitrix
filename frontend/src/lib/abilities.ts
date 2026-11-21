import { tr } from '@/lib/i18n';

/** What an agent can do, in words; each one stands for a handful of tools. */
export interface Ability { id: string; label: string; help: string; tools: string[] }

export const ABILITIES: Ability[] = [
  {
    id: 'web',
    get label() { return tr('ui.abilities.web'); },
    get help() { return tr('ui.abilities.webHelp'); },
    tools: ['Search', 'Tavily Search', 'Webfetch', 'Http Request', 'Learn From Website'],
  },
  {
    id: 'files',
    get label() { return tr('ui.abilities.files'); },
    get help() { return tr('ui.abilities.filesHelp'); },
    tools: ['Read', 'Write', 'Edit', 'Glob', 'Grep', 'Open File', 'Request Folder'],
  },
  {
    id: 'code',
    get label() { return tr('ui.abilities.code'); },
    get help() { return tr('ui.abilities.codeHelp'); },
    tools: ['Bash', 'Bash Output', 'Kill Shell', 'Run Tests', 'Coding Agent', 'Copy Into Sandbox', 'Copy Back From Sandbox', 'Sandbox Changes', 'Switch Computer For Code'],
  },
  {
    id: 'screen',
    get label() { return tr('ui.abilities.screen'); },
    get help() { return tr('ui.abilities.screenHelp'); },
    tools: [
      'Take Screenshot', 'Mouse Click', 'Mouse Double Click', 'Mouse Right Click', 'Mouse Scroll', 'Key Press', 'Hot Key', 'Text Input',
      'Open App', 'List Windows', 'Look At Window', 'Click In Window', 'Click Element', 'Type In Window', 'Press Key In Window',
      'Scroll Window', 'Set Text', 'Ask To Take Over', 'Ask For Sign In',
    ],
  },
  {
    id: 'pages',
    get label() { return tr('ui.abilities.pages'); },
    get help() { return tr('ui.abilities.pagesHelp'); },
    tools: ['Publish Artifact', 'Read Artifact', 'List Artifacts', 'Artifact Comments', 'Publish App', 'Check App', 'Test App', 'App Status', 'Show Card', 'Generate Image'],
  },
  {
    id: 'recall',
    get label() { return tr('ui.abilities.recall'); },
    get help() { return tr('ui.abilities.recallHelp'); },
    tools: ['Memory', 'Search Knowledge', 'Search Chats', 'Read Chat', 'Load Skill', 'Propose Skill'],
  },
  {
    id: 'connections',
    get label() { return tr('ui.abilities.connections'); },
    get help() { return tr('ui.abilities.connectionsHelp'); },
    tools: ['List Mcp Tools', 'Run Mcp Tool', 'List Credentials', 'Send Email'],
  },
  {
    id: 'teamwork',
    get label() { return tr('ui.abilities.teamwork'); },
    get help() { return tr('ui.abilities.teamworkHelp'); },
    tools: [
      'Call Agent', 'List Agents', 'Assign Task', 'Create Task', 'Start Task', 'Get Task', 'List Tasks', 'Update Task',
      'Start Chat', 'Message Chat', 'Check Chat', 'Spawn Workers', 'Swarm',
    ],
  },
];

/** Every agent gets these: asking you something and telling you when it is done. */
export const ALWAYS_TOOLS = ['Ask User', 'Notify Me', 'Follow Up Later'];

export interface AgentTemplate { id: string; label: string; name: string; purpose: string; instructions: string; abilities: string[] }

export const AGENT_TEMPLATES: AgentTemplate[] = [
  {
    id: 'researcher',
    get label() { return tr('ui.abilities.tplResearcher'); },
    get name() { return tr('ui.abilities.tplResearcher'); },
    get purpose() { return tr('ui.abilities.tplResearcherPurpose'); },
    get instructions() { return tr('ui.abilities.tplResearcherHow'); },
    abilities: ['web', 'recall', 'pages'],
  },
  {
    id: 'writer',
    get label() { return tr('ui.abilities.tplWriter'); },
    get name() { return tr('ui.abilities.tplWriter'); },
    get purpose() { return tr('ui.abilities.tplWriterPurpose'); },
    get instructions() { return tr('ui.abilities.tplWriterHow'); },
    abilities: ['files', 'recall', 'pages'],
  },
  {
    id: 'coder',
    get label() { return tr('ui.abilities.tplCoder'); },
    get name() { return tr('ui.abilities.tplCoder'); },
    get purpose() { return tr('ui.abilities.tplCoderPurpose'); },
    get instructions() { return tr('ui.abilities.tplCoderHow'); },
    abilities: ['files', 'code', 'web'],
  },
  {
    id: 'inbox',
    get label() { return tr('ui.abilities.tplInbox'); },
    get name() { return tr('ui.abilities.tplInbox'); },
    get purpose() { return tr('ui.abilities.tplInboxPurpose'); },
    get instructions() { return tr('ui.abilities.tplInboxHow'); },
    abilities: ['connections', 'recall', 'web'],
  },
];

/** The tool names a set of abilities stands for, limited to tools that exist here. */
export function toolsFor(abilityIds: string[], available: Set<string>): string[] {
  const names = new Set<string>(ALWAYS_TOOLS);
  for (const ability of ABILITIES) {
    if (abilityIds.includes(ability.id)) ability.tools.forEach((name) => names.add(name));
  }
  return [...names].filter((name) => available.has(name));
}

/** 'all', 'some' or 'none' of an ability's available tools are picked. */
export function abilityState(ability: Ability, selected: Set<string>, available: Set<string>): 'all' | 'some' | 'none' {
  const have = ability.tools.filter((name) => available.has(name));
  if (have.length === 0) return 'none';
  const picked = have.filter((name) => selected.has(name)).length;
  return picked === 0 ? 'none' : picked === have.length ? 'all' : 'some';
}
