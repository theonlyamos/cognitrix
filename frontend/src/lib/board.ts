import { tr } from '@/lib/i18n';

export type BoardKind = 'finding' | 'question' | 'request' | 'decision' | 'heads_up';

export const BOARD_KINDS: BoardKind[] = ['finding', 'question', 'request', 'decision', 'heads_up'];

export type AuthorKind = 'agent' | 'person' | 'external';

export interface BoardReply {
  author_kind: AuthorKind;
  /** Written by whoever is looking. */
  mine?: boolean;
  author_id: string;
  author_name: string;
  text: string;
  at: string;
  /** Agents tagged with @name in the answer. */
  mentions?: BoardMention[];
  /** People tagged with @name in the answer. */
  people?: BoardMention[];
}

export interface BoardMention {
  name: string;
  id?: string;
}

export interface BoardPost {
  id: string;
  board: string;
  kind: BoardKind;
  text: string;
  tags: string[];
  author_kind: AuthorKind;
  /** Written by whoever is looking. */
  mine?: boolean;
  author_id: string;
  author_name: string;
  chat_id: string;
  /** '' for anyone, 'you' for the person, 'lead' or a worker on a swarm board, else an agent's name. */
  to_name: string;
  to_id: string;
  status: '' | 'open' | 'answered' | 'done';
  pinned: boolean;
  replies: BoardReply[];
  /** Agents tagged with @name in the text. */
  mentions?: BoardMention[];
  /** Withdrawn by its author. */
  retracted?: boolean;
  /** A question or request for one agent starts a chat so it can answer. */
  wake: { state?: 'sent' | 'skipped'; chat_id?: string; reason?: string; command?: string };
  /** Chats started because someone was tagged. */
  mention_wakes?: { name: string; state: 'sent' | 'skipped'; chat_id?: string; reason?: string }[];
  posted_at: string;
  changed_at: string;
  edited_at?: string;
  /** Milliseconds since 1970 it was posted; pages on from here. */
  stamp?: number;
  /** A decision saved to (or proposed for) the agents' memory. */
  memory_id?: string;
  /** A thread in a project channel, such as task:<id>. */
  thread?: string;
  thread_title?: string;
  /** Who took on this request or question. */
  claimed_by?: { kind?: AuthorKind; id?: string; name?: string };
  claimed_at?: string;
  /** People tagged with @name. */
  people?: BoardMention[];
  /** The /command the post ran, if any. */
  command?: string;
}

export interface BoardInfo {
  key: string;
  kind: 'agents' | 'swarm' | 'project';
  project_id?: string;
  /** Someone else's project channel this person was added to. */
  shared?: boolean;
  title: string;
  status: string;
  open: number;
  total: number;
  latest: string;
  swarm_id?: string;
  session_id?: string;
  agent_name?: string;
}

export interface BoardSettings {
  enabled: boolean;
  wakes_per_day: number;
  keep_days: number;
  /** Hours before an unanswered question is brought up again (0: never). */
  nudge_hours: number;
  wakes_today: number;
}

export interface BoardFollow {
  agent_id: string;
  agent_name: string;
  kinds: BoardKind[];
  tags: string[];
  wake: boolean;
}

export function kindLabel(kind: BoardKind): string {
  switch (kind) {
    case 'finding': return tr('ui.board.kindFinding');
    case 'question': return tr('ui.board.kindQuestion');
    case 'request': return tr('ui.board.kindRequest');
    case 'decision': return tr('ui.board.kindDecision');
    default: return tr('ui.board.kindHeadsUp');
  }
}

/** The colour of a kind's label. */
export function kindTone(kind: BoardKind): string {
  switch (kind) {
    case 'finding': return 'bg-info/15 text-info-ink';
    case 'question': return 'bg-warn/15 text-warn-ink';
    case 'request': return 'bg-danger/15 text-danger-ink';
    case 'decision': return 'bg-ok/15 text-ok';
    default: return 'bg-fg-dim/15 text-fg-dim';
  }
}

export interface MentionName {
  name: string;
  kind?: 'agent' | 'person' | 'external';
}

export interface ChannelView {
  board: string;
  project_id: string;
  title: string;
  owner: boolean;
  members: { id: string; name: string; email: string }[];
  project_agents: { id: string; name: string }[];
  agents: { id: string; name: string }[];
  externals: string[];
  known_externals: string[];
}

export interface BoardSchedule {
  id: string;
  board: string;
  thread: string;
  to: string;
  kind: BoardKind;
  text: string;
  days: number[];
  time: string;
  when: string;
  enabled: boolean;
  next_at: string;
  last_at: string;
  created_by: string;
}

export interface BoardCommand {
  id?: string;
  name: string;
  agent_id?: string;
  agent_name?: string;
  prompt: string;
  description: string;
}

export interface BoardMirror {
  id: string;
  board: string;
  channel_id: string;
  slack_channel: string;
}

/** A thread's label: its title, else its name. */
export function threadLabel(post: Pick<BoardPost, 'thread' | 'thread_title'>): string {
  return post.thread_title || post.thread || '';
}
