import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { MicButton, VoiceBar, VoiceNotice, useVoiceInput } from './VoiceInput';
import { VoiceSettingsCard } from './VoiceSettingsCard';
import { encodeWav, resample } from '@/lib/wav';
import { formatDuration, insertTranscript } from '@/lib/voice';

const apiGet = vi.hoisted(() => vi.fn());
const apiPost = vi.hoisted(() => vi.fn());
const apiPut = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api', () => ({
  api: { get: apiGet, post: apiPost, put: apiPut },
  API_BASE: '/api/v1',
  errorMessage: (e: unknown, fallback: string) =>
    (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || fallback,
}));

const recorder = vi.hoisted(() => ({
  start: vi.fn(),
  stop: vi.fn(),
  cancel: vi.fn(),
  state: 'idle' as 'idle' | 'starting' | 'recording',
}));

vi.mock('@/hooks/useVoiceRecorder', async () => {
  const { useState: useReactState } = await import('react');
  return {
    micErrorMessage: () => 'Microphone access is blocked.',
    useVoiceRecorder: () => {
      const [state, setState] = useReactState<'idle' | 'starting' | 'recording'>('idle');
      return {
        state,
        elapsed: 3,
        analyser: null,
        start: async () => { await recorder.start(); setState('recording'); },
        stop: async () => { setState('idle'); return recorder.stop(); },
        cancel: () => { recorder.cancel(); setState('idle'); },
      };
    },
  };
});

const providers = [
  { id: 'openai', label: 'OpenAI', models: ['gpt-transcribe', 'whisper-1'], default_model: 'gpt-transcribe', server_key: false, user_key: false, key_hint: null, key_url: 'https://example.com' },
  { id: 'groq', label: 'Groq', models: ['whisper-large-v3-turbo'], default_model: 'whisper-large-v3-turbo', server_key: true, user_key: false, key_hint: null, key_url: 'https://example.com' },
];

const settings = (over: Record<string, unknown> = {}) => ({
  provider: '',
  model: '',
  effective: { provider: 'groq', model: 'whisper-large-v3-turbo', ready: true },
  max_seconds: 600,
  providers,
  ...over,
});

function Composer({ initial = '' }: { initial?: string }) {
  const [value, setValue] = useState(initial);
  const voice = useVoiceInput({ onText: (text) => setValue((v) => insertTranscript(v, text).value) });
  return (
    <MemoryRouter>
      {voice.active && <VoiceBar voice={voice} />}
      <textarea aria-label="Message" value={value} onChange={(e) => setValue(e.target.value)} />
      {!voice.active && <MicButton voice={voice} />}
      <VoiceNotice voice={voice} />
    </MemoryRouter>
  );
}

describe('voice input', () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    apiPut.mockReset();
    recorder.start.mockReset();
    recorder.stop.mockReset();
    recorder.cancel.mockReset();
  });

  it('records, stops, and puts the transcript in the box without sending', async () => {
    apiGet.mockResolvedValue({ data: settings() });
    recorder.stop.mockResolvedValue(new Blob(['x'], { type: 'audio/wav' }));
    apiPost.mockResolvedValue({ data: { text: 'hello there' } });
    render(<Composer initial="Say" />);

    await waitFor(() => expect(apiGet).toHaveBeenCalledWith('/audio/settings'));
    fireEvent.click(screen.getByRole('button', { name: 'Voice input' }));
    const stop = await screen.findByRole('button', { name: 'Stop recording' });
    expect(screen.getByText('0:03')).toBeInTheDocument();

    fireEvent.click(stop);
    await waitFor(() => expect(screen.getByLabelText('Message')).toHaveValue('Say hello there'));
    expect(apiPost).toHaveBeenCalledTimes(1);
    expect(apiPost.mock.calls[0][0]).toBe('/audio/transcriptions');
    expect(screen.getByRole('button', { name: 'Voice input' })).toBeInTheDocument();
  });

  it('Escape cancels without transcribing', async () => {
    apiGet.mockResolvedValue({ data: settings() });
    render(<Composer />);
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: 'Voice input' }));
    await screen.findByRole('button', { name: 'Stop recording' });

    act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })); });
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop recording' })).not.toBeInTheDocument());
    expect(recorder.cancel).toHaveBeenCalled();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it('points to Settings when no provider has a key', async () => {
    apiGet.mockResolvedValue({ data: settings({ effective: { provider: '', model: '', ready: false } }) });
    render(<Composer />);
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: 'Voice input' }));

    expect(await screen.findByRole('link', { name: 'Set it up in Settings' })).toHaveAttribute('href', '/settings/voice');
    expect(recorder.start).not.toHaveBeenCalled();
  });

  it('shows mic and transcription errors', async () => {
    apiGet.mockResolvedValue({ data: settings() });
    recorder.start.mockRejectedValueOnce(Object.assign(new Error('no'), { name: 'NotAllowedError' }));
    render(<Composer />);
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: 'Voice input' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Microphone access is blocked.');

    recorder.stop.mockResolvedValue(new Blob(['x']));
    apiPost.mockRejectedValue({ response: { data: { detail: 'OpenAI rejected the API key.' } } });
    fireEvent.click(screen.getByRole('button', { name: 'Voice input' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Stop recording' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('OpenAI rejected the API key.'));
  });
});

describe('voice settings', () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPut.mockReset();
  });

  it('saves the provider, then a key for it, without showing the key back', async () => {
    apiGet.mockResolvedValue({ data: settings() });
    apiPut
      .mockResolvedValueOnce({ data: settings({ provider: 'openai', effective: { provider: 'openai', model: 'gpt-transcribe', ready: false } }) })
      .mockResolvedValueOnce({
        data: settings({
          provider: 'openai',
          effective: { provider: 'openai', model: 'gpt-transcribe', ready: true },
          providers: [{ ...providers[0], user_key: true, key_hint: '…1234' }, providers[1]],
        }),
      });
    render(<VoiceSettingsCard />);

    const provider = await screen.findByLabelText(/PROVIDER/);
    expect(provider).toHaveDisplayValue('Auto (Groq)');
    fireEvent.change(provider, { target: { value: 'openai' } });
    await waitFor(() => expect(apiPut).toHaveBeenCalledWith('/audio/settings', { provider: 'openai' }));
    expect(await screen.findByText(/No OpenAI key on this engine yet/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('OpenAI API key for voice input'), { target: { value: 'sk-abc1234' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save key' }));
    await waitFor(() => expect(apiPut).toHaveBeenLastCalledWith('/audio/settings', { provider: 'openai', api_key: 'sk-abc1234' }));
    expect(await screen.findByText('…1234')).toBeInTheDocument();
    expect(screen.getByLabelText('OpenAI API key for voice input')).toHaveValue('');
  });

  it('saves a custom model on Enter', async () => {
    apiGet.mockResolvedValue({ data: settings({ provider: 'groq', model: '' }) });
    apiPut.mockResolvedValue({ data: settings({ provider: 'groq', model: 'whisper-large-v3' }) });
    render(<VoiceSettingsCard />);

    const model = await screen.findByLabelText(/MODEL/);
    fireEvent.change(model, { target: { value: 'whisper-large-v3' } });
    fireEvent.keyDown(model, { key: 'Enter' });
    await waitFor(() => expect(apiPut).toHaveBeenCalledWith('/audio/settings', { provider: 'groq', model: 'whisper-large-v3' }));
  });
});

describe('audio helpers', () => {
  it('encodes 16-bit mono WAV', async () => {
    const wav = encodeWav(new Float32Array([0, 1, -1, 0.5]));
    const buffer = await new Promise<ArrayBuffer>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as ArrayBuffer);
      reader.readAsArrayBuffer(wav);
    });
    const bytes = new DataView(buffer);
    expect(wav.type).toBe('audio/wav');
    expect(bytes.byteLength).toBe(44 + 8);
    expect(bytes.getUint32(24, true)).toBe(16000);
    expect(bytes.getInt16(46, true)).toBe(32767);
    expect(bytes.getInt16(48, true)).toBe(-32768);
  });

  it('downsamples 48 kHz to 16 kHz', () => {
    const out = resample(new Float32Array(48000).fill(0.25), 48000);
    expect(out.length).toBe(16000);
    expect(out[100]).toBeCloseTo(0.25);
  });

  it('inserts at the cursor with spacing and formats time', () => {
    expect(insertTranscript('', 'hi')).toEqual({ value: 'hi', cursor: 2 });
    expect(insertTranscript('Hello world', 'big', 6, 6)).toEqual({ value: 'Hello big world', cursor: 9 });
    expect(insertTranscript('ab', '  ')).toEqual({ value: 'ab', cursor: 2 });
    expect(formatDuration(65)).toBe('1:05');
  });
});
