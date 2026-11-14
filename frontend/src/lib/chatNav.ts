import { useSyncExternalStore } from 'react';

/**
 * What the chat page tells the app sidebar (and back), without one owning the
 * other: the sidebar lists every chat on every page, the chat page owns the
 * chat on screen.
 *
 * - The chat page publishes which chat is open and which are running.
 * - The sidebar changes chats (rename, pin, archive, delete) and says so with
 *   CHATS_EDITED, so the chat page re-reads its list.
 * - The chat page says its list moved on (a turn ended, a chat was made) with
 *   CHATS_STALE, so the sidebar re-reads its list.
 * Two event names, one per direction, so neither re-read starts the other.
 */
export interface ChatNavState {
  /** The chat open on the chat page, or null for a new chat or another page. */
  activeChatId: string | null;
  /** Chats with a turn still running that the chat page knows about. */
  runningIds: readonly string[];
}

export const CHATS_EDITED = 'cognitrix:chats-edited';
export const CHATS_STALE = 'cognitrix:chats-stale';

export interface ChatsEditedDetail {
  /** A chat that was deleted, so the chat page can leave it. */
  deletedId?: string;
}

let state: ChatNavState = { activeChatId: null, runningIds: [] };
const listeners = new Set<() => void>();

export function setChatNav(next: Partial<ChatNavState>) {
  const merged = { ...state, ...next };
  if (merged.activeChatId === state.activeChatId && sameIds(merged.runningIds, state.runningIds)) return;
  state = merged;
  listeners.forEach((fn) => fn());
}

const sameIds = (a: readonly string[], b: readonly string[]) =>
  a.length === b.length && a.every((id, i) => id === b[i]);

const subscribe = (fn: () => void) => {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
};

export function useChatNav(): ChatNavState {
  return useSyncExternalStore(subscribe, () => state, () => state);
}

export function chatsEdited(detail: ChatsEditedDetail = {}) {
  window.dispatchEvent(new CustomEvent<ChatsEditedDetail>(CHATS_EDITED, { detail }));
}

export function chatsStale() {
  window.dispatchEvent(new Event(CHATS_STALE));
}

/** Where a chat lives: one-to-one chats on the chat page, team chats on the team's page. */
export function chatHref(chat: { id: string; kind?: 'agent' | 'team'; team_id?: string | null }) {
  return chat.kind === 'team'
    ? `/teams/${encodeURIComponent(chat.team_id || '')}/chat?chat=${encodeURIComponent(chat.id)}`
    : `/home?chat=${encodeURIComponent(chat.id)}`;
}

/** A new chat: with no target, with the last agent; else with that agent or team. */
export function newChatHref(target?: { kind: 'agent' | 'team'; id: string }) {
  if (!target) return '/home?new=1';
  return target.kind === 'team'
    ? `/teams/${encodeURIComponent(target.id)}/chat`
    : `/home?new=${encodeURIComponent(`agent:${target.id}`)}`;
}
