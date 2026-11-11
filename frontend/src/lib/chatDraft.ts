/** A question typed elsewhere (the dashboard's ask bar) that the chat page
 *  puts in its message box once, ready to send. */
export const CHAT_DRAFT_KEY = 'cognitrix.chatDraft';

export function takeChatDraft(): string {
  try {
    const text = sessionStorage.getItem(CHAT_DRAFT_KEY) || '';
    if (text) sessionStorage.removeItem(CHAT_DRAFT_KEY);
    return text;
  } catch {
    return '';
  }
}
