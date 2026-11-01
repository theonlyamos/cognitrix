import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { useSystemInfo } from '@/hooks/useSystemInfo';
import { Input } from '@/lib/components/ui/input';
import { Textarea } from '@/lib/components/ui/textarea';
import { Select } from '@/lib/components/ui/select';
import { LoadingState } from '@/components/list-ui';
import { Field, PageForm } from '@/components/form';
import { cn } from '@/lib/utils';
import { REASONING_LEVELS } from '@/lib/reasoning';
import { BASE_URLS, MODELS, PROVIDERS, ignoresTemperature } from '@/lib/providers';
import { listMcpServers, type McpServer } from '@/lib/mcp';

interface Tool { name: string; description?: string; category?: string }
interface AgentData {
  id?: string;
  name?: string;
  system_prompt?: string;
  llm?: { provider?: string; model?: string; temperature?: number; base_url?: string; max_tokens?: number; reasoning_effort?: string };
  tools?: Tool[];
  mcp_servers?: string[];
}

export default function AgentPage() {
  const { agentId } = useParams();
  const navigate = useNavigate();
  const editing = Boolean(agentId);
  const backTo = editing ? `/agents/${agentId}` : '/agents';

  const { data: existing, loading: loadingAgent } = useResource<AgentData>(agentId ? `/agents/${agentId}` : null);
  const { data: toolList } = useResource<Tool[]>('/tools');
  const { info: systemInfo } = useSystemInfo();

  const [name, setName] = useState('');
  const [provider, setProvider] = useState('google');
  const [model, setModel] = useState('gemini-3.5-flash');
  const [temperature, setTemperature] = useState(0.4);
  const [reasoning, setReasoning] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [maxTokens, setMaxTokens] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [mcpServers, setMcpServers] = useState<McpServer[] | null>(null);
  const [selectedServers, setSelectedServers] = useState<string[]>([]);

  useEffect(() => {
    listMcpServers().then((l) => setMcpServers(Array.isArray(l?.servers) ? l.servers : [])).catch(() => setMcpServers([]));
  }, []);

  useEffect(() => {
    if (!existing) return;
    setName(existing.name || '');
    setProvider(existing.llm?.provider || 'google');
    setModel(existing.llm?.model || '');
    setTemperature(existing.llm?.temperature ?? 0.4);
    setReasoning(existing.llm?.reasoning_effort || '');
    setSystemPrompt(existing.system_prompt || '');
    setSelected(new Set((existing.tools || []).map((t) => t.name)));
    setMaxTokens(existing.llm?.max_tokens ? String(existing.llm.max_tokens) : '');
    setBaseUrl(existing.llm?.base_url || '');
    setSelectedServers(existing.mcp_servers || []);
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

  const noTemperature = ignoresTemperature(provider, model);

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
          reasoning_effort: reasoning,
          ...(baseUrl ? { base_url: baseUrl } : {}),
          ...(maxTokens ? { max_tokens: Number(maxTokens) } : {}),
        },
        tools: (toolList || []).filter((t) => selected.has(t.name)),
        mcp_servers: selectedServers,
      };
      const response = await api.post('/agents', payload);
      const id = agentId || response.data?.id;
      navigate(id ? `/agents/${id}` : '/agents');
    } catch (e) {
      setError(errorMessage(e, 'Could not save the agent.'));
    } finally {
      setSaving(false);
    }
  };

  if (editing && loadingAgent) {
    return (
      <div className="flex-1 flex flex-col h-full min-w-0 bg-bg">
        <LoadingState label="loading agent…" />
      </div>
    );
  }

  return (
    <PageForm
      eyebrow={editing ? 'EDIT AGENT' : 'NEW AGENT'}
      title={editing ? name || 'Edit agent' : 'New agent'}
      backTo={backTo}
      error={error}
      onSave={save}
      saving={saving}
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

      <Field label="TEMPERATURE" hint={noTemperature ? 'not used' : temperature.toFixed(2)}>
        <input
          type="range" min={0} max={1} step={0.05} value={temperature}
          onChange={(e) => setTemperature(Number(e.target.value))}
          disabled={noTemperature}
          aria-label="Temperature"
          className="h-2 w-full cursor-pointer appearance-none rounded-full bg-panel-2 accent-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-40"
        />
        {noTemperature && (
          <p className="mt-1.5 text-[12px] text-fg-dim">
            This Claude model sets its own temperature, so this setting isn't sent. Use the reasoning level to tune it.
          </p>
        )}
      </Field>

      <Field label="REASONING" hint="sent as reasoning_effort">
        <Select aria-label="Reasoning level" value={reasoning} onChange={(e) => setReasoning(e.target.value)}>
          <option value="">Provider default</option>
          {REASONING_LEVELS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
        </Select>
        <p className="mt-1.5 text-[12px] text-fg-dim">
          How hard the model thinks before answering. Works with models that support reasoning levels (OpenAI, Anthropic, Gemini, Grok and others through their OpenAI-compatible APIs). You can change it per chat under the message box.
        </p>
      </Field>

      <Field label="SYSTEM PROMPT">
        <Textarea rows={6} value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)} placeholder="You are a helpful assistant that…" />
      </Field>

      {selected.has('Bash') && (
        <p className="-mt-1 rounded border border-line bg-panel-2 px-3 py-2 text-[12.5px] text-fg-dim">
          Shell commands start in the working folder
          {systemInfo?.working_folder ? <> <span className="font-mono text-[12px] text-fg">{systemInfo.working_folder}</span></> : null}. Read-only commands run right away; anything else waits for your approval.{' '}
          {systemInfo && !systemInfo.desktop && 'On a shared server, the shell is off in chats and tasks; it works in the desktop app and the CLI. '}
          <Link to="/settings" className="text-accent-ink hover:underline">Change</Link>
        </p>
      )}
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

      <Field label={`MCP SERVERS · ${selectedServers.length} selected`} composite>
        <div role="group" className="rounded border border-line">
          {mcpServers === null ? (
            <div className="px-3 py-3 font-mono text-[11px] text-fg-dim">loading…</div>
          ) : mcpServers.length === 0 && selectedServers.length === 0 ? (
            <div className="px-3 py-3 text-[12.5px] text-fg-dim">
              No MCP servers yet. <Link to="/settings#mcp-servers" className="text-accent-ink hover:underline">Add one in Settings</Link>
            </div>
          ) : (
            <>
              {mcpServers.map((s) => {
                const checked = selectedServers.includes(s.ref) || selectedServers.includes(s.name);
                return (
                  <label key={s.ref} className="flex cursor-pointer items-start gap-2.5 border-b border-line px-3 py-2 last:border-b-0 hover:bg-panel-2">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => setSelectedServers((cur) =>
                        checked ? cur.filter((r) => r !== s.ref && r !== s.name) : [...cur, s.ref])}
                      className="mt-0.5 accent-[var(--accent)]"
                    />
                    <div className="min-w-0">
                      <div className="font-mono text-[12px]">
                        {s.name}
                        {s.shared && <span className="ml-2 font-sans text-[11px] text-fg-dim">shared</span>}
                        {!s.enabled && <span className="ml-2 font-sans text-[11px] text-fg-dim">off</span>}
                      </div>
                      {s.description && <div className="truncate text-[12px] text-fg-dim">{s.description}</div>}
                    </div>
                  </label>
                );
              })}
              {selectedServers
                .filter((r) => !mcpServers.some((s) => s.ref === r || s.name === r))
                .map((r) => (
                  <div key={r} className="flex items-center justify-between gap-2 border-b border-line px-3 py-2 text-[12px] last:border-b-0">
                    <span className="text-fg-dim">
                      <span className="font-mono">{r}</span> isn't available any more
                    </span>
                    <button type="button" onClick={() => setSelectedServers((cur) => cur.filter((x) => x !== r))}
                      className="text-danger-ink hover:underline">
                      Remove
                    </button>
                  </div>
                ))}
            </>
          )}
        </div>
        <p className="mt-1.5 text-[12px] text-fg-dim">
          The agent can list and run these servers' tools. Tools that aren't marked read-only ask for your approval first.
        </p>
      </Field>

      <button
        type="button"
        onClick={() => setShowAdvanced((v) => !v)}
        className="flex min-h-11 items-center gap-2 font-mono text-[11px] tracking-[0.06em] text-fg-dim transition-colors hover:text-fg md:min-h-0"
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
            {baseUrl.trim() && (
              <p className="mt-1.5 text-[12px] leading-relaxed text-fg-dim">
                Your stored provider key is only sent to the provider's own address or to addresses listed in
                COGNITRIX_TRUSTED_BASE_URLS. Any other address is blocked from receiving it.
              </p>
            )}
          </Field>
        </div>
      )}
    </PageForm>
  );
}
