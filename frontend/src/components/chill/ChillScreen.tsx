import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from 'react';
import { AppWindow, History, Monitor, MessageSquareText, Plus, SquareStack } from 'lucide-react';
import { KindIcon } from '@/components/files/FileCards';
import type { FileKind } from '@/lib/producedFiles';
import type { LivePhase } from '@/hooks/useLiveVoice';
import { greetingFor } from '@/components/NewChatGreeting';
import { cn } from '@/lib/utils';
import { plainText } from '@/lib/plainText';
import { tr } from '@/lib/i18n';

const MarkdownMessage = lazy(() => import('@/components/MarkdownMessage'));

/** One thing the chat made, in the rail that flips the stage between them. */
export interface ChillRailItem {
  key: string;
  kind: 'answer' | 'page' | 'file' | 'app' | 'computer';
  label: string;
  fileKind?: FileKind;
}

export interface ChillChat {
  id: string;
  title: string;
}

export interface ChillScreenProps {
  /** Nothing said yet: the greeting with the box in the middle. */
  empty: boolean;
  name?: string | null;
  assistantName: string;
  /** The message box. */
  composer: ReactNode;
  /** A file, page, app or computer filling the window, or null for the answer. */
  stage: ReactNode | null;
  /** What the person said last. */
  question: string | null;
  /** The newest reply. */
  answer: string | null;
  streaming: boolean;
  /** Shown while the assistant works before it says anything. */
  thinking: string | null;
  /** The helper agent the assistant is working with right now. */
  helper: { name: string; doing: string } | null;
  /** Live voice on: what it hears. */
  listening: { phase: LivePhase; heard: string } | null;
  /** Approvals, questions and errors: always above the box. */
  cards: ReactNode;
  /** Under the answer: file cards, changes. */
  extras?: ReactNode;
  rail: ChillRailItem[];
  railActive: string;
  onRail: (key: string) => void;
  suggestions: string[];
  onSuggestion: (text: string) => void;
  onNewChat: () => void;
  chats: ChillChat[];
  activeChatId: string | null;
  onOpenChat: (id: string) => void;
  onFullMode: () => void;
}

function RailIcon({ item }: { item: ChillRailItem }) {
  if (item.kind === 'file' && item.fileKind) return <KindIcon kind={item.fileKind} size={16} />;
  if (item.kind === 'page') return <SquareStack size={16} aria-hidden />;
  if (item.kind === 'app') return <AppWindow size={16} aria-hidden />;
  if (item.kind === 'computer') return <Monitor size={16} aria-hidden />;
  return <MessageSquareText size={16} aria-hidden />;
}

/** The Chill / Full switch, top right. */
export function ModeSwitch({ chill, onChill, onFull, className }: { chill: boolean; onChill: () => void; onFull: () => void; className?: string }) {
  return (
    <div
      role="group"
      aria-label={tr('ui.chill.appMode')}
      title={tr('ui.chill.switchModesCtrlShift')}
      className={cn('chill-glass flex flex-none items-center gap-0.5 rounded-full p-[3px] text-[12.5px] font-medium', className)}
    >
      <button type="button" aria-pressed={chill} onClick={onChill}
        className={cn('h-9 rounded-full px-3 transition-colors md:h-7', chill ? 'bg-fg text-bg' : 'text-fg-dim hover:text-fg')}>
        {tr('ui.chill.chill')}
      </button>
      <button type="button" aria-pressed={!chill} onClick={onFull}
        className={cn('h-9 rounded-full px-3 transition-colors md:h-7', !chill ? 'bg-fg text-bg' : 'text-fg-dim hover:text-fg')}>
        {tr('ui.chill.full')}
      </button>
    </div>
  );
}

function ChatHistory({ chats, activeChatId, onOpenChat, onNewChat }: Pick<ChillScreenProps, 'chats' | 'activeChatId' | 'onOpenChat' | 'onNewChat'>) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => { if (!ref.current?.contains(event.target as Node)) setOpen(false); };
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);
  return (
    <div ref={ref} className="relative flex flex-none items-center gap-1.5">
      <button type="button" onClick={onNewChat} aria-label={tr('ui.chill.newChat')} title={tr('ui.chill.newChat')}
        className="chill-glass grid h-11 w-11 place-items-center rounded-xl text-fg-dim transition-colors hover:text-fg md:h-9 md:w-9">
        <Plus size={17} aria-hidden />
      </button>
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-label={tr('ui.chill.earlierChats')} title={tr('ui.chill.earlierChats')}
        className="chill-glass grid h-11 w-11 place-items-center rounded-xl text-fg-dim transition-colors hover:text-fg md:h-9 md:w-9">
        <History size={17} aria-hidden />
      </button>
      {open && (
        <div className="animate-rise absolute right-0 top-full z-50 mt-2 w-[min(320px,calc(100vw-24px))] overflow-hidden rounded-xl border border-line bg-panel shadow-lg">
          <div className="border-b border-line px-3 py-2 text-[12px] font-medium text-fg-dim">{tr('ui.chill.earlierChats')}</div>
          <ul className="max-h-[50vh] overflow-y-auto py-1">
            {chats.length === 0 && <li className="px-3 py-2 text-[13px] text-fg-dim">{tr('ui.chill.noChatsYet')}</li>}
            {chats.map((chat) => (
              <li key={chat.id}>
                <button type="button" onClick={() => { setOpen(false); onOpenChat(chat.id); }}
                  className={cn('block min-h-11 w-full truncate px-3 py-2 text-left text-[13.5px] transition-colors hover:bg-panel-2 md:min-h-0',
                    chat.id === activeChatId && 'bg-panel-2 font-medium')}>
                  {chat.title || tr('ui.chill.untitledChat')}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function HelperPill({ helper }: { helper: NonNullable<ChillScreenProps['helper']> }) {
  return (
    <div role="status" className="chill-glass inline-flex max-w-full items-center gap-2 whitespace-nowrap rounded-full py-1.5 pl-1.5 pr-3 text-[12.5px]">
      <span aria-hidden className="grid h-[22px] w-[22px] flex-none place-items-center rounded-full bg-info text-[11px] font-bold text-[#0b0c0e]">
        {helper.name.trim().charAt(0).toUpperCase() || '•'}
      </span>
      <span className="font-semibold">{helper.name}</span>
      <span className="min-w-0 truncate text-fg-dim">{helper.doing}</span>
      <span aria-hidden className="think-bars flex-none"><i /><i /><i /><i /></span>
    </div>
  );
}

function Listening({ listening }: { listening: NonNullable<ChillScreenProps['listening']> }) {
  const hearing = listening.phase === 'hearing' || listening.phase === 'transcribing';
  return (
    <div className="flex flex-col items-center text-center" aria-hidden>
      <div className="relative mb-8 h-[120px] w-[120px]">
        <span className="absolute inset-0 rounded-full border-[1.5px] border-accent-ink opacity-25" />
        <span className={cn('absolute inset-4 rounded-full border-[1.5px] border-accent-ink opacity-45', hearing && 'animate-ping motion-reduce:animate-none')} />
        <span className="chill-core absolute inset-[34px] rounded-full bg-accent" />
      </div>
      {listening.heard ? (
        <p className="max-w-[900px] text-[clamp(24px,3.2vw,38px)] font-medium leading-tight tracking-tight">{listening.heard}</p>
      ) : (
        <p className="text-[clamp(22px,2.6vw,30px)] font-medium tracking-tight text-fg-dim">
          {hearing ? tr('ui.chill.hearingYou') : tr('ui.chill.goAheadImListening')}
        </p>
      )}
    </div>
  );
}

/**
 * Chill mode: one box and the main assistant. Before anything is said the
 * box sits in the middle of an almost empty page; after that it floats at the
 * bottom and whatever the assistant made (an answer, a file, a page, an app
 * or its computer) fills the window above it.
 */
export function ChillScreen(props: ChillScreenProps) {
  const {
    empty, name, assistantName, composer, stage, question, answer, streaming, thinking, helper, listening,
    cards, extras, rail, railActive, onRail, suggestions, onSuggestion, onFullMode,
  } = props;
  const first = (name || '').trim().split(/\s+/)[0];
  const answerRef = useRef<HTMLDivElement>(null);
  // Follow a reply as it streams in.
  useEffect(() => {
    const el = answerRef.current;
    if (el && streaming) el.scrollTop = el.scrollHeight;
  }, [answer, streaming]);
  const showListening = !!listening && !stage && (empty || listening.phase === 'hearing' || listening.phase === 'transcribing' || (!answer && !thinking));

  const top = (
    <div className="relative z-30 flex h-16 flex-none items-center gap-2.5 px-3 sm:px-5">
      <div className="flex min-w-0 items-center gap-2 text-[14px] font-semibold text-fg-dim">
        <span aria-hidden className="grid h-[22px] w-[22px] flex-none place-items-center rounded-md bg-accent text-[13px] font-bold text-accent-foreground">c</span>
        <span className="hidden truncate sm:inline">{assistantName}</span>
      </div>
      <div className="flex min-w-0 flex-1 justify-center">{helper && !empty && <HelperPill helper={helper} />}</div>
      <ChatHistory chats={props.chats} activeChatId={props.activeChatId} onOpenChat={props.onOpenChat} onNewChat={props.onNewChat} />
      <ModeSwitch chill onChill={() => {}} onFull={onFullMode} />
    </div>
  );

  // Once you start talking the box moves down and the page is yours.
  if (empty && !listening) {
    return (
      <div className="chill relative flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-bg text-fg">
        {top}
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto px-4 pb-10">
          <div aria-hidden className="chill-orb mb-7 h-[120px] w-[120px] flex-none sm:h-[150px] sm:w-[150px]" />
          <h1 className="text-center text-[30px] font-semibold tracking-tight sm:text-[40px]">
            {first ? tr('ui.chill.greetingName', { greeting: greetingFor(), name: first }) : greetingFor()}
          </h1>
          <p className="mb-7 mt-2.5 text-center text-[15px] text-fg-dim sm:text-[16px]">{tr('ui.chill.typeOrTapTheWave')}</p>
          <div className="w-full max-w-[700px]">
            {cards}
            {composer}
          </div>
          <div className="mt-4 hidden flex-wrap justify-center gap-2 sm:flex">
            {suggestions.map((text) => (
              <button key={text} type="button" onClick={() => onSuggestion(text)}
                className="rounded-full border border-line bg-panel px-3.5 py-1.5 text-[13.5px] text-fg-dim transition-colors hover:text-fg">
                {text}
              </button>
            ))}
          </div>
        </div>
        <p className="pointer-events-none absolute inset-x-0 bottom-5 hidden text-center text-[12px] text-fg-dim md:block">
          {tr('ui.chill.talkingToNameItBrings', { name: assistantName })}
        </p>
      </div>
    );
  }

  return (
    <div className="chill relative flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-bg text-fg">
      {top}
      <div className="relative flex min-h-0 flex-1 flex-col gap-3 px-3 sm:px-5 md:flex-row">
        {/* Phone: the rail is a strip over the stage. */}
        {rail.length > 1 && (
          <nav aria-label={tr('ui.chill.whatThisChatMade')} className="-m-1 flex flex-none gap-1.5 overflow-x-auto p-1 md:hidden">
            {rail.map((item) => (
              <button key={item.key} type="button" onClick={() => onRail(item.key)} aria-current={item.key === railActive}
                title={item.label} aria-label={item.label}
                className={cn('chill-glass grid h-9 w-12 flex-none place-items-center rounded-lg text-fg-dim', item.key === railActive && 'text-fg outline outline-2 outline-offset-2 outline-accent')}>
                <RailIcon item={item} />
              </button>
            ))}
          </nav>
        )}
        <section aria-label={tr('ui.chill.stage')} className="relative min-h-0 min-w-0 flex-1">
          {stage ? (
            <div className="chill-stage absolute inset-0 flex flex-col overflow-hidden rounded-2xl border border-line bg-panel">{stage}</div>
          ) : showListening ? (
            <div className="absolute inset-0 grid place-items-center px-4"><Listening listening={listening!} /></div>
          ) : (
            <div ref={answerRef} role="log" aria-live="polite" className="absolute inset-0 overflow-y-auto overscroll-contain">
              <div className="mx-auto w-full max-w-[860px] px-1 pb-10 pt-4 sm:pt-10">
                {question && (
                  <p className="mb-4 line-clamp-3 text-[14px] text-fg-dim">
                    <span className="font-medium">{tr('ui.chill.you')}</span>{' '}{question}
                  </p>
                )}
                {answer && (
                  <div className="md break-words text-[17px] leading-relaxed sm:text-[20px]">
                    <Suspense fallback={<p className="whitespace-pre-wrap">{answer}</p>}>
                      <MarkdownMessage content={answer} />
                    </Suspense>
                  </div>
                )}
                {thinking && (
                  <div role="status" className={cn('flex items-center gap-2.5 text-[16px] text-fg-dim', answer && 'mt-5')}>
                    <span className="think-bars"><i /><i /><i /><i /></span>
                    {thinking}
                  </div>
                )}
                {extras && <div className="mt-6 text-[14px]">{extras}</div>}
              </div>
            </div>
          )}
        </section>
        {rail.length > 1 && (
          <nav aria-label={tr('ui.chill.whatThisChatMade')} className="hidden w-[62px] flex-none flex-col items-center justify-center gap-2.5 md:flex">
            <span className="text-[10.5px] uppercase tracking-[0.06em] text-fg-dim">{tr('ui.chill.thisChat')}</span>
            {rail.map((item) => (
              <button key={item.key} type="button" onClick={() => onRail(item.key)} aria-current={item.key === railActive}
                title={item.label} aria-label={item.label}
                className={cn('chill-glass grid h-[42px] w-[58px] place-items-center rounded-[10px] text-fg-dim transition-colors hover:text-fg',
                  item.key === railActive && 'text-fg outline outline-2 outline-offset-2 outline-accent')}>
                <RailIcon item={item} />
              </button>
            ))}
          </nav>
        )}
      </div>
      <div className="relative z-20 mx-auto w-full max-w-[760px] flex-none px-2.5 pb-3.5 pt-2 sm:px-4 sm:pb-6">
        {stage && (answer || thinking) && (
          <div className="mb-2.5 px-1">
            <p className="line-clamp-2 text-[15px] leading-normal sm:text-[17px]">{answer ? plainText(answer) : thinking}</p>
            {answer && (
              <button type="button" onClick={() => onRail('answer')} className="mt-1 min-h-11 text-[13px] text-fg-dim underline decoration-line underline-offset-4 hover:text-fg md:min-h-0">
                {tr('ui.chill.fullAnswer')}
              </button>
            )}
          </div>
        )}
        <div className="max-h-[45vh] overflow-y-auto">{cards}</div>
        {composer}
      </div>
    </div>
  );
}
