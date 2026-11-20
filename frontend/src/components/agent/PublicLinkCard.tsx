import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, API_BASE, errorMessage } from '@/lib/api';
import { isDesktop } from '@/lib/desktop';
import { Card, Check, Pill, buttonClass, inputClass, linkButtonClass, primaryButtonClass } from '@/components/agent/ui';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

export interface PublicLink {
  enabled: boolean;
  url: string;
  widget_url: string;
  title: string;
  greeting: string;
  instructions: string;
  web_search: boolean;
  sites: string[];
  daily_limit: number;
  visitor_hourly_limit: number;
  used_today: number;
}

export interface PublicLinkView {
  allowed: boolean;
  agent_name: string;
  has_knowledge: boolean;
  has_web_search: boolean;
  link: PublicLink | null;
}

interface VisitorChat { id: string; preview: string; messages: number; updated: string; running: boolean }
interface Message { role: 'user' | 'assistant'; content: string }

/** Where visitors reach this server: the engine's own address. */
export function serverOrigin(): string {
  try {
    return new URL(API_BASE, window.location.href).origin;
  } catch {
    return window.location.origin;
  }
}

export function embedSnippet(widgetUrl: string): string {
  return `<script src="${serverOrigin()}${widgetUrl}" async></script>`;
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <button type="button" className={buttonClass} aria-label={label}
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setDone(true);
          window.setTimeout(() => setDone(false), 1500);
        }).catch(() => undefined);
      }}>
      {done ? tr('ui.agent.publicLinkCard.copied') : tr('ui.agent.publicLinkCard.copy')}
    </button>
  );
}

function when(value: string): string {
  const date = new Date(value.includes('T') || value.endsWith('Z') ? value : `${value.replace(' ', 'T')}Z`);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

/** Agent page › Sharing › Public link: anyone with the link, or a website
 *  that adds the chat bubble, can chat with this agent without an account. */
export default function PublicLinkCard({ agentId }: { agentId: string }) {
  const [view, setView] = useState<PublicLinkView | null>(null);
  const [form, setForm] = useState<PublicLink | null>(null);
  const [sitesText, setSitesText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const [chats, setChats] = useState<VisitorChat[] | null>(null);
  const [openChat, setOpenChat] = useState<{ id: string; messages: Message[] } | null>(null);

  const apply = useCallback((next: PublicLinkView) => {
    setView(next);
    setForm(next.link);
    setSitesText((next.link?.sites || []).join('\n'));
  }, []);

  const base = `/public-links/agents/${encodeURIComponent(agentId)}`;

  useEffect(() => {
    api.get<PublicLinkView>(base).then((r) => apply(r.data))
      .catch((e) => setError(errorMessage(e, tr('ui.agent.publicLinkCard.couldNotLoadThePublic'))));
  }, [base, apply]);

  const loadChats = useCallback(() => {
    api.get<{ chats: VisitorChat[] }>(`${base}/chats`).then((r) => setChats(r.data.chats))
      .catch(() => setChats([]));
  }, [base]);

  useEffect(() => {
    if (view?.link) loadChats();
  }, [view?.link, loadChats]);

  const dirty = useMemo(() => {
    const link = view?.link;
    if (!link || !form) return false;
    return JSON.stringify([form.title, form.greeting, form.instructions, form.web_search, form.daily_limit, form.visitor_hourly_limit, sitesText.trim()])
      !== JSON.stringify([link.title, link.greeting, link.instructions, link.web_search, link.daily_limit, link.visitor_hourly_limit, link.sites.join('\n')]);
  }, [view, form, sitesText]);

  const call = async (fn: () => Promise<{ data: PublicLinkView }>, done: string) => {
    setBusy(true);
    setError(null);
    setNotice('');
    try {
      apply((await fn()).data);
      setNotice(done);
    } catch (e) {
      setError(errorMessage(e, tr('ui.agent.publicLinkCard.couldNotSaveThePublic')));
    } finally {
      setBusy(false);
    }
  };

  if (!view) {
    return error ? <p role="alert" className="text-[13px] text-danger-ink">{error}</p> : null;
  }

  const link = view.link;
  const fullUrl = link ? `${serverOrigin()}${link.url}` : '';

  const save = () => call(() => api.put(base, {
    title: form?.title ?? '',
    greeting: form?.greeting ?? '',
    instructions: form?.instructions ?? '',
    web_search: !!form?.web_search,
    daily_limit: Number(form?.daily_limit) || 200,
    visitor_hourly_limit: Number(form?.visitor_hourly_limit) || 20,
    sites: sitesText.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean),
  }), tr('ui.agent.publicLinkCard.publicLinkSaved'));

  const set = <K extends keyof PublicLink>(key: K, value: PublicLink[K]) => {
    setForm((cur) => (cur ? { ...cur, [key]: value } : cur));
    setNotice('');
  };

  return (
    <Card
      title={tr('ui.agent.publicLinkCard.publicLink')}
      description={tr('ui.agent.publicLinkCard.anyoneWithTheLinkCan')}
      aside={link ? <Pill tone={link.enabled ? 'ok' : 'dim'}>{link.enabled ? tr('ui.agent.publicLinkCard.on') : tr('ui.agent.publicLinkCard.off')}</Pill> : undefined}
    >
      {!view.allowed && (
        <p className="mb-3 rounded-md border border-line px-3 py-2.5 text-[13px] text-fg-dim">
          {tr('ui.agent.publicLinkCard.publicAgentLinksAreTurned')}
        </p>
      )}
      <p className="text-[13.5px] leading-relaxed text-fg-dim">
        {view.has_web_search ? tr('ui.agent.publicLinkCard.visitorsOnlyGetAnswersWeb') : tr('ui.agent.publicLinkCard.visitorsOnlyGetAnswers')}
      </p>
      {isDesktop() && (
        <p className="mt-3 rounded-md border border-warn-ink/50 px-3 py-2.5 text-[13px] leading-relaxed">
          {tr('ui.agent.publicLinkCard.thisLinkPointsAtThis')}
        </p>
      )}

      {!link ? (
        <div className="mt-4">
          <button type="button" className={primaryButtonClass} disabled={busy || !view.allowed}
            onClick={() => void call(() => api.put(base, { enabled: true }), tr('ui.agent.publicLinkCard.publicLinkIsOn'))}>
            {busy ? tr('ui.agent.publicLinkCard.turningOn') : tr('ui.agent.publicLinkCard.turnOnPublicLink')}
          </button>
          {error && <p role="alert" className="mt-2 text-[13px] text-danger-ink">{error}</p>}
        </div>
      ) : (
        <div className="mt-4 flex flex-col gap-5">
          <Check
            checked={link.enabled}
            disabled={busy || (!view.allowed && !link.enabled)}
            onChange={(v) => void call(() => api.put(base, { enabled: v }), v ? tr('ui.agent.publicLinkCard.publicLinkIsOn') : tr('ui.agent.publicLinkCard.publicLinkIsOff'))}
            label={tr('ui.agent.publicLinkCard.linkIsOn')}
            detail={tr('ui.agent.publicLinkCard.turnItOffToStop')}
          />

          <div>
            <div className="text-[14.5px] font-medium">{tr('ui.agent.publicLinkCard.link')}</div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <input readOnly value={fullUrl} aria-label={tr('ui.agent.publicLinkCard.publicLink')} className={cn(inputClass, 'min-w-0 flex-1 font-mono text-[12.5px]')}
                onFocus={(e) => e.currentTarget.select()} />
              <CopyButton text={fullUrl} label={tr('ui.agent.publicLinkCard.copyPublicLink')} />
              <a href={fullUrl} target="_blank" rel="noreferrer" className={buttonClass}>{tr('ui.agent.publicLinkCard.open')}</a>
            </div>
          </div>

          <div>
            <div className="text-[14.5px] font-medium">{tr('ui.agent.publicLinkCard.chatBubbleForAWebsite')}</div>
            <div className="mt-0.5 text-[13px] text-fg-dim">{tr('ui.agent.publicLinkCard.pasteThisBeforeBodyOn')}</div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <input readOnly value={embedSnippet(link.widget_url)} aria-label={tr('ui.agent.publicLinkCard.websiteCode')} className={cn(inputClass, 'min-w-0 flex-1 font-mono text-[12.5px]')}
                onFocus={(e) => e.currentTarget.select()} />
              <CopyButton text={embedSnippet(link.widget_url)} label={tr('ui.agent.publicLinkCard.copyWebsiteCode')} />
            </div>
          </div>

          {form && (
            <div className="flex flex-col gap-4 border-t border-line pt-4">
              <label className="flex flex-col gap-1.5">
                <span className="text-[14.5px] font-medium">{tr('ui.agent.publicLinkCard.title')}</span>
                <input className={inputClass} value={form.title} maxLength={80} placeholder={view.agent_name}
                  onChange={(e) => set('title', e.target.value)} />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-[14.5px] font-medium">{tr('ui.agent.publicLinkCard.greeting')}</span>
                <span className="text-[13px] text-fg-dim">{tr('ui.agent.publicLinkCard.theFirstMessageVisitorsSee')}</span>
                <textarea className={cn(inputClass, 'h-auto min-h-[72px] py-2')} value={form.greeting} maxLength={500}
                  placeholder={tr('ui.agent.publicLinkCard.hiAskMeAnythingAbout')} onChange={(e) => set('greeting', e.target.value)} />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-[14.5px] font-medium">{tr('ui.agent.publicLinkCard.instructionsForVisitors')}</span>
                <span className="text-[13px] text-fg-dim">{tr('ui.agent.publicLinkCard.addedToTheAgentS')}</span>
                <textarea className={cn(inputClass, 'h-auto min-h-[96px] py-2')} value={form.instructions} maxLength={4000}
                  placeholder={tr('ui.agent.publicLinkCard.onlyAnswerQuestionsAboutOur')}
                  onChange={(e) => set('instructions', e.target.value)} />
              </label>
              <Check
                checked={form.web_search}
                disabled={!view.has_web_search}
                onChange={(v) => set('web_search', v)}
                label={tr('ui.agent.publicLinkCard.letItSearchTheWeb')}
                detail={view.has_web_search ? tr('ui.agent.publicLinkCard.usesTheAgentSWeb') : tr('ui.agent.publicLinkCard.thisAgentHasNoWeb')}
              />
              {!view.has_knowledge && (
                <p className="text-[13px] text-fg-dim">{tr('ui.agent.publicLinkCard.tipGiveTheAgentDocuments')}</p>
              )}
              <div className="flex flex-wrap gap-4">
                <label className="flex flex-col gap-1.5">
                  <span className="text-[14.5px] font-medium">{tr('ui.agent.publicLinkCard.messagesADay')}</span>
                  <span className="text-[13px] text-fg-dim">{tr('ui.agent.publicLinkCard.allVisitorsTogetherUsedToday', { used_today: link.used_today })}</span>
                  <input type="number" min={1} max={10000} className={cn(inputClass, 'w-[150px]')} value={form.daily_limit}
                    onChange={(e) => set('daily_limit', Number(e.target.value))} />
                </label>
                <label className="flex flex-col gap-1.5">
                  <span className="text-[14.5px] font-medium">{tr('ui.agent.publicLinkCard.perVisitorPerHour')}</span>
                  <span className="text-[13px] text-fg-dim">{tr('ui.agent.publicLinkCard.messagesOneVisitorCanSend')}</span>
                  <input type="number" min={1} max={500} className={cn(inputClass, 'w-[150px]')} value={form.visitor_hourly_limit}
                    onChange={(e) => set('visitor_hourly_limit', Number(e.target.value))} />
                </label>
              </div>
              <label className="flex flex-col gap-1.5">
                <span className="text-[14.5px] font-medium">{tr('ui.agent.publicLinkCard.websitesThatMayShowThe')}</span>
                <span className="text-[13px] text-fg-dim">{tr('ui.agent.publicLinkCard.onePerLineLikeHttps')}</span>
                <textarea className={cn(inputClass, 'h-auto min-h-[72px] py-2 font-mono text-[12.5px]')} value={sitesText}
                  onChange={(e) => { setSitesText(e.target.value); setNotice(''); }} />
              </label>
              <div className="flex flex-wrap items-center gap-3">
                <button type="button" className={primaryButtonClass} disabled={busy || !dirty} onClick={() => void save()}>
                  {busy ? tr('ui.agent.publicLinkCard.saving') : tr('ui.agent.publicLinkCard.save')}
                </button>
                {error && <p role="alert" className="text-[13px] text-danger-ink">{error}</p>}
                {notice && !dirty && <p role="status" className="text-[13px] text-fg-dim">{notice}</p>}
              </div>
            </div>
          )}

          <div className="border-t border-line pt-4">
            <div className="flex flex-wrap items-center gap-2">
              <div className="min-w-[200px] flex-1">
                <div className="text-[14.5px] font-medium">{tr('ui.agent.publicLinkCard.visitorsChats')}</div>
                <div className="mt-0.5 text-[13px] text-fg-dim">{tr('ui.agent.publicLinkCard.whatVisitorsAskedAndWhat')}</div>
              </div>
              <button type="button" className={buttonClass} onClick={loadChats}>{tr('ui.agent.publicLinkCard.refresh')}</button>
            </div>
            {chats === null ? (
              <p className="mt-3 text-[13px] text-fg-dim">{tr('ui.agent.publicLinkCard.loading')}</p>
            ) : chats.length === 0 ? (
              <p className="mt-3 text-[13px] text-fg-dim">{tr('ui.agent.publicLinkCard.noVisitorsYet')}</p>
            ) : (
              <ul className="mt-2 divide-y divide-line">
                {chats.map((chat) => (
                  <li key={chat.id} className="py-2.5">
                    <button type="button" className="flex w-full flex-wrap items-baseline gap-x-3 gap-y-1 text-left"
                      aria-expanded={openChat?.id === chat.id}
                      onClick={() => {
                        if (openChat?.id === chat.id) { setOpenChat(null); return; }
                        api.get<{ messages: Message[] }>(`${base}/chats/${encodeURIComponent(chat.id)}`)
                          .then((r) => setOpenChat({ id: chat.id, messages: r.data.messages }))
                          .catch((e) => setError(errorMessage(e, tr('ui.agent.publicLinkCard.couldNotOpenThatChat'))));
                      }}>
                      <span className="min-w-0 flex-1 truncate text-[14px]">{chat.preview || tr('ui.agent.publicLinkCard.noMessage')}</span>
                      <span className="font-mono text-[11.5px] text-fg-dim">{tr('ui.agent.publicLinkCard.messagesMessagesWhenValue', { messages: chat.messages, when: when(chat.updated), value: chat.running ? tr('ui.agent.publicLinkCard.answering') : '' })}</span>
                    </button>
                    {openChat?.id === chat.id && (
                      <div className="mt-2 flex flex-col gap-2 rounded-md border border-line bg-bg p-3">
                        {openChat.messages.map((m, i) => (
                          <div key={i} className={cn('whitespace-pre-wrap break-words text-[13.5px] leading-relaxed', m.role === 'user' ? 'text-fg' : 'text-fg-dim')}>
                            <span className="mr-2 font-mono text-[11px] uppercase tracking-wide">{m.role === 'user' ? tr('ui.agent.publicLinkCard.visitor') : view.agent_name}</span>
                            {m.content}
                          </div>
                        ))}
                        <div>
                          <button type="button" className={linkButtonClass}
                            onClick={() => {
                              void api.delete(`${base}/chats/${encodeURIComponent(chat.id)}`).then(() => { setOpenChat(null); loadChats(); });
                            }}>{tr('ui.agent.publicLinkCard.deleteThisChat')}</button>
                        </div>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="flex flex-wrap gap-2 border-t border-line pt-4">
            <button type="button" className={buttonClass} disabled={busy}
              onClick={() => {
                if (window.confirm(tr('ui.agent.publicLinkCard.makeANewLinkAddress'))) {
                  void call(() => api.post(`${base}/rotate`), tr('ui.agent.publicLinkCard.newLinkMadeUpdateYour'));
                }
              }}>{tr('ui.agent.publicLinkCard.newLinkAddress')}</button>
            <button type="button" className={cn(buttonClass, 'text-danger-ink')} disabled={busy}
              onClick={() => {
                if (window.confirm(tr('ui.agent.publicLinkCard.removeThePublicLinkAnd'))) {
                  void call(() => api.delete(base), tr('ui.agent.publicLinkCard.publicLinkRemoved'));
                }
              }}>{tr('ui.agent.publicLinkCard.removeLink')}</button>
          </div>
        </div>
      )}
    </Card>
  );
}
