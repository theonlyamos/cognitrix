import { useEffect, useId, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { Select } from '@/lib/components/ui/select';
import { Field } from '@/components/form';
import ModelPicker from '@/components/ModelPicker';
import { AGENTS_CHANGED_EVENT, MODELS, PROVIDER_LABELS } from '@/lib/providers';

interface ProviderOption {
  id: string;
  needs_key: boolean;
  has_key: boolean;
  default_model: string;
}

export interface OnboardingStatus {
  needs_setup: boolean;
  has_agents: boolean;
  suggested: { name: string; provider: string; model: string };
  providers: ProviderOption[];
}

function keyNote(option: ProviderOption | undefined): string {
  if (!option) return '';
  if (!option.needs_key) return 'no key needed';
  return option.has_key ? 'key found' : 'needs a key';
}

/**
 * First-run dialog: after a user's first sign-in, offer to set up their own
 * default assistant (name, provider, model). Renders nothing once the user
 * has finished or skipped it (GET /onboarding says needs_setup: false).
 */
export default function AssistantSetup() {
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .get<OnboardingStatus>('/onboarding')
      .then((r) => {
        if (cancelled) return;
        setStatus(r.data);
        setOpen(Boolean(r.data?.needs_setup));
      })
      // An older engine without /onboarding, or an API key session: no dialog.
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  if (!open || !status) return null;
  return <AssistantSetupDialog status={status} onDone={() => setOpen(false)} />;
}

export function AssistantSetupDialog({ status, onDone }: { status: OnboardingStatus; onDone: () => void }) {
  const navigate = useNavigate();
  const titleId = useId();
  const descId = useId();
  const [name, setName] = useState(status.suggested.name || 'Assistant');
  const [provider, setProvider] = useState(status.suggested.provider);
  const [model, setModel] = useState(status.suggested.model);
  const [apiKey, setApiKey] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<'create' | 'skip' | null>(null);

  const option = status.providers.find((p) => p.id === provider);
  const askForKey = Boolean(option?.needs_key && !option.has_key);

  const changeProvider = (next: string) => {
    setProvider(next);
    const nextOption = status.providers.find((p) => p.id === next);
    setModel(nextOption?.default_model || MODELS[next]?.[0] || '');
    setApiKey('');
    setError('');
  };

  const finish = (agentId: string | undefined) => {
    if (agentId) {
      localStorage.setItem('selectedAgentId', agentId);
      window.dispatchEvent(new CustomEvent(AGENTS_CHANGED_EVENT, { detail: agentId }));
      navigate('/home');
    }
    onDone();
  };

  const create = async () => {
    if (!name.trim()) return setError('Give your assistant a name.');
    if (!model.trim()) return setError('Choose a model.');
    if (askForKey && !apiKey.trim()) return setError(`Paste your ${PROVIDER_LABELS[provider] || provider} API key, or pick a provider with a key.`);
    setError('');
    setBusy('create');
    try {
      const res = await api.post('/onboarding/assistant', {
        name: name.trim(),
        provider,
        model: model.trim(),
        ...(askForKey ? { api_key: apiKey.trim() } : {}),
      });
      finish(res.data?.agent?.id);
    } catch (e) {
      setError(errorMessage(e, 'Could not create the assistant.'));
    } finally {
      setBusy(null);
    }
  };

  const skip = async () => {
    setError('');
    setBusy('skip');
    try {
      const res = await api.post('/onboarding/skip');
      finish(res.data?.agent?.id);
    } catch (e) {
      setError(errorMessage(e, 'Could not skip setup.'));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="fixed inset-0 z-[75] flex items-start justify-center overflow-y-auto bg-[rgba(5,6,7,.6)] p-4 sm:items-center">
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        onSubmit={(e) => {
          e.preventDefault();
          void create();
        }}
        className="my-8 w-[520px] max-w-full overflow-hidden rounded-2xl border border-line bg-panel shadow-panel"
      >
        <div className="space-y-2 border-b border-line px-6 pb-5 pt-6">
          <div className="font-mono text-[10.5px] tracking-[0.14em] text-accent-ink">WELCOME TO COGNITRIX</div>
          <h2 id={titleId} className="text-[22px] font-semibold leading-tight">Set up your assistant</h2>
          <p id={descId} className="text-[13.5px] leading-relaxed text-fg-dim">
            Cognitrix comes with a personal assistant that can chat, use tools and work with your files. Name it and
            pick the model it runs on. You can change any of this later on the Agents page.
          </p>
        </div>

        <div className="space-y-4 px-6 py-5">
          <Field label="NAME" required>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={60}
              placeholder="Assistant"
              autoFocus
            />
          </Field>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="PROVIDER" hint={keyNote(option)}>
              <Select value={provider} onChange={(e) => changeProvider(e.target.value)}>
                {status.providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {PROVIDER_LABELS[p.id] || p.id}
                    {p.needs_key && p.has_key ? ' ✓' : ''}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="MODEL" required>
              <ModelPicker provider={provider} value={model} onChange={setModel} />
            </Field>
          </div>

          {askForKey && (
            <Field label="API KEY" required hint="kept with this assistant">
              <Input
                type="password"
                autoComplete="off"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={`${provider.toUpperCase()}_API_KEY`}
              />
            </Field>
          )}
          {askForKey && (
            <p className="-mt-2 text-[12px] leading-relaxed text-fg-dim">
              No {PROVIDER_LABELS[provider] || provider} key is set up on this engine yet. A key pasted here is saved
              only with this assistant. To share one key with every agent, add{' '}
              <span className="font-mono text-[11.5px]">{provider.toUpperCase()}_API_KEY</span> to the engine's .env
              file (Settings shows where it is).
            </p>
          )}
          {provider === 'ollama' && (
            <p className="-mt-2 text-[12px] leading-relaxed text-fg-dim">
              Ollama runs models on this computer. Start it and pull the model first, for example{' '}
              <span className="font-mono text-[11.5px]">ollama pull {model || 'llama3.2'}</span>.
            </p>
          )}

          {error && (
            <p role="alert" className="rounded border border-danger/40 bg-danger/10 px-3 py-2 text-[13px] text-danger">
              {error}
            </p>
          )}
        </div>

        <div className="flex flex-col-reverse gap-2 border-t border-line bg-panel-2/60 px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
          <Button type="button" variant="ghost" onClick={() => void skip()} disabled={busy !== null}>
            {busy === 'skip' ? 'Skipping…' : status.has_agents ? 'Skip for now' : 'Skip and use defaults'}
          </Button>
          <Button type="submit" disabled={busy !== null}>
            {busy === 'create' ? 'Creating…' : 'Create assistant'}
          </Button>
        </div>
      </form>
    </div>
  );
}
