import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { micErrorMessage } from '@/hooks/useVoiceRecorder';
import { getVoiceSettings, transcribeAudio } from '@/lib/voice';
import { encodeWav, resample } from '@/lib/wav';
import { speakText, stopSpeaking } from '@/lib/speech';
import { VoiceActivityDetector } from '@/lib/vad';
import {
  approvalRetrySpeech,
  approvalSpeech,
  getLiveVoicePrefs,
  isExitCommand,
  isStopCommand,
  parseApprovalAnswer,
  questionAnswer,
  questionSpeech,
  type SpokenApproval,
  type SpokenQuestion,
} from '@/lib/liveVoice';

export type LivePhase = 'starting' | 'listening' | 'hearing' | 'transcribing' | 'waiting' | 'speaking' | 'error';

export interface LiveVoiceOptions {
  /** The overlay is open. Turning it off releases the microphone. */
  enabled: boolean;
  /** The agent is working on a turn. */
  busy: boolean;
  approvals: SpokenApproval[];
  question: SpokenQuestion | null;
  agentName: string;
  /** The newest reply, read aloud when a turn ends. */
  lastReply: { id: string; content: string } | null;
  /** Sends a message, or steers the running turn when busy. */
  onSend: (text: string) => Promise<boolean> | boolean;
  onStop: () => void;
  onApproval: (requestId: string, approved: boolean) => void;
  onAnswer: (question: SpokenQuestion, answer: { option_id: string } | { text: string }) => void;
  onExit: () => void;
}

// Collects samples in blocks of about 20 ms so the detector isn't flooded
// with 128-sample messages.
const WORKLET = `
class LiveCollector extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(1024); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) {
      for (let i = 0; i < ch.length; i++) {
        this.buf[this.n++] = ch[i];
        if (this.n === this.buf.length) { this.port.postMessage(this.buf.slice(0)); this.n = 0; }
      }
    }
    return true;
  }
}
registerProcessor('cognitrix-live-collector', LiveCollector);
`;

/** How much louder than usual someone must talk to cut into a reply being read. */
const INTERRUPT_SENSITIVITY = 2.2;

interface Mic {
  stream: MediaStream;
  context: AudioContext;
  nodes: AudioNode[];
}

/**
 * Hands-free conversation with the agent in the open chat: listens, sends
 * what you say when you pause, reads the reply aloud, and asks approval
 * prompts and questions out loud so they can be answered by voice.
 */
export function useLiveVoice(opts: LiveVoiceOptions) {
  const [phase, setPhase] = useState<LivePhase>('starting');
  const [muted, setMuted] = useState(false);
  const [heard, setHeard] = useState('');
  const [said, setSaid] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [needsSetup, setNeedsSetup] = useState(false);
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null);

  const optsRef = useRef(opts);
  optsRef.current = opts;
  const mutedRef = useRef(muted);
  mutedRef.current = muted;
  const micRef = useRef<Mic | null>(null);
  const vadRef = useRef<VoiceActivityDetector | null>(null);
  const speakingRef = useRef(false);
  const transcribingRef = useRef(false);
  const sayGen = useRef(0);
  const announced = useRef(new Set<string>());
  const spokenReplies = useRef(new Set<string>());
  const abortRef = useRef<AbortController | null>(null);

  const restingPhase = useCallback((): LivePhase => (optsRef.current.busy ? 'waiting' : 'listening'), []);

  const settle = useCallback(() => {
    if (speakingRef.current || transcribingRef.current) return;
    if (vadRef.current?.active) setPhase('hearing');
    else setPhase(restingPhase());
  }, [restingPhase]);

  /** Read `text` aloud; talking over it (when allowed) cuts it short. */
  const say = useCallback(async (text: string, id = 'live-voice') => {
    const mine = ++sayGen.current;
    speakingRef.current = true;
    setSaid(text);
    setPhase('speaking');
    const vad = vadRef.current;
    if (vad) {
      vad.reset();
      vad.sensitivity = getLiveVoicePrefs().interrupt ? INTERRUPT_SENSITIVITY : Number.POSITIVE_INFINITY;
    }
    try {
      await speakText(id, text);
    } finally {
      if (mine === sayGen.current) {
        speakingRef.current = false;
        if (vadRef.current) vadRef.current.sensitivity = 1;
        settle();
      }
    }
  }, [settle]);

  const interrupt = useCallback(() => {
    sayGen.current += 1;
    stopSpeaking();
    speakingRef.current = false;
    if (vadRef.current) vadRef.current.sensitivity = 1;
    settle();
  }, [settle]);

  const handleText = useCallback(async (raw: string) => {
    const text = raw.trim();
    if (!text) return;
    setHeard(text);
    const o = optsRef.current;
    if (isExitCommand(text)) {
      o.onExit();
      return;
    }
    const approval = o.approvals[0];
    if (approval) {
      const answer = parseApprovalAnswer(text, approval.risk_level);
      if (answer === null) {
        void say(approvalRetrySpeech(approval));
        return;
      }
      o.onApproval(approval.request_id, answer === 'yes');
      void say(answer === 'yes' ? 'Approved.' : 'Denied.');
      return;
    }
    const q = o.question;
    if (q?.secret) {
      // A sign-in request is typed into its own masked form; what was said is
      // never sent to the chat, where the agent would read it.
      void say('That one needs typing. I’ve left it on screen.');
      return;
    }
    if (q) {
      const answer = questionAnswer(text, q);
      if (!answer) {
        void say(`Sorry, I didn’t catch which one. ${questionSpeech(q)}`);
        return;
      }
      o.onAnswer(q, answer);
      return;
    }
    if (o.busy && isStopCommand(text)) {
      o.onStop();
      void say('Stopped.');
      return;
    }
    const sent = await o.onSend(text);
    if (!sent) setError('That message could not be sent. Check the connection and try again.');
  }, [say]);

  const handleUtterance = useCallback(async (samples: Float32Array, rate: number) => {
    transcribingRef.current = true;
    setPhase('transcribing');
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const text = await transcribeAudio(encodeWav(resample(samples, rate)), controller.signal);
      if (controller.signal.aborted) return;
      setError(null);
      transcribingRef.current = false;
      settle();
      await handleText(text);
    } catch (err) {
      if (!controller.signal.aborted) setError(errorMessage(err, 'Could not understand that. Try again.'));
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      transcribingRef.current = false;
      settle();
    }
  }, [handleText, settle]);

  const releaseMic = useCallback(() => {
    const mic = micRef.current;
    micRef.current = null;
    vadRef.current = null;
    setAnalyser(null);
    if (!mic) return;
    mic.nodes.forEach((n) => { try { n.disconnect(); } catch { /* gone */ } });
    mic.stream.getTracks().forEach((t) => t.stop());
    void mic.context.close().catch(() => undefined);
  }, []);

  // Open the microphone while the overlay is up.
  useEffect(() => {
    if (!opts.enabled) return;
    let cancelled = false;
    setPhase('starting');
    setError(null);
    setNeedsSetup(false);
    announced.current = new Set();
    // Replies already on screen aren't read out when voice mode opens.
    spokenReplies.current = new Set(optsRef.current.lastReply ? [optsRef.current.lastReply.id] : []);

    (async () => {
      try {
        const settings = await getVoiceSettings().catch(() => null);
        if (cancelled) return;
        if (settings && !settings.effective.ready) {
          setNeedsSetup(true);
          setPhase('error');
          return;
        }
        if (!navigator.mediaDevices?.getUserMedia) throw new Error('unsupported');
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        const Ctx: typeof AudioContext = window.AudioContext
          || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
        const context = new Ctx();
        if (context.state === 'suspended') await context.resume();
        const source = context.createMediaStreamSource(stream);
        const meter = context.createAnalyser();
        meter.fftSize = 1024;
        source.connect(meter);
        const vad = new VoiceActivityDetector({ sampleRate: context.sampleRate, pauseMs: getLiveVoicePrefs().pauseMs });
        vadRef.current = vad;
        const onBlock = (block: Float32Array) => {
          if (mutedRef.current || transcribingRef.current) return;
          const event = vad.push(block);
          if (!event) return;
          if (event.type === 'speech-start') {
            if (speakingRef.current) interrupt();
            setPhase('hearing');
          } else if (event.type === 'speech-end') {
            void handleUtterance(event.samples, context.sampleRate);
          } else {
            settle();
          }
        };
        let node: AudioNode;
        try {
          const url = URL.createObjectURL(new Blob([WORKLET], { type: 'application/javascript' }));
          try {
            await context.audioWorklet.addModule(url);
          } finally {
            URL.revokeObjectURL(url);
          }
          const worklet = new AudioWorkletNode(context, 'cognitrix-live-collector');
          worklet.port.onmessage = (e: MessageEvent<Float32Array>) => onBlock(e.data);
          node = worklet;
        } catch {
          const processor = context.createScriptProcessor(1024, 1, 1);
          processor.onaudioprocess = (e) => onBlock(new Float32Array(e.inputBuffer.getChannelData(0)));
          node = processor;
        }
        const mute = context.createGain();
        mute.gain.value = 0;
        source.connect(node);
        node.connect(mute);
        mute.connect(context.destination);
        micRef.current = { stream, context, nodes: [source, meter, node, mute] };
        if (cancelled) {
          releaseMic();
          return;
        }
        setAnalyser(meter);
        settle();
      } catch (err) {
        if (cancelled) return;
        setError(micErrorMessage(err));
        setPhase('error');
      }
    })();

    return () => {
      cancelled = true;
      sayGen.current += 1;
      speakingRef.current = false;
      transcribingRef.current = false;
      abortRef.current?.abort();
      abortRef.current = null;
      stopSpeaking();
      releaseMic();
    };
  }, [opts.enabled, handleUtterance, interrupt, releaseMic, settle]);

  // Approval prompts and questions are asked out loud as they arrive.
  const approvalId = opts.approvals[0]?.request_id ?? '';
  const questionId = opts.question?.request_id ?? '';
  useEffect(() => {
    if (!opts.enabled || phase === 'starting' || phase === 'error') return;
    const o = optsRef.current;
    const approval = o.approvals[0];
    if (approval && !announced.current.has(approval.request_id)) {
      announced.current.add(approval.request_id);
      void say(approvalSpeech(approval, o.agentName));
      return;
    }
    const q = o.question;
    if (!approval && q && !announced.current.has(q.request_id)) {
      announced.current.add(q.request_id);
      void say(questionSpeech(q));
    }
  }, [opts.enabled, approvalId, questionId, phase === 'starting' || phase === 'error', say]); // eslint-disable-line react-hooks/exhaustive-deps

  // When a turn ends, read the reply.
  const wasBusy = useRef(opts.busy);
  useEffect(() => {
    const before = wasBusy.current;
    wasBusy.current = opts.busy;
    if (!opts.enabled) return;
    if (before && !opts.busy) {
      const reply = optsRef.current.lastReply;
      if (reply && reply.content.trim() && !spokenReplies.current.has(reply.id)) {
        spokenReplies.current.add(reply.id);
        void say(reply.content, reply.id);
        return;
      }
    }
    settle();
  }, [opts.busy, opts.enabled, say, settle]);

  const toggleMute = useCallback(() => {
    setMuted((m) => {
      if (!m) vadRef.current?.reset();
      return !m;
    });
    settle();
  }, [settle]);

  /** Push-to-talk style: say it again from the top (drops a half-heard phrase). */
  const retry = useCallback(() => {
    abortRef.current?.abort();
    vadRef.current?.reset();
    transcribingRef.current = false;
    setError(null);
    settle();
  }, [settle]);

  return { phase, muted, heard, said, error, needsSetup, analyser, toggleMute, interrupt, retry, say };
}

export type LiveVoice = ReturnType<typeof useLiveVoice>;
