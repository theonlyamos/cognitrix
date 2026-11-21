import { useId, useState } from 'react';
import { Link } from 'react-router-dom';
import ModelPicker from '@/components/ModelPicker';
import { PROVIDER_LABELS } from '@/lib/providers';
import { handleFor, type TabProps } from '@/lib/agentDraft';
import { Card, Check, Pill, buttonClass, linkButtonClass } from '@/components/agent/ui';
import { tr } from '@/lib/i18n';

export default function TeamsTab({ agent, draft, update, overview }: TabProps) {
  const modelId = useId();
  const teams = overview?.teams || [];
  const providerLabel = PROVIDER_LABELS[draft.provider] || draft.provider;
  // "A specific model" stays picked while its name is being typed (even when empty).
  const [specificPicked, setSpecificPicked] = useState(false);
  const specific = draft.subAgentModel === '' ? specificPicked : draft.subAgentModel !== 'same';

  return (
    <div className="flex flex-col gap-[18px]">
      <Card title={tr('ui.agent.teamsTab.teams')} aside={<Link to="/agents?tab=teams" className={buttonClass}>{tr('ui.agent.teamsTab.addToATeam')}</Link>}>
        {!overview ? (
          <p className="text-[13px] text-fg-dim">{tr('ui.agent.teamsTab.loading')}</p>
        ) : teams.length === 0 ? (
          <p className="text-[13px] text-fg-dim">
            {tr('ui.agent.teamsTab.notInATeamIn')}
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
                      <Pill tone={lead ? 'accent' : 'dim'}>{lead ? tr('ui.agent.teamsTab.leader') : tr('ui.agent.teamsTab.member')}</Pill>
                    </div>
                    <div className="mt-1 truncate text-[12.5px] text-fg-dim">
                      {others.length ? tr('ui.agent.teamsTab.withMap', { map: others.map((m) => handleFor(m.name)).join(', ') }) : tr('ui.agent.teamsTab.noOtherMembers')}
                    </div>
                  </div>
                  <Link to={`/home?team=${t.id}`} className={buttonClass}>{tr('ui.agent.teamsTab.openGroupChat')}</Link>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Card title={tr('ui.agent.teamsTab.delegation')} description={tr('ui.agent.teamsTab.subAgentsAreHelpersThis')}>
        <div className="flex flex-col gap-4">
          <Check
            checked={draft.canCreateSubAgents}
            onChange={(canCreateSubAgents) => update({ canCreateSubAgents })}
            label={tr('ui.agent.teamsTab.letItCreateSubAgents')}
            detail={tr('ui.agent.teamsTab.offRemovesTheCreateAgent')}
          />
          <div className={draft.canCreateSubAgents ? '' : 'pointer-events-none opacity-50'} aria-disabled={!draft.canCreateSubAgents}>
            <div className="text-[14.5px] font-medium">{tr('ui.agent.teamsTab.subAgentModel')}</div>
            <div className="mt-2 flex flex-col gap-2" role="radiogroup" aria-label={tr('ui.agent.teamsTab.subAgentModel')}>
              <label className="flex cursor-pointer items-center gap-2.5 text-[14px]">
                <input type="radio" name="sub-model" checked={!specific && draft.subAgentModel === ''} disabled={!draft.canCreateSubAgents}
                  onChange={() => { setSpecificPicked(false); update({ subAgentModel: '' }); }} className="h-4 w-4 accent-[var(--accent)]" />
                {tr('ui.agent.teamsTab.theAgentChooses')}
              </label>
              <label className="flex cursor-pointer items-center gap-2.5 text-[14px]">
                <input type="radio" name="sub-model" checked={!specific && draft.subAgentModel === 'same'} disabled={!draft.canCreateSubAgents}
                  onChange={() => { setSpecificPicked(false); update({ subAgentModel: 'same' }); }} className="h-4 w-4 accent-[var(--accent)]" />
                {tr('ui.agent.teamsTab.sameAsThisAgentModel', { model: draft.model || '—' })}
              </label>
              <label className="flex cursor-pointer items-center gap-2.5 text-[14px]">
                <input type="radio" name="sub-model" checked={specific} disabled={!draft.canCreateSubAgents}
                  onChange={() => { setSpecificPicked(true); update({ subAgentModel: draft.model }); }} className="h-4 w-4 accent-[var(--accent)]" />
                {tr('ui.agent.teamsTab.aSpecificProviderlabelModel', { providerLabel })}
              </label>
              {specific && (
                <div className="ml-[26px] max-w-[360px]">
                  <label htmlFor={modelId} className="sr-only">{tr('ui.agent.teamsTab.subAgentModel')}</label>
                  <ModelPicker id={modelId} provider={draft.provider} value={draft.subAgentModel}
                    onChange={(subAgentModel) => update({ subAgentModel })} disabled={!draft.canCreateSubAgents} />
                  <p className="mt-1.5 text-[12px] text-fg-dim">{draft.subAgentModel === '' && tr('ui.agent.teamsTab.untilYouNameAModel')}{tr('ui.agent.teamsTab.usesThisAgentSProvider')}</p>
                </div>
              )}
            </div>
          </div>
        </div>
        <p className="mt-4 text-[12.5px] text-fg-dim">
          {tr('ui.agent.teamsTab.toHandWorkToAgents')}{' '}<Link to="/teams/new" className={linkButtonClass}>{tr('ui.agent.teamsTab.makeATeam')}</Link>
        </p>
      </Card>
    </div>
  );
}
