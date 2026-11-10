/**
 * Which chat the person is looking at, so an approval or question from that
 * chat doesn't also pop a notification. Home sets it; NotificationsContext
 * reads it.
 */
let viewing: string | null = null;

export function setViewingChat(sessionId: string | null): void {
  viewing = sessionId || null;
}

/** True when this chat is open and the window is in front of the person. */
export function isLookingAtChat(sessionId: string | null | undefined): boolean {
  if (!sessionId || viewing !== sessionId || typeof document === 'undefined') return false;
  return !document.hidden && document.hasFocus();
}
