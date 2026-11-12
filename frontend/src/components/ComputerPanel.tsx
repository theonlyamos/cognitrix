import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { cn } from '@/lib/utils';
import { buttonClass, primaryButtonClass, quietButtonClass } from '@/components/settings/ui';
import {
  desktopName, handBack, openViewer, resetSandbox, sandboxStatus, takeOver,
  type SandboxDesktop, type SandboxStatus,
} from '@/lib/sandbox';

interface PanelAgent {
  id: string;
  name: string;
}

/** The agents' sandboxed computer: one desktop per agent, watched live, with
 *  Take over / Hand back. While a person has control the agent waits and
 *  sees nothing of what they do. */
export function ComputerPanel({ agents, focusAgentId, onClose }: {
  agents: PanelAgent[];
  focusAgentId?: string | null;
  onClose: () => void;
}) {
  const [status, setStatus] = useState<SandboxStatus | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      setStatus(await sandboxStatus());
    } catch (err) {
      setError(errorMessage(err, "Couldn't reach the agents' computer."));
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(timer);
  }, [load]);

  const desktops = useMemo(() => status?.desktops ?? [], [status]);
  const focusName = focusAgentId ? desktopName(focusAgentId) : null;
  useEffect(() => {
    if (selected && desktops.some((d) => d.name === selected)) return;
    const pick = desktops.find((d) => d.name === focusName) ?? desktops[0];
    if (pick) setSelected(pick.name);
  }, [desktops, focusName, selected]);

  const label = (d: SandboxDesktop) => agents.find((a) => desktopName(a.id) === d.name)?.name ?? (d.name === 'main' ? 'Shared' : d.name);
  const current = desktops.find((d) => d.name === selected) ?? null;

  return (
    <section aria-label="Agents' computer" className="flex h-full min-h-0 flex-col bg-panel">
      <header className="flex flex-none items-center gap-2 border-b border-line px-3 py-2">
        <h2 className="text-[13px] font-semibold">Agents' computer</h2>
        <div className="ml-auto flex items-center gap-1">
          {status?.running && <ResetButton onDone={load} />}
          <button type="button" className={quietButtonClass} onClick={onClose} aria-label="Close the agents' computer">
            Close
          </button>
        </div>
      </header>

      {desktops.length > 1 && (
        <div role="tablist" aria-label="Agent desktops" className="flex flex-none gap-1 overflow-x-auto border-b border-line px-2 py-1.5">
          {desktops.map((d) => (
            <button
              key={d.name}
              role="tab"
              type="button"
              aria-selected={d.name === selected}
              onClick={() => setSelected(d.name)}
              className={cn('flex-none rounded-md px-2.5 py-1 text-[12px]', d.name === selected ? 'bg-panel-2 font-medium text-fg' : 'text-fg-dim hover:bg-panel-2')}
            >
              {label(d)}
              {d.held_by && <span className="ml-1.5 text-[10.5px] text-accent-ink">you</span>}
            </button>
          ))}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-hidden">
        {error && !status && <p role="alert" className="p-4 text-[12.5px] text-fg-dim">{error}</p>}
        {status && !status.enabled && (
          <p className="p-4 text-[12.5px] leading-relaxed text-fg-dim">
            The sandboxed computer is off. It gives each agent its own desktop in a container, which you can watch and take
            over. Turn it on with <code>COGNITRIX_COMPUTER_SANDBOX=docker</code> (needs Docker).
          </p>
        )}
        {status?.enabled && !current && (
          <p className="p-4 text-[12.5px] leading-relaxed text-fg-dim">
            {status.error || 'Nothing to show yet. The computer starts when an agent first uses it, and each agent gets its own desktop.'}
          </p>
        )}
        {current && <DesktopScreen key={current.name} desktop={current} agentName={label(current)} onChange={load} />}
      </div>
    </section>
  );
}

function DesktopScreen({ desktop, agentName, onChange }: { desktop: SandboxDesktop; agentName: string; onChange: () => void }) {
  const screenRef = useRef<HTMLDivElement>(null);
  const [control, setControl] = useState<string | null>(null); // full password while you have control
  const [state, setState] = useState<'connecting' | 'live' | 'closed'>('connecting');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let rfb: import('@novnc/novnc/lib/rfb').default | null = null;
    let cancelled = false;
    setState('connecting');
    (async () => {
      try {
        const [{ default: RFB }, viewer] = await Promise.all([import('@novnc/novnc/lib/rfb'), openViewer(desktop.name)]);
        if (cancelled || !screenRef.current) return;
        rfb = new RFB(screenRef.current, viewer.url, { credentials: { password: control ?? viewer.password }, wsProtocols: ['binary'] });
        rfb.viewOnly = !control;
        rfb.scaleViewport = true;
        rfb.background = 'transparent';
        rfb.addEventListener('connect', () => setState('live'));
        rfb.addEventListener('disconnect', () => setState('closed'));
      } catch (err) {
        if (!cancelled) {
          setState('closed');
          setError(errorMessage(err, "Couldn't open the screen."));
        }
      }
    })();
    return () => {
      cancelled = true;
      rfb?.disconnect();
    };
  }, [desktop.name, control, attempt]);

  // Closing the panel while in control hands the desktop back, so the agent
  // isn't left waiting.
  const controlRef = useRef<string | null>(null);
  controlRef.current = control;
  useEffect(() => () => {
    if (controlRef.current) void handBack(desktop.name).catch(() => {});
  }, [desktop.name]);

  const heldByYou = control !== null;
  const heldElsewhere = !heldByYou && !!desktop.held_by;

  async function take() {
    setBusy(true);
    setError('');
    try {
      setControl(await takeOver(desktop.name));
      onChange();
    } catch (err) {
      setError(errorMessage(err, "Couldn't take over."));
    } finally {
      setBusy(false);
    }
  }

  async function giveBack() {
    setBusy(true);
    setError('');
    try {
      await handBack(desktop.name);
      setControl(null);
      onChange();
    } catch (err) {
      setError(errorMessage(err, "Couldn't hand back."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className={cn('flex flex-none flex-wrap items-center gap-2 px-3 py-2 text-[12.5px]', heldByYou && 'bg-accent/10')}>
        <span className="min-w-0 flex-1 truncate" aria-live="polite">
          {heldByYou
            ? `You're in control. ${agentName} is paused and sees nothing until you hand back.`
            : heldElsewhere
              ? `${desktop.held_by} has control; ${agentName} is paused.`
              : `${agentName}'s desktop${state === 'live' ? '' : state === 'connecting' ? ' (connecting…)' : ' (disconnected)'}`}
        </span>
        {state === 'closed' && (
          <button type="button" className={quietButtonClass} onClick={() => setAttempt((n) => n + 1)}>Reconnect</button>
        )}
        {heldByYou || heldElsewhere ? (
          <button type="button" className={primaryButtonClass} disabled={busy} onClick={() => void giveBack()}>Hand back</button>
        ) : (
          <button type="button" className={buttonClass} disabled={busy} onClick={() => void take()}>Take over</button>
        )}
      </div>
      {error && <p role="alert" className="px-3 pb-2 text-[12px] text-[#b42318] dark:text-[#ff8a80]">{error}</p>}
      <div
        ref={screenRef}
        aria-label={`${agentName}'s screen`}
        className={cn('min-h-0 flex-1 bg-panel-2', heldByYou ? 'cursor-default ring-2 ring-inset ring-accent' : 'cursor-not-allowed')}
      />
    </div>
  );
}

function ResetButton({ onDone }: { onDone: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  if (!confirming) {
    return (
      <button type="button" className={quietButtonClass} onClick={() => setConfirming(true)}>Reset</button>
    );
  }
  return (
    <span className="flex items-center gap-1 text-[12px]">
      <span className="text-fg-dim">Erase its files and logins?</span>
      <button
        type="button"
        className={buttonClass}
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            await resetSandbox();
          } finally {
            setBusy(false);
            setConfirming(false);
            onDone();
          }
        }}
      >
        Erase
      </button>
      <button type="button" className={quietButtonClass} onClick={() => setConfirming(false)}>Keep</button>
    </span>
  );
}
