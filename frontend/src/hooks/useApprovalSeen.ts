import { useEffect } from 'react';
import { api } from '@/lib/api';

/** How often an open chat says its approval cards are still on screen. The
 *  server treats a card as left unanswered a minute after the last report. */
const REPORT_MS = 15_000;

/**
 * Tell the server the person is looking at these approval cards, so they
 * aren't also sent to their phone or chat apps. Only counts while the window
 * is in front of them. Once they leave, the card goes out as a notification.
 */
export function useApprovalSeen(approvals: { request_id: string }[]): void {
  useEffect(() => {
    if (approvals.length === 0) return;
    const report = () => {
      if (document.hidden || !document.hasFocus()) return;
      for (const a of approvals) {
        api.post('/agents/approval/seen', { request_id: a.request_id }).catch(() => { /* already answered */ });
      }
    };
    report();
    const timer = window.setInterval(report, REPORT_MS);
    window.addEventListener('focus', report);
    document.addEventListener('visibilitychange', report);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', report);
      document.removeEventListener('visibilitychange', report);
    };
  }, [approvals]);
}
