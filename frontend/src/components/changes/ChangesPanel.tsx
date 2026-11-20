import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDownToLine, Maximize2, Minimize2, RotateCcw, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';
import type { ChangeSource, ChatChangesState, Hunk } from '@/lib/fileChanges';
import { savedOn } from '@/lib/fileChanges';
import { Counts, FileDiff, PathText, StateChip, StatusBadge, WherePill } from '@/components/changes/DiffParts';

const VIEW_STORE = 'changes.view';
const iconButton = 'grid h-9 w-9 flex-none place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg md:h-8 md:w-8';
const btn = 'inline-flex h-10 items-center gap-1.5 whitespace-nowrap rounded-lg border border-line bg-panel px-3 text-[13px] disabled:opacity-50 md:h-8';

function loadView(): 'inline' | 'split' {
  try {
    return localStorage.getItem(VIEW_STORE) === 'split' ? 'split' : 'inline';
  } catch {
    return 'inline';
  }
}

/** The chat's changes in the right panel: pick a file, read its diff, accept
 *  or put back each part. Widens to side by side. */
export function ChangesPanel({ state, initialSource, initialPath, onBack, onClose, expanded, onToggleExpand, onCopyBack }: {
  state: ChatChangesState;
  initialSource?: string;
  initialPath?: string;
  onBack?: () => void;
  onClose: () => void;
  expanded?: boolean;
  onToggleExpand?: () => void;
  onCopyBack: (source: ChangeSource) => void;
}) {
  const sources = useMemo(() => state.changes?.sources ?? [], [state.changes]);
  const [sourceKey, setSourceKey] = useState(initialSource || '');
  const [path, setPath] = useState(initialPath || '');
  const [view, setView] = useState(loadView);
  const [confirmUndo, setConfirmUndo] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => { if (initialSource) setSourceKey(initialSource); }, [initialSource]);
  useEffect(() => { if (initialPath) setPath(initialPath); }, [initialPath]);

  const source = sources.find((s) => s.key === sourceKey) ?? sources[0];
  const files = useMemo(() => source?.files ?? [], [source]);
  const index = Math.max(0, files.findIndex((f) => f.full_path === path));
  const file = files[index];

  useEffect(() => { bodyRef.current?.scrollTo({ top: 0 }); }, [file?.full_path]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))) return;
      if (e.ctrlKey || e.metaKey || e.altKey || !files.length) return;
      if (e.key === 'j') setPath(files[Math.min(files.length - 1, index + 1)].full_path);
      else if (e.key === 'k') setPath(files[Math.max(0, index - 1)].full_path);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [files, index]);

  const pickView = (v: 'inline' | 'split') => {
    setView(v);
    try { localStorage.setItem(VIEW_STORE, v); } catch { /* not remembered */ }
  };

  const split = view === 'split' && expanded;
  const actions = source && {
    kind: source.kind,
    working: state.working,
    onAccept: (h: Hunk) => void state.act({ action: 'accept', source: source.key, path: file!.full_path, hunk: h.id }),
    onUnaccept: (h: Hunk) => void state.act({ action: 'unaccept', source: source.key, path: file!.full_path, hunk: h.id }),
    onReject: (h: Hunk) => void state.act({ action: 'reject', source: source.key, path: file!.full_path, hunk: h.id }),
  };
  const accepted = files.filter((f) => f.state === 'accepted').length;

  const header = (
    <header className="flex min-h-12 flex-none items-center gap-1.5 border-b border-line py-1.5 pl-2 pr-2 md:px-3">
      {onBack && !expanded && (
        <button type="button" onClick={onBack} className="inline-flex h-9 flex-none items-center gap-1 rounded-lg px-2 font-mono text-[11.5px] text-fg-dim hover:bg-panel-2 hover:text-fg md:h-8">
          <span aria-hidden>‹</span>{' '}{tr('ui.fileChanges.thisChat')}
        </button>
      )}
      <h2 className="ml-1 text-[14px] font-semibold">{tr('ui.fileChanges.changes')}</h2>
      {source && <WherePill source={source} className="ml-1 max-w-[40%]" />}
      <span className="ml-auto" />
      {onToggleExpand && (
        <button type="button" onClick={onToggleExpand} className={cn(iconButton, 'hidden md:grid')}
          aria-label={expanded ? tr('ui.fileChanges.shrink') : tr('ui.fileChanges.widen')} title={expanded ? tr('ui.fileChanges.shrink') : tr('ui.fileChanges.widen')}>
          {expanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
        </button>
      )}
      <button type="button" onClick={onClose} aria-label={tr('ui.fileChanges.close')} className={iconButton}><X size={16} /></button>
    </header>
  );

  if (!source) {
    return (
      <section aria-label={tr('ui.fileChanges.changes')} className="flex h-full min-h-0 flex-col bg-panel">
        {header}
        <p className="p-4 text-[13px] text-fg-dim">{state.changes ? tr('ui.fileChanges.noChanges') : tr('ui.fileChanges.loading')}</p>
      </section>
    );
  }

  const fileList = (
    <ul className={cn('overflow-y-auto', expanded ? 'max-h-48 border-b border-line md:max-h-none md:border-b-0' : 'max-h-48 border-b border-line')}>
      {files.map((f) => (
        <li key={f.full_path}>
          <button type="button" onClick={() => setPath(f.full_path)} aria-current={f === file ? 'true' : undefined}
            className={cn('flex min-h-11 w-full items-center gap-2 px-3 text-left hover:bg-panel-2 md:min-h-8', f === file && 'bg-panel-2')}>
            <StatusBadge status={f.status} />
            <PathText path={f.path} gone={f.status === 'deleted'} />
            <span className="ml-auto flex flex-none items-center gap-2">
              <StateChip state={f.state} />
              <Counts added={f.added} removed={f.removed} />
            </span>
          </button>
        </li>
      ))}
    </ul>
  );

  return (
    <section aria-label={tr('ui.fileChanges.changes')} className="flex h-full min-h-0 flex-col bg-panel">
      {header}
      <div className="flex min-h-10 flex-none flex-wrap items-center gap-2 border-b border-line px-3 py-1.5">
        {expanded && (
          <span role="group" aria-label={tr('ui.fileChanges.view')} className="hidden rounded-lg bg-panel-2 p-0.5 md:inline-flex">
            {(['inline', 'split'] as const).map((v) => (
              <button key={v} type="button" aria-pressed={view === v} onClick={() => pickView(v)}
                className={cn('rounded-md px-2.5 py-1 text-[12.5px]', view === v ? 'bg-panel font-medium text-fg shadow-sm' : 'text-fg-dim')}>
                {v === 'inline' ? tr('ui.fileChanges.inline') : tr('ui.fileChanges.sideBySide')}
              </button>
            ))}
          </span>
        )}
        {sources.length > 1 && (
          <select value={source.key} onChange={(e) => { setSourceKey(e.target.value); setPath(''); }}
            aria-label={tr('ui.fileChanges.where')} className="h-9 rounded-lg border border-line bg-panel px-2 text-[12.5px] md:h-7">
            {sources.map((s) => (
              <option key={s.key} value={s.key}>{s.kind === 'sandbox' ? `${tr('ui.fileChanges.sandboxCopy')} · ${s.project}` : s.label}</option>
            ))}
          </select>
        )}
        <Counts added={source.added} removed={source.removed} />
        <span className="ml-auto font-mono text-[11.5px] text-fg-dim">
          {files.length ? `${index + 1} / ${files.length}` : ''}<span className="hidden md:inline"> · {tr('ui.fileChanges.jkToMove')}</span>
        </span>
      </div>
      <div className={cn('flex min-h-0 flex-1 flex-col', expanded && 'md:grid md:grid-cols-[minmax(220px,300px)_1fr]')}>
        <div className={cn('flex-none', expanded && 'md:min-h-0 md:overflow-y-auto md:border-r md:border-line')}>{fileList}</div>
        <div ref={bodyRef} className="min-h-0 flex-1 overflow-y-auto bg-bg p-3">
          {state.error && (
            <p role="alert" className="mb-3 flex items-start gap-2 rounded-lg border border-danger/40 bg-panel px-3 py-2 text-[12.5px] text-danger-ink">
              <span className="flex-1">{state.error}</span>
              <button type="button" onClick={state.clearError} aria-label={tr('ui.fileChanges.close')} className="text-fg-dim hover:text-fg"><X size={14} /></button>
            </p>
          )}
          {file && actions && (
            <FileDiff
              file={file}
              mode={split ? 'split' : 'inline'}
              actions={actions}
              onAcceptFile={() => void state.act({ action: 'accept', source: source.key, path: file.full_path })}
              onRejectFile={() => void state.act({ action: 'reject', source: source.key, path: file.full_path })}
            />
          )}
        </div>
      </div>
      <footer className="flex flex-none flex-wrap items-center gap-2 border-t border-line bg-panel px-3 py-2.5 pb-[max(0.625rem,env(safe-area-inset-bottom))]">
        <span className="w-full text-[12.5px] text-fg-dim sm:w-auto">
          {tr('ui.fileChanges.acceptedOf', { accepted, total: files.length })}
          {source.kind === 'sandbox' ? ` · ${tr('ui.fileChanges.rejectedAreGone')}` : ` · ${savedOn(source)}`}
        </span>
        <span className="ml-auto flex flex-wrap items-center gap-1.5">
          <button type="button" disabled={state.working}
            className={cn(btn, confirmUndo ? 'border-danger/50 text-danger-ink' : 'border-transparent bg-transparent text-fg-dim hover:text-fg')}
            onClick={() => {
              if (!confirmUndo) { setConfirmUndo(true); window.setTimeout(() => setConfirmUndo(false), 6000); return; }
              setConfirmUndo(false);
              void state.act({ action: 'reject', source: source.key });
            }}>
            <RotateCcw size={14} aria-hidden /> {confirmUndo ? tr('ui.fileChanges.undoAllConfirm') : source.kind === 'sandbox' ? tr('ui.fileChanges.rejectAll') : tr('ui.fileChanges.undoAll')}
          </button>
          {source.kind === 'sandbox' && source.source ? (
            <button type="button" disabled={state.working} className={cn(btn, 'border-transparent bg-accent font-semibold text-accent-foreground')} onClick={() => onCopyBack(source)}>
              <ArrowDownToLine size={15} aria-hidden /> {tr('ui.fileChanges.copyBack')}
            </button>
          ) : (
            files.some((f) => f.state !== 'accepted') && (
              <button type="button" disabled={state.working} className={cn(btn, 'border-transparent bg-accent font-semibold text-accent-foreground')}
                onClick={() => void state.act({ action: 'accept', source: source.key })}>
                {tr('ui.fileChanges.acceptTheRest')}
              </button>
            )
          )}
        </span>
      </footer>
    </section>
  );
}
