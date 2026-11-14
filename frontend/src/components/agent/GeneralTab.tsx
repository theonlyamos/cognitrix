import { useId, useState } from 'react';
import { AgentBadge } from '@/components/StartChat';
import { AVATAR_HUES } from '@/lib/agentAvatar';
import { Link, useNavigate } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import ModelPicker from '@/components/ModelPicker';
import { BASE_URLS, HARNESS_PROVIDERS, MODELS, PROVIDERS, PROVIDER_LABELS, ignoresTemperature, isHarness } from '@/lib/providers';
import HarnessNote from '@/components/HarnessNote';
import PlanNote from '@/components/PlanNote';
import { REASONING_LEVELS } from '@/lib/reasoning';
import { handleFor, payloadFrom, promptSummary, type TabProps } from '@/lib/agentDraft';
import { Card, Check, Eyebrow, buttonClass, inputClass, linkButtonClass } from '@/components/agent/ui';
import { cn } from '@/lib/utils';

const labelClass = 'flex flex-col gap-1.5 text-[13px] font-medium';

export default function GeneralTab({ agent, draft, update, overview, catalog, remove }: TabProps) {
  const navigate = useNavigate();
  const ids = { name: useId(), desc: useId(), provider: useId(), model: useId(), reasoning: useId(), temp: useId(), max: useId(), base: useId(), prompt: useId() };
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const noTemperature = ignoresTemperature(draft.provider, draft.model);
  const words = draft.systemPrompt.trim() ? draft.systemPrompt.trim().split(/\s+/).length : 0;

  const changeProvider = (next: string) => {
    update({ provider: next, model: MODELS[next]?.[0] || '', baseUrl: BASE_URLS[next] || '' });
  };

  const teams = overview?.teams || [];
  const teamLine = teams.length
    ? teams.map((t) => {
      const others = t.members.filter((m) => m.id !== agent.id).map((m) => handleFor(m.name));
      return `Team: ${t.leader_id === agent.id ? 'you lead' : 'you are in'} “${t.name}”${others.length ? ` with ${others.join(', ')}` : ''}. Mention a teammate to hand work over.`;
    }).join('\n')
    : '';

  const duplicate = async () => {
    setBusy(true);
    setError('');
    try {
      const payload = payloadFrom(agent.id, draft, catalog, agent.tools || []);
      delete payload.id;
      payload.name = `${draft.name.trim() || agent.name} (copy)`;
      const res = await api.post<{ id: string }>('/agents', payload);
      if (res.data?.id) navigate(`/agents/${res.data.id}/general`);
    } catch (e) {
      setError(errorMessage(e, 'Could not copy the agent.'));
    } finally {
      setBusy(false);
    }
  };

  const exportFile = () => {
    const payload = payloadFrom(agent.id, draft, catalog, agent.tools || []);
    delete payload.id;
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${(draft.name || 'agent').trim().replace(/[^\w-]+/g, '-').toLowerCase()}.agent.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex flex-col gap-[18px]">
      <Card title="Identity" description="How the agent shows up in chats, teams and the agent list.">
        <div className="grid grid-cols-1 gap-x-[18px] gap-y-3.5 md:grid-cols-2">
          <label htmlFor={ids.name} className={labelClass}>
            Name
            <input id={ids.name} value={draft.name} onChange={(e) => update({ name: e.target.value })} className={inputClass} />
          </label>
          <div className={labelClass}>
            Mention name
            <div className="flex h-11 items-center rounded border border-dashed border-line px-3 font-mono text-[13px] font-normal text-fg-dim md:h-10">
              {handleFor(draft.name || agent.name)}
            </div>
            <span className="text-[12px] font-normal text-fg-dim">Made from the name. Teammates use it to @mention the agent.</span>
          </div>
          <label htmlFor={ids.desc} className={cn(labelClass, 'md:col-span-2')}>
            Short description
            <input id={ids.desc} value={draft.description} maxLength={300} onChange={(e) => update({ description: e.target.value })}
              placeholder="What this agent is for, in one line" className={inputClass} />
            <span className="text-[12px] font-normal text-fg-dim">Shown on the agent list, and to teammates deciding who to hand work to.</span>
          </label>
          <div className={cn(labelClass, 'md:col-span-2')}>
            Thumbnail
            <div className="flex flex-wrap items-center gap-3 font-normal">
              <AgentBadge name={draft.name || agent.name} avatar={{ emoji: draft.avatarEmoji, hue: draft.avatarHue }} size="lg" />
              <div role="radiogroup" aria-label="Thumbnail colour" className="flex flex-wrap items-center gap-1.5">
                <button
                  type="button"
                  role="radio"
                  aria-checked={draft.avatarHue === null}
                  onClick={() => update({ avatarHue: null })}
                  className={cn('h-11 rounded-md border px-2.5 text-[12px] md:h-7', draft.avatarHue === null ? 'border-fg text-fg' : 'border-line text-fg-dim hover:border-fg-dim')}
                >
                  Auto
                </button>
                {AVATAR_HUES.map((hue) => (
                  <button
                    key={hue}
                    type="button"
                    role="radio"
                    aria-checked={draft.avatarHue === hue}
                    aria-label={`Colour ${AVATAR_HUES.indexOf(hue) + 1}`}
                    onClick={() => update({ avatarHue: hue })}
                    style={{ background: `hsl(${hue} 65% 52%)` }}
                    className={cn('h-11 w-11 rounded-md border-2 md:h-7 md:w-7', draft.avatarHue === hue ? 'border-fg' : 'border-transparent')}
                  />
                ))}
              </div>
              <input
                value={draft.avatarEmoji}
                onChange={(e) => update({ avatarEmoji: e.target.value.replace(/\p{ASCII}/gu, "").slice(0, 8) })}
                aria-label="Thumbnail emoji"
                placeholder="Emoji"
                className={cn(inputClass, 'w-24 text-center')}
              />
            </div>
            <span className="text-[12px] font-normal text-fg-dim">Shown in the chat list and its minimized rail. Leave the emoji empty to show the first letter.</span>
          </div>
        </div>
      </Card>

      <Card title="Model" description="Provider keys are set in Settings › Models and keys.">
        <div className="grid grid-cols-1 gap-x-[18px] gap-y-3.5 md:grid-cols-2">
          <label htmlFor={ids.provider} className={labelClass}>
            Provider
            <select id={ids.provider} value={draft.provider} onChange={(e) => changeProvider(e.target.value)} className={inputClass}>
              <optgroup label="Model providers">
                {[...new Set([...PROVIDERS, ...(isHarness(draft.provider) ? [] : [draft.provider])])].map((p) => <option key={p} value={p}>{PROVIDER_LABELS[p] || p}</option>)}
              </optgroup>
              <optgroup label="Coding agents on this computer">
                {HARNESS_PROVIDERS.map((p) => <option key={p} value={p}>{PROVIDER_LABELS[p]}</option>)}
              </optgroup>
            </select>
          </label>
          <div className={labelClass}>
            <label htmlFor={ids.model}>Model</label>
            <ModelPicker id={ids.model} provider={draft.provider} value={draft.model} onChange={(model) => update({ model })} className="font-normal" />
          </div>
          {draft.provider === 'chatgpt' && <div className="md:col-span-2"><PlanNote provider={draft.provider} /></div>}
          {isHarness(draft.provider) && (
            <div className="md:col-span-2">
              <HarnessNote provider={draft.provider} mode={draft.harnessMode} onModeChange={(harnessMode) => update({ harnessMode })} selectClass={inputClass} />
            </div>
          )}
          <label htmlFor={ids.reasoning} className={labelClass}>
            Reasoning
            <select id={ids.reasoning} value={draft.reasoning} onChange={(e) => update({ reasoning: e.target.value })} className={inputClass}>
              <option value="">Provider default</option>
              {REASONING_LEVELS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
            </select>
            <span className="text-[12px] font-normal text-fg-dim">How hard it thinks before answering. You can change it per chat too.</span>
          </label>
        </div>
        <details className="mt-4">
          <summary className="cursor-pointer text-[13px] text-fg-dim hover:text-fg">Advanced: temperature, max tokens, custom base URL</summary>
          <div className="mt-3 grid grid-cols-1 gap-x-[18px] gap-y-3.5 md:grid-cols-3">
            <label htmlFor={ids.temp} className={labelClass}>
              Temperature {noTemperature ? '(not used)' : `· ${draft.temperature.toFixed(2)}`}
              <input id={ids.temp} type="range" min={0} max={1} step={0.05} value={draft.temperature} disabled={noTemperature}
                onChange={(e) => update({ temperature: Number(e.target.value) })}
                className="mt-3 h-2 w-full cursor-pointer appearance-none rounded-full bg-panel-2 accent-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-40" />
            </label>
            <label htmlFor={ids.max} className={labelClass}>
              Max tokens
              <input id={ids.max} inputMode="numeric" value={draft.maxTokens} onChange={(e) => update({ maxTokens: e.target.value.replace(/[^\d]/g, '') })}
                placeholder="Provider default" className={cn(inputClass, 'font-normal')} />
            </label>
            <label htmlFor={ids.base} className={labelClass}>
              Base URL
              <input id={ids.base} value={draft.baseUrl} onChange={(e) => update({ baseUrl: e.target.value })}
                placeholder={BASE_URLS[draft.provider] || 'https://…'} className={cn(inputClass, 'font-mono text-[12px] font-normal')} />
            </label>
          </div>
        </details>
      </Card>

      <Card title="Instructions" description="Your part of the system prompt.">
        <label htmlFor={ids.prompt} className="sr-only">System prompt</label>
        <textarea id={ids.prompt} rows={10} value={draft.systemPrompt} onChange={(e) => update({ systemPrompt: e.target.value })}
          placeholder="You are a helpful assistant that…"
          className="w-full resize-y rounded border border-line bg-bg p-3 font-mono text-[12.5px] leading-relaxed text-fg placeholder:text-fg-dim focus:border-accent" />
        <div className="mt-1.5 text-[12px] text-fg-dim">{words} {words === 1 ? 'word' : 'words'}</div>
      </Card>

      <Card
        title="Memory"
        description="Notes this agent keeps about you between chats."
        aside={<Link to="/settings/memory" className={linkButtonClass}>See memories</Link>}
      >
        <div className="flex flex-col gap-4">
          <Check
            checked={draft.memoryReadGlobal}
            onChange={(memoryReadGlobal) => update({ memoryReadGlobal })}
            label="Use your shared memories"
            detail="Off for agents that shouldn't know personal details about you. It still keeps its own notes."
          />
          <Check
            checked={draft.memoryReadGlobal && draft.memorySaveGlobal}
            disabled={!draft.memoryReadGlobal}
            onChange={(memorySaveGlobal) => update({ memorySaveGlobal })}
            label="Save new memories for all agents"
            detail="Off keeps what it learns to itself. You can move any memory between agents in Settings › Memory."
          />
        </div>
      </Card>

      <Card>
        <Eyebrow>WHAT THE AGENT IS TOLD</Eyebrow>
        <pre className="mt-3 whitespace-pre-wrap font-mono text-[12.5px] leading-relaxed text-fg">{promptSummary(draft, teamLine)}</pre>
        <p className="mt-2.5 text-[12px] text-fg-dim">A summary of what every tab adds around your instructions. It updates as you edit.</p>
      </Card>

      <Card tone="danger">
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="min-w-[240px] flex-1">
            <h2 className="text-[15px] font-semibold">Copy or remove</h2>
            <p className="mt-0.5 text-[13px] text-fg-dim">Deleting removes the agent from its teams. Past chats are kept.</p>
          </div>
          <button type="button" onClick={() => void duplicate()} disabled={busy} className={buttonClass}>Duplicate</button>
          <button type="button" onClick={exportFile} className={buttonClass}>Export as file</button>
          {(agent.my_role || 'owner') === 'owner' && (
            <button type="button" onClick={remove} className={cn(buttonClass, 'border-danger/60 text-danger-ink hover:bg-danger/10')}>Delete agent</button>
          )}
        </div>
        {error && <p role="alert" className="mt-2 text-[12.5px] text-danger-ink">{error}</p>}
      </Card>
    </div>
  );
}
