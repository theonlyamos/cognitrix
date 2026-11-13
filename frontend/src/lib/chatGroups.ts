/** Sorting the chat list into pinned chats, one section per agent or team,
 * and the archive. Kept apart from the page so it can be tested alone. */

export interface GroupableChat {
  id: string;
  kind?: 'agent' | 'team';
  agent_id?: string | null;
  team_id?: string | null;
  updated_at?: string | null;
  pinned?: boolean;
  archived?: boolean;
  running?: boolean;
}

export interface ChatGroup<T extends GroupableChat> {
  /** 'agent:<id>' or 'team:<id>'; also the key used to remember open/closed. */
  key: string;
  kind: 'agent' | 'team';
  id: string;
  chats: T[];
  /** Newest change of any chat in the group, pinned and archived included. */
  latest: string;
}

export interface GroupedChats<T extends GroupableChat> {
  pinned: T[];
  groups: ChatGroup<T>[];
  archived: T[];
}

export const PINNED_KEY = 'pinned';
export const ARCHIVED_KEY = 'archived';
/** Chats each section shows before "show more". */
export const GROUP_PREVIEW = 4;

export function groupKey(chat: GroupableChat): string {
  return chat.kind === 'team' ? `team:${chat.team_id || ''}` : `agent:${chat.agent_id || ''}`;
}

const newest = (chats: GroupableChat[]) =>
  chats.reduce((best, c) => (String(c.updated_at || '') > best ? String(c.updated_at || '') : best), '');

/**
 * Split chats (already sorted newest first) into sections.
 *
 * Normally pinned chats sit only in the pinned section and archived chats only
 * in the archive. While searching (`flat`), every match goes to its own
 * agent's or team's section so results read by who you talked to.
 */
export function groupChats<T extends GroupableChat>(
  chats: T[],
  { searching = false }: { searching?: boolean } = {},
): GroupedChats<T> {
  const pinned: T[] = [];
  const archived: T[] = [];
  const byKey = new Map<string, T[]>();
  for (const chat of chats) {
    if (!searching && chat.archived) {
      archived.push(chat);
      continue;
    }
    if (!searching && chat.pinned) {
      pinned.push(chat);
      continue;
    }
    const key = groupKey(chat);
    const list = byKey.get(key);
    if (list) list.push(chat);
    else byKey.set(key, [chat]);
  }
  // A section is ordered by its newest chat, counting pinned ones too, so an
  // agent you just used stays near the top even if its latest chat is pinned.
  const all = new Map<string, T[]>();
  for (const chat of chats) {
    const key = groupKey(chat);
    all.set(key, [...(all.get(key) || []), chat]);
  }
  const groups = [...byKey.entries()].map(([key, list]) => {
    const first = list[0];
    return {
      key,
      kind: first.kind === 'team' ? 'team' as const : 'agent' as const,
      id: String((first.kind === 'team' ? first.team_id : first.agent_id) || ''),
      chats: list,
      latest: newest(all.get(key) || list),
    };
  });
  groups.sort((a, b) => b.latest.localeCompare(a.latest));
  return { pinned, groups, archived };
}

const STORE = 'chatList.closedSections';

/** Which sections are closed. The archive starts closed; others start open. */
export function loadClosedSections(): Set<string> {
  try {
    const raw = localStorage.getItem(STORE);
    if (raw) {
      const value = JSON.parse(raw);
      if (Array.isArray(value)) return new Set(value.filter((v) => typeof v === 'string'));
    }
  } catch {
    // Storage blocked or bad JSON: fall back to the defaults.
  }
  return new Set([ARCHIVED_KEY]);
}

export function saveClosedSections(closed: Set<string>) {
  try {
    localStorage.setItem(STORE, JSON.stringify([...closed]));
  } catch {
    // Not remembered this time; nothing else depends on it.
  }
}

const WIDTH_STORE = 'chatList.width';
export const PANEL_MIN = 240;
export const PANEL_MAX = 520;
export const PANEL_DEFAULT = 280;

export const clampPanelWidth = (width: number) =>
  Math.round(Math.min(PANEL_MAX, Math.max(PANEL_MIN, width)));

export function loadPanelWidth(): number {
  try {
    const value = Number(localStorage.getItem(WIDTH_STORE));
    if (Number.isFinite(value) && value > 0) return clampPanelWidth(value);
  } catch {
    // ignore
  }
  return PANEL_DEFAULT;
}

export function savePanelWidth(width: number) {
  try {
    localStorage.setItem(WIDTH_STORE, String(clampPanelWidth(width)));
  } catch {
    // ignore
  }
}
