import { useEffect, useRef, useState, type ReactNode } from 'react';
import { REASONING_LEVELS, reasoningLabel } from '@/lib/reasoning';
import { cn } from '@/lib/utils';
import { composerBtn, composerDanger, composerIconBtn, composerPopover, composerWarn } from '@/components/composerStyles';
import { tr } from '@/lib/i18n';

const LEVELS = [{ value: '', label: 'default' }, ...REASONING_LEVELS.map((l) => ({ value: l.value, label: l.label.toLowerCase() }))];

const svg = (d: ReactNode, size = 15) => (
  <svg aria-hidden width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" className="flex-none">{d}</svg>
);
const ClipIcon = () => svg(<path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />);
const TaskIcon = () => svg(<><path d="M9 11l3 3 8-8" /><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9" /></>);
/** Auto-approve: approvals are off, so the shield is crossed out. */
const ShieldOffIcon = ({ size = 15 }: { size?: number }) => svg(<><path d="M19.7 14A8.6 8.6 0 0 0 20 12V5l-8-3-3.2 1.2M4.7 4.7 4 5v7c0 6 8 10 8 10a20 20 0 0 0 5.6-4.4" /><path d="M2 2l20 20" /></>, size);
const EyeOffIcon = ({ size = 15 }: { size?: number }) => svg(<><path d="M17.94 17.94A10 10 0 0 1 12 20c-7 0-11-8-11-8a18 18 0 0 1 5.06-5.94M9.9 4.24A9 9 0 0 1 12 4c7 0 11 8 11 8a18 18 0 0 1-2.16 3.19" /><path d="M1 1l22 22" /></>, size);
const BoxIcon = ({ size = 15 }: { size?: number }) => svg(<><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" /><path d="M3.27 6.96L12 12.01l8.73-5.05M12 22.08V12" /></>, size);
const SwarmIcon = ({ size = 15 }: { size?: number }) => svg(<><circle cx="12" cy="5" r="2.2" /><circle cx="5" cy="18" r="2.2" /><circle cx="12" cy="18" r="2.2" /><circle cx="19" cy="18" r="2.2" /><path d="M12 7.2v8.6M12 11H5v4.8M12 11h7v4.8" /></>, size);
const WavesIcon = () => svg(<path d="M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4" />);
const FastIcon = ({ size = 15 }: { size?: number }) => svg(<><path d="M13 19l9-7-9-7v14z" /><path d="M2 19l9-7-9-7v14z" /></>, size);
/** Reasoning effort: stacked lines, "how much thinking". */
const EffortIcon = ({ size = 15 }: { size?: number }) => svg(<path d="M4 18h2M4 14h5M4 10h9M4 6h14" />, size);
const ChevronIcon = ({ open }: { open: boolean }) => (
  <svg aria-hidden width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" className={cn('flex-none transition-transform', open && 'rotate-180')}><path d="M6 9l6 6 6-6" /></svg>
);

function Switch({ on, tone }: { on: boolean; tone: 'danger' | 'warn' | 'accent' }) {
  return (
    <span
      aria-hidden
      className={cn(
        'relative h-[17px] w-[30px] flex-none rounded-full border transition-colors',
        on
          ? (tone === 'danger' ? 'border-danger bg-danger' : tone === 'accent' ? 'border-accent bg-accent' : 'border-warn-ink bg-warn-ink')
          : 'border-line bg-panel-2',
      )}
    >
      <span className={cn('absolute top-[2px] h-[11px] w-[11px] rounded-full transition-all', on ? 'left-[15px] bg-white' : 'left-[2px] bg-fg-dim')} />
    </span>
  );
}

/** The effort tag beside the model chip, with its own small picker. Shown
 *  only when a level is picked; "default" hides it again. */
function EffortChip({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  const label = reasoningLabel(value);
  return (
    <div ref={rootRef} className="relative flex-none">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={tr('ui.composerMenu.reasoningReasoninglabelClickToChange', { reasoningLabel: label.toLowerCase() })}
        aria-label={tr('ui.composerMenu.reasoningReasoninglabelChange', { reasoningLabel: label.toLowerCase() })}
        className={cn(composerBtn, open && 'bg-panel-2 text-fg')}
      >
        <EffortIcon size={15} />
        <span className="hidden sm:inline">{label}</span>
        <ChevronIcon open={open} />
      </button>
      {open && (
        <div role="listbox" aria-label={tr('ui.composerMenu.reasoningLevel')} className="absolute bottom-full left-0 z-50 mb-2 min-w-[9.5rem] rounded-xl border border-line bg-panel p-1 shadow-lg">
          {LEVELS.map((l) => {
            const on = l.value === value;
            return (
              <button
                key={l.value || 'default'}
                type="button"
                role="option"
                aria-selected={on}
                onClick={() => { onChange(l.value); setOpen(false); }}
                className={cn('flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left font-mono text-[12px] hover:bg-panel-2 md:py-1.5 md:text-[11px]', on ? 'text-accent-ink' : 'text-fg-dim hover:text-fg')}
              >
                <span aria-hidden className="w-2.5">{on ? '✓' : ''}</span>
                {l.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
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
  /** Swarm: the agent splits this message's job across parallel workers. Left out where swarms can't run. */
  swarm?: { on: boolean; onToggle: () => void };
  /** Run in sandbox: shown when the sandboxed computer is on (or the chat already uses it). */
  sandbox?: { on: boolean; available: boolean; onToggle: () => void; /** The computer chip already shows it. */ hideTag?: boolean };
  /** Fast mode for agents on a coding agent CLI that has one (Claude Code, Codex). */
  fast?: { on: boolean; onToggle: () => void };
  /** Shown between the + button and the on/off indicators (who the chat is with, its project). */
  children?: ReactNode;
  /** The chat's model chip. The effort and fast mode chips follow it, then `settings`.
   *  Beside the + button on a wide screen; inside the + sheet on a phone. */
  model?: ReactNode;
  /** The chat's other settings chips (computer, folders). Beside the + button on a
   *  wide screen; inside the + sheet on a phone, with their names written out. */
  settings?: ReactNode;
  /** A phone-width screen: the row keeps only the + button and what is switched on. */
  phone?: boolean;
  /** Hands-free talking. On a phone it is offered in the sheet instead of the row. */
  onLiveVoice?: () => void;
  liveVoiceDisabled?: boolean;
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

  return (
    <div ref={rootRef} className="relative flex min-w-0 flex-wrap items-center gap-0.5">
      <div className="relative">
        <button
          ref={buttonRef}
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-label={tr('ui.composerMenu.moreOptions')}
          aria-haspopup="menu"
          aria-expanded={open}
          title={tr('ui.composerMenu.attachRunAsTaskSwarm')}
          className={cn(composerIconBtn, open && 'bg-panel-2 text-fg')}
        >
          <svg aria-hidden width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className={cn('transition-transform', open && 'rotate-45')}><path d="M12 5v14M5 12h14" /></svg>
        </button>
        {open && (
          <>
            <div aria-hidden className="fixed inset-0 z-40 bg-black/40 md:hidden" onClick={() => setOpen(false)} />
            <div
              role="menu"
              aria-label={tr('ui.composerMenu.messageOptions')}
              className={cn(composerPopover, 'md:w-80')}
            >
              <div aria-hidden className="mx-auto mb-2 h-1 w-9 rounded-full bg-line md:hidden" />
              {p.phone && (p.model || p.settings) && (
                <>
                  <div className="px-2.5 pb-1 pt-1 text-[12px] text-fg-dim">{tr('ui.composerMenu.thisChat')}</div>
                  {/* The chips keep their own pickers; here their names are written out. */}
                  <div data-testid="composer-sheet-settings" className="flex flex-wrap items-center gap-1.5 px-1 pb-1 [&>div>button>span.hidden]:!inline [&>div>button]:border [&>div>button]:border-line [&>div>button]:px-3">
                    {p.model}
                    {p.settings}
                  </div>
                  <div className="mx-1 my-1 h-px bg-line" />
                </>
              )}
              <button type="button" role="menuitem" className={item} onClick={() => { setOpen(false); p.onAttach(); }}>
                <ClipIcon />
                <span className="min-w-0 flex-1">{tr('ui.composerMenu.attachFiles')}<span className={sub}>{tr('ui.composerMenu.imagesPdfsTextCode')}</span></span>
              </button>
              <button
                type="button"
                role="menuitem"
                className={item}
                disabled={p.runTaskDisabled}
                title={p.runTaskTitle}
                aria-label={tr('ui.composerMenu.runAsTask')}
                onClick={() => { setOpen(false); p.onRunTask(); }}
              >
                <TaskIcon />
                <span className="min-w-0 flex-1">{tr('ui.composerMenu.runAsTask')}<span className={sub}>{tr('ui.composerMenu.savedTaskWithStepTracking')}</span></span>
              </button>
              {p.phone && p.onLiveVoice && (
                <button type="button" role="menuitem" className={item} disabled={p.liveVoiceDisabled} onClick={() => { setOpen(false); p.onLiveVoice?.(); }}>
                  <WavesIcon />
                  <span className="min-w-0 flex-1">{tr('ui.composerMenu.talkOutLoud')}<span className={sub}>{tr('ui.composerMenu.handsFreeBackAndForth')}</span></span>
                </button>
              )}
              <div className="mx-1 my-1 h-px bg-line" />
              <button
                type="button"
                role="menuitemcheckbox"
                aria-checked={p.bypass}
                aria-label={tr('ui.composerMenu.autoApprove')}
                title={tr('ui.composerMenu.autoApproveToolCallsFor')}
                className={item}
                onClick={p.onToggleBypass}
              >
                <ShieldOffIcon />
                <span className="min-w-0 flex-1">{tr('ui.composerMenu.autoApprove')}<span className={sub}>{tr('ui.composerMenu.skipApprovalPromptsInThis')}</span></span>
                <Switch on={p.bypass} tone="danger" />
              </button>
              <button
                type="button"
                role="menuitemcheckbox"
                aria-checked={p.incognito}
                aria-label={tr('ui.composerMenu.incognito')}
                title={p.incognitoTitle}
                disabled={p.incognitoDisabled}
                className={item}
                onClick={p.onToggleIncognito}
              >
                <EyeOffIcon />
                <span className="min-w-0 flex-1">{tr('ui.composerMenu.incognito')}<span className={sub}>{tr('ui.composerMenu.newChatsOnlyNothingRemembered')}</span></span>
                <Switch on={p.incognito} tone="warn" />
              </button>
              {p.swarm && (
                <button
                  type="button"
                  role="menuitemcheckbox"
                  aria-checked={p.swarm.on}
                  aria-label={tr('ui.composerMenu.swarm')}
                  title={tr('ui.composerMenu.theAgentSplitsTheJob')}
                  className={item}
                  onClick={p.swarm.onToggle}
                >
                  <SwarmIcon />
                  <span className="min-w-0 flex-1">{tr('ui.composerMenu.swarm')}<span className={sub}>{tr('ui.composerMenu.splitTheJobAcrossParallel')}</span></span>
                  <Switch on={p.swarm.on} tone="accent" />
                </button>
              )}
              {p.sandbox && (
                <button
                  type="button"
                  role="menuitemcheckbox"
                  aria-checked={p.sandbox.on}
                  aria-label={tr('ui.composerMenu.runInSandbox')}
                  title={p.sandbox.available
                    ? tr('ui.composerMenu.runCodeTestsAndFile')
                    : tr('ui.composerMenu.theSandboxedComputerIsOff')}
                  disabled={!p.sandbox.available && !p.sandbox.on}
                  className={item}
                  onClick={p.sandbox.onToggle}
                >
                  <BoxIcon />
                  <span className="min-w-0 flex-1">{tr('ui.composerMenu.runInSandbox')}<span className={sub}>{tr('ui.composerMenu.codeAndTestsRunOn')}</span></span>
                  <Switch on={p.sandbox.on} tone="accent" />
                </button>
              )}
              <div className="mx-1 my-1 h-px bg-line" />
              <div title={tr('ui.composerMenu.howHardTheModelThinks')}>
                <div className="flex items-center gap-2.5 px-2.5 pb-1.5 pt-2 text-[14px] text-fg md:text-[13px]">
                  <EffortIcon />
                  <span className="flex-1">{tr('ui.composerMenu.reasoning')}</span>
                  <span className="font-mono text-[11px] text-accent-ink">{p.reasoning ? reasoningLabel(p.reasoning).toLowerCase() : tr('ui.composerMenu.default')}</span>
                </div>
                <div role="radiogroup" aria-label={tr('ui.composerMenu.reasoningLevel')} className="flex flex-wrap gap-1 px-2.5 pb-2 md:pl-[2.1rem]">
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
              {p.fast && (
                <button
                  type="button"
                  role="menuitemcheckbox"
                  aria-checked={p.fast.on}
                  aria-label={tr('ui.composerMenu.fastMode')}
                  title={tr('ui.composerMenu.runsTheCodingAgentIn')}
                  className={item}
                  onClick={p.fast.onToggle}
                >
                  <FastIcon />
                  <span className="min-w-0 flex-1">{tr('ui.composerMenu.fastMode')}<span className={sub}>{tr('ui.composerMenu.quickerRepliesCostsMore')}</span></span>
                  <Switch on={p.fast.on} tone="accent" />
                </button>
              )}
            </div>
          </>
        )}
      </div>
      {p.children}
      {!p.phone && p.model}
      {p.reasoning && <EffortChip value={p.reasoning} onChange={p.onReasoning} />}
      {p.fast?.on && (
        <button
          type="button"
          onClick={p.fast.onToggle}
          title={tr('ui.composerMenu.fastModeIsOnClick')}
          aria-label={tr('ui.composerMenu.turnOffFastMode')}
          className={cn(composerBtn, 'text-accent-ink')}
        >
          <FastIcon size={15} />
          <span className="hidden sm:inline">{tr('ui.composerMenu.fast')}</span>
        </button>
      )}
      {!p.phone && p.settings}
      {p.swarm?.on && (
        <button
          type="button"
          onClick={p.swarm.onToggle}
          title={tr('ui.composerMenu.swarmIsOnTheAgent')}
          aria-label={tr('ui.composerMenu.turnOffSwarm')}
          className={cn(composerBtn, 'text-accent-ink')}
        >
          <SwarmIcon size={15} />
          <span className="hidden sm:inline">{tr('ui.composerMenu.swarm')}</span>
        </button>
      )}
      {p.sandbox?.on && !p.sandbox.hideTag && !p.phone && (
        <button
          type="button"
          onClick={p.sandbox.onToggle}
          title={p.sandbox.available
            ? tr('ui.composerMenu.runInSandboxIsOn')
            : tr('ui.composerMenu.runInSandboxIsOn_1513')}
          aria-label={tr('ui.composerMenu.turnOffRunInSandbox')}
          className={cn(composerBtn, 'text-accent-ink', !p.sandbox.available && composerWarn)}
        >
          <BoxIcon size={15} />
          <span className="hidden sm:inline">{tr('ui.composerMenu.sandbox')}</span>
        </button>
      )}
      {p.bypass && (
        <button type="button" onClick={p.onToggleBypass} title={tr('ui.composerMenu.autoApproveIsOnTool')} aria-label={tr('ui.composerMenu.turnOffAutoApprove')} className={cn(composerIconBtn, composerDanger)}>
          <ShieldOffIcon size={15} />
        </button>
      )}
      {p.incognito && (
        p.incognitoDisabled ? (
          <span title={p.incognitoTitle} aria-label={tr('ui.composerMenu.incognitoChat')} role="img" className={cn(composerIconBtn, composerWarn, 'hover:bg-warn-ink/10')}>
            <EyeOffIcon size={15} />
          </span>
        ) : (
          <button type="button" onClick={p.onToggleIncognito} title={tr('ui.composerMenu.incognitoIsOnClickTo')} aria-label={tr('ui.composerMenu.turnOffIncognito')} className={cn(composerIconBtn, composerWarn)}>
            <EyeOffIcon size={15} />
          </button>
        )
      )}
    </div>
  );
}
