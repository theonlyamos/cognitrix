import { useEffect, useId, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { Select } from '@/lib/components/ui/select';
import { Field } from '@/components/form';
import ModelPicker from '@/components/ModelPicker';
import { AGENTS_CHANGED_EVENT, MODELS, PROVIDER_LABELS } from '@/lib/providers';
import { SignInWaiting, useChatGPTSignIn } from '@/components/settings/ChatGPTPlan';
import { tr } from '@/lib/i18n';

interface ProviderOption {
  id: string;
  needs_key: boolean;
  has_key: boolean;
  default_model: string;
  /** Connected by signing in (ChatGPT plan), not with a key. */
  sign_in?: boolean;
}

/** A specialist the assistant can hand work to (cognitrix/agents/starter_team.py). */
export interface StarterAgent {
  key: string;
  name: string;
  description: string;
  /** This person doesn't have it yet. */
  missing: boolean;
}

export interface OnboardingStatus {
  needs_setup: boolean;
  has_agents: boolean;
  suggested: { name: string; provider: string; model: string };
  providers: ProviderOption[];
  /** Absent on an older engine. */
  starter_team?: StarterAgent[];
}

function keyNote(option: ProviderOption | undefined): string {
  if (!option) return '';
  if (!option.needs_key) return 'no key needed';
  if (option.sign_in) return option.has_key ? 'signed in' : 'needs sign-in';
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
  const [planConnected, setPlanConnected] = useState(false);
  const starterAgents = status.starter_team ?? [];
  const [starterTeam, setStarterTeam] = useState(true);
  const [proactive, setProactive] = useState(true);
  const planSignIn = useChatGPTSignIn(() => setPlanConnected(true));

  const option = status.providers.find((p) => p.id === provider);
  const needsPlanSignIn = Boolean(option?.sign_in && !option.has_key && !planConnected);
  const askForKey = Boolean(option?.needs_key && !option.has_key && !option.sign_in);

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
    if (!name.trim()) return setError(tr('ui.assistantSetup.giveYourAssistantAName'));
    if (!model.trim()) return setError(tr('ui.assistantSetup.chooseAModel'));
    if (needsPlanSignIn) return setError(tr('ui.assistantSetup.continueWithChatgptFirstOr'));
    if (askForKey && !apiKey.trim()) return setError(tr('ui.assistantSetup.pasteYourItemApiKey', { item: PROVIDER_LABELS[provider] || provider }));
    setError('');
    setBusy('create');
    try {
      const res = await api.post('/onboarding/assistant', {
        name: name.trim(),
        provider,
        model: model.trim(),
        ...(askForKey ? { api_key: apiKey.trim() } : {}),
        ...(starterAgents.length ? { starter_team: starterTeam } : {}),
        proactive,
      });
      finish(res.data?.agent?.id);
    } catch (e) {
      setError(errorMessage(e, tr('ui.assistantSetup.couldNotCreateTheAssistant')));
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
      setError(errorMessage(e, tr('ui.assistantSetup.couldNotSkipSetup')));
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
          <div className="font-mono text-[10.5px] tracking-[0.14em] text-accent-ink">{tr('ui.assistantSetup.welcomeToCognitrix')}</div>
          <h2 id={titleId} className="text-[22px] font-semibold leading-tight">{tr('ui.assistantSetup.setUpYourAssistant')}</h2>
          <p id={descId} className="text-[13.5px] leading-relaxed text-fg-dim">
            {tr('ui.assistantSetup.cognitrixComesWithAPersonal')}
          </p>
        </div>

        <div className="space-y-4 px-6 py-5">
          <Field label={tr('ui.assistantSetup.name')} required>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={60}
              placeholder={tr('ui.assistantSetup.assistant')}
              autoFocus
            />
          </Field>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label={tr('ui.assistantSetup.provider')} hint={keyNote(option)}>
              <Select value={provider} onChange={(e) => changeProvider(e.target.value)}>
                {status.providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {PROVIDER_LABELS[p.id] || p.id}
                    {p.needs_key && (p.has_key || (p.sign_in && planConnected)) ? ' ✓' : ''}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={tr('ui.assistantSetup.model')} required>
              <ModelPicker provider={provider} value={model} onChange={setModel} />
            </Field>
          </div>

          {askForKey && (
            <Field label={tr('ui.assistantSetup.apiKey')} required hint={tr('ui.assistantSetup.keptWithThisAssistant')}>
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
              {tr('ui.assistantSetup.noItemKeyIsSet', { item: PROVIDER_LABELS[provider] || provider })}{' '}
              <span className="font-mono text-[11.5px]">{provider.toUpperCase()}_API_KEY</span>{' '}{tr('ui.assistantSetup.toTheEngineSEnv')}
            </p>
          )}
          {option?.sign_in && (
            <div className="-mt-1 space-y-2 rounded border border-line bg-panel-2 py-3 text-[12.5px] leading-relaxed text-fg-dim">
              <p className="px-3.5">
                {needsPlanSignIn
                  ? tr('ui.assistantSetup.runsOnYourChatgptPlus')
                  : tr('ui.assistantSetup.usingChatgptPlanYouCan')}
              </p>
              {needsPlanSignIn && !planSignIn.flowId && (
                <div className="px-3.5">
                  <button
                    type="button"
                    onClick={() => void planSignIn.start(false)}
                    className="inline-flex h-9 items-center rounded-lg bg-[#0d0d0d] px-3.5 text-[12.5px] font-medium text-white hover:bg-[#2a2a2a] dark:bg-white dark:text-[#0d0d0d]"
                  >
                    {tr('ui.assistantSetup.continueWithChatgpt')}
                  </button>
                </div>
              )}
              <SignInWaiting signIn={planSignIn} />
              {planSignIn.error && <p role="alert" className="px-3.5 text-danger-ink">{planSignIn.error}</p>}
            </div>
          )}
          {provider === 'ollama' && (
            <p className="-mt-2 text-[12px] leading-relaxed text-fg-dim">
              {tr('ui.assistantSetup.ollamaRunsModelsOnThis')}{' '}
              <span className="font-mono text-[11.5px]">{tr('ui.assistantSetup.ollamaPullModel', { model: model || 'llama3.2' })}</span>.
            </p>
          )}

          {starterAgents.length > 0 && (
            <label className="flex cursor-pointer items-start gap-2.5 rounded border border-line bg-panel-2 px-3.5 py-3">
              <input
                type="checkbox"
                checked={starterTeam}
                onChange={(e) => setStarterTeam(e.target.checked)}
                className="mt-[3px] accent-[var(--accent)]"
              />
              <span className="min-w-0">
                <span className="block text-[13.5px] font-medium">{tr('ui.assistantSetup.addAStarterTeam')}</span>
                <span className="mt-0.5 block text-[12.5px] leading-relaxed text-fg-dim">
                  {tr('ui.assistantSetup.mapYourAssistantHandsBigger', { map: starterAgents.map((a) => a.name).join(', ') })}
                </span>
              </span>
            </label>
          )}

          <label className="flex cursor-pointer items-start gap-2.5 rounded border border-line bg-panel-2 px-3.5 py-3">
            <input
              type="checkbox"
              checked={proactive}
              onChange={(e) => setProactive(e.target.checked)}
              className="mt-[3px] accent-[var(--accent)]"
            />
            <span className="min-w-0">
              <span className="block text-[13.5px] font-medium">{tr('ui.assistantSetup.checkInOnMyDay')}</span>
              <span className="mt-0.5 block text-[12.5px] leading-relaxed text-fg-dim">
                {tr('ui.assistantSetup.everyFewHoursItLooks')}
              </span>
            </span>
          </label>

          {error && (
            <p role="alert" className="rounded border border-danger/40 bg-danger/10 px-3 py-2 text-[13px] text-danger">
              {error}
            </p>
          )}
        </div>

        <div className="flex flex-col-reverse gap-2 border-t border-line bg-panel-2/60 px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
          <Button type="button" variant="ghost" onClick={() => void skip()} disabled={busy !== null}>
            {busy === 'skip' ? tr('ui.assistantSetup.skipping') : status.has_agents ? tr('ui.assistantSetup.skipForNow') : tr('ui.assistantSetup.skipAndUseDefaults')}
          </Button>
          <Button type="submit" disabled={busy !== null}>
            {busy === 'create' ? tr('ui.assistantSetup.creating') : tr('ui.assistantSetup.createAssistant')}
          </Button>
        </div>
      </form>
    </div>
  );
}
