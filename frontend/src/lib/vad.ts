// A small energy-based voice activity detector for live voice mode. It learns
// the room's background level, notices when someone starts talking, and says
// when they have stopped (a pause longer than `pauseMs`). Fed one block of
// samples at a time; no audio APIs, so it can be tested with plain arrays.

export interface VadOptions {
  sampleRate: number;
  /** Silence that ends an utterance. */
  pauseMs?: number;
  /** Talking this long counts as speech starting (filters clicks and coughs). */
  startMs?: number;
  /** Audio kept from just before speech started, so first words aren't clipped. */
  preRollMs?: number;
  /** An utterance is cut off here and sent as is. */
  maxMs?: number;
  /** Utterances shorter than this are dropped. */
  minSpeechMs?: number;
  /** Lowest level that can count as speech, whatever the room. */
  floor?: number;
}

export type VadEvent =
  | { type: 'speech-start' }
  | { type: 'speech-end'; samples: Float32Array; durationMs: number }
  | { type: 'discarded' };

export function rms(block: Float32Array): number {
  if (block.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < block.length; i++) sum += block[i] * block[i];
  return Math.sqrt(sum / block.length);
}

export class VoiceActivityDetector {
  private readonly rate: number;
  private readonly pauseMs: number;
  private readonly startMs: number;
  private readonly preRollMs: number;
  private readonly maxMs: number;
  private readonly minSpeechMs: number;
  private readonly floor: number;

  /** Running estimate of the background level. */
  noise = 0.004;
  /** Multiplies the threshold, e.g. while a reply is playing. */
  sensitivity = 1;

  private speaking = false;
  private loudMs = 0;
  private quietMs = 0;
  private spokenMs = 0;
  private pre: Float32Array[] = [];
  private preMs = 0;
  private chunks: Float32Array[] = [];
  private totalMs = 0;

  constructor(opts: VadOptions) {
    this.rate = opts.sampleRate;
    this.pauseMs = opts.pauseMs ?? 1100;
    this.startMs = opts.startMs ?? 160;
    this.preRollMs = opts.preRollMs ?? 350;
    this.maxMs = opts.maxMs ?? 60_000;
    this.minSpeechMs = opts.minSpeechMs ?? 350;
    this.floor = opts.floor ?? 0.012;
  }

  get active(): boolean {
    return this.speaking;
  }

  threshold(): number {
    return Math.max(this.floor, this.noise * 3) * this.sensitivity;
  }

  reset(): void {
    this.speaking = false;
    this.loudMs = 0;
    this.quietMs = 0;
    this.spokenMs = 0;
    this.pre = [];
    this.preMs = 0;
    this.chunks = [];
    this.totalMs = 0;
  }

  /** Feed one block; returns what happened, if anything. */
  push(block: Float32Array): VadEvent | null {
    const ms = (block.length / this.rate) * 1000;
    const level = rms(block);
    const loud = level > this.threshold();

    if (!this.speaking) {
      // Learn the background only while nobody is talking; rise slowly, fall fast.
      if (!loud) this.noise = level > this.noise ? this.noise * 0.995 + level * 0.005 : this.noise * 0.9 + level * 0.1;
      this.pre.push(block);
      this.preMs += ms;
      while (this.pre.length > 1 && this.preMs - (this.pre[0].length / this.rate) * 1000 >= this.preRollMs) {
        this.preMs -= (this.pre.shift()!.length / this.rate) * 1000;
      }
      this.loudMs = loud ? this.loudMs + ms : 0;
      if (this.loudMs >= this.startMs) {
        this.speaking = true;
        this.chunks = [...this.pre];
        this.totalMs = this.preMs;
        this.spokenMs = this.loudMs;
        this.quietMs = 0;
        this.pre = [];
        this.preMs = 0;
        return { type: 'speech-start' };
      }
      return null;
    }

    this.chunks.push(block);
    this.totalMs += ms;
    if (loud) {
      this.quietMs = 0;
      this.spokenMs += ms;
    } else {
      this.quietMs += ms;
    }
    if (this.quietMs >= this.pauseMs || this.totalMs >= this.maxMs) {
      const chunks = this.chunks;
      const spoken = this.spokenMs;
      const duration = this.totalMs;
      this.reset();
      if (spoken < this.minSpeechMs) return { type: 'discarded' };
      const total = chunks.reduce((n, c) => n + c.length, 0);
      const samples = new Float32Array(total);
      let offset = 0;
      for (const c of chunks) {
        samples.set(c, offset);
        offset += c.length;
      }
      return { type: 'speech-end', samples, durationMs: duration };
    }
    return null;
  }
}
