/** Chats shared between people on the same server (backend: sessions/sharing.py). */

export type ShareRole = 'view' | 'chat';

export interface SharedPerson { id: string; name: string; email: string; role: ShareRole }
export interface Directory { id: string; name: string; email: string }
export interface ChatPeople { people: SharedPerson[]; directory: Directory[] }

export interface SharedChatRow {
  id: string;
  title: string;
  owner: string;
  agent: { id: string; name: string; avatar?: { emoji?: string; hue?: number } };
  role: ShareRole;
  updated_at: string;
  running: boolean;
}

export interface SharedMessage {
  role: 'user' | 'assistant' | 'activity';
  content: string;
  author?: string;
  guest?: boolean;
}

export interface SharedChat {
  id: string;
  title: string;
  owner: { id: string; name: string };
  agent: SharedChatRow['agent'];
  role: 'owner' | ShareRole;
  people: { name: string; role: ShareRole }[];
  messages: SharedMessage[];
  running: boolean;
  run_id: string | null;
}

export const ROLE_LABEL: Record<ShareRole, string> = { view: 'Can read', chat: 'Can write' };

// Mirrors sharing._PREFIX_RE: a message someone the chat is shared with wrote.
const GUEST_PREFIX = /^\[Message from (.{1,80}?) in this shared chat\. [^\]\n]*\]\n\n/s;

/** The guest's name and their own words, or null author for anyone else's message. */
export function splitAuthor(content: string): { author: string | null; text: string } {
  const match = GUEST_PREFIX.exec(content || '');
  if (!match) return { author: null, text: content };
  return { author: match[1], text: content.slice(match[0].length) };
}

export function sharedChatHref(id: string): string {
  return `/shared/${encodeURIComponent(id)}`;
}
