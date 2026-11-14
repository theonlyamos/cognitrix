import { useEffect, useRef, useState, type ReactNode } from 'react';
import { REASONING_LEVELS, reasoningLabel } from '@/lib/reasoning';
import { cn } from '@/lib/utils';

const LEVELS = [{ value: '', label: 'default' }, ...REASONING_LEVELS.map((l) => ({ value: l.value, label: l.label.toLowerCase() }))];

const svg = (d: ReactNode, size = 15) => (
  <svg aria-hidden width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" className="flex-none">{d}</svg>
);
const ClipIcon = () => svg(<path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />);
const TaskIcon = () => svg(<><path d="M9 11l3 3 8-8" /><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9" /></>);
const BoltIcon = ({ size = 15 }: { size?: number }) => svg(<path d="M13 2L3 14h9l-1 8 10-12h-9z" />, size);
const EyeOffIcon = ({ size = 15 }: { size?: number }) => svg(<><path d="M17.94 17.94A10 10 0 0 1 12 20c-7 0-11-8-11-8a18 18 0 0 1 5.06-5.94M9.9 4.24A9 9 0 0 1 12 4c7 0 11 8 11 8a18 18 0 0 1-2.16 3.19" /><path d="M1 1l22 22" /></>, size);
const ClockIcon = () => svg(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>);

function Switch({ on, tone }: { on: boolean; tone: 'danger' | 'warn' }) {
  return (
    <span
      aria-hidden
      className={cn(
        'relative h-[17px] w-[30px] flex-none rounded-full border transition-colors',
        on ? (tone === 'danger' ? 'border-danger bg-danger' : 'border-warn-ink bg-warn-ink') : 'border-line bg-panel-2',
      )}
    >
      <span className={cn('absolute top-[2px] h-[11px] w-[11px] rounded-full transition-all', on ? 'left-[15px] bg-white' : 'left-[2px] bg-fg-dim')} />
    </span>
  );
}

interface Props {
  onAttach: () => void;
  onRunTask: () => void;
  runTaskDisabled: boolean;
  runTaskTitle: string;
  bypass: boolean;
  onToggleBypass: () => void;
  incognito: boolean;
  onToggleIncognito: () => void;
  incognitoDisabled: boolean;
  incognitoTitle: string;
  reasoning: string;
  onReasoning: (value: string) => void;
}

/**
 * The composer's "+" button and the options behind it (attach, run as task,
 * auto-approve, incognito, reasoning). Options that are switched on show as
 * tags beside the button. Opens as a popover on desktop, a bottom sheet on phones.
 */
export default function ComposerMenu(p: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const item = 'flex w-full min-h-12 items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-[14px] transition-colors hover:bg-panel-2 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent md:min-h-0 md:text-[13px] [&>svg]:text-fg-dim';
  const sub = 'block text-[11.5px] text-fg-dim md:text-[11px]';
  const tag = 'inline-flex min-h-8 items-center gap-1.5 whitespace-nowrap rounded border px-2 font-mono text-[10.5px] transition-colors md:min-h-6';

  return (
    <div ref={rootRef} className="flex min-w-0 flex-wrap items-center gap-1.5">
      <div className="relative">
        <button
          ref={buttonRef}
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-label="More options"
          aria-haspopup="menu"
          aria-expanded={open}
          title="Attach, run as task, auto-approve, incognito, reasoning"
          className={cn(
            'grid h-11 w-11 place-items-center rounded border text-fg-dim transition-colors hover:border-fg-dim hover:text-fg md:h-8 md:w-8',
            open ? 'border-fg-dim bg-panel-2 text-fg' : 'border-line',
          )}
        >
          <svg aria-hidden width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className={cn('transition-transform', open && 'rotate-45')}><path d="M12 5v14M5 12h14" /></svg>
        </button>
        {open && (
          <>
            <div aria-hidden className="fixed inset-0 z-40 bg-black/40 md:hidden" onClick={() => setOpen(false)} />
            <div
              role="menu"
              aria-label="Message options"
              className="animate-rise fixed inset-x-0 bottom-0 z-50 rounded-t-2xl border-t border-line bg-panel px-2.5 pb-6 pt-2 shadow-lg md:absolute md:inset-x-auto md:bottom-full md:left-0 md:mb-2 md:w-80 md:rounded-lg md:border md:p-1.5 md:pb-1.5"
            >
              <div aria-hidden className="mx-auto mb-2 h-1 w-9 rounded-full bg-line md:hidden" />
              <button type="button" role="menuitem" className={item} onClick={() => { setOpen(false); p.onAttach(); }}>
                <ClipIcon />
                <span className="min-w-0 flex-1">Attach files<span className={sub}>Images, PDFs, text, code</span></span>
              </button>
              <button
                type="button"
                role="menuitem"
                className={item}
                disabled={p.runTaskDisabled}
                title={p.runTaskTitle}
                aria-label="Run as task"
                onClick={() => { setOpen(false); p.onRunTask(); }}
              >
                <TaskIcon />
                <span className="min-w-0 flex-1">Run as task<span className={sub}>Saved task with step tracking</span></span>
              </button>
              <div className="mx-1 my-1 h-px bg-line" />
              <button
                type="button"
                role="menuitemcheckbox"
                aria-checked={p.bypass}
                aria-label="Auto-approve"
                title="Auto-approve tool calls for this browser (skips the approval prompt). Use with care."
                className={item}
                onClick={p.onToggleBypass}
              >
                <BoltIcon />
                <span className="min-w-0 flex-1">Auto-approve<span className={sub}>Skip approval prompts in this browser</span></span>
                <Switch on={p.bypass} tone="danger" />
              </button>
              <button
                type="button"
                role="menuitemcheckbox"
                aria-checked={p.incognito}
                aria-label="Incognito"
                title={p.incognitoTitle}
                disabled={p.incognitoDisabled}
                className={item}
                onClick={p.onToggleIncognito}
              >
                <EyeOffIcon />
                <span className="min-w-0 flex-1">Incognito<span className={sub}>New chats only, nothing remembered</span></span>
                <Switch on={p.incognito} tone="warn" />
              </button>
              <div className="mx-1 my-1 h-px bg-line" />
              <div title="How hard the model thinks before answering. Default uses the level set on the agent.">
                <div className="flex items-center gap-2.5 px-2.5 pb-1.5 pt-2 text-[14px] text-fg md:text-[13px]">
                  <ClockIcon />
                  <span className="flex-1">Reasoning</span>
                  <span className="font-mono text-[11px] text-accent-ink">{p.reasoning ? reasoningLabel(p.reasoning).toLowerCase() : 'default'}</span>
                </div>
                <div role="radiogroup" aria-label="Reasoning level" className="flex flex-wrap gap-1 px-2.5 pb-2 md:pl-[2.1rem]">
                  {LEVELS.map((l) => {
                    const on = l.value === p.reasoning;
                    return (
                      <button
                        key={l.value || 'default'}
                        type="button"
                        role="radio"
                        aria-checked={on}
                        onClick={() => p.onReasoning(l.value)}
                        className={cn(
                          'min-h-9 rounded border px-2 font-mono text-[11px] transition-colors md:min-h-6 md:text-[10.5px]',
                          on ? 'border-accent bg-accent text-accent-foreground' : 'border-line text-fg-dim hover:border-fg-dim hover:text-fg',
                        )}
                      >
                        {l.label}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          </>
        )}
      </div>
      {p.bypass && (
        <button type="button" onClick={p.onToggleBypass} title="Auto-approve is on. Click to turn it off." aria-label="Turn off auto-approve" className={cn(tag, 'border-danger/40 text-danger-ink hover:border-danger')}>
          <BoltIcon size={11} /> auto-approve <span aria-hidden className="opacity-70">✕</span>
        </button>
      )}
      {p.incognito && (
        p.incognitoDisabled ? (
          <span title={p.incognitoTitle} className={cn(tag, 'border-warn-ink/40 text-warn-ink')}>
            <EyeOffIcon size={11} /> incognito
          </span>
        ) : (
          <button type="button" onClick={p.onToggleIncognito} title="Incognito is on. Click to turn it off." aria-label="Turn off incognito" className={cn(tag, 'border-warn-ink/40 text-warn-ink hover:border-warn-ink')}>
            <EyeOffIcon size={11} /> incognito <span aria-hidden className="opacity-70">✕</span>
          </button>
        )
      )}
      {p.reasoning && (
        <button type="button" onClick={() => setOpen(true)} title="Reasoning level for this chat. Click to change it." className={cn(tag, 'border-line text-fg-dim hover:border-fg-dim')}>
          reasoning <span className="text-accent-ink">{reasoningLabel(p.reasoning).toLowerCase()}</span>
        </button>
      )}
    </div>
  );
}
