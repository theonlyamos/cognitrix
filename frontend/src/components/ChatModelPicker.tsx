import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { MODELS, PROVIDER_LABELS, isHarness } from '@/lib/providers';
import { fetchProviderModels } from '@/components/ModelPicker';
import { composerBtn, composerPopover } from '@/components/composerStyles';
import { cn } from '@/lib/utils';

export interface ChatModel {
  provider: string;
  model: string;
}

interface ProviderOption {
  id: string;
  default_model: string;
}

// Which providers the engine can use (has a key or plan). Asked once per page load.
let optionsPending: Promise<ProviderOption[]> | null = null;
function fetchChatProviders(): Promise<ProviderOption[]> {
  if (!optionsPending) {
    optionsPending = api
      .get<{ providers: ProviderOption[] }>('/providers/chat-options')
      .then((r) => (Array.isArray(r.data?.providers) ? r.data.providers : []))
      .catch(() => {
        optionsPending = null;
        return [];
      });
  }
  return optionsPending;
}

/** Test hook. */
export function clearChatProviderCache() {
  optionsPending = null;
}

/** "anthropic/claude-sonnet-5.5" -> "claude-sonnet-5.5" for the button. */
export function shortModel(model: string): string {
  return model.split('/').pop() || model;
}

const ChipIcon = () => (
  <svg aria-hidden width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" className="flex-none">
    <rect x="6" y="6" width="12" height="12" rx="2" /><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" />
  </svg>
);
const Check = () => (
  <svg aria-hidden width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" className="flex-none text-accent-ink"><path d="M20 6 9 17l-5-5" /></svg>
);

interface Props {
  /** The agent's own provider and model. */
  agentLlm?: { provider?: string | null; model?: string | null } | null;
  /** The model picked for this chat; null uses the agent's. */
  value: ChatModel | null;
  onChange: (value: ChatModel | null) => void;
  disabled?: boolean;
}

/**
 * Provider -> model choice for the chat box. Picks only change what this chat
 * sends with each message; the agent's saved model stays as it is.
 */
export default function ChatModelPicker({ agentLlm, value, onChange, disabled }: Props) {
  const agentProvider = (agentLlm?.provider || '').toLowerCase();
  const agentModel = agentLlm?.model || '';
  const [open, setOpen] = useState(false);
  const [providers, setProviders] = useState<ProviderOption[]>([]);
  const [provider, setProvider] = useState(value?.provider || agentProvider);
  const [live, setLive] = useState<string[]>([]);
  const [custom, setCustom] = useState('');
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    setProvider(value?.provider || agentProvider);
    let alive = true;
    void fetchChatProviders().then((rows) => { if (alive) setProviders(rows); });
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setOpen(false); buttonRef.current?.focus(); }
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      alive = false;
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
    // Only when the popover opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open || !provider) return;
    let alive = true;
    setLive([]);
    void fetchProviderModels(provider).then((r) => { if (alive) setLive(r.models); });
    return () => { alive = false; };
  }, [open, provider]);

  const providerIds = useMemo(() => {
    const ids = providers.map((p) => p.id);
    if (agentProvider && !ids.includes(agentProvider)) ids.unshift(agentProvider);
    return ids;
  }, [providers, agentProvider]);

  const models = useMemo(() => {
    const list = [...(MODELS[provider] || []), ...live];
    if (provider === agentProvider && agentModel) list.unshift(agentModel);
    if (value?.provider === provider && value.model) list.unshift(value.model);
    return Array.from(new Set(list.filter(Boolean)));
  }, [provider, live, agentProvider, agentModel, value]);

  if (!agentProvider || isHarness(agentProvider)) return null;

  const current = value?.model || agentModel;
  const pick = (next: ChatModel | null) => {
    // Picking the agent's own model is the same as no override.
    if (next && next.provider === agentProvider && next.model === agentModel) next = null;
    onChange(next);
    setOpen(false);
    setCustom('');
  };
  const isSelected = (m: string) => (value ? value.provider === provider && value.model === m : provider === agentProvider && m === agentModel);

  return (
    <div ref={rootRef} className="min-w-0">
      <button
        ref={buttonRef}
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Model: ${current || 'agent default'}${value ? '' : ' (agent default)'}. Change`}
        title={value ? `${PROVIDER_LABELS[value.provider] || value.provider} · ${value.model} (this chat only)` : `${PROVIDER_LABELS[agentProvider] || agentProvider} · ${agentModel} (the agent's model)`}
        className={cn(composerBtn, open && 'bg-panel-2 text-fg', value && 'text-fg')}
      >
        <ChipIcon />
        <span className="hidden max-w-[10rem] truncate sm:inline">{shortModel(current) || 'Model'}</span>
        {value && <span aria-hidden className="h-1.5 w-1.5 flex-none rounded-full bg-accent" />}
        <svg aria-hidden width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="hidden opacity-60 sm:block"><path d="m6 9 6 6 6-6" /></svg>
      </button>
      {open && (
        <>
          <div aria-hidden className="fixed inset-0 z-40 bg-black/40 md:hidden" onClick={() => setOpen(false)} />
          <div role="dialog" aria-label="Model for this chat" className={cn(composerPopover, 'md:w-[22rem]')}>
            <div aria-hidden className="mx-auto mb-2 h-1 w-9 rounded-full bg-line md:hidden" />
            <div className="px-2 pb-1 pt-1.5 font-mono text-[10.5px] tracking-[0.1em] text-fg-dim">MODEL FOR THIS CHAT</div>
            <button
              type="button"
              onClick={() => pick(null)}
              className={cn('flex min-h-11 w-full items-center gap-2 rounded-lg px-2 text-left hover:bg-panel-2 md:min-h-9', !value && 'bg-panel-2')}
            >
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] text-fg">Agent's model</span>
                <span className="block truncate text-[11px] text-fg-dim">{PROVIDER_LABELS[agentProvider] || agentProvider} · {agentModel || 'default'}</span>
              </span>
              {!value && <Check />}
            </button>
            <div className="mx-1 my-1.5 h-px bg-line" />
            <div role="tablist" aria-label="Provider" className="flex flex-wrap gap-1 px-1.5 pb-1.5">
              {providerIds.map((id) => (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  aria-selected={id === provider}
                  onClick={() => setProvider(id)}
                  className={cn(
                    'min-h-9 rounded-md border px-2 text-[12px] transition-colors md:min-h-7',
                    id === provider ? 'border-transparent bg-accent text-accent-foreground' : 'border-line text-fg-dim hover:border-fg-dim hover:text-fg',
                  )}
                >
                  {PROVIDER_LABELS[id] || id}
                </button>
              ))}
            </div>
            <div role="listbox" aria-label={`${PROVIDER_LABELS[provider] || provider} models`} className="max-h-56 overflow-y-auto">
              {models.map((m) => (
                <button
                  key={m}
                  type="button"
                  role="option"
                  aria-selected={isSelected(m)}
                  onClick={() => pick({ provider, model: m })}
                  className="flex min-h-11 w-full items-center gap-2 rounded-lg px-2 text-left font-mono text-[12px] text-fg hover:bg-panel-2 md:min-h-8"
                >
                  <span className="min-w-0 flex-1 truncate">{m}</span>
                  {isSelected(m) && <Check />}
                </button>
              ))}
              {models.length === 0 && <p className="px-2 py-1.5 text-[12px] text-fg-dim">No models listed for this provider. Type one below.</p>}
            </div>
            <form className="flex gap-1.5 px-1.5 pb-1 pt-1.5" onSubmit={(e) => { e.preventDefault(); if (custom.trim()) pick({ provider, model: custom.trim() }); }}>
              <label className="sr-only" htmlFor="chat-model-custom">Other model name</label>
              <input
                id="chat-model-custom"
                value={custom}
                onChange={(e) => setCustom(e.target.value)}
                placeholder="Other model name"
                className="min-w-0 flex-1 rounded-md border border-line bg-bg px-2 py-1.5 font-mono text-[12px] text-fg placeholder:text-fg-dim"
              />
              <button type="submit" disabled={!custom.trim()} className="rounded-md border border-line px-2.5 text-[12px] hover:bg-panel-2 disabled:opacity-50">Use</button>
            </form>
          </div>
        </>
      )}
    </div>
  );
}
