import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { errorMessage } from '@/lib/api';
import { cn } from '@/lib/utils';
import { buttonClass, primaryButtonClass, quietButtonClass } from '@/components/settings/ui';
import {
  desktopName, handBack, openViewer, resetSandbox, sandboxStatus, takeOver, updateSandbox,
  type SandboxDesktop, type SandboxStatus,
} from '@/lib/sandbox';

interface PanelAgent {
  id: string;
  name: string;
}

/** The agents' sandboxed computer: one desktop per agent, watched live, with
 *  Take over / Hand back. While a person has control the agent waits and
 *  sees nothing of what they do. */
export function ComputerPanel({ agents, focusAgentId, onClose, onBack }: {
  agents: PanelAgent[];
  focusAgentId?: string | null;
  onClose: () => void;
  /** Beside a chat with its side panel open: go back to that panel. */
  onBack?: () => void;
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

  // Expand fills the app window; Full screen then takes the whole monitor.
  // On a phone the panel already fills the window, so Expand goes straight to
  // full screen and asks to turn sideways.
  const rootRef = useRef<HTMLElement>(null);
  const [expanded, setExpanded] = useState(false);
  const fullscreen = useFullscreen(rootRef);

  const enterFullscreen = useCallback(async () => {
    const el = rootRef.current;
    if (!el?.requestFullscreen) return;
    try {
      await el.requestFullscreen();
      if (isPhone()) await (screen.orientation as ScreenOrientation & { lock?: (o: string) => Promise<void> }).lock?.('landscape');
    } catch {
      // Full screen or turning sideways can be refused; the expanded view still works.
    }
  }, []);

  const expand = useCallback(() => {
    setExpanded(true);
    if (isPhone()) void enterFullscreen();
  }, [enterFullscreen]);

  const collapse = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
    setExpanded(false);
  }, []);

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
    else void enterFullscreen();
  }, [enterFullscreen]);

  // Esc collapses, except while you're in control: then keys go to the desktop.
  const inControl = useRef(false);
  const onControl = useCallback((held: boolean) => { inControl.current = held; }, []);
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || document.fullscreenElement) return;
      if (inControl.current) return;
      collapse();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [expanded, collapse]);

  useEffect(() => () => {
    if (document.fullscreenElement && document.fullscreenElement === rootRef.current) void document.exitFullscreen().catch(() => {});
  }, []);

  const closeButton = (
    <button type="button" className={iconButtonClass} onClick={onClose} aria-label="Close the agents' computer" title="Close">
      <CloseIcon />
    </button>
  );
  const tabs = desktops.length > 1 && (
    <DesktopTabs desktops={desktops} selected={selected} label={label} onSelect={setSelected} />
  );
  const oneBar = expanded && !!current;

  return (
    <section
      ref={rootRef}
      aria-label="Agents' computer"
      data-view={fullscreen ? 'fullscreen' : expanded ? 'expanded' : 'docked'}
      className={cn(
        'flex min-h-0 flex-col bg-panel',
        expanded ? 'fixed inset-0 z-50' : 'h-full',
      )}
    >
      {!oneBar && (
        <header className="flex flex-none items-center gap-1 border-b border-line px-3 py-2">
          {onBack && !expanded && (
            <button type="button" onClick={onBack} className="-ml-1 mr-1 inline-flex h-9 flex-none items-center gap-1 rounded-lg px-2 font-mono text-[11.5px] text-fg-dim hover:bg-panel-2 hover:text-fg md:h-8">
              <span aria-hidden>‹</span> This chat
            </button>
          )}
          <h2 className="mr-auto text-[13px] font-semibold">Agents' computer</h2>
          {status?.running && status.update_available && <UpdateButton onDone={load} />}
          {status?.running && <ResetButton onDone={load} />}
          {expanded ? (
            <button type="button" className={buttonClass} onClick={collapse} title="Collapse (Esc)">
              <CollapseIcon /><span className="ml-1.5">Collapse</span>
            </button>
          ) : (
            <button type="button" className={buttonClass} onClick={expand} title="Expand">
              <ExpandIcon /><span className="ml-1.5">Expand</span>
            </button>
          )}
          {closeButton}
        </header>
      )}

      {!oneBar && tabs && (
        <div className="flex-none border-b border-line px-2 py-1.5">{tabs}</div>
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
        {current && (
          <DesktopScreen
            key={current.name}
            desktop={current}
            agentName={label(current)}
            onChange={load}
            onControl={onControl}
            view={fullscreen && !isPhone() ? 'fullscreen' : expanded ? 'expanded' : 'docked'}
            leading={oneBar && (
              <>
                <button type="button" className={buttonClass} onClick={collapse} title="Collapse (Esc)" aria-label="Collapse">
                  <CollapseIcon /><span className="ml-1.5 max-md:hidden side:hidden">Collapse</span>
                </button>
                <h2 className="text-[13px] font-semibold max-md:hidden side:hidden">Agents' computer</h2>
                {desktops.length > 1 && (
                  <>
                    <div className="min-w-0 max-md:hidden side:hidden">{tabs}</div>
                    <DesktopSelect desktops={desktops} selected={selected} label={label} onSelect={setSelected} className="md:hidden side:block side:max-w-[84px] side:px-1 side:text-[11.5px]" />
                  </>
                )}
              </>
            )}
            trailing={oneBar && (
              <>
                <button
                  type="button"
                  className={cn(iconButtonClass, 'border border-line max-md:hidden side:hidden')}
                  onClick={toggleFullscreen}
                  aria-label={fullscreen ? 'Exit full screen' : 'Full screen'}
                  title={fullscreen ? 'Exit full screen (Esc)' : 'Full screen'}
                >
                  {fullscreen ? <ExitFullscreenIcon /> : <FullscreenIcon />}
                </button>
                {!fullscreen && closeButton}
              </>
            )}
          />
        )}
      </div>
    </section>
  );
}

const iconButtonClass = 'inline-flex h-9 w-9 flex-none items-center justify-center rounded-lg text-fg-dim hover:bg-panel-2 hover:text-fg md:h-8 md:w-8';

function isPhone() {
  return typeof window !== 'undefined'
    && !!window.matchMedia?.('(max-width: 767px), (orientation: landscape) and (max-height: 500px)').matches;
}

/** Whether `ref` is the element shown full screen right now. */
function useFullscreen(ref: React.RefObject<HTMLElement>) {
  const [on, setOn] = useState(false);
  useEffect(() => {
    const sync = () => setOn(!!ref.current && document.fullscreenElement === ref.current);
    document.addEventListener('fullscreenchange', sync);
    return () => document.removeEventListener('fullscreenchange', sync);
  }, [ref]);
  return on;
}

function DesktopTabs({ desktops, selected, label, onSelect }: {
  desktops: SandboxDesktop[];
  selected: string | null;
  label: (d: SandboxDesktop) => string;
  onSelect: (name: string) => void;
}) {
  return (
    <div role="tablist" aria-label="Agent desktops" className="flex gap-1 overflow-x-auto">
      {desktops.map((d) => (
        <button
          key={d.name}
          role="tab"
          type="button"
          aria-selected={d.name === selected}
          onClick={() => onSelect(d.name)}
          className={cn('flex-none rounded-md px-2.5 py-1 text-[12px]', d.name === selected ? 'bg-panel-2 font-medium text-fg' : 'text-fg-dim hover:bg-panel-2')}
        >
          {label(d)}
          {d.held_by && <span className="ml-1.5 text-[10.5px] text-accent-ink">you</span>}
        </button>
      ))}
    </div>
  );
}

/** The desktop picker where tabs don't fit (an expanded phone screen). */
function DesktopSelect({ desktops, selected, label, onSelect, className }: {
  desktops: SandboxDesktop[];
  selected: string | null;
  label: (d: SandboxDesktop) => string;
  onSelect: (name: string) => void;
  className?: string;
}) {
  return (
    <select
      aria-label="Agent desktop"
      value={selected ?? ''}
      onChange={(e) => onSelect(e.target.value)}
      className={cn('h-9 max-w-[9rem] rounded-lg border border-line bg-panel px-2 text-[12.5px] text-fg', className)}
    >
      {desktops.map((d) => <option key={d.name} value={d.name}>{label(d)}</option>)}
    </select>
  );
}

type View = 'docked' | 'expanded' | 'fullscreen';

function DesktopScreen({ desktop, agentName, onChange, onControl, view, leading, trailing }: {
  desktop: SandboxDesktop;
  agentName: string;
  onChange: () => void;
  /** Told whether you have control, so the panel leaves Esc to the desktop. */
  onControl?: (held: boolean) => void;
  view: View;
  /** Expanded: the panel's own controls, put in this desktop's one bar. */
  leading?: ReactNode;
  trailing?: ReactNode;
}) {
  const screenRef = useRef<HTMLDivElement>(null);
  const [control, setControl] = useState<string | null>(null); // full password while you have control
  useEffect(() => {
    onControl?.(control !== null);
    return () => onControl?.(false);
  }, [control, onControl]);
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

  const barVisible = useAutoHide(view === 'fullscreen');
  const statusText = heldByYou
    ? `You're in control. ${agentName} is paused and sees nothing until you hand back.`
    : heldElsewhere
      ? `${desktop.held_by} has control; ${agentName} is paused.`
      : `${agentName}'s desktop${state === 'live' ? '' : state === 'connecting' ? ' (connecting…)' : ' (disconnected)'}`;
  const actions = (
    <>
      {state === 'closed' && (
        <button type="button" className={quietButtonClass} onClick={() => setAttempt((n) => n + 1)}>Reconnect</button>
      )}
      {heldByYou || heldElsewhere ? (
        <button type="button" className={primaryButtonClass} disabled={busy} onClick={() => void giveBack()}>Hand back</button>
      ) : (
        <button type="button" className={view === 'docked' ? buttonClass : primaryButtonClass} disabled={busy} onClick={() => void take()}>Take over</button>
      )}
    </>
  );
  const docked = view === 'docked';
  const full = view === 'fullscreen';

  return (
    <div className={cn('relative flex h-full min-h-0 flex-col', !docked && 'side:flex-row')}>
      <div
        className={cn(
          'flex flex-none flex-wrap items-center gap-2 text-[12.5px]',
          docked && 'px-3 py-2',
          !docked && 'border-b border-line bg-panel px-3 py-2 side:order-last side:w-[100px] side:flex-col side:flex-nowrap side:justify-center side:border-b-0 side:border-l side:px-2',
          full && 'absolute left-1/2 top-3 z-10 max-w-[calc(100%-24px)] -translate-x-1/2 rounded-xl border border-line bg-panel/95 shadow-lg backdrop-blur transition-opacity duration-300',
          full && !barVisible && 'pointer-events-none opacity-0',
          heldByYou && docked && 'bg-accent/10',
        )}
      >
        {leading}
        <span
          className={cn('min-w-0 truncate', docked ? 'flex-1' : 'ml-auto max-w-[32rem] text-fg-dim side:hidden', heldByYou && !docked && 'text-accent-ink')}
          aria-live="polite"
        >
          {statusText}
        </span>
        {actions}
        {trailing}
      </div>
      {error && <p role="alert" className="px-3 pb-2 text-[12px] text-danger-ink">{error}</p>}
      <div className={cn('flex min-h-0 min-w-0 flex-1', docked ? 'bg-panel-2 p-3' : full ? 'bg-black' : 'bg-bg p-4 md:p-6', 'max-md:p-0 side:p-0')}>
        <div
          ref={screenRef}
          data-computer-screen
          aria-label={`${agentName}'s screen`}
          className={cn('h-full w-full', heldByYou ? 'cursor-default ring-2 ring-inset ring-accent' : 'cursor-not-allowed')}
        />
      </div>
      {full && (
        <p className={cn('pointer-events-none absolute bottom-4 left-1/2 -translate-x-1/2 rounded-lg bg-black/70 px-3 py-1.5 text-[12px] text-white transition-opacity duration-300', !barVisible && 'opacity-0')}>
          Controls hide after a few seconds. Move the pointer to show them; Esc leaves full screen.
        </p>
      )}
    </div>
  );
}

/** In full screen the controls fade out after a few idle seconds and come
 *  back when the pointer moves. */
function useAutoHide(active: boolean) {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    if (!active) {
      setVisible(true);
      return;
    }
    let timer = 0;
    const show = () => {
      setVisible(true);
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setVisible(false), 3000);
    };
    show();
    document.addEventListener('pointermove', show);
    document.addEventListener('pointerdown', show);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener('pointermove', show);
      document.removeEventListener('pointerdown', show);
    };
  }, [active]);
  return visible;
}

function UpdateButton({ onDone }: { onDone: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  if (!confirming) {
    return (
      <button
        type="button"
        className={primaryButtonClass}
        onClick={() => setConfirming(true)}
        title="A newer version of the agents' computer is ready"
      >
        Update
      </button>
    );
  }
  return (
    <span className="flex items-center gap-1 text-[12px]">
      <span className="text-fg-dim">{error || 'Restart it? Open windows close; files and logins stay.'}</span>
      <button
        type="button"
        className={buttonClass}
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError('');
          try {
            await updateSandbox();
            setConfirming(false);
          } catch (err) {
            setError(errorMessage(err, "Couldn't restart it."));
          } finally {
            setBusy(false);
            onDone();
          }
        }}
      >
        {busy ? 'Restarting…' : 'Restart'}
      </button>
      <button type="button" className={quietButtonClass} disabled={busy} onClick={() => setConfirming(false)}>Later</button>
    </span>
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

function Icon({ d }: { d: string }) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}
const ExpandIcon = () => <Icon d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />;
const CollapseIcon = () => <Icon d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" />;
const FullscreenIcon = () => <Icon d="M3 8V3h5M21 8V3h-5M3 16v5h5M21 16v5h-5" />;
const ExitFullscreenIcon = () => <Icon d="M8 3v5H3M16 3v5h5M8 21v-5H3M16 21v-5h5" />;
const CloseIcon = () => <Icon d="M18 6 6 18M6 6l12 12" />;
