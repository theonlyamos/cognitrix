import { useEffect, useRef, useState } from 'react';
import { composerBtn, composerPopover, composerWarn } from '@/components/composerStyles';
import { platformLabel, type PairedDevice } from '@/lib/devices';
import { tr } from '@/lib/i18n';
import { cn } from '@/lib/utils';

const PcIcon = ({ size = 15 }: { size?: number }) => (
  <svg aria-hidden width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" className="flex-none">
    <rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" />
  </svg>
);
const BoxIcon = ({ size = 15 }: { size?: number }) => (
  <svg aria-hidden width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" className="flex-none">
    <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" /><path d="M3.27 6.96L12 12.01l8.73-5.05M12 22.08V12" />
  </svg>
);
const Check = () => (
  <svg aria-hidden width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" className="flex-none text-accent-ink"><path d="M5 12l5 5L20 7" /></svg>
);

interface Props {
  devices: PairedDevice[];
  /** The PC this chat works on, or null for the agent's usual computer. */
  value: string | null;
  onChange: (deviceId: string | null) => void;
  /** What the usual computer is for this chat: the sandbox or this server. */
  usualIsSandbox: boolean;
  onPair: () => void;
  onOpen?: () => void;
  disabled?: boolean;
}

/**
 * The composer's computer chip: which computer this chat's commands and files
 * use. The agent's usual one (normally the sandbox), or one of the person's
 * own paired PCs.
 */
export default function ComputerChip({ devices, value, onChange, usualIsSandbox, onPair, onOpen, disabled }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const picked = value ? devices.find((d) => d.id === value) ?? null : null;

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const usualLabel = usualIsSandbox ? tr('ui.computerChip.sandbox') : tr('ui.computerChip.usualComputer');
  const offline = !!picked && (!picked.online || picked.paused);
  const label = picked ? picked.name : value ? tr('ui.computerChip.unknownPc') : usualLabel;
  const pick = (id: string | null) => {
    setOpen(false);
    if (id !== value) onChange(id);
  };
  const item = 'flex min-h-11 w-full items-center gap-2.5 rounded-lg px-2.5 text-left text-[14px] text-fg hover:bg-panel-2 disabled:opacity-50 disabled:hover:bg-transparent md:min-h-9 md:text-[13px]';
  const sub = 'block text-[12px] text-fg-dim md:text-[11.5px]';

  return (
    <div ref={rootRef} className="relative flex-none">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        title={picked
          ? (offline ? tr('ui.computerChip.nameIsOfflineAgentsCan', { name: picked.name }) : tr('ui.computerChip.commandsAndFilesInThis', { name: picked.name }))
          : tr('ui.computerChip.whichComputerThisChatWorks')}
        aria-label={tr('ui.computerChip.computerLabel', { label })}
        onClick={() => { setOpen((v) => !v); onOpen?.(); }}
        className={cn(composerBtn, picked && !offline && 'text-accent-ink', offline && composerWarn)}
      >
        {picked ? <PcIcon /> : <BoxIcon />}
        <span className="hidden max-w-[9rem] truncate sm:inline">{label}</span>
        {picked && <span aria-hidden className={cn('h-1.5 w-1.5 flex-none rounded-full', offline ? 'bg-warn-ink' : 'bg-ok')} />}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40 bg-black/30 md:hidden" aria-hidden onClick={() => setOpen(false)} />
          <div role="menu" aria-label={tr('ui.computerChip.computerForThisChat')} className={cn(composerPopover, 'md:w-[19rem]')}>
            <div className="px-2.5 pb-1 pt-1.5 font-mono text-[10.5px] uppercase tracking-[0.08em] text-fg-dim">{tr('ui.computerChip.workOn')}</div>
            <button type="button" role="menuitemradio" aria-checked={!value} className={item} onClick={() => pick(null)}>
              <BoxIcon />
              <span className="min-w-0 flex-1">{usualLabel}<span className={sub}>{usualIsSandbox ? tr('ui.computerChip.theAgentsOwnComputerSafe') : tr('ui.computerChip.whereThisAgentNormallyWorks')}</span></span>
              {!value && <Check />}
            </button>
            {devices.map((d) => {
              const kind = platformLabel(d.platform);
              const state = d.paused ? tr('ui.computerChip.paused') : d.online ? tr('ui.computerChip.online') : tr('ui.computerChip.offline');
              return (
                <button
                  key={d.id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={value === d.id}
                  className={cn(item, !d.online && 'text-fg-dim')}
                  onClick={() => pick(d.id)}
                >
                  <PcIcon />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{d.name}</span>
                    <span className={sub}>
                      <span aria-hidden className={cn('mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle', d.paused ? 'bg-warn-ink' : d.online ? 'bg-ok' : 'bg-fg-dim')} />
                      {[state, kind].filter(Boolean).join(' · ')}
                    </span>
                  </span>
                  {value === d.id && <Check />}
                </button>
              );
            })}
            <div className="mx-1 my-1 h-px bg-line" />
            <button type="button" role="menuitem" className={cn(item, 'text-fg-dim')} onClick={() => { setOpen(false); onPair(); }}>
              <span className="w-[15px] text-center">+</span>
              <span className="flex-1">{tr('ui.computerChip.pairAComputer')}</span>
            </button>
          </div>
        </>
      )}
    </div>
  );
}
