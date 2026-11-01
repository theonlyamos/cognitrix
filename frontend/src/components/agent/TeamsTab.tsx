import { useId, useState } from 'react';
import { Link } from 'react-router-dom';
import ModelPicker from '@/components/ModelPicker';
import { PROVIDER_LABELS } from '@/lib/providers';
import { handleFor, type TabProps } from '@/lib/agentDraft';
import { Card, Check, Pill, buttonClass, linkButtonClass } from '@/components/agent/ui';

export default function TeamsTab({ agent, draft, update, overview }: TabProps) {
  const modelId = useId();
  const teams = overview?.teams || [];
  const providerLabel = PROVIDER_LABELS[draft.provider] || draft.provider;
  // "A specific model" stays picked while its name is being typed (even when empty).
  const [specificPicked, setSpecificPicked] = useState(false);
  const specific = draft.subAgentModel === '' ? specificPicked : draft.subAgentModel !== 'same';

  return (
    <div className="flex flex-col gap-[18px]">
      <Card title="Teams" aside={<Link to="/teams" className={buttonClass}>+ Add to a team</Link>}>
        {!overview ? (
          <p className="text-[13px] text-fg-dim">Loading…</p>
        ) : teams.length === 0 ? (
          <p className="text-[13px] text-fg-dim">
            Not in a team. In a team, agents share a group chat and hand work to each other by @mention.
          </p>
        ) : (
          <ul className="-mt-1">
            {teams.map((t) => {
              const lead = t.leader_id === agent.id;
              const others = t.members.filter((m) => m.id !== agent.id);
              return (
                <li key={t.id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-line py-3 first:border-t-0">
                  <div className="min-w-[220px] flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Link to={`/teams/${t.id}`} className="text-[15px] font-semibold hover:text-accent-ink hover:underline">{t.name}</Link>
                      <Pill tone={lead ? 'accent' : 'dim'}>{lead ? 'leader' : 'member'}</Pill>
                    </div>
                    <div className="mt-1 truncate text-[12.5px] text-fg-dim">
                      {others.length ? `With ${others.map((m) => handleFor(m.name)).join(', ')}` : 'No other members'}
                    </div>
                  </div>
                  <Link to={`/teams/${t.id}/chat`} className={buttonClass}>Open group chat</Link>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Card title="Delegation" description="Sub-agents are helpers this agent makes for one job.">
        <div className="flex flex-col gap-4">
          <Check
            checked={draft.canCreateSubAgents}
            onChange={(canCreateSubAgents) => update({ canCreateSubAgents })}
            label="Let it create sub-agents"
            detail="Off removes the Create Agent tool, so the agent does the work itself or hands it to a teammate."
          />
          <div className={draft.canCreateSubAgents ? '' : 'pointer-events-none opacity-50'} aria-disabled={!draft.canCreateSubAgents}>
            <div className="text-[14.5px] font-medium">Sub-agent model</div>
            <div className="mt-2 flex flex-col gap-2" role="radiogroup" aria-label="Sub-agent model">
              <label className="flex cursor-pointer items-center gap-2.5 text-[14px]">
                <input type="radio" name="sub-model" checked={!specific && draft.subAgentModel === ''} disabled={!draft.canCreateSubAgents}
                  onChange={() => { setSpecificPicked(false); update({ subAgentModel: '' }); }} className="h-4 w-4 accent-[var(--accent)]" />
                The agent chooses
              </label>
              <label className="flex cursor-pointer items-center gap-2.5 text-[14px]">
                <input type="radio" name="sub-model" checked={!specific && draft.subAgentModel === 'same'} disabled={!draft.canCreateSubAgents}
                  onChange={() => { setSpecificPicked(false); update({ subAgentModel: 'same' }); }} className="h-4 w-4 accent-[var(--accent)]" />
                Same as this agent ({draft.model || '—'})
              </label>
              <label className="flex cursor-pointer items-center gap-2.5 text-[14px]">
                <input type="radio" name="sub-model" checked={specific} disabled={!draft.canCreateSubAgents}
                  onChange={() => { setSpecificPicked(true); update({ subAgentModel: draft.model }); }} className="h-4 w-4 accent-[var(--accent)]" />
                A specific {providerLabel} model
              </label>
              {specific && (
                <div className="ml-[26px] max-w-[360px]">
                  <label htmlFor={modelId} className="sr-only">Sub-agent model</label>
                  <ModelPicker id={modelId} provider={draft.provider} value={draft.subAgentModel}
                    onChange={(subAgentModel) => update({ subAgentModel })} disabled={!draft.canCreateSubAgents} />
                  <p className="mt-1.5 text-[12px] text-fg-dim">{draft.subAgentModel === '' && 'Until you name a model, the agent chooses. '}Uses this agent's provider and key. A cheaper model keeps helper jobs cheap.</p>
                </div>
              )}
            </div>
          </div>
        </div>
        <p className="mt-4 text-[12.5px] text-fg-dim">
          To hand work to agents you set up yourself, put them in a team. <Link to="/teams/new" className={linkButtonClass}>Make a team</Link>
        </p>
      </Card>
    </div>
  );
}
