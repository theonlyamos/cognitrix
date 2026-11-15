import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Waveform } from '@/components/VoiceInput';
import type { LivePhase, LiveVoice, LiveVoiceOptions } from '@/hooks/useLiveVoice';
import { getLiveVoicePrefs, setLiveVoicePrefs } from '@/lib/liveVoice';
import { useSpeechState } from '@/lib/speech';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

const STATUS: Record<LivePhase, string> = {
  starting: 'Starting the microphone…',
  listening: 'Listening',
  hearing: 'Hearing you…',
  transcribing: 'Got it…',
  waiting: 'Working. Talk to add something, or say “stop”.',
  speaking: 'Speaking',
  error: 'Voice mode can’t start',
};

const PAUSES = [
  { ms: 800, get label() { return tr('ui.liveVoice.short'); } },
  { ms: 1100, get label() { return tr('ui.liveVoice.normal'); } },
  { ms: 1800, get label() { return tr('ui.liveVoice.long'); } },
];

/** Starts live voice mode; sits next to the mic in the chat box. */
export function LiveVoiceButton({ onClick, disabled, className }: { onClick: () => void; disabled?: boolean; className?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={tr('ui.liveVoice.liveVoiceMode')}
      title={tr('ui.liveVoice.liveVoiceTalkHandsFree')}
      className={cn(
        'grid h-11 w-11 place-items-center rounded text-fg-dim transition-colors hover:bg-panel-2 hover:text-fg disabled:cursor-not-allowed disabled:opacity-60 md:h-8 md:w-8',
        className,
      )}
    >
      <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <path d="M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4" />
      </svg>
    </button>
  );
}

/**
 * The live voice panel, shown at the top of the chat box. The chat stays
 * visible above it, so approvals and questions can still be clicked. The
 * conversation itself runs in useLiveVoice, held by the page, so moving the
 * chat box (a new chat's first message) doesn't restart the microphone.
 */
export function LiveVoicePanel({ live, ...props }: Pick<LiveVoiceOptions, 'approvals' | 'question' | 'onExit'> & { live: LiveVoice }) {
  const speech = useSpeechState();
  const [showOptions, setShowOptions] = useState(false);
  const [prefs, setPrefs] = useState(getLiveVoicePrefs);
  const approval = props.approvals[0];
  const asking = !!approval || (!!props.question && !props.question.secret);

  const status = live.muted && live.phase !== 'speaking' && live.phase !== 'error'
    ? 'Muted. Unmute to talk.'
    : (live.phase === 'listening' || live.phase === 'waiting') && approval
      ? approval.risk_level === 'high' ? 'Say “approve” or “no”' : 'Say “yes” or “no”'
      : (live.phase === 'listening' || live.phase === 'waiting') && asking
        ? 'Say your answer'
        : STATUS[live.phase];
  const level = live.phase === 'hearing' || live.phase === 'listening' || live.phase === 'waiting';

  return (
    <div className="px-2 py-2" role="region" aria-label={tr('ui.liveVoice.liveVoiceMode')}>
      <div className="flex items-center gap-3">
        <div
          aria-hidden="true"
          className={cn(
            'relative grid h-10 w-10 flex-none place-items-center rounded-full border transition-colors',
            live.phase === 'speaking' ? 'border-accent bg-accent/15 text-accent-ink'
              : live.phase === 'hearing' ? 'border-accent bg-accent text-accent-foreground'
                : live.phase === 'error' ? 'border-danger text-danger-ink'
                  : 'border-line text-fg-dim',
          )}
        >
          {(live.phase === 'hearing' || live.phase === 'speaking') && (
            <span className="absolute inset-0 animate-ping rounded-full border border-accent opacity-40 motion-reduce:hidden" />
          )}
          {live.phase === 'transcribing' || live.phase === 'starting' ? (
            <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" fill="none" className="opacity-25" /><path d="M4 12a8 8 0 0 1 8-8" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" /></svg>
          ) : (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4" /></svg>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-medium text-fg" role="status" aria-live="polite">{status}</div>
          {live.phase === 'speaking' ? (
            <div className="truncate text-[11.5px] text-fg-dim">
              {prefs.interrupt ? tr('ui.liveVoice.talkToInterrupt') : tr('ui.liveVoice.pressInterruptToTalk')}
              {speech.error ? ` · ${speech.error}` : ''}
            </div>
          ) : live.heard ? (
            <div className="truncate text-[11.5px] text-fg-dim" title={live.heard}>{tr('ui.liveVoice.youSaidHeard', { heard: live.heard })}</div>
          ) : (
            <div className="truncate text-[11.5px] text-fg-dim">{tr('ui.liveVoice.speakNaturallyAShortPause')}</div>
          )}
        </div>
        <div className={cn('hidden w-28 flex-none sm:block', !level || live.muted ? 'opacity-30' : '')}>
          <Waveform analyser={live.muted ? null : live.analyser} />
        </div>
        <div className="flex flex-none items-center gap-1">
          {live.phase === 'speaking' && (
            <button
              type="button"
              onClick={live.interrupt}
              className="h-11 rounded border border-line px-3 text-[12px] text-fg hover:bg-panel-2 md:h-8"
            >
              {tr('ui.liveVoice.interrupt')}
            </button>
          )}
          <button
            type="button"
            onClick={live.toggleMute}
            aria-pressed={live.muted}
            aria-label={live.muted ? tr('ui.liveVoice.unmuteMicrophone') : tr('ui.liveVoice.muteMicrophone')}
            title={live.muted ? tr('ui.liveVoice.unmute') : tr('ui.liveVoice.mute')}
            className={cn(
              'grid h-11 w-11 place-items-center rounded transition-colors hover:bg-panel-2 md:h-8 md:w-8',
              live.muted ? 'text-danger-ink' : 'text-fg-dim hover:text-fg',
            )}
          >
            <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="9" y="2" width="6" height="12" rx="3" />
              <path d="M5 10a7 7 0 0 0 14 0M12 17v5" />
              {live.muted && <path d="M3 3l18 18" />}
            </svg>
          </button>
          <button
            type="button"
            onClick={() => setShowOptions((v) => !v)}
            aria-expanded={showOptions}
            aria-label={tr('ui.liveVoice.voiceModeOptions')}
            title={tr('ui.liveVoice.options')}
            className="grid h-11 w-11 place-items-center rounded text-fg-dim transition-colors hover:bg-panel-2 hover:text-fg md:h-8 md:w-8"
          >
            <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M4 6h16M4 12h16M4 18h16" /><circle cx="9" cy="6" r="2" fill="currentColor" /><circle cx="15" cy="12" r="2" fill="currentColor" /><circle cx="8" cy="18" r="2" fill="currentColor" /></svg>
          </button>
          <button
            type="button"
            onClick={props.onExit}
            className="h-11 rounded bg-danger px-3 text-[12px] font-medium text-bg transition hover:brightness-105 md:h-8"
          >
            {tr('ui.liveVoice.end')}
          </button>
        </div>
      </div>
      {showOptions && (
        <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-line pt-2 text-[12px] text-fg-dim">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={prefs.interrupt}
              onChange={(e) => setPrefs(setLiveVoicePrefs({ interrupt: e.target.checked }))}
            />
            {tr('ui.liveVoice.talkingCutsInWhileIt')}
          </label>
          <span className="flex items-center gap-2">
            {tr('ui.liveVoice.pauseBeforeSending')}
            {PAUSES.map((p) => (
              <button
                key={p.ms}
                type="button"
                aria-pressed={prefs.pauseMs === p.ms}
                onClick={() => setPrefs(setLiveVoicePrefs({ pauseMs: p.ms }))}
                className={cn('rounded border px-2 py-0.5', prefs.pauseMs === p.ms ? 'border-accent text-fg' : 'border-line hover:text-fg')}
              >
                {p.label}
              </button>
            ))}
          </span>
          <span className="text-[11px]">{tr('ui.liveVoice.pauseChangesApplyNextTime')}{' '}<Link to="/settings/voice" className="text-accent-ink hover:underline">{tr('ui.liveVoice.settingsVoice')}</Link>.</span>
        </div>
      )}
      {live.needsSetup && (
        <div role="alert" className="mt-1.5 font-mono text-[10.5px] text-fg-dim">
          {tr('ui.liveVoice.voiceModeNeedsASpeech')}{' '}
          <Link to="/settings/voice" className="text-accent-ink hover:underline">{tr('ui.liveVoice.setItUpInSettings')}</Link>
        </div>
      )}
      {live.error && (
        <div role="alert" className="mt-1.5 font-mono text-[10.5px] text-danger-ink">
          {live.error}
          {live.phase !== 'error' && (
            <button type="button" onClick={live.retry} className="ml-2 text-fg-dim hover:text-fg">{tr('ui.liveVoice.dismiss')}</button>
          )}
        </div>
      )}
    </div>
  );
}
