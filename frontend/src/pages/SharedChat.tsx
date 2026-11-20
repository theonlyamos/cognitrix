import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, Users } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { useEventStream } from '@/hooks/useEventStream';
import MarkdownMessage from '@/components/MarkdownMessage';
import { AgentBadge } from '@/components/StartChat';
import { ROLE_LABEL, type SharedChat as SharedChatData } from '@/lib/sharedChats';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

interface LiveEvent { type: string; content?: string; name?: string }

const stripThinking = (text: string) => text.replace(/<think>[\s\S]*?(<\/think>|$)/g, '').trim();

/** /shared/:chatId: a chat someone shared with me. I read it and watch replies
 *  arrive; with "Can write" I can ask the agent things here too. */
export default function SharedChat() {
  const { chatId = '' } = useParams();
  const [chat, setChat] = useState<SharedChatData | null>(null);
  const [error, setError] = useState('');
  const [live, setLive] = useState<string | null>(null);
  const [activity, setActivity] = useState('');
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState('');
  const scroller = useRef<HTMLDivElement>(null);
  const path = `/shared-chats/${encodeURIComponent(chatId)}`;

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<SharedChatData>(path);
      setChat(data);
      setError('');
      document.title = `${data.title} · Cognitrix`;
      if (!data.running) {
        setLive(null);
        setActivity('');
      }
    } catch (e) {
      setChat(null);
      setError(errorMessage(e, tr('ui.sharedChat.thisChatIsnTShared')));
    }
  }, [path]);

  useEffect(() => { void load(); }, [load]);

  const onEvent = useCallback((event: LiveEvent) => {
    if (event.type === 'run_started') {
      setLive('');
      setActivity('');
      void load();
    } else if (event.type === 'text' && typeof event.content === 'string') {
      setLive((cur) => (cur ?? '') + event.content);
      setActivity('');
    } else if (event.type === 'tool') {
      setActivity(event.name ? tr('ui.sharedChat.usingName', { name: event.name }) : tr('ui.sharedChat.working'));
    } else if (event.type === 'waiting') {
      setActivity(tr('ui.sharedChat.waitingForNameToApprove', { name: chat?.owner.name ?? tr('ui.sharedChat.theOwner') }));
    } else if (event.type === 'changed') {
      setLive(null);
      setActivity('');
      void load();
    } else if (event.type === 'gone') {
      setChat(null);
      setError(tr('ui.sharedChat.thisChatIsnTShared'));
    }
  }, [load, chat?.owner.name]);

  useEventStream<LiveEvent>({
    path: chat ? `${path}/events` : null,
    enabled: !!chat && !error,
    onEvent: ({ data }) => onEvent(data),
  });

  const count = chat?.messages.length ?? 0;
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [count, live]);

  const send = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    setSendError('');
    try {
      await api.post(`${path}/messages`, { message: text });
      setDraft('');
      setLive('');
      void load();
    } catch (e) {
      setSendError(errorMessage(e, tr('ui.sharedChat.couldNotSendThat')));
    } finally {
      setSending(false);
    }
  };

  if (error) {
    return (
      <div role="alert" className="mx-auto flex h-full max-w-sm flex-col items-center justify-center px-6 text-center">
        <h1 className="text-lg font-semibold">{tr('ui.sharedChat.thisChatIsnTAvailable')}</h1>
        <p className="mt-2 text-sm text-fg-dim">{error}</p>
        <Link to="/home" className="mt-4 text-[13px] text-accent-ink hover:underline">{tr('ui.sharedChat.backToChats')}</Link>
      </div>
    );
  }
  if (!chat) {
    return <div role="status" className="flex h-full items-center justify-center font-mono text-[12px] text-fg-dim">{tr('ui.sharedChat.loading')}</div>;
  }

  const busy = chat.running || live !== null;
  const canWrite = chat.role === 'chat';

  return (
    <div className="flex h-full min-h-0 flex-col bg-bg text-fg">
      <header className="flex flex-none items-center gap-2.5 border-b border-line px-3 py-2.5 md:px-5">
        <Link to="/home" aria-label={tr('ui.sharedChat.backToChats')} className="grid h-9 w-9 flex-none place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg md:hidden"><ArrowLeft size={16} /></Link>
        <AgentBadge name={chat.agent.name} avatar={chat.agent.avatar} />
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-[15px] font-semibold">{chat.title}</h1>
          <p className="truncate text-[12px] text-fg-dim">
            {tr('ui.sharedChat.ownerChatWithAgent', { owner: chat.owner.name, agent: chat.agent.name })}
            {chat.role !== 'owner' && (chat.role === 'chat' ? tr('ui.sharedChat.youCanWrite') : tr('ui.sharedChat.youCanRead'))}
          </p>
        </div>
        {chat.people.length > 0 && (
          <span className="hidden flex-none items-center gap-1 font-mono text-[11px] text-fg-dim sm:inline-flex"
            title={chat.people.map((p) => `${p.name} (${ROLE_LABEL[p.role].toLowerCase()})`).join(', ')}>
            <Users size={13} aria-hidden /> {chat.people.length + 1}
          </span>
        )}
      </header>

      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto px-3 py-4 md:px-6" aria-live="polite">
        <div className="mx-auto flex max-w-3xl flex-col gap-4">
          {chat.messages.length === 0 && <p className="text-center text-[13px] text-fg-dim">{tr('ui.sharedChat.nothingHereYet')}</p>}
          {chat.messages.map((m, i) => (
            m.role === 'activity' ? (
              <p key={i} className="font-mono text-[11.5px] text-fg-dim">{tr('ui.sharedChat.usedContent', { content: m.content })}</p>
            ) : m.role === 'user' ? (
              <div key={i} className="flex flex-col items-end gap-1">
                <span className="font-mono text-[11px] text-fg-dim">{m.author}</span>
                <div className={cn('max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md px-3.5 py-2 text-[14px]', m.guest ? 'border border-line bg-panel' : 'bg-panel-2')}>
                  {m.content}
                </div>
              </div>
            ) : (
              <div key={i} className="flex flex-col gap-1">
                <span className="font-mono text-[11px] text-fg-dim">{chat.agent.name}</span>
                <div className="min-w-0 text-[14px] leading-relaxed"><MarkdownMessage content={m.content} /></div>
              </div>
            )
          ))}
          {busy && (
            <div className="flex flex-col gap-1">
              <span className="font-mono text-[11px] text-fg-dim">{chat.agent.name}</span>
              {live && stripThinking(live) ? (
                <div className="min-w-0 text-[14px] leading-relaxed"><MarkdownMessage content={stripThinking(live)} /></div>
              ) : null}
              <span role="status" className="inline-flex items-center gap-2 text-[12.5px] text-fg-dim">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" /> {activity || tr('ui.sharedChat.answering')}
              </span>
            </div>
          )}
        </div>
      </div>

      {canWrite ? (
        <form className="flex-none border-t border-line px-3 py-3 md:px-6" onSubmit={(e) => { e.preventDefault(); void send(); }}>
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <label htmlFor="shared-chat-box" className="sr-only">{tr('ui.sharedChat.message')}</label>
            <textarea id="shared-chat-box" value={draft} rows={1} maxLength={20000}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }}
              placeholder={busy ? tr('ui.sharedChat.waitForTheReply') : tr('ui.sharedChat.askName', { name: chat.agent.name })}
              className="max-h-40 min-h-11 min-w-0 flex-1 resize-none rounded-xl border border-line bg-panel px-3 py-2.5 text-[14px] text-fg placeholder:text-fg-dim focus:border-accent focus:outline-none" />
            <button type="submit" disabled={!draft.trim() || sending || busy}
              className="inline-flex min-h-11 flex-none items-center rounded-xl bg-accent px-4 text-[13.5px] font-semibold text-accent-foreground disabled:opacity-50">
              {tr('ui.sharedChat.send')}
            </button>
          </div>
          <p className="mx-auto mt-1.5 max-w-3xl text-[11.5px] text-fg-dim">
            {sendError ? <span role="alert" className="text-danger-ink">{sendError}</span>
              : tr('ui.sharedChat.nameAnswersYouReadOnly', { name: chat.agent.name })}
          </p>
        </form>
      ) : chat.role === 'view' ? (
        <p className="flex-none border-t border-line px-4 py-3 text-center text-[12.5px] text-fg-dim">{tr('ui.sharedChat.youCanReadThisChat', { name: chat.owner.name })}</p>
      ) : null}
    </div>
  );
}
