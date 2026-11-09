import { useEffect, useState } from 'react';
import {
  browserSpeechAvailable, getSpeechOptions, setSpeechPrefs, speakText, stopSpeaking, useSpeechPrefs, useSpeechState,
  type SpeechOptions,
} from '@/lib/speech';
import { Toggle } from '@/pages/Notifications';
import { Group, SettingRow, buttonClass } from './ui';

const selectClass = 'h-9 max-w-[220px] rounded-lg border border-line bg-panel-2 px-2 text-[12.5px] outline-none focus:border-accent md:h-8';
const SAMPLE = 'Hello. This is how your agents will sound when they read a reply aloud.';

/** Settings › Voice: who reads replies aloud, in which voice, and whether
 *  every reply is read without asking. Saved on this device. */
export function SpokenRepliesSettings() {
  const prefs = useSpeechPrefs();
  const speech = useSpeechState();
  const [options, setOptions] = useState<SpeechOptions | null>(null);
  const [browserVoices, setBrowserVoices] = useState<string[]>([]);

  useEffect(() => {
    getSpeechOptions().then(setOptions).catch(() => setOptions({ max_chars: 0, providers: [] }));
  }, []);

  useEffect(() => {
    if (!browserSpeechAvailable()) return;
    const synth = window.speechSynthesis;
    const read = () => setBrowserVoices(synth.getVoices().map((v) => v.name));
    read();
    synth.addEventListener?.('voiceschanged', read);
    return () => synth.removeEventListener?.('voiceschanged', read);
  }, []);

  useEffect(() => () => stopSpeaking(), []);

  const ready = options?.providers.filter((p) => p.ready) ?? [];
  const chosen = prefs.provider === 'browser' ? 'browser' : ready.find((p) => p.id === prefs.provider)?.id ?? '';
  const effective = chosen || ready[0]?.id || 'browser';
  const voices = effective === 'browser' ? browserVoices : ready.find((p) => p.id === effective)?.voices ?? [];
  const testing = speech.id === 'speech-test' && speech.phase !== 'idle';
  const autoLabel = ready[0] ? `Auto (${ready[0].label})` : 'Auto (this browser)';

  return (
    <>
      <Group>
        <SettingRow label="Read replies aloud" detail="Each reply is read when it finishes. You can always press listen under a reply instead.">
          <Toggle label="Read replies aloud" checked={prefs.autoRead} onChange={(v) => setSpeechPrefs({ autoRead: v })} />
        </SettingRow>
        <SettingRow
          label="Read by"
          detail={ready.length
            ? 'OpenAI and Gemini sound more natural and use your key. The browser voice is free and works offline.'
            : 'Using the voices built into this browser. Add an OpenAI or Gemini key above for more natural voices.'}
        >
          <select
            aria-label="Read by"
            className={selectClass}
            value={chosen}
            onChange={(e) => setSpeechPrefs({ provider: e.target.value, voice: '' })}
          >
            <option value="">{autoLabel}</option>
            {ready.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            <option value="browser">This browser</option>
          </select>
        </SettingRow>
        <SettingRow label="Voice">
          <select
            aria-label="Voice"
            className={selectClass}
            value={prefs.provider === effective ? prefs.voice : ''}
            disabled={!prefs.provider}
            onChange={(e) => setSpeechPrefs({ voice: e.target.value })}
          >
            <option value="">Default</option>
            {voices.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
          <button
            type="button"
            className={buttonClass}
            onClick={() => (testing ? stopSpeaking() : void speakText('speech-test', SAMPLE, options))}
          >
            {testing ? 'Stop' : 'Play sample'}
          </button>
        </SettingRow>
      </Group>
      {!prefs.provider && <p className="mt-2 text-[12px] text-fg-dim">Pick who reads to choose a voice.</p>}
      {speech.id === 'speech-test' && speech.error && <p role="alert" className="mt-2 text-[12.5px] text-danger-ink">{speech.error}</p>}
    </>
  );
}
