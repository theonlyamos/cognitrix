import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { Input } from '@/lib/components/ui/input';
import { Textarea } from '@/lib/components/ui/textarea';
import { Select } from '@/lib/components/ui/select';
import { LoadingState } from '@/components/list-ui';
import { Field, PageForm } from '@/components/form';
import { cn } from '@/lib/utils';

const PROVIDERS = ['openrouter', 'openai', 'google', 'groq', 'cerebras', 'ollama'];
const MODELS: Record<string, string[]> = {
  openrouter: ['google/gemini-3.5-flash', 'openai/gpt-4o', 'anthropic/claude-3.5-sonnet'],
  openai: ['gpt-4o', 'gpt-4o-mini', 'o1-mini'],
  google: ['gemini-3.5-flash', 'gemini-2.0-flash', 'gemini-1.5-pro'],
  groq: ['llama-3.3-70b-versatile', 'llama-3.1-8b-instant'],
  cerebras: ['llama-3.3-70b', 'llama3.1-8b'],
  ollama: ['llama3.2', 'qwen2.5', 'mistral'],
};
// Default OpenAI-compatible endpoint per provider — mirrors the backend's
// _DEFAULT_BASE_URLS in cognitrix/providers/base.py.
const BASE_URLS: Record<string, string> = {
  openrouter: 'https://openrouter.ai/api/v1',
  openai: 'https://api.openai.com/v1',
  google: 'https://generativelanguage.googleapis.com/v1beta/openai/v1',
  groq: 'https://api.groq.com/openai/v1',
  cerebras: 'https://api.cerebras.com/v1',
  ollama: 'http://localhost:11434/v1',
};

interface Tool { name: string; description?: string; category?: string }
interface AgentData {
  id?: string;
  name?: string;
  system_prompt?: string;
  llm?: { provider?: string; model?: string; temperature?: number; base_url?: string; max_tokens?: number };
  tools?: Tool[];
}

export default function AgentPage() {
  const { agentId } = useParams();
  const navigate = useNavigate();
  const editing = Boolean(agentId);

  const { data: existing, loading: loadingAgent } = useResource<AgentData>(agentId ? `/agents/${agentId}` : null);
  const { data: toolList } = useResource<Tool[]>('/tools');

  const [name, setName] = useState('');
  const [provider, setProvider] = useState('google');
  const [model, setModel] = useState('gemini-3.5-flash');
  const [temperature, setTemperature] = useState(0.4);
  const [systemPrompt, setSystemPrompt] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [maxTokens, setMaxTokens] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!existing) return;
    setName(existing.name || '');
    setProvider(existing.llm?.provider || 'google');
    setModel(existing.llm?.model || '');
    setTemperature(existing.llm?.temperature ?? 0.4);
    setSystemPrompt(existing.system_prompt || '');
    setSelected(new Set((existing.tools || []).map((t) => t.name)));
    setMaxTokens(existing.llm?.max_tokens ? String(existing.llm.max_tokens) : '');
    setBaseUrl(existing.llm?.base_url || '');
  }, [existing]);

  const toolsByCategory = useMemo(() => {
    const groups: Record<string, Tool[]> = {};
    for (const t of toolList || []) (groups[t.category || 'general'] ||= []).push(t);
    return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b));
  }, [toolList]);

  // Switching provider resets the model and base URL to that provider's
  // defaults — an old model/endpoint won't work against a different provider.
  const changeProvider = (next: string) => {
    setProvider(next);
    setModel(MODELS[next]?.[0] || '');
    setBaseUrl(BASE_URLS[next] || '');
  };

  const toggleTool = (n: string) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return next;
    });

  const save = async () => {
    if (!name.trim()) return setError('Give the agent a name.');
    if (!model.trim()) return setError('Choose a model.');
    setError('');
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        ...(agentId ? { id: agentId } : {}),
        name: name.trim(),
        system_prompt: systemPrompt,
        // api_key is intentionally omitted — the backend resolves it from env.
        llm: {
          provider,
          model: model.trim(),
          temperature,
          ...(baseUrl ? { base_url: baseUrl } : {}),
          ...(maxTokens ? { max_tokens: Number(maxTokens) } : {}),
        },
        tools: (toolList || []).filter((t) => selected.has(t.name)),
        mcp_servers: [],
      };
      await api.post('/agents', payload);
      navigate('/agents');
    } catch (e) {
      setError(errorMessage(e, 'Could not save the agent.'));
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!agentId || !confirm('Delete this agent? This cannot be undone.')) return;
    try {
      await api.delete(`/agents/${agentId}`);
      navigate('/agents');
    } catch (e) {
      setError(errorMessage(e, 'Could not delete the agent.'));
    }
  };

  if (editing && loadingAgent) {
    return (
      <div className="flex-1 flex flex-col h-screen min-w-0 bg-bg">
        <LoadingState label="loading agent…" />
      </div>
    );
  }

  return (
    <PageForm
      eyebrow={editing ? 'EDIT AGENT' : 'NEW AGENT'}
      title={editing ? name || 'Edit agent' : 'New agent'}
      backTo="/agents"
      error={error}
      onSave={save}
      saving={saving}
      onDelete={editing ? remove : undefined}
    >
      <Field label="NAME" required>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Research Assistant" autoFocus />
      </Field>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="PROVIDER">
          <Select value={provider} onChange={(e) => changeProvider(e.target.value)}>
            {PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
          </Select>
        </Field>
        <Field label="MODEL" hint="free text — suggestions per provider" required>
          <Input list="model-suggestions" value={model} onChange={(e) => setModel(e.target.value)} placeholder="model name" />
        </Field>
        <datalist id="model-suggestions">
          {(MODELS[provider] || []).map((m) => <option key={m} value={m} />)}
        </datalist>
      </div>

      <Field label="TEMPERATURE" hint={temperature.toFixed(2)}>
        <input
          type="range" min={0} max={1} step={0.05} value={temperature}
          onChange={(e) => setTemperature(Number(e.target.value))}
          className="h-2 w-full cursor-pointer appearance-none rounded-full bg-panel-2 accent-[var(--accent)]"
        />
      </Field>

      <Field label="SYSTEM PROMPT">
        <Textarea rows={6} value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)} placeholder="You are a helpful assistant that…" />
      </Field>

      <Field label={`TOOLS · ${selected.size} selected`} composite>
        <div role="group" className="max-h-72 overflow-y-auto rounded border border-line">
          {toolsByCategory.length === 0 ? (
            <div className="px-3 py-4 font-mono text-[11px] text-fg-dim">no tools available</div>
          ) : (
            toolsByCategory.map(([cat, tools]) => (
              <div key={cat} className="border-b border-line last:border-b-0">
                <div className="bg-panel-2 px-3 py-1.5 font-mono text-[10px] tracking-[0.14em] text-fg-dim">{cat.toUpperCase()}</div>
                {tools.map((t) => (
                  <label key={t.name} className="flex cursor-pointer items-start gap-2.5 px-3 py-2 hover:bg-panel-2">
                    <input type="checkbox" checked={selected.has(t.name)} onChange={() => toggleTool(t.name)}
                      className="mt-0.5 accent-[var(--accent)]" />
                    <div className="min-w-0">
                      <div className="font-mono text-[12px]">{t.name}</div>
                      {t.description && <div className="truncate text-[12px] text-fg-dim">{t.description}</div>}
                    </div>
                  </label>
                ))}
              </div>
            ))
          )}
        </div>
      </Field>

      <button
        type="button"
        onClick={() => setShowAdvanced((v) => !v)}
        className="flex items-center gap-2 font-mono text-[11px] tracking-[0.06em] text-fg-dim transition-colors hover:text-fg"
      >
        <span className={cn('transition-transform', showAdvanced && 'rotate-90')}>▸</span> ADVANCED
      </button>
      {showAdvanced && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="MAX TOKENS" hint="blank = provider default">
            <Input type="number" value={maxTokens} onChange={(e) => setMaxTokens(e.target.value)} placeholder="8192" />
          </Field>
          <Field label="BASE URL" hint="override endpoint (e.g. local Ollama)">
            <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://…" />
          </Field>
        </div>
      )}
    </PageForm>
  );
}
