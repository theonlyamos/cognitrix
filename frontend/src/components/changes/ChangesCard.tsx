import { useEffect, useState } from 'react';
import { ArrowDownToLine, Check, FileDiff as FileDiffIcon, RotateCcw } from 'lucide-react';
import { cn } from '@/lib/utils';
import { tr, trn } from '@/lib/i18n';
import type { ChangeSource } from '@/lib/fileChanges';
import { savedOn } from '@/lib/fileChanges';
import { Bars, Counts, PathText, StateChip, StatusBadge, WherePill } from '@/components/changes/DiffParts';

const KEPT_STORE = 'changes.kept';

/** A source's changes as one signature, so "Keep in the sandbox" hides the
 *  card only until something new changes. */
function signature(source: ChangeSource): string {
  return source.files.map((f) => `${f.full_path}:${f.hunks.map((h) => h.id).join('.')}`).join('|');
}

function loadKept(): Record<string, string> {
  try {
    const value = JSON.parse(localStorage.getItem(KEPT_STORE) || '{}');
    return value && typeof value === 'object' ? value : {};
  } catch {
    return {};
  }
}

function saveKept(value: Record<string, string>) {
  try {
    localStorage.setItem(KEPT_STORE, JSON.stringify(value));
  } catch {
    // Not remembered this time.
  }
}

const btn = 'inline-flex h-10 items-center gap-1.5 whitespace-nowrap rounded-lg border border-line bg-panel px-3 text-[13px] disabled:opacity-50 md:h-8';
const primary = 'border-transparent bg-accent font-semibold text-accent-foreground';
const quiet = 'border-transparent bg-transparent text-fg-dim hover:text-fg';

/** Under the last reply: what the agent changed, one card per place
 *  (the sandbox copy, a paired PC, this computer). */
export function ChangesCards({ sessionId, sources, working, onOpen, onCopyBack, onUndoAll }: {
  sessionId: string;
  sources: ChangeSource[];
  working?: boolean;
  onOpen: (source: string, path?: string) => void;
  onCopyBack: (source: ChangeSource) => void;
  onUndoAll: (source: ChangeSource) => void;
}) {
  const [kept, setKept] = useState(loadKept);
  const [confirming, setConfirming] = useState<string | null>(null);

  useEffect(() => {
    if (!confirming) return;
    const t = window.setTimeout(() => setConfirming(null), 6000);
    return () => window.clearTimeout(t);
  }, [confirming]);

  const shown = sources.filter((s) => s.pending > 0 && kept[`${sessionId}|${s.key}`] !== signature(s));
  if (shown.length === 0) return null;

  const keep = (s: ChangeSource) => {
    const next = { ...loadKept(), [`${sessionId}|${s.key}`]: signature(s) };
    saveKept(next);
    setKept(next);
  };

  return (
    <>
      {shown.map((s) => {
        const sandbox = s.kind === 'sandbox';
        return (
          <section key={s.key} aria-label={tr('ui.fileChanges.changes')} className="mx-auto my-3 w-[calc(100%-1.5rem)] max-w-[720px] overflow-hidden rounded-xl border border-line bg-panel sm:w-[calc(100%-3rem)]">
            <header className="flex min-h-12 flex-wrap items-center gap-2 border-b border-line px-3.5 py-2">
              <FileDiffIcon size={16} aria-hidden className="flex-none text-fg-dim" />
              <h2 className="text-[14px] font-semibold">{trn(s.files.length, 'ui.fileChanges.filesChanged_one', 'ui.fileChanges.filesChanged_other')}</h2>
              <Counts added={s.added} removed={s.removed} />
              <WherePill source={s} className="ml-auto" />
            </header>
            <ul>
              {s.files.slice(0, 8).map((f) => (
                <li key={f.full_path} className="border-b border-line last:border-b-0">
                  <button type="button" onClick={() => onOpen(s.key, f.full_path)}
                    className="flex min-h-11 w-full items-center gap-2.5 px-3.5 text-left hover:bg-panel-2 md:min-h-9">
                    <StatusBadge status={f.status} />
                    <PathText path={f.path} gone={f.status === 'deleted'} />
                    <span className="ml-auto flex flex-none items-center gap-2">
                      {f.state === 'accepted' && <StateChip state="accepted" />}
                      <Counts added={f.added} removed={f.removed} />
                      <span className="hidden sm:inline-flex"><Bars added={f.added} removed={f.removed} /></span>
                    </span>
                  </button>
                </li>
              ))}
              {s.files.length > 8 && (
                <li>
                  <button type="button" onClick={() => onOpen(s.key)} className="min-h-10 w-full px-3.5 text-left text-[12.5px] text-fg-dim hover:bg-panel-2 hover:text-fg">
                    {tr('ui.fileChanges.andMore', { count: s.files.length - 8 })}
                  </button>
                </li>
              )}
            </ul>
            <footer className="flex flex-wrap items-center gap-2 border-t border-line bg-bg px-3.5 py-2.5">
              <span className="text-[12.5px] text-fg-dim">
                {sandbox ? tr('ui.fileChanges.notOnYourComputerYet') : (
                  <span className="inline-flex items-center gap-1"><Check size={13} aria-hidden />{savedOn(s)}</span>
                )}
              </span>
              <span className="ml-auto flex flex-wrap items-center gap-1.5">
                {sandbox ? (
                  <>
                    <button type="button" className={cn(btn, quiet)} onClick={() => keep(s)}>{tr('ui.fileChanges.keepInSandbox')}</button>
                    <button type="button" className={btn} onClick={() => onOpen(s.key)}>{tr('ui.fileChanges.review')}</button>
                    {s.source && (
                      <button type="button" className={cn(btn, primary)} disabled={working} onClick={() => onCopyBack(s)}>
                        <ArrowDownToLine size={15} aria-hidden /> {tr('ui.fileChanges.copyBack')}
                      </button>
                    )}
                  </>
                ) : (
                  <>
                    <button type="button" className={cn(btn, confirming === s.key ? 'border-danger/50 text-danger-ink' : quiet)} disabled={working}
                      onClick={() => { if (confirming === s.key) { setConfirming(null); onUndoAll(s); } else setConfirming(s.key); }}>
                      <RotateCcw size={14} aria-hidden /> {confirming === s.key ? tr('ui.fileChanges.undoAllConfirm') : tr('ui.fileChanges.undoAll')}
                    </button>
                    <button type="button" className={cn(btn, primary)} onClick={() => onOpen(s.key)}>{tr('ui.fileChanges.review')}</button>
                  </>
                )}
              </span>
            </footer>
          </section>
        );
      })}
    </>
  );
}
