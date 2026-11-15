import { useEffect, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { Input } from '@/lib/components/ui/input';
import { Select } from '@/lib/components/ui/select';
import { Field } from '@/components/form';
import { getVoiceSettings, saveVoiceSettings, type VoiceSettings } from '@/lib/voice';
import { tr } from '@/lib/i18n';

/** Settings › Voice input: which speech-to-text provider and model the mic
 *  button uses, and a key for it when the engine has none. */
export function VoiceSettingsCard() {
  const [settings, setSettings] = useState<VoiceSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    getVoiceSettings()
      .then((s) => { setSettings(s); setModel(s.model); })
      .catch((e) => setLoadError(errorMessage(e, tr('ui.voiceSettingsCard.couldNotLoadVoiceSettings'))));
  }, []);

  useEffect(() => {
    if (settings && window.location.hash === '#voice-input') {
      document.getElementById('voice-input')?.scrollIntoView({ block: 'start' });
    }
  }, [settings]);

  if (loadError) return <p role="alert" className="text-[12.5px] text-danger-ink">{loadError}</p>;
  if (!settings) return <p className="text-[13px] text-fg-dim">{tr('ui.voiceSettingsCard.loading')}</p>;

  const providerId = settings.provider || settings.effective.provider;
  const option = settings.providers.find((p) => p.id === providerId);
  const hasKey = Boolean(option && (option.server_key || option.user_key));

  const save = async (update: Parameters<typeof saveVoiceSettings>[0], message: string) => {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      const next = await saveVoiceSettings(update);
      setSettings(next);
      setModel(next.model);
      setSaved(message);
    } catch (e) {
      setError(errorMessage(e, tr('ui.voiceSettingsCard.couldNotSaveVoiceSettings')));
    } finally {
      setBusy(false);
    }
  };

  const changeProvider = (next: string) => {
    setApiKey('');
    void save({ provider: next }, tr('ui.voiceSettingsCard.providerSaved'));
  };

  const saveModel = () => {
    if (!settings.provider) return;
    if (model.trim() === settings.model) return;
    void save({ provider: settings.provider, model: model.trim() }, tr('ui.voiceSettingsCard.modelSaved'));
  };

  const saveKey = () => {
    if (!apiKey.trim() || !providerId) return;
    void save({ provider: providerId, api_key: apiKey.trim() }, tr('ui.voiceSettingsCard.keySaved'));
    setApiKey('');
  };

  const removeKey = () => void save({ provider: providerId, api_key: '' }, tr('ui.voiceSettingsCard.keyRemoved'));

  const autoLabel = settings.effective.provider
    ? `Auto (${settings.providers.find((p) => p.id === settings.effective.provider)?.label})`
    : 'Auto (no provider with a key yet)';

  return (
    <div className="space-y-3">
      <p className="text-[13px] leading-relaxed text-fg-dim">
        {tr('ui.voiceSettingsCard.theMicButtonInThe')}
      </p>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label={tr('ui.voiceSettingsCard.provider')}>
          <Select value={settings.provider} onChange={(e) => changeProvider(e.target.value)} disabled={busy}>
            <option value="">{autoLabel}</option>
            {settings.providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
                {p.server_key || p.user_key ? ' ✓' : ''}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={tr('ui.voiceSettingsCard.model')} hint={settings.provider ? undefined : tr('ui.voiceSettingsCard.pickAProviderToChange')}>
          <Input
            list="voice-model-suggestions"
            value={settings.provider ? model : settings.effective.model}
            placeholder={option?.default_model || tr('ui.voiceSettingsCard.modelName')}
            onChange={(e) => setModel(e.target.value)}
            onBlur={saveModel}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); saveModel(); } }}
            disabled={busy || !settings.provider}
          />
        </Field>
        <datalist id="voice-model-suggestions">
          {(option?.models || []).map((m) => <option key={m} value={m} />)}
        </datalist>
      </div>

      {option && (
        <div className="rounded-[10px] border border-line bg-bg px-3 py-2.5">
          {option.server_key && !option.user_key ? (
            <p className="text-[12.5px] text-fg-dim">
              {tr('ui.voiceSettingsCard.usingTheEngineSLabel', { label: option.label })}
            </p>
          ) : option.user_key ? (
            <div className="flex items-center justify-between gap-3 text-[12.5px]">
              <span className="text-fg-dim">
                {tr('ui.voiceSettingsCard.yourLabelKey', { label: option.label })}{' '}<span className="font-mono text-fg">{option.key_hint}</span>{' '}{tr('ui.voiceSettingsCard.isSaved')}
              </span>
              <button type="button" onClick={removeKey} disabled={busy} className="text-danger-ink hover:underline disabled:opacity-60">
                {tr('ui.voiceSettingsCard.remove')}
              </button>
            </div>
          ) : (
            <p className="text-[12.5px] text-fg-dim">
              {tr('ui.voiceSettingsCard.noLabelKeyOnThis', { label: option.label })}{' '}
              <a href={option.key_url} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">{tr('ui.voiceSettingsCard.getAKey')}</a>
            </p>
          )}
          <form
            className="mt-2 flex gap-2"
            onSubmit={(e) => { e.preventDefault(); saveKey(); }}
          >
            <Input
              type="password"
              autoComplete="off"
              aria-label={tr('ui.voiceSettingsCard.labelApiKeyForVoice', { label: option.label })}
              placeholder={tr('ui.voiceSettingsCard.labelApiKey', { label: option.label })}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              disabled={busy}
            />
            <button
              type="submit"
              disabled={busy || !apiKey.trim()}
              className="h-11 flex-none rounded-lg border border-line px-3 text-[13px] hover:bg-panel-2 disabled:opacity-60 md:h-10"
            >
              {tr('ui.voiceSettingsCard.saveKey')}
            </button>
          </form>
        </div>
      )}

      <div aria-live="polite" className="min-h-[1rem] text-[12px]">
        {error ? (
          <span role="alert" className="text-danger-ink">{error}</span>
        ) : saved ? (
          <span className="text-fg-dim">{saved}</span>
        ) : !hasKey && settings.effective.provider === '' ? (
          <span className="text-fg-dim">{tr('ui.voiceSettingsCard.voiceInputIsOffUntil')}</span>
        ) : null}
      </div>
    </div>
  );
}
