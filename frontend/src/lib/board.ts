import { tr } from '@/lib/i18n';

export type BoardKind = 'finding' | 'question' | 'request' | 'decision' | 'heads_up';

export const BOARD_KINDS: BoardKind[] = ['finding', 'question', 'request', 'decision', 'heads_up'];

export interface BoardReply {
  author_kind: 'agent' | 'person';
  author_id: string;
  author_name: string;
  text: string;
  at: string;
}

export interface BoardPost {
  id: string;
  board: string;
  kind: BoardKind;
  text: string;
  tags: string[];
  author_kind: 'agent' | 'person';
  author_id: string;
  author_name: string;
  chat_id: string;
  /** '' for anyone, 'you' for the person, 'lead' or a worker on a swarm board, else an agent's name. */
  to_name: string;
  to_id: string;
  status: '' | 'open' | 'answered' | 'done';
  pinned: boolean;
  replies: BoardReply[];
  /** A question or request for one agent starts a chat so it can answer. */
  wake: { state?: 'sent' | 'skipped'; chat_id?: string; reason?: string };
  posted_at: string;
  changed_at: string;
}

export interface BoardInfo {
  key: string;
  kind: 'agents' | 'swarm';
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
