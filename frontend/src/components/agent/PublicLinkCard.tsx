import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, API_BASE, errorMessage } from '@/lib/api';
import { isDesktop } from '@/lib/desktop';
import { Card, Check, Pill, buttonClass, inputClass, linkButtonClass, primaryButtonClass } from '@/components/agent/ui';
import { cn } from '@/lib/utils';

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
      {done ? 'Copied' : 'Copy'}
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
      .catch((e) => setError(errorMessage(e, 'Could not load the public link.')));
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
      setError(errorMessage(e, 'Could not save the public link.'));
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
  }), 'Public link saved.');

  const set = <K extends keyof PublicLink>(key: K, value: PublicLink[K]) => {
    setForm((cur) => (cur ? { ...cur, [key]: value } : cur));
    setNotice('');
  };

  return (
    <Card
      title="Public link"
      description="Anyone with the link can chat with this agent, no account needed."
      aside={link ? <Pill tone={link.enabled ? 'ok' : 'dim'}>{link.enabled ? 'on' : 'off'}</Pill> : undefined}
    >
      {!view.allowed && (
        <p className="mb-3 rounded-md border border-line px-3 py-2.5 text-[13px] text-fg-dim">
          Public agent links are turned off on this server (COGNITRIX_PUBLIC_AGENT_LINKS).
        </p>
      )}
      <p className="text-[13.5px] leading-relaxed text-fg-dim">
        Visitors can only get answers: the agent can look things up in the documents you gave it
        {view.has_web_search ? ' and, if you allow it, search the web' : ''}. It can't use its other tools, your files,
        connected apps, memories or chats for them, and nothing waits for your approval. What visitors use counts toward your usage and spending limit.
      </p>
      {isDesktop() && (
        <p className="mt-3 rounded-md border border-warn-ink/50 px-3 py-2.5 text-[13px] leading-relaxed">
          This link points at this computer. People elsewhere can only open it if this engine is reachable from the internet, for example when Cognitrix runs on a server.
        </p>
      )}

      {!link ? (
        <div className="mt-4">
          <button type="button" className={primaryButtonClass} disabled={busy || !view.allowed}
            onClick={() => void call(() => api.put(base, { enabled: true }), 'Public link is on.')}>
            {busy ? 'Turning on…' : 'Turn on public link'}
          </button>
          {error && <p role="alert" className="mt-2 text-[13px] text-danger-ink">{error}</p>}
        </div>
      ) : (
        <div className="mt-4 flex flex-col gap-5">
          <Check
            checked={link.enabled}
            disabled={busy || (!view.allowed && !link.enabled)}
            onChange={(v) => void call(() => api.put(base, { enabled: v }), v ? 'Public link is on.' : 'Public link is off.')}
            label="Link is on"
            detail="Turn it off to stop all visitors at once. The address stays the same when you turn it back on."
          />

          <div>
            <div className="text-[14.5px] font-medium">Link</div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <input readOnly value={fullUrl} aria-label="Public link" className={cn(inputClass, 'min-w-0 flex-1 font-mono text-[12.5px]')}
                onFocus={(e) => e.currentTarget.select()} />
              <CopyButton text={fullUrl} label="Copy public link" />
              <a href={fullUrl} target="_blank" rel="noreferrer" className={buttonClass}>Open</a>
            </div>
          </div>

          <div>
            <div className="text-[14.5px] font-medium">Chat bubble for a website</div>
            <div className="mt-0.5 text-[13px] text-fg-dim">Paste this before &lt;/body&gt; on your site. It adds a chat button in the corner.</div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <input readOnly value={embedSnippet(link.widget_url)} aria-label="Website code" className={cn(inputClass, 'min-w-0 flex-1 font-mono text-[12.5px]')}
                onFocus={(e) => e.currentTarget.select()} />
              <CopyButton text={embedSnippet(link.widget_url)} label="Copy website code" />
            </div>
          </div>

          {form && (
            <div className="flex flex-col gap-4 border-t border-line pt-4">
              <label className="flex flex-col gap-1.5">
                <span className="text-[14.5px] font-medium">Title</span>
                <input className={inputClass} value={form.title} maxLength={80} placeholder={view.agent_name}
                  onChange={(e) => set('title', e.target.value)} />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-[14.5px] font-medium">Greeting</span>
                <span className="text-[13px] text-fg-dim">The first message visitors see.</span>
                <textarea className={cn(inputClass, 'h-auto min-h-[72px] py-2')} value={form.greeting} maxLength={500}
                  placeholder="Hi! Ask me anything about…" onChange={(e) => set('greeting', e.target.value)} />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-[14.5px] font-medium">Instructions for visitors</span>
                <span className="text-[13px] text-fg-dim">Added to the agent's own instructions when it answers visitors. Visitors don't see them.</span>
                <textarea className={cn(inputClass, 'h-auto min-h-[96px] py-2')} value={form.instructions} maxLength={4000}
                  placeholder="Only answer questions about our product. Point people to support@… for refunds."
                  onChange={(e) => set('instructions', e.target.value)} />
              </label>
              <Check
                checked={form.web_search}
                disabled={!view.has_web_search}
                onChange={(v) => set('web_search', v)}
                label="Let it search the web for visitors"
                detail={view.has_web_search ? 'Uses the agent\'s web search tool.' : 'This agent has no web search tool. Add one on the Tools tab first.'}
              />
              {!view.has_knowledge && (
                <p className="text-[13px] text-fg-dim">Tip: give the agent documents (Connections › Knowledge) so it can answer from them.</p>
              )}
              <div className="flex flex-wrap gap-4">
                <label className="flex flex-col gap-1.5">
                  <span className="text-[14.5px] font-medium">Messages a day</span>
                  <span className="text-[13px] text-fg-dim">All visitors together. {link.used_today} used today.</span>
                  <input type="number" min={1} max={10000} className={cn(inputClass, 'w-[150px]')} value={form.daily_limit}
                    onChange={(e) => set('daily_limit', Number(e.target.value))} />
                </label>
                <label className="flex flex-col gap-1.5">
                  <span className="text-[14.5px] font-medium">Per visitor, per hour</span>
                  <span className="text-[13px] text-fg-dim">Messages one visitor can send.</span>
                  <input type="number" min={1} max={500} className={cn(inputClass, 'w-[150px]')} value={form.visitor_hourly_limit}
                    onChange={(e) => set('visitor_hourly_limit', Number(e.target.value))} />
                </label>
              </div>
              <label className="flex flex-col gap-1.5">
                <span className="text-[14.5px] font-medium">Websites that may show the chat</span>
                <span className="text-[13px] text-fg-dim">One per line, like https://example.com. Leave empty to allow any website.</span>
                <textarea className={cn(inputClass, 'h-auto min-h-[72px] py-2 font-mono text-[12.5px]')} value={sitesText}
                  onChange={(e) => { setSitesText(e.target.value); setNotice(''); }} />
              </label>
              <div className="flex flex-wrap items-center gap-3">
                <button type="button" className={primaryButtonClass} disabled={busy || !dirty} onClick={() => void save()}>
                  {busy ? 'Saving…' : 'Save'}
                </button>
                {error && <p role="alert" className="text-[13px] text-danger-ink">{error}</p>}
                {notice && !dirty && <p role="status" className="text-[13px] text-fg-dim">{notice}</p>}
              </div>
            </div>
          )}

          <div className="border-t border-line pt-4">
            <div className="flex flex-wrap items-center gap-2">
              <div className="min-w-[200px] flex-1">
                <div className="text-[14.5px] font-medium">Visitors' chats</div>
                <div className="mt-0.5 text-[13px] text-fg-dim">What visitors asked and what the agent answered. Visitors are anonymous.</div>
              </div>
              <button type="button" className={buttonClass} onClick={loadChats}>Refresh</button>
            </div>
            {chats === null ? (
              <p className="mt-3 text-[13px] text-fg-dim">Loading…</p>
            ) : chats.length === 0 ? (
              <p className="mt-3 text-[13px] text-fg-dim">No visitors yet.</p>
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
                          .catch((e) => setError(errorMessage(e, 'Could not open that chat.')));
                      }}>
                      <span className="min-w-0 flex-1 truncate text-[14px]">{chat.preview || '(no message)'}</span>
                      <span className="font-mono text-[11.5px] text-fg-dim">{chat.messages} messages · {when(chat.updated)}{chat.running ? ' · answering' : ''}</span>
                    </button>
                    {openChat?.id === chat.id && (
                      <div className="mt-2 flex flex-col gap-2 rounded-md border border-line bg-bg p-3">
                        {openChat.messages.map((m, i) => (
                          <div key={i} className={cn('whitespace-pre-wrap break-words text-[13.5px] leading-relaxed', m.role === 'user' ? 'text-fg' : 'text-fg-dim')}>
                            <span className="mr-2 font-mono text-[11px] uppercase tracking-wide">{m.role === 'user' ? 'Visitor' : view.agent_name}</span>
                            {m.content}
                          </div>
                        ))}
                        <div>
                          <button type="button" className={linkButtonClass}
                            onClick={() => {
                              void api.delete(`${base}/chats/${encodeURIComponent(chat.id)}`).then(() => { setOpenChat(null); loadChats(); });
                            }}>Delete this chat</button>
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
                if (window.confirm('Make a new link address? The current link and websites using it stop working.')) {
                  void call(() => api.post(`${base}/rotate`), 'New link made. Update your website code.');
                }
              }}>New link address</button>
            <button type="button" className={cn(buttonClass, 'text-danger-ink')} disabled={busy}
              onClick={() => {
                if (window.confirm('Remove the public link and delete all visitors\' chats?')) {
                  void call(() => api.delete(base), 'Public link removed.');
                }
              }}>Remove link</button>
          </div>
        </div>
      )}
    </Card>
  );
}
