import { useCallback, useEffect, useRef, useState } from 'react';
import { concatFloat32, encodeWav, resample } from '@/lib/wav';

export type RecorderState = 'idle' | 'starting' | 'recording';

// Collects raw samples off the audio thread. Loaded from a Blob URL so it
// needs no separate file in the build.
const WORKLET = `
class CollectorProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor('cognitrix-collector', CollectorProcessor);
`;

/** Turn a getUserMedia failure into something a person can act on. */
export function micErrorMessage(err: unknown): string {
  const name = (err as { name?: string })?.name;
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return /Windows/i.test(navigator.userAgent)
      ? 'Microphone access is blocked. Allow it for this app, and check Windows Settings › Privacy › Microphone.'
      : 'Microphone access is blocked. Allow it in your browser or system settings and try again.';
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'No microphone was found.';
  if (name === 'NotReadableError') return 'The microphone is in use by another app.';
  if (typeof navigator !== 'undefined' && !navigator.mediaDevices?.getUserMedia) {
    return 'This browser can’t record audio here. Voice input needs HTTPS or localhost.';
  }
  return 'Could not start the microphone.';
}

interface Session {
  stream: MediaStream;
  context: AudioContext;
  source: MediaStreamAudioSourceNode;
  node: AudioNode;
  chunks: Float32Array[];
  startedAt: number;
  timer: number;
}

/**
 * Records the microphone into memory and hands back a 16 kHz mono WAV on
 * stop. `analyser` feeds the live waveform while recording. Recording stops by
 * itself after `maxSeconds`, calling `onLimit`.
 */
export function useVoiceRecorder({ maxSeconds = 600, onLimit }: { maxSeconds?: number; onLimit?: () => void } = {}) {
  const [state, setState] = useState<RecorderState>('idle');
  const [elapsed, setElapsed] = useState(0);
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null);
  const session = useRef<Session | null>(null);
  // Bumped by cancel and unmount so a start still waiting on the permission
  // prompt (or worklet load) knows to drop what it acquired.
  const generation = useRef(0);
  const onLimitRef = useRef(onLimit);
  onLimitRef.current = onLimit;

  const teardown = useCallback(() => {
    generation.current += 1;
    const s = session.current;
    session.current = null;
    if (!s) return;
    window.clearInterval(s.timer);
    try { s.source.disconnect(); s.node.disconnect(); } catch { /* already gone */ }
    s.stream.getTracks().forEach((t) => t.stop());
    void s.context.close().catch(() => undefined);
    setAnalyser(null);
  }, []);

  useEffect(() => teardown, [teardown]);

  const start = useCallback(async () => {
    if (session.current || state !== 'idle') return;
    setState('starting');
    setElapsed(0);
    const gen = ++generation.current;
    const stale = () => gen !== generation.current;
    let stream: MediaStream | null = null;
    let context: AudioContext | null = null;
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('unsupported');
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      if (stale()) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      const Ctx: typeof AudioContext = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      context = new Ctx();
      if (context.state === 'suspended') await context.resume();
      const source = context.createMediaStreamSource(stream);
      const meter = context.createAnalyser();
      meter.fftSize = 1024;
      source.connect(meter);
      const chunks: Float32Array[] = [];

      let node: AudioNode;
      try {
        const url = URL.createObjectURL(new Blob([WORKLET], { type: 'application/javascript' }));
        try {
          await context.audioWorklet.addModule(url);
        } finally {
          URL.revokeObjectURL(url);
        }
        const worklet = new AudioWorkletNode(context, 'cognitrix-collector');
        worklet.port.onmessage = (e: MessageEvent<Float32Array>) => chunks.push(e.data);
        node = worklet;
      } catch {
        // Older engines without AudioWorklet.
        const processor = context.createScriptProcessor(4096, 1, 1);
        processor.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
        node = processor;
      }
      // Route through a muted gain so the node keeps processing without
      // playing the mic back through the speakers.
      const mute = context.createGain();
      mute.gain.value = 0;
      source.connect(node);
      node.connect(mute);
      mute.connect(context.destination);

      if (stale()) {
        stream.getTracks().forEach((t) => t.stop());
        void context.close().catch(() => undefined);
        return;
      }
      const startedAt = performance.now();
      const timer = window.setInterval(() => {
        const secs = (performance.now() - startedAt) / 1000;
        setElapsed(secs);
        if (secs >= maxSeconds) onLimitRef.current?.();
      }, 250);
      const ctx = context;
      session.current = { stream, context: ctx, source, node, chunks, startedAt, timer };
      setAnalyser(meter);
      setState('recording');
    } catch (err) {
      stream?.getTracks().forEach((t) => t.stop());
      void context?.close().catch(() => undefined);
      if (stale()) return;
      setState('idle');
      throw err;
    }
  }, [maxSeconds, state]);

  /** Stop and return the WAV (null when nothing was captured). */
  const stop = useCallback(async (): Promise<Blob | null> => {
    const s = session.current;
    if (!s) return null;
    const rate = s.context.sampleRate;
    const chunks = s.chunks;
    teardown();
    setState('idle');
    const samples = concatFloat32(chunks);
    if (samples.length < rate * 0.3) return null; // under a third of a second
    return encodeWav(resample(samples, rate));
  }, [teardown]);

  const cancel = useCallback(() => {
    teardown();
    setState('idle');
    setElapsed(0);
  }, [teardown]);

  return { state, elapsed, analyser, start, stop, cancel };
}
