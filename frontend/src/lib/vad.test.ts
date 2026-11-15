import { describe, expect, it } from 'vitest';
import { VoiceActivityDetector } from './vad';

const RATE = 16000;
const BLOCK = 320; // 20 ms

function tone(level: number): Float32Array {
  const out = new Float32Array(BLOCK);
  for (let i = 0; i < BLOCK; i++) out[i] = Math.sin(i / 3) * level * Math.SQRT2;
  return out;
}

function feed(vad: VoiceActivityDetector, level: number, ms: number) {
  const events = [];
  for (let t = 0; t < ms; t += 20) {
    const e = vad.push(tone(level));
    if (e) events.push(e);
  }
  return events;
}

describe('VoiceActivityDetector', () => {
  it('finds an utterance between pauses and keeps the lead-in', () => {
    const vad = new VoiceActivityDetector({ sampleRate: RATE, pauseMs: 600 });
    expect(feed(vad, 0.002, 1000)).toEqual([]);
    const start = feed(vad, 0.1, 800);
    expect(start.map((e) => e.type)).toEqual(['speech-start']);
    const end = feed(vad, 0.002, 700);
    expect(end).toHaveLength(1);
    expect(end[0].type).toBe('speech-end');
    if (end[0].type === 'speech-end') {
      // 800 ms of talking + the pause + up to 350 ms from before it started.
      expect(end[0].durationMs).toBeGreaterThan(1300);
      expect(end[0].durationMs).toBeLessThan(1900);
    }
  });

  it('ignores a short click', () => {
    const vad = new VoiceActivityDetector({ sampleRate: RATE });
    feed(vad, 0.002, 500);
    expect(feed(vad, 0.2, 60)).toEqual([]);
    expect(vad.active).toBe(false);
  });

  it('drops an utterance that was mostly silence', () => {
    const vad = new VoiceActivityDetector({ sampleRate: RATE, pauseMs: 400, startMs: 100, minSpeechMs: 500 });
    feed(vad, 0.002, 500);
    const events = [...feed(vad, 0.1, 140), ...feed(vad, 0.002, 500)];
    expect(events.map((e) => e.type)).toEqual(['speech-start', 'discarded']);
  });

  it('needs louder speech while sensitivity is raised', () => {
    const vad = new VoiceActivityDetector({ sampleRate: RATE });
    feed(vad, 0.002, 500);
    vad.sensitivity = 5;
    expect(feed(vad, 0.03, 400)).toEqual([]);
    vad.sensitivity = 1;
    expect(feed(vad, 0.03, 400).map((e) => e.type)).toEqual(['speech-start']);
  });

  it('cuts off very long speech', () => {
    const vad = new VoiceActivityDetector({ sampleRate: RATE, maxMs: 2000 });
    feed(vad, 0.002, 300);
    const events = feed(vad, 0.1, 2600);
    // Talking on afterwards starts the next piece.
    expect(events.map((e) => e.type)).toEqual(['speech-start', 'speech-end', 'speech-start']);
  });
});
