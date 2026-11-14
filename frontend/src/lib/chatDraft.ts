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

/** Set beside a draft when the chat should send it as soon as it is connected
 *  (Create app's Build button). */
export const CHAT_AUTOSEND_KEY = 'cognitrix.chatDraftSend';

export function takeChatAutoSend(): boolean {
  try {
    const on = sessionStorage.getItem(CHAT_AUTOSEND_KEY) === '1';
    // Cleared a moment later, so a second render (React's strict mode) still sees it.
    if (on) setTimeout(() => sessionStorage.removeItem(CHAT_AUTOSEND_KEY), 0);
    return on;
  } catch {
    return false;
  }
}
