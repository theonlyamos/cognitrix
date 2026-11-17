import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Sunrise, ThumbsDown, ThumbsUp, X } from 'lucide-react';
import { errorMessage } from '@/lib/api';
import { timeAgo } from '@/lib/agentDraft';
import { cn } from '@/lib/utils';
import {
  briefFeedback, briefNow, enableProactive, getProactivity, getSituation, latestBrief,
  type BriefItem, type BriefSection, type BriefVerdict, type MorningBrief, type Proactivity, type Situation,
} from '@/lib/proactive';
import { tr } from '@/lib/i18n';

const ORDER: BriefSection[] = ['today', 'reply', 'waiting', 'stalled', 'offers', 'other'];

function sectionTitle(section: BriefSection): string {
  switch (section) {
    case 'today': return 'Today';
    case 'reply': return tr('ui.briefCard.needsYourReply');
    case 'waiting': return tr('ui.briefCard.waitingOnOthers');
    case 'stalled': return 'Stuck';
    case 'offers': return tr('ui.briefCard.iCanTakeThese');
    default: return 'Also';
  }
}

function todayLocal(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** Dashboard: the morning brief from your main assistant, with feedback, "Brief me now", and the setup banner. */
export function BriefCard({ className }: { className?: string }) {
  const [settings, setSettings] = useState<Proactivity | null>(null);
  const [brief, setBrief] = useState<MorningBrief | null | undefined>(undefined);
  const [situation, setSituation] = useState<Situation | null>(null);
  const [busy, setBusy] = useState(false);
  const [asked, setAsked] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [s, b, sit] = await Promise.allSettled([getProactivity(), latestBrief(), getSituation()]);
    if (s.status === 'fulfilled') setSettings(s.value);
    setBrief(b.status === 'fulfilled' ? b.value : null);
    if (sit.status === 'fulfilled') setSituation(sit.value);
  }, []);

  useEffect(() => { void load(); }, [load]);
  // While a brief someone asked for is being written, look again every few seconds.
  useEffect(() => {
    if (!asked) return undefined;
    const started = Date.now();
    const timer = window.setInterval(() => {
      void latestBrief().then((b) => {
        if (b && (b.id !== brief?.id || b.summary !== brief?.summary)) { setBrief(b); setAsked(false); }
      }).catch(() => {});
      if (Date.now() - started > 5 * 60_000) setAsked(false);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [asked, brief]);

  if (!settings || brief === undefined) return null;
  if (settings.level === 'off' && !brief) return null;

  const act = async (fn: () => Promise<void>, fallback: string) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errorMessage(e, fallback));
    } finally {
      setBusy(false);
    }
  };

  if (settings.needs_setup) {
    if (!settings.main_assistant) return null;
    return (
      <section aria-label={tr('ui.briefCard.turnOnCheckIns')} className={cn('flex flex-wrap items-center gap-3 rounded-[14px] border border-accent/40 bg-accent/10 px-4 py-3', className)}>
        <Sunrise className="h-[18px] w-[18px] flex-none text-accent-ink" aria-hidden="true" />
        <p className="min-w-0 flex-1 basis-[240px] text-[13px]">
          {tr('ui.briefCard.letNameKeepAnEye', { name: settings.main_assistant.name })}
        </p>
        <Link to="/settings/assistant" className="text-[12.5px] text-fg-dim hover:text-fg hover:underline">{tr('ui.briefCard.settings')}</Link>
        <button type="button" disabled={busy}
          className="inline-flex h-9 items-center rounded-lg bg-accent px-3 text-[12.5px] font-medium text-accent-foreground disabled:opacity-60 md:h-8"
          onClick={() => void act(async () => { setSettings(await enableProactive()); }, tr('ui.briefCard.couldNotTurnCheckIns'))}>
          {tr('ui.briefCard.turnOn')}
        </button>
        {error && <p role="alert" className="basis-full text-[12.5px] text-danger-ink">{error}</p>}
      </section>
    );
  }

  const isToday = brief?.day === todayLocal();
  const grouped = ORDER
    .map((section) => ({ section, items: (brief?.items || []).filter((i) => i.section === section && i.feedback !== 'never') }))
    .filter((g) => g.items.length);
  const comingUp = [...(situation?.meetings || []), ...(situation?.due || [])].slice(0, 4);

  const answer = (item: BriefItem, verdict: BriefVerdict) => brief && void act(async () => {
    setBrief(await briefFeedback(brief.id, item.index, verdict));
  }, tr('ui.briefCard.couldNotSaveThat'));

  return (
    <section aria-label={tr('ui.briefCard.morningBrief')} className={cn('flex min-w-0 flex-col rounded-[14px] border border-line bg-panel', className)}>
      <header className="flex min-h-12 flex-wrap items-center gap-2.5 border-b border-line px-4 py-2">
        <Sunrise className="h-[15px] w-[15px] flex-none text-fg-dim" strokeWidth={1.9} aria-hidden="true" />
        <h2 className="flex-1 text-[13.5px] font-semibold tracking-tight">
          {isToday ? tr('ui.briefCard.yourMorningBrief') : brief ? tr('ui.briefCard.briefFromDay', { day: brief.day }) : tr('ui.briefCard.morningBrief')}
        </h2>
        {brief?.agent_name && <span className="font-mono text-[11px] text-fg-dim">{brief.agent_name}{brief.created_at ? ` · ${timeAgo(brief.created_at)}` : ''}</span>}
        <button type="button" disabled={busy || asked}
          className="inline-flex h-8 items-center rounded-lg border border-line px-2.5 text-[12px] hover:bg-panel-2 disabled:opacity-60"
          onClick={() => void act(async () => { await briefNow(); setAsked(true); }, tr('ui.briefCard.couldNotStartTheBrief'))}>
          {asked ? tr('ui.briefCard.writing') : tr('ui.briefCard.briefMeNow')}
        </button>
      </header>
      <div className="space-y-3 px-4 py-3">
        {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
        {!brief ? (
          <p className="text-[13px] text-fg-dim">
            {tr('ui.briefCard.yourFirstBriefArrivesAt', { brief_time: settings.brief_time })}
          </p>
        ) : (
          <>
            {brief.summary && <p className="text-[13.5px] leading-relaxed">{brief.summary}</p>}
            {grouped.map((g) => (
              <div key={g.section}>
                <h3 className="mb-1 font-mono text-[10.5px] tracking-[0.12em] text-fg-dim">{sectionTitle(g.section).toUpperCase()}</h3>
                <ul className="space-y-1">
                  {g.items.map((item) => (
                    <li key={item.index} className="group flex items-start gap-2">
                      <div className="min-w-0 flex-1 text-[13px] leading-snug">
                        {item.link ? (
                          item.link.startsWith('/')
                            ? <Link to={item.link} className="hover:underline">{item.text}</Link>
                            : <a href={item.link} target="_blank" rel="noreferrer" className="hover:underline">{item.text}</a>
                        ) : item.text}
                        {item.why && <span className="text-fg-dim">{` · ${item.why}`}</span>}
                      </div>
                      {item.feedback ? (
                        <span className="flex-none font-mono text-[10.5px] text-fg-dim">{item.feedback === 'useful' ? tr('ui.briefCard.useful') : tr('ui.briefCard.notUseful')}</span>
                      ) : (
                        <span className="flex flex-none items-center gap-0.5 opacity-70 transition-opacity group-hover:opacity-100">
                          <button type="button" disabled={busy} aria-label={tr('ui.briefCard.useful_61ef')} title={tr('ui.briefCard.useful_61ef')}
                            className="grid h-7 w-7 place-items-center rounded text-fg-dim hover:bg-panel-2 hover:text-fg"
                            onClick={() => answer(item, 'useful')}><ThumbsUp className="h-3.5 w-3.5" aria-hidden="true" /></button>
                          <button type="button" disabled={busy} aria-label={tr('ui.briefCard.notUseful_33d2')} title={tr('ui.briefCard.notUseful_33d2')}
                            className="grid h-7 w-7 place-items-center rounded text-fg-dim hover:bg-panel-2 hover:text-fg"
                            onClick={() => answer(item, 'not_useful')}><ThumbsDown className="h-3.5 w-3.5" aria-hidden="true" /></button>
                          <button type="button" disabled={busy} aria-label={tr('ui.briefCard.neverShowThisAgain')} title={tr('ui.briefCard.neverShowThisAgain')}
                            className="grid h-7 w-7 place-items-center rounded text-fg-dim hover:bg-panel-2 hover:text-danger-ink"
                            onClick={() => answer(item, 'never')}><X className="h-3.5 w-3.5" aria-hidden="true" /></button>
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            {!grouped.length && <p className="text-[13px] text-fg-dim">{tr('ui.briefCard.nothingNeedsYou')}</p>}
          </>
        )}
        {comingUp.length > 0 && (
          <div className="border-t border-line pt-2.5">
            <h3 className="mb-1 font-mono text-[10.5px] tracking-[0.12em] text-fg-dim">{tr('ui.briefCard.comingUp')}</h3>
            <ul className="space-y-0.5">
              {comingUp.map((i) => (
                <li key={i.key || i.title} className={cn('text-[12.5px]', i.urgent ? 'text-warn-ink' : 'text-fg-dim')}>
                  {i.href?.startsWith('/') ? <Link to={i.href} className="hover:text-fg hover:underline">{i.title}</Link> : i.title}
                  {i.detail ? ` · ${i.detail}` : ''}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}
