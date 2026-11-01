import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { errorMessage } from '@/lib/api';
import { formatDuration, getVoiceSettings, transcribeAudio, type VoiceSettings } from '@/lib/voice';
import { micErrorMessage, useVoiceRecorder } from '@/hooks/useVoiceRecorder';
import { cn } from '@/lib/utils';

export type VoiceStatus = 'idle' | 'starting' | 'recording' | 'transcribing';

/**
 * Click the mic, speak, click stop: the recording is transcribed by the
 * engine and handed to `onText`. Never sends the message by itself.
 */
export function useVoiceInput({ onText }: { onText: (text: string) => void }) {
  const [settings, setSettings] = useState<VoiceSettings | null>(null);
  const [transcribing, setTranscribing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsSetup, setNeedsSetup] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const onTextRef = useRef(onText);
  onTextRef.current = onText;
  const stopRef = useRef<() => void>(() => undefined);

  const recorder = useVoiceRecorder({
    maxSeconds: settings?.max_seconds ?? 600,
    onLimit: () => stopRef.current(),
  });

  useEffect(() => {
    let live = true;
    getVoiceSettings().then((s) => { if (live) setSettings(s); }).catch(() => undefined);
    return () => { live = false; abortRef.current?.abort(); };
  }, []);

  const start = useCallback(async () => {
    setError(null);
    setNeedsSetup(false);
    // Check again in case the user just set it up in another tab.
    let current = settings;
    if (!current?.effective.ready) {
      current = await getVoiceSettings().catch(() => current);
      if (current) setSettings(current);
    }
    if (current && !current.effective.ready) {
      setNeedsSetup(true);
      return;
    }
    try {
      await recorder.start();
    } catch (err) {
      setError(micErrorMessage(err));
    }
  }, [recorder, settings]);

  const stop = useCallback(async () => {
    const audio = await recorder.stop();
    if (!audio) return;
    setTranscribing(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const text = await transcribeAudio(audio, controller.signal);
      if (text.trim()) onTextRef.current(text);
      else setError('No speech was picked up. Try again a little closer to the mic.');
    } catch (err) {
      if (!controller.signal.aborted) setError(errorMessage(err, 'Could not transcribe the recording.'));
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setTranscribing(false);
    }
  }, [recorder]);
  stopRef.current = () => void stop();

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setTranscribing(false);
    recorder.cancel();
  }, [recorder]);

  const status: VoiceStatus = transcribing ? 'transcribing' : recorder.state;

  // Enter stops, Escape cancels while the bar is showing.
  useEffect(() => {
    if (status !== 'recording' && status !== 'transcribing') return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); cancel(); }
      else if (e.key === 'Enter' && status === 'recording') { e.preventDefault(); void stop(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [status, cancel, stop]);

  return {
    status,
    active: status === 'recording' || status === 'transcribing',
    elapsed: recorder.elapsed,
    maxSeconds: settings?.max_seconds ?? 600,
    analyser: recorder.analyser,
    error,
    needsSetup,
    dismiss: () => { setError(null); setNeedsSetup(false); },
    start,
    stop,
    cancel,
  };
}

export type VoiceInput = ReturnType<typeof useVoiceInput>;

/** Scrolling level bars drawn from the live microphone signal. */
export function Waveform({ analyser, className }: { analyser: AnalyserNode | null; className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !analyser) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const data = new Uint8Array(analyser.fftSize);
    const levels: number[] = [];
    const BAR = 3;
    const GAP = 2;
    let frame = 0;
    let raf = 0;

    const draw = () => {
      raf = requestAnimationFrame(draw);
      const dpr = window.devicePixelRatio || 1;
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
        canvas.width = Math.round(width * dpr);
        canvas.height = Math.round(height * dpr);
      }
      // A new bar every other frame (~30 per second).
      if (frame++ % 2 === 0) {
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) {
          const v = (data[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / data.length);
        levels.push(Math.min(1, Math.pow(rms * 4, 0.7)));
        const max = Math.ceil(width / (BAR + GAP)) + 1;
        if (levels.length > max) levels.splice(0, levels.length - max);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = getComputedStyle(canvas).color;
      const mid = height / 2;
      for (let i = 0; i < levels.length; i++) {
        const x = width - (levels.length - i) * (BAR + GAP);
        const h = Math.max(2, levels[i] * (height - 4));
        ctx.fillRect(x, mid - h / 2, BAR, h);
      }
    };
    draw();
    return () => cancelAnimationFrame(raf);
  }, [analyser]);

  return <canvas ref={canvasRef} aria-hidden="true" className={cn('h-8 w-full text-accent', className)} />;
}

const MicIcon = () => (
  <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="9" y="2" width="6" height="12" rx="3" />
    <path d="M5 10a7 7 0 0 0 14 0M12 17v5M8 22h8" />
  </svg>
);

export function MicButton({ voice, disabled, className }: { voice: VoiceInput; disabled?: boolean; className?: string }) {
  return (
    <button
      type="button"
      onClick={() => void voice.start()}
      disabled={disabled || voice.status === 'starting'}
      aria-label="Voice input"
      title="Voice input"
      className={cn(
        'grid h-11 w-11 place-items-center rounded text-fg-dim transition-colors hover:bg-panel-2 hover:text-fg disabled:cursor-not-allowed disabled:opacity-60 md:h-8 md:w-8',
        voice.status === 'starting' && 'animate-pulse text-accent-ink',
        className,
      )}
    >
      <MicIcon />
    </button>
  );
}

/** Replaces the text box while recording or transcribing. */
export function VoiceBar({ voice }: { voice: VoiceInput }) {
  const transcribing = voice.status === 'transcribing';
  const nearLimit = voice.maxSeconds - voice.elapsed <= 30;
  return (
    <div className="flex min-h-11 items-center gap-2 px-1.5 py-1" role="group" aria-label="Voice recording">
      <button
        type="button"
        onClick={voice.cancel}
        aria-label={transcribing ? 'Cancel transcription' : 'Cancel recording'}
        title="Cancel (Esc)"
        className="grid h-11 w-11 flex-none place-items-center rounded text-fg-dim transition-colors hover:bg-panel-2 hover:text-danger-ink md:h-8 md:w-8"
      >
        <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
      </button>
      <div className="min-w-0 flex-1">
        {transcribing ? (
          <div className="flex h-8 items-center gap-2 font-mono text-[11.5px] text-fg-dim">
            <svg className="h-3.5 w-3.5 animate-spin text-accent-ink" viewBox="0 0 24 24" aria-hidden><circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" fill="none" className="opacity-25" /><path d="M4 12a8 8 0 0 1 8-8" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" /></svg>
            Transcribing…
          </div>
        ) : (
          <Waveform analyser={voice.analyser} />
        )}
      </div>
      <span
        className={cn('flex-none font-mono text-[11.5px] tabular-nums', nearLimit && !transcribing ? 'text-danger-ink' : 'text-fg-dim')}
        aria-hidden="true"
      >
        {transcribing ? '' : formatDuration(voice.elapsed)}
      </span>
      <span className="sr-only" role="status" aria-live="polite">
        {transcribing ? 'Transcribing' : 'Recording. Press Enter or the stop button when you are done.'}
      </span>
      <button
        type="button"
        onClick={() => void voice.stop()}
        disabled={transcribing}
        aria-label="Stop recording"
        title="Stop (Enter)"
        className="grid h-11 w-11 flex-none place-items-center rounded bg-danger text-bg transition hover:brightness-105 disabled:opacity-50 md:h-8 md:w-8"
      >
        <svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="2" /></svg>
      </button>
    </div>
  );
}

/** Errors and the "set it up first" hint, shown under the composer. */
export function VoiceNotice({ voice }: { voice: VoiceInput }) {
  if (voice.needsSetup) {
    return (
      <div role="alert" className="mt-1.5 font-mono text-[10.5px] text-fg-dim">
        Voice input needs a speech-to-text provider.{' '}
        <Link to="/settings#voice-input" className="text-accent-ink hover:underline">Set it up in Settings</Link>
        <button type="button" onClick={voice.dismiss} className="ml-2 hover:text-fg" aria-label="Dismiss">✕</button>
      </div>
    );
  }
  if (!voice.error) return null;
  return (
    <div role="alert" className="mt-1.5 font-mono text-[10.5px] text-danger-ink">
      {voice.error}
      <button type="button" onClick={voice.dismiss} className="ml-2 text-fg-dim hover:text-fg" aria-label="Dismiss">✕</button>
    </div>
  );
}
