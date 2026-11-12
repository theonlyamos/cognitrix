import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { desktop, isDesktop, onDesktopEvent, systemNotify, type EngineStatus } from '@/lib/desktop';
import { usePolling } from '@/hooks/usePolling';
import { useUser } from '@/context/AppContext';
import { isLookingAtChat } from '@/lib/chatPresence';
import { answerApproval, approvalPreview, approvalTitle } from '@/lib/approvals';

/** Events the app can notify about. Matches the rows on the Notifications screen. */
export type NotificationEvent =
  | 'approval'
  | 'question'
  | 'agent_alert'
  | 'chat_reply'
  | 'task_finished'
  | 'task_failed'
  | 'refused'
  | 'engine';

export const EVENT_LABELS: Record<NotificationEvent, { title: string; detail: string; alwaysInApp?: boolean }> = {
  approval: { title: 'Approval needed', detail: 'A chat or task is waiting on a risky step', alwaysInApp: true },
  question: { title: 'Agent asks a question', detail: 'A chat is waiting for your answer', alwaysInApp: true },
  agent_alert: { title: 'Agent alert', detail: 'An agent used Notify Me, offered to help, or drafted a skill for review' },
  chat_reply: { title: 'Chat reply', detail: 'An agent finished replying while you were in another window' },
  task_finished: { title: 'Task finished', detail: 'Only when the window isn’t focused' },
  task_failed: { title: 'Task failed', detail: 'With the reason and a link to retry' },
  refused: { title: 'Command refused or address blocked', detail: 'Outside the working folder, or a blocked provider URL' },
  engine: { title: 'Engine stopped', detail: 'The local engine quit or failed to start', alwaysInApp: true },
};

export interface ChannelPrefs {
  inApp: boolean;
  desktop: boolean;
}

export type NotificationPrefs = Record<NotificationEvent, ChannelPrefs>;

const DEFAULT_PREFS: NotificationPrefs = {
  approval: { inApp: true, desktop: true },
  question: { inApp: true, desktop: true },
  agent_alert: { inApp: true, desktop: true },
  chat_reply: { inApp: false, desktop: true },
  task_finished: { inApp: true, desktop: true },
  task_failed: { inApp: true, desktop: true },
  refused: { inApp: true, desktop: false },
  engine: { inApp: true, desktop: true },
};

export interface AppNotification {
  id: string;
  event: NotificationEvent;
  title: string;
  body: string;
  /** In-app route to open, e.g. /tasks/abc or /inbox. */
  href?: string;
  createdAt: number;
  read: boolean;
  /** Same tag as the push message for this item, so the browser shows one. */
  tag?: string;
  /** An approval's id: the Windows notification gets Approve and Deny buttons. */
  approvalId?: string;
}

export interface InboxApproval {
  kind: 'approval';
  /** 'chat' when a chat turn is waiting on it; task runs leave it out. */
  source?: 'chat' | 'task';
  id: string;
  /** Chat approvals: the chat, where it opens, and which agent asks. */
  session_id?: string;
  href?: string;
  agent_name?: string;
  full_shell?: boolean;
  run_id: string;
  task_id: string | null;
  step_index: number | null;
  tool_name: string;
  params: Record<string, unknown>;
  risk_level: string;
  details: string;
  status: string;
  created_at: string;
  task_title: string;
}

export interface InboxQuestion {
  kind: 'question';
  id: string;
  session_id: string;
  stream_id: string;
  question: {
    request_id: string;
    prompt?: string;
    header?: string;
    options?: { id: string; label: string; description?: string | null }[];
    allow_free_text?: boolean;
    recommended_option_id?: string | null;
    [key: string]: unknown;
  };
  created_at: string | null;
}

export interface InboxNotification {
  kind: 'notification';
  id: string;
  task_id: string | null;
  run_id: string | null;
  session_id: string | null;
  title: string;
  message: string;
  /** In-app page the alert opens, e.g. /settings/apps for "Reconnect Gmail". */
  link?: string | null;
  created_at: string | null;
  task_title: string;
}

/** Something a standing assistant offered to do (agent page, Check-ins). */
export interface InboxOffer {
  kind: 'offer';
  id: string;
  agent_id: string;
  agent_name: string;
  title: string;
  details: string;
  plan: string;
  created_at: string | null;
}

/** A skill learned from work, waiting for review before agents can use it. */
export interface InboxSkillDraft {
  kind: 'skill_draft';
  id: string;
  agent_id: string | null;
  agent_name: string;
  session_id: string | null;
  name: string;
  description: string;
  body: string;
  mine: boolean;
  can_approve: boolean;
  created_at: string | null;
}

export type InboxItem = InboxApproval | InboxQuestion | InboxNotification | InboxOffer | InboxSkillDraft;

interface TaskSummary {
  id: string;
  title: string;
  status?: string;
}

interface NotificationsContextValue {
  notifications: AppNotification[];
  unread: number;
  inbox: InboxItem[];
  inboxLoaded: boolean;
  refreshInbox: () => Promise<void>;
  markAllRead: () => void;
  markRead: (id: string) => void;
  dismiss: (id: string) => void;
  prefs: NotificationPrefs;
  setPref: (event: NotificationEvent, channel: keyof ChannelPrefs, value: boolean) => void;
  /** Report something that happened in the UI (e.g. a refused command). */
  push: (n: Omit<AppNotification, 'id' | 'createdAt' | 'read'>) => void;
  toasts: AppNotification[];
  dismissToast: (id: string) => void;
  engine: EngineStatus | null;
}

const NotificationsContext = createContext<NotificationsContextValue | null>(null);

const STORE_KEY = 'cognitrix.notifications';
const PREFS_KEY = 'cognitrix.notificationPrefs';
const MAX_KEPT = 60;

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage full or blocked: notifications just won't persist */
  }
}

export function NotificationsProvider({ children }: { children: React.ReactNode }) {
  // History holds task titles and agent questions, so it is kept per account:
  // a different sign-in on the same machine never sees someone else's.
  const { user } = useUser();
  const account = user?.id || user?.email || 'anonymous';
  const storeKey = `${STORE_KEY}:${account}`;
  const prefsKey = `${PREFS_KEY}:${account}`;
  const [notifications, setNotifications] = useState<AppNotification[]>(() => readJson(storeKey, []));
  const [prefs, setPrefs] = useState<NotificationPrefs>(() => ({ ...DEFAULT_PREFS, ...readJson(prefsKey, {}) }));
  const [inbox, setInbox] = useState<InboxItem[]>([]);
  const [inboxLoaded, setInboxLoaded] = useState(false);
  const [toasts, setToasts] = useState<AppNotification[]>([]);
  const [engine, setEngine] = useState<EngineStatus | null>(null);

  const prefsRef = useRef(prefs);
  prefsRef.current = prefs;
  const seenInbox = useRef<Set<string> | null>(null);
  const inboxRef = useRef<InboxItem[]>([]);
  const taskStatus = useRef<Map<string, string> | null>(null);

  // Switch to the new account's history when the sign-in changes.
  const loadedKey = useRef(storeKey);
  useEffect(() => {
    if (loadedKey.current === storeKey) return;
    loadedKey.current = storeKey;
    setNotifications(readJson(storeKey, []));
    setPrefs({ ...DEFAULT_PREFS, ...readJson(prefsKey, {}) });
    setToasts([]);
    seenInbox.current = null;
    taskStatus.current = null;
  }, [storeKey, prefsKey]);
  useEffect(() => {
    if (loadedKey.current === storeKey) writeJson(storeKey, notifications.slice(0, MAX_KEPT));
  }, [notifications, storeKey]);
  useEffect(() => {
    if (loadedKey.current === storeKey) writeJson(prefsKey, prefs);
  }, [prefs, prefsKey, storeKey]);

  const push = useCallback((n: Omit<AppNotification, 'id' | 'createdAt' | 'read'>) => {
    const item: AppNotification = {
      ...n,
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      createdAt: Date.now(),
      read: false,
    };
    const channel = prefsRef.current[n.event] ?? DEFAULT_PREFS[n.event];
    const inApp = channel.inApp || EVENT_LABELS[n.event].alwaysInApp;
    if (inApp) setNotifications((list) => [item, ...list].slice(0, MAX_KEPT));
    const popToast = () => {
      if (inApp) setToasts((list) => [item, ...list].slice(0, 3));
    };

    // "Task finished" only pops a system notification when you're elsewhere.
    // In the desktop app the shell raises its own "engine stopped" and
    // "engine couldn't start" notifications.
    const away = typeof document !== 'undefined' && (document.hidden || !document.hasFocus());
    const system =
      channel.desktop && ((n.event !== 'task_finished' && n.event !== 'chat_reply') || away) && !(n.event === 'engine' && isDesktop());
    if (!system) {
      popToast();
      return;
    }
    const urgent = n.event === 'approval' || n.event === 'question';
    void systemNotify({ title: n.title, body: n.body, href: n.href, urgent, tag: n.tag, approval: n.approvalId }).then((shown) => {
      // In the desktop app a Windows notification replaces the in-app toast
      // (it's still listed under the bell). On the web, or when Windows
      // notifications are off, the in-app toast shows as before.
      if (!(shown && isDesktop())) popToast();
    });
  }, []);

  const refreshInbox = useCallback(async () => {
    try {
      const { data } = await api.get<{ items: InboxItem[] }>('/inbox');
      const items = data.items ?? [];
      setInbox(items);
      setInboxLoaded(true);
      // First load only records what's there; later loads notify on new items.
      // An approval or question from the chat on screen waits: it notifies
      // if the person leaves that chat without answering.
      const deferred = new Set<string>();
      if (seenInbox.current) {
        for (const item of items) {
          if (seenInbox.current.has(item.id)) continue;
          if (item.kind === 'approval') {
            // The chat is on screen: its card is right there.
            if (item.source === 'chat' && isLookingAtChat(item.session_id)) {
              deferred.add(item.id);
              continue;
            }
            push({
              event: 'approval',
              title: `Approval needed: ${approvalTitle(item)}`,
              body: approvalPreview(item) || (item.task_title ? `${item.task_title} is paused until you answer.` : 'Paused until you answer.'),
              href: item.source === 'chat' ? item.href || '/inbox' : '/inbox',
              tag: `approval-${item.id}`,
              approvalId: item.id,
            });
          } else if (item.kind === 'notification') {
            push({
              event: 'agent_alert',
              title: item.title,
              body: item.message || (item.task_title ? `From ${item.task_title}` : 'Open the inbox to see it.'),
              href: '/inbox',
            });
          } else if (item.kind === 'offer') {
            push({
              event: 'agent_alert',
              title: `${item.agent_name || 'Your assistant'} offers: ${item.title}`,
              body: item.details || 'Open the inbox to accept or dismiss it.',
              href: '/inbox',
              tag: `offer-${item.id}`,
            });
          } else if (item.kind === 'skill_draft') {
            push({
              event: 'agent_alert',
              title: `Skill to review: ${item.name}`,
              body: item.description || 'Open the inbox to review it.',
              href: '/inbox',
            });
          } else {
            if (isLookingAtChat(item.session_id)) {
              deferred.add(item.id);
              continue;
            }
            push({
              event: 'question',
              title: 'An agent asked a question',
              body: String(item.question.prompt ?? item.question.header ?? 'Open the inbox to answer.'),
              href: '/inbox',
            });
          }
        }
      }
      seenInbox.current = new Set(items.map((i) => i.id).filter((id) => !deferred.has(id)));
      // A toast for an approval answered elsewhere has nothing left to do.
      const waiting = new Set(items.map((i) => i.id));
      setToasts((list) => {
        const kept = list.filter((t) => !t.approvalId || waiting.has(t.approvalId));
        return kept.length === list.length ? list : kept;
      });
      inboxRef.current = items;
    } catch {
      /* signed out or engine restarting: try again on the next tick */
    }
  }, [push]);

  const refreshTasks = useCallback(async () => {
    try {
      const { data } = await api.get<TaskSummary[]>('/tasks');
      const next = new Map((data ?? []).map((t) => [t.id, t.status ?? 'pending']));
      const previous = taskStatus.current;
      if (previous) {
        for (const task of data ?? []) {
          const before = previous.get(task.id);
          const now = task.status ?? 'pending';
          if (!before || before === now) continue;
          if (now === 'completed') {
            push({ event: 'task_finished', title: `Task finished: ${task.title}`, body: 'The report is ready to review.', href: `/tasks/${task.id}` });
          } else if (now === 'failed') {
            push({ event: 'task_failed', title: `Task failed: ${task.title}`, body: 'Open the task to see why and run it again.', href: `/tasks/${task.id}` });
          }
        }
      }
      taskStatus.current = next;
    } catch {
      /* ignore */
    }
  }, [push]);

  useEffect(() => {
    void refreshInbox();
    void refreshTasks();
  }, [refreshInbox, refreshTasks]);
  usePolling(refreshInbox, 5000, true);
  usePolling(refreshTasks, 10000, true);

  // Engine state from the desktop shell.
  useEffect(() => {
    if (!isDesktop()) return;
    let stop: (() => void) | undefined;
    let last: string | undefined;
    void desktop.engineStatus().then((s) => s && setEngine(s));
    void onDesktopEvent<EngineStatus>('engine-status', (s) => {
      setEngine(s);
      if ((s.status === 'stopped' || s.status === 'failed') && last !== s.status) {
        push({ event: 'engine', title: 'The local engine stopped', body: s.error ?? 'Restart it from Settings or the tray.', href: '/settings/engine' });
      }
      last = s.status;
    }).then((fn) => (stop = fn));
    return () => stop?.();
  }, [push]);

  // Approve / Deny pressed on a Windows notification.
  useEffect(() => {
    if (!isDesktop()) return;
    let stop: (() => void) | undefined;
    void onDesktopEvent<{ approval: string; approved: boolean }>('notification-action', ({ approval, approved }) => {
      const item = inboxRef.current.find((i): i is InboxApproval => i.kind === 'approval' && i.id === approval);
      if (!item) return;
      answerApproval(item, approved)
        .catch(() => {
          push({ event: 'approval', title: 'That approval has already ended', body: 'It was answered elsewhere or timed out.', href: '/inbox' });
        })
        .finally(() => void refreshInbox());
    }).then((fn) => (stop = fn));
    return () => stop?.();
  }, [push, refreshInbox]);

  const markAllRead = useCallback(() => setNotifications((list) => list.map((n) => ({ ...n, read: true }))), []);
  const markRead = useCallback((id: string) => setNotifications((list) => list.map((n) => (n.id === id ? { ...n, read: true } : n))), []);
  const dismiss = useCallback((id: string) => setNotifications((list) => list.filter((n) => n.id !== id)), []);
  const dismissToast = useCallback((id: string) => setToasts((list) => list.filter((n) => n.id !== id)), []);
  const setPref = useCallback((event: NotificationEvent, channel: keyof ChannelPrefs, value: boolean) => {
    setPrefs((p) => ({ ...p, [event]: { ...p[event], [channel]: value } }));
  }, []);

  const value = useMemo<NotificationsContextValue>(
    () => ({
      notifications,
      unread: notifications.filter((n) => !n.read).length,
      inbox,
      inboxLoaded,
      refreshInbox,
      markAllRead,
      markRead,
      dismiss,
      prefs,
      setPref,
      push,
      toasts,
      dismissToast,
      engine,
    }),
    [notifications, inbox, inboxLoaded, refreshInbox, markAllRead, markRead, dismiss, prefs, setPref, push, toasts, dismissToast, engine],
  );

  return <NotificationsContext.Provider value={value}>{children}</NotificationsContext.Provider>;
}

export function useNotifications(): NotificationsContextValue {
  const ctx = useContext(NotificationsContext);
  if (!ctx) throw new Error('useNotifications must be used inside NotificationsProvider');
  return ctx;
}

/** Same as useNotifications, but null outside the provider (for components
 *  that also render in tests or pages without the app shell). */
export function useOptionalNotifications(): NotificationsContextValue | null {
  return useContext(NotificationsContext);
}
