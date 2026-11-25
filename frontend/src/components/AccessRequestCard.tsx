import { useState } from 'react';
import { PanelButton, PanelCard } from '@/components/cards/PanelCard';
import { errorMessage } from '@/lib/api';
import { signInWithBrowser, startAppSignIn } from '@/lib/apps';
import { tr } from '@/lib/i18n';

/** What an agent asks for with Request Access (the approval event's `access`). */
export interface AccessRequest {
  kind: 'tool' | 'app';
  name: string;
  label: string;
  reason?: string;
  description?: string;
  /** "Add to agent" is offered: the person owns the agent. */
  can_add?: boolean;
  app?: { id: string | null; connected: boolean; can_connect: boolean; setup?: string };
}

export type AccessGrant = 'chat' | 'agent';

/** The card for an agent asking for a tool or connected app it doesn't have.
 *  Same panel as the other approval cards: allow in this chat, add to the
 *  agent, connect the app first, or deny. */
export function AccessRequestCard({
  access, agentName, onAnswer,
}: {
  access: AccessRequest;
  agentName?: string;
  /** A grant to allow it, null to deny. */
  onAnswer: (grant: AccessGrant | null) => void;
}) {
  const [connected, setConnected] = useState(access.kind !== 'app' || !!access.app?.connected);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isApp = access.kind === 'app';
  const who = agentName || tr('ui.accessRequestCard.theAgent');

  const connect = async () => {
    const appId = access.app?.id;
    if (!appId) return;
    setConnecting(true);
    setError(null);
    try {
      const result = await signInWithBrowser(() => startAppSignIn(appId));
      if (result.status === 'error') setError(result.error || tr('ui.accessRequestCard.signInFailed'));
      else if (result.status === 'done') setConnected(true);
    } catch (e) {
      setError(errorMessage(e, tr('ui.accessRequestCard.couldNotStartTheSignIn')));
    } finally {
      setConnecting(false);
    }
  };

  const canConnect = isApp && !connected && !!access.app?.can_connect;
  return (
    <PanelCard
      tag={<>{isApp ? tr('ui.accessRequestCard.appRequest') : tr('ui.accessRequestCard.toolRequest')}</>}
      title={(
        <>
          {tr('ui.accessRequestCard.whoWantsToUse', { who })}{' '}
          <span className="text-accent-ink">{access.label}</span>
        </>
      )}
      subtitle={access.reason || undefined}
      fields={[
        { label: isApp ? tr('ui.accessRequestCard.app') : tr('ui.accessRequestCard.tool'), value: access.label },
        ...(isApp ? [{
          label: tr('ui.accessRequestCard.status'),
          value: connected
            ? tr('ui.accessRequestCard.connected')
            : <span className="text-danger-ink">{tr('ui.accessRequestCard.notConnectedYet')}</span>,
        }] : []),
      ]}
      footer={(
        <>
          {canConnect ? (
            <PanelButton kind="primary" disabled={connecting} onClick={() => void connect()}>
              {connecting ? tr('ui.accessRequestCard.waitingForSignIn') : tr('ui.accessRequestCard.connectLabel', { label: access.label })}
            </PanelButton>
          ) : (
            <PanelButton kind="primary" disabled={!connected} onClick={() => onAnswer('chat')}
              title={tr('ui.accessRequestCard.onlyInThisChat')}>
              {tr('ui.accessRequestCard.allowInThisChat')}
            </PanelButton>
          )}
          {access.can_add && (
            <PanelButton disabled={!connected} onClick={() => onAnswer('agent')}
              title={tr('ui.accessRequestCard.savedOnTheAgentFor')}>
              {agentName ? tr('ui.accessRequestCard.addToWho', { who: agentName }) : tr('ui.accessRequestCard.addToAgent')}
            </PanelButton>
          )}
          <span className="grow" />
          <PanelButton kind="danger" onClick={() => onAnswer(null)}>{tr('ui.accessRequestCard.deny')}</PanelButton>
        </>
      )}
    >
      {access.description && <p className="text-[12.5px] leading-relaxed text-fg-dim">{access.description}</p>}
      {isApp && !connected && (
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-fg-dim">
          {canConnect
            ? tr('ui.accessRequestCard.connectFirstHint', { label: access.label })
            : access.app?.setup || tr('ui.accessRequestCard.connectItInSettings', { label: access.label })}
        </p>
      )}
      {error && <p role="alert" className="mt-1.5 text-[12.5px] text-danger-ink">{error}</p>}
    </PanelCard>
  );
}
