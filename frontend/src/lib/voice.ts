import { api } from '@/lib/api';

export interface VoiceProviderOption {
  id: string;
  label: string;
  models: string[];
  default_model: string;
  /** The engine has its own key for this provider. */
  server_key: boolean;
  /** The user saved their own key for this provider. */
  user_key: boolean;
  key_hint: string | null;
  key_url: string;
}

export interface VoiceSettings {
  /** '' means Auto: the first provider with a key. */
  provider: string;
  /** '' means the provider's default model. */
  model: string;
  effective: { provider: string; model: string; ready: boolean };
  max_seconds: number;
  providers: VoiceProviderOption[];
}

export interface VoiceSettingsUpdate {
  provider?: string;
  model?: string;
  /** A key for `provider`; '' removes the saved one. */
  api_key?: string;
}

export async function getVoiceSettings(): Promise<VoiceSettings> {
  return (await api.get<VoiceSettings>('/audio/settings')).data;
}

export async function saveVoiceSettings(update: VoiceSettingsUpdate): Promise<VoiceSettings> {
  return (await api.put<VoiceSettings>('/audio/settings', update)).data;
}

export async function transcribeAudio(audio: Blob, signal?: AbortSignal): Promise<string> {
  const res = await api.post<{ text: string }>('/audio/transcriptions', audio, {
    headers: { 'Content-Type': audio.type || 'audio/wav' },
    signal,
    timeout: 180_000,
  });
  return res.data?.text ?? '';
}

/** Put `text` into `value` at the selection, with a space on either side
 *  where it would otherwise run into the existing words. Returns the new value
 *  and where the cursor should go. */
export function insertTranscript(
  value: string,
  text: string,
  start = value.length,
  end = start,
): { value: string; cursor: number } {
  const clean = text.trim();
  if (!clean) return { value, cursor: end };
  const before = value.slice(0, start);
  const after = value.slice(end);
  const lead = before && !/\s$/.test(before) ? ' ' : '';
  const trail = after && !/^\s/.test(after) ? ' ' : '';
  const inserted = `${lead}${clean}${trail}`;
  return { value: before + inserted + after, cursor: before.length + lead.length + clean.length };
}

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
