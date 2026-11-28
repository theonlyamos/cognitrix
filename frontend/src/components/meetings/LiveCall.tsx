import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { ArrowUp, Eye, EyeOff, HelpCircle, Lock, LogOut, Pause, Play } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { ComputerPanel } from '@/components/ComputerPanel';
import MarkdownMessage from '@/components/MarkdownMessage';
import { Choice, MeetingTabs } from '@/components/MeetingExtras';
import { Badge, PaneHeader } from '@/components/meetings/PaneHeader';
import { PLATFORM, length, type AgentLite, type Meeting } from '@/components/meetings/types';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

type Mode = 'say' | 'note' | 'raise';
type View = 'transcript' | 'notes' | 'private';

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** A transcript line with the person's watch words marked. */
function Marked({ text, words }: { text: string; words: RegExp | null }) {
  if (!words) return <>{text}</>;
  const parts = text.split(words);
  return <>{parts.map((part, i) => (i % 2 ? <mark key={i} className="rounded bg-warn/25 px-0.5 text-inherit">{part}</mark> : part))}</>;
}

function SideHeading({ children }: { children: ReactNode }) {
  return <h3 className="text-[11px] font-medium uppercase tracking-[.08em] text-fg-dim">{children}</h3>;
}

/** An agent in a call: what it hears as it comes in, its question for the
 *  person, one box to talk to the call or to the agent privately, running
 *  notes and guidance on the side, its screen on demand, and Leave. */
export function LiveCall({ meeting, agents, onBack, onChanged, onReload }: {
  meeting: Meeting; agents: AgentLite[]; onBack: () => void; onChanged: () => void; onReload: () => Promise<unknown>;
}) {
  const [watching, setWatching] = useState(false);
  const [view, setView] = useState<View>('transcript');
  const [mode, setMode] = useState<Mode>('say');
  const [text, setText] = useState('');
  const [answer, setAnswer] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const end = useRef<HTMLDivElement>(null);
  const lines = meeting.transcript.split('\n').filter((l) => l.trim());
  const bot = meeting.bot_name || 'The agent';
  const live = meeting.status === 'live';
  const steering = live && !meeting.test;
  const leaving = meeting.progress === 'leaving the call';
  const guidance = meeting.guidance ?? [];
  const watchWords = (meeting.watch_words ?? []).filter((w) => w.trim());
  const marks = watchWords.length ? new RegExp(`(${watchWords.map(escape).join('|')})`, 'gi') : null;

  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }); }, [lines.length]);

  const post = async (path: string, body: object, done: string) => {
    setBusy(true);
    setError('');
    try {
      await api.post(`/meetings/${meeting.id}/${path}`, body);
      setNotice(done);
      window.setTimeout(() => setNotice(''), 4000);
      return true;
    } catch (e) {
      setError(errorMessage(e, tr('ui.meetingExtras.couldNotReachTheAgent')));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const leave = async () => {
    setBusy(true);
    setError('');
    try {
      await api.post(`/meetings/${meeting.id}/leave`);
      await onReload();
      onChanged();
    } catch (e) {
      setError(errorMessage(e, tr('ui.meetings.couldNotReachTheAgent')));
    } finally {
      setBusy(false);
    }
  };
  const reply = async (event: FormEvent) => {
    event.preventDefault();
    if (!answer.trim()) return;
    if (await post('steer', { kind: 'answer', text: answer.trim() }, tr('ui.meetingExtras.botnamePassesYourAnswerOn', { botName: bot }))) {
      setAnswer('');
      void onReload();
    }
  };
  const send = async (event: FormEvent) => {
    event.preventDefault();
    const said = text.trim();
    if (!said) return;
    const ok = mode === 'say'
      ? await post('say', { text: said }, tr('ui.meetings.liveCall.sentToTheCall'))
      : await post('steer', { kind: mode, text: said }, mode === 'raise' ? tr('ui.meetings.liveCall.botBringsItUpAt', { bot }) : tr('ui.meetings.liveCall.botUsesItInIts', { bot }));
    if (ok) {
      setText('');
      if (mode !== 'say') void onReload();
    }
  };
  const pause = () => void post('pause', { paused: !meeting.paused },
    meeting.paused ? tr('ui.meetingExtras.backOnTheRecord') : tr('ui.meetingExtras.pausedNothingIsKeptUntil')).then(() => void onReload());

  const where = PLATFORM[meeting.platform ?? ''] ?? 'the call';
  const badge = live
    ? (meeting.paused ? <Badge tone="warn" pulse={false}>{tr('ui.meetingExtras.offTheRecord')}</Badge>
      : <Badge tone="live" pulse>{['Live', length(meeting.seconds)].filter(Boolean).join(' · ')}</Badge>)
    : meeting.status === 'waiting' ? <Badge tone="plain" pulse>{tr('ui.meetings.liveCall.waitingToBeLetIn')}</Badge>
      : <Badge tone="plain" pulse>{tr('ui.meetings.liveCall.joining')}</Badge>;
  const answers = meeting.reply_mode === 'voice' ? 'answers out loud' : meeting.reply_mode === 'off' ? 'only takes notes' : 'answers in the call\'s chat';
  const meta = live ? `${bot} in ${where} · ${answers}`
    : meeting.status === 'waiting' ? `Waiting for someone in ${where} to let ${bot} in${meeting.progress ? ` · ${meeting.progress}` : ''}`
      : `Joining ${where}${meeting.progress ? ` · ${meeting.progress}` : ''}`;

  const transcript = (
    <div className="min-h-0 flex-1 overflow-y-auto text-[13px] leading-relaxed" aria-label={tr('ui.meetings.liveTranscript')} aria-live="polite">
      {lines.length === 0 ? (
        <p className="text-fg-dim">{live ? tr('ui.meetings.listeningWhatPeopleSayShows') : tr('ui.meetings.nothingHeardYet')}</p>
      ) : lines.map((line, i) => {
        const match = /^\[([\d:]+)\]\s*(.*)$/.exec(line);
        const body = match ? match[2] : line;
        const mine = body.startsWith(`${bot} (`);
        const aside = /^\(.*\)$/.test(body);
        return (
          <p key={i} className={cn('flex gap-2.5 py-[3px]', mine && 'text-accent-ink', aside && 'italic text-fg-dim')}>
            <span className="w-[52px] flex-none pt-[3px] font-mono text-[10.5px] leading-[1.6] text-fg-dim">{match?.[1] ?? ''}</span>
            <span className="min-w-0 break-words">{aside ? body.slice(1, -1) : <Marked text={body} words={marks} />}</span>
          </p>
        );
      })}
      <div ref={end} />
    </div>
  );
  const notes = meeting.live_notes
    ? <div className="md break-words text-[13px]"><MarkdownMessage content={meeting.live_notes} /></div>
    : <p className="text-[12.5px] text-fg-dim">{tr('ui.meetings.liveCall.runningNotesShowUpHere')}</p>;
  const privately = (
    <div className="flex flex-col gap-3">
      {guidance.length === 0 ? (
        <p className="text-[12.5px] text-fg-dim">{tr('ui.meetings.liveCall.nothingYetPickTellBot', { bot })}</p>
      ) : (
        <ul className="flex flex-col gap-1.5 text-[12.5px]">
          {guidance.map((g, i) => <li key={i} className="flex gap-2"><Lock size={12} aria-hidden className="mt-[3px] flex-none text-fg-dim" />{g}</li>)}
        </ul>
      )}
      {watchWords.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <SideHeading>{tr('ui.meetings.liveCall.alertMeOn')}</SideHeading>
          <div className="flex flex-wrap gap-1">
            {watchWords.map((w) => <span key={w} className="rounded-full bg-panel-2 px-2 py-0.5 text-[11.5px] text-fg-dim">{w}</span>)}
          </div>
        </div>
      )}
    </div>
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PaneHeader title={meeting.title} badge={badge} meta={meta} onBack={onBack} actions={(
        <>
          <Button type="button" size="sm" variant="ghost" aria-pressed={watching} onClick={() => setWatching((v) => !v)}>
            {watching ? <EyeOff size={14} aria-hidden /> : <Eye size={14} aria-hidden />}
            {watching ? tr('ui.meetings.hideScreen') : tr('ui.meetings.watch')}
          </Button>
          {steering && (
            <Button type="button" size="sm" variant="outline" disabled={busy} onClick={pause}>
              {meeting.paused ? <Play size={14} aria-hidden /> : <Pause size={14} aria-hidden />}
              {meeting.paused ? tr('ui.meetingExtras.resumeNotes') : tr('ui.meetingExtras.pauseNotes')}
            </Button>
          )}
          <Button type="button" size="sm" variant="outline" disabled={busy || leaving} onClick={() => void leave()}
            className="border-danger/40 text-danger-ink hover:border-danger hover:bg-danger/10">
            <LogOut size={14} aria-hidden />
            {leaving ? tr('ui.meetings.leaving') : tr('ui.meetings.liveCall.leave')}
          </Button>
        </>
      )} />
      <div className="flex min-h-0 flex-1">
        <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 px-3 py-3 md:px-6 md:py-4">
          {steering && meeting.owner_question && (
            <form onSubmit={(e) => void reply(e)} role="alert"
              className="flex flex-none flex-col gap-2 rounded-[10px] border border-warn/60 bg-warn/10 px-3 py-2.5">
              <p className="flex items-start gap-2 text-[13px] leading-relaxed">
                <HelpCircle size={16} aria-hidden className="mt-0.5 flex-none text-warn-ink" />
                <span><span className="font-semibold">{tr('ui.meetingExtras.botnameIsAskingYou', { botName: bot })}</span> {meeting.owner_question}</span>
              </p>
              <div className="flex gap-2">
                <Input value={answer} onChange={(e) => setAnswer(e.target.value)} maxLength={600} autoFocus
                  placeholder={tr('ui.meetingExtras.yourAnswerPassedOnIn')} aria-label={tr('ui.meetingExtras.yourAnswer')} className="h-9 flex-1 bg-panel" />
                <Button type="submit" size="sm" disabled={busy || !answer.trim()}>{tr('ui.meetingExtras.answer')}</Button>
              </div>
            </form>
          )}
          {watching && (
            <div className="h-[380px] flex-none overflow-hidden rounded-[10px] border border-line">
              <ComputerPanel agents={agents} focusAgentId={meeting.agent_id} onClose={() => setWatching(false)} />
            </div>
          )}
          {steering && (
            <MeetingTabs className="flex-none wide:hidden" tabs={['transcript', 'notes', 'private'] as const} value={view} onChange={setView}
              labels={{ transcript: 'Transcript', notes: tr('ui.meetingExtras.runningNotes'), private: 'Private' }} />
          )}
          {(!steering || view === 'transcript') ? transcript : (
            <>
              <div className="hidden min-h-0 flex-1 flex-col wide:flex">{transcript}</div>
              <div className="min-h-0 flex-1 overflow-y-auto wide:hidden">{view === 'notes' ? notes : privately}</div>
            </>
          )}
          {live && (
            <form onSubmit={(e) => void send(e)} className="flex flex-none flex-col gap-2">
              {steering && (
                <div className="self-start">
                  <Choice name={`mode-${meeting.id}`} value={mode} onChange={setMode} options={[
                    { id: 'say', label: tr('ui.meetings.liveCall.sayInCall') },
                    { id: 'note', label: tr('ui.meetings.liveCall.tellBot', { bot }) },
                    { id: 'raise', label: tr('ui.meetingExtras.bringItUp') },
                  ]} />
                </div>
              )}
              <div className="flex gap-2">
                <Input value={text} onChange={(e) => setText(e.target.value)} maxLength={600} className="h-10 flex-1"
                  aria-label={mode === 'say' ? tr('ui.meetings.sayInTheCall') : tr('ui.meetingExtras.privateGuidance')}
                  placeholder={mode === 'raise' ? tr('ui.meetings.liveCall.botRaisesItAtThe', { bot }) : mode === 'note' ? tr('ui.meetings.liveCall.onlyBotSeesThisE', { bot })
                    : meeting.reply_mode === 'voice' ? tr('ui.meetings.haveBotNameSaySomething', { bot_name: bot }) : tr('ui.meetings.haveBotNameWriteIn', { bot_name: bot })} />
                <Button type="submit" size="icon" aria-label={tr('ui.meetings.send')} disabled={busy || !text.trim()} className="h-10 w-10 md:h-10 md:w-10">
                  <ArrowUp size={16} aria-hidden />
                </Button>
              </div>
            </form>
          )}
          {notice && <p aria-live="polite" className="flex-none text-[12.5px] text-fg-dim">{notice}</p>}
          {error && <p role="alert" className="flex-none text-[12.5px] text-danger-ink">{error}</p>}
        </div>
        {steering && (
          <aside className="hidden w-[300px] flex-none flex-col gap-4 overflow-y-auto border-l border-line px-5 py-4 wide:flex" aria-label={tr('ui.meetings.liveCall.callSidePanel')}>
            <div className="flex flex-col gap-2"><SideHeading>{tr('ui.meetingExtras.runningNotes')}</SideHeading>{notes}</div>
            <div className="flex flex-col gap-2 border-t border-line pt-3"><SideHeading>{tr('ui.meetings.liveCall.toldBotPrivately', { bot })}</SideHeading>{privately}</div>
            <p className="mt-auto text-[11.5px] leading-relaxed text-fg-dim">{tr('ui.meetingExtras.anyoneInTheCallCan', { botName: bot })}</p>
          </aside>
        )}
      </div>
    </div>
  );
}
