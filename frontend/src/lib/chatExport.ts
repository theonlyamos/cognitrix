import { api } from '@/lib/api';
import type { ChatMessage } from '@/context/SessionContext';
import { parseChatEntries, toChatMessages, type BackendChatEntry } from '@/lib/transcript';

const withoutThinking = (text: string) => text.replace(/<think>[\s\S]*?<\/think>/g, '').trim();

/** A chat as a Markdown document: one heading per message, tools as a list. */
export function chatToMarkdown(title: string, who: string, messages: ChatMessage[]): string {
  const lines = [`# ${title}`, '', `Chat with ${who}, exported ${new Date().toLocaleString()}`, ''];
  for (const message of messages) {
    if (message.role === 'tool') {
      const tools = (message.tools || []).map((tool) => `- Used ${tool.name.replace(/_/g, ' ')}`);
      if (tools.length) lines.push(...tools, '');
      continue;
    }
    const text = withoutThinking(message.content);
    if (!text) continue;
    lines.push(`## ${message.role === 'user' ? 'You' : message.speaker || who}`, '', text, '');
  }
  return lines.join('\n');
}

export const exportFileName = (title: string) =>
  `${title.replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'chat'}.md`;

export function downloadText(filename: string, text: string, type = 'text/markdown') {
  const url = URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Fetch a saved chat and download it as Markdown. */
export async function exportChat(id: string, title: string, who: string) {
  const res = await api.get<BackendChatEntry[]>(`/sessions/${encodeURIComponent(id)}/chat`);
  downloadText(exportFileName(title), chatToMarkdown(title, who, toChatMessages(parseChatEntries(res.data))));
}
