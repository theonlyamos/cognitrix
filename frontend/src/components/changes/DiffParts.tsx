import { Check, Monitor, Package, RotateCcw, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';
import { hunkHeader, numbered, sideBySide, type ChangeSource, type ChangedFile, type Hunk } from '@/lib/fileChanges';

/** M / A / D in a small box, coloured like the change. */
export function StatusBadge({ status }: { status: string }) {
  const letter = status === 'added' ? 'A' : status === 'deleted' ? 'D' : 'M';
  const label = status === 'added' ? tr('ui.fileChanges.added') : status === 'deleted' ? tr('ui.fileChanges.deleted') : tr('ui.fileChanges.modified');
  return (
    <span
      title={label}
      aria-label={label}
      className={cn('grid h-[18px] w-[18px] flex-none place-items-center rounded border border-line font-mono text-[10.5px] font-semibold',
        letter === 'A' ? 'text-ok' : letter === 'D' ? 'text-danger-ink' : 'text-warn-ink')}
    >
      {letter}
    </span>
  );
}

export function Counts({ added, removed, className }: { added: number; removed: number; className?: string }) {
  return (
    <span className={cn('inline-flex flex-none items-center gap-1.5 font-mono text-[12px]', className)}>
      {added > 0 && <span className="text-ok">+{added}</span>}
      {removed > 0 && <span className="text-danger-ink">−{removed}</span>}
    </span>
  );
}

/** Five small squares: how much of the change is additions vs removals. */
export function Bars({ added, removed }: { added: number; removed: number }) {
  const total = added + removed;
  if (!total) return null;
  let a = Math.round((5 * added) / total);
  if (added && !a) a = 1;
  let d = Math.round((5 * removed) / total);
  if (removed && !d) d = 1;
  if (a + d > 5) d = 5 - a;
  return (
    <span aria-hidden className="inline-flex flex-none gap-0.5">
      {Array.from({ length: 5 }, (_, i) => (
        <i key={i} className={cn('h-1.5 w-1.5 rounded-[2px]', i < a ? 'bg-ok' : i < a + d ? 'bg-danger' : 'bg-panel-2')} />
      ))}
    </span>
  );
}

export function PathText({ path, gone, className }: { path: string; gone?: boolean; className?: string }) {
  const cut = path.lastIndexOf('/');
  return (
    <span className={cn('min-w-0 truncate font-mono text-[12.5px]', gone && 'line-through decoration-fg-dim', className)} title={path}>
      {cut >= 0 && <span className="text-fg-dim">{path.slice(0, cut + 1)}</span>}
      {path.slice(cut + 1)}
    </span>
  );
}

export function StateChip({ state }: { state: 'accepted' | 'pending' }) {
  return state === 'accepted'
    ? <span className="flex-none whitespace-nowrap rounded bg-ok/10 px-1.5 font-mono text-[11px] text-ok">{tr('ui.fileChanges.accepted')}</span>
    : <span className="flex-none whitespace-nowrap rounded border border-line px-1.5 font-mono text-[11px] text-fg-dim">{tr('ui.fileChanges.toReview')}</span>;
}

/** Where the changes are: the sandbox copy, a paired PC, or this computer. */
export function WherePill({ source, className }: { source: ChangeSource; className?: string }) {
  const Icon = source.kind === 'sandbox' ? Package : Monitor;
  const label = source.kind === 'sandbox' ? tr('ui.fileChanges.sandboxCopy') : source.kind === 'local' ? tr('ui.fileChanges.thisComputer') : source.label;
  return (
    <span className={cn('inline-flex min-w-0 flex-none items-center gap-1 rounded-md border border-line px-1.5 py-px font-mono text-[11px] text-fg', className)}
      title={source.kind === 'sandbox' && source.source ? tr('ui.fileChanges.copyOf', { folder: source.source }) : undefined}>
      <Icon size={12} aria-hidden className="flex-none" />
      <span className="truncate">{label}</span>
    </span>
  );
}

const hb = 'inline-flex h-8 items-center gap-1 whitespace-nowrap rounded-md border border-line bg-panel px-2 text-[12.5px] disabled:opacity-50 md:h-7';

export interface HunkActions {
  onAccept: (hunk: Hunk) => void;
  onUnaccept: (hunk: Hunk) => void;
  onReject: (hunk: Hunk) => void;
  working?: boolean;
  /** 'pc' and 'local': rejecting puts the old text back in the real file. */
  kind: ChangeSource['kind'];
}

function HunkHead({ hunk, accepted, actions }: { hunk: Hunk; accepted: boolean; actions?: HunkActions }) {
  return (
    <div className="flex min-h-9 items-center gap-2 border-y border-line bg-panel-2/60 py-1 pl-3 pr-1.5 font-mono text-[11.5px] text-fg-dim">
      <span className="truncate">{hunkHeader(hunk)}</span>
      {actions && (
        <span className="ml-auto flex flex-none items-center gap-1 font-sans">
          {accepted ? (
            <>
              <StateChip state="accepted" />
              <button type="button" className={cn(hb, 'px-1.5 text-fg-dim')} disabled={actions.working}
                onClick={() => actions.onUnaccept(hunk)} title={tr('ui.fileChanges.undoAccept')} aria-label={tr('ui.fileChanges.undoAccept')}>
                <RotateCcw size={13} aria-hidden />
              </button>
            </>
          ) : (
            <>
              <button type="button" className={cn(hb, 'text-danger-ink')} disabled={actions.working} onClick={() => actions.onReject(hunk)}
                title={actions.kind === 'sandbox' ? tr('ui.fileChanges.rejectSandboxHint') : tr('ui.fileChanges.rejectHint')}>
                <X size={13} aria-hidden /> {tr('ui.fileChanges.reject')}
              </button>
              <button type="button" className={cn(hb, 'text-ok')} disabled={actions.working} onClick={() => actions.onAccept(hunk)}>
                <Check size={13} aria-hidden /> {tr('ui.fileChanges.accept')}
              </button>
            </>
          )}
        </span>
      )}
    </div>
  );
}

const lnCell = 'w-10 select-none px-1.5 text-right align-top text-[11px] text-fg-dim/80';
const rowTone = (op: string) => (op === '+' ? 'bg-ok/10' : op === '-' ? 'bg-danger/10' : '');

function InlineHunk({ hunk }: { hunk: Hunk }) {
  return (
    <table className="w-full table-fixed border-collapse font-mono text-[12px] leading-[1.65]">
      <colgroup><col className="w-10" /><col className="w-10" /><col className="w-4" /><col /></colgroup>
      <tbody>
        {numbered(hunk).map((l, i) => (
          <tr key={i} className={rowTone(l.op)}>
            <td className={lnCell}>{l.old ?? ''}</td>
            <td className={lnCell}>{l.new ?? ''}</td>
            <td className={cn('select-none text-center align-top', l.op === '+' ? 'text-ok' : l.op === '-' ? 'text-danger-ink' : 'text-fg-dim')}>{l.op === ' ' ? '' : l.op === '-' ? '−' : '+'}</td>
            <td className="whitespace-pre-wrap break-all pr-2">{l.text || ' '}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SideHunk({ hunk }: { hunk: Hunk }) {
  return (
    <table className="w-full table-fixed border-collapse font-mono text-[12px] leading-[1.65]">
      <colgroup><col className="w-10" /><col /><col className="w-10" /><col /></colgroup>
      <tbody>
        {sideBySide(hunk).map((r, i) => (
          <tr key={i}>
            <td className={cn(lnCell, r.left ? rowTone(r.left.op) : 'bg-panel-2/60')}>{r.left?.n ?? ''}</td>
            <td className={cn('whitespace-pre-wrap break-all pr-2', r.left ? rowTone(r.left.op) : 'bg-panel-2/60')}>{r.left ? r.left.text || ' ' : ''}</td>
            <td className={cn(lnCell, 'border-l border-line', r.right ? rowTone(r.right.op) : 'bg-panel-2/60')}>{r.right?.n ?? ''}</td>
            <td className={cn('whitespace-pre-wrap break-all pr-2', r.right ? rowTone(r.right.op) : 'bg-panel-2/60')}>{r.right ? r.right.text || ' ' : ''}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** One file's diff in a box: its header with whole-file buttons, then each hunk. */
export function FileDiff({ file, mode, actions, onAcceptFile, onRejectFile }: {
  file: ChangedFile;
  mode: 'inline' | 'split';
  actions?: HunkActions;
  onAcceptFile?: () => void;
  onRejectFile?: () => void;
}) {
  const accepted = new Set(file.accepted);
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-panel">
      <div className="flex min-h-11 flex-wrap items-center gap-2 border-b border-line px-3 py-1.5">
        <StatusBadge status={file.status} />
        <PathText path={file.path} gone={file.status === 'deleted'} className="text-[13px] font-medium" />
        <Counts added={file.added} removed={file.removed} />
        {actions && (
          <span className="ml-auto flex flex-none items-center gap-1">
            {file.state === 'accepted' ? <StateChip state="accepted" /> : (
              <>
                {onRejectFile && (
                  <button type="button" className={cn(hb, 'text-danger-ink')} disabled={actions.working} onClick={onRejectFile}>
                    <X size={13} aria-hidden /> {tr('ui.fileChanges.rejectFile')}
                  </button>
                )}
                {onAcceptFile && file.hunks.length > 0 && (
                  <button type="button" className={cn(hb, 'text-ok')} disabled={actions.working} onClick={onAcceptFile}>
                    <Check size={13} aria-hidden /> {tr('ui.fileChanges.acceptFile')}
                  </button>
                )}
              </>
            )}
          </span>
        )}
      </div>
      {file.binary ? (
        <p className="px-3 py-3 text-[12.5px] text-fg-dim">{tr('ui.fileChanges.binary')}</p>
      ) : file.too_big || file.hunks.length === 0 ? (
        <p className="px-3 py-3 text-[12.5px] text-fg-dim">{tr('ui.fileChanges.tooBig')}</p>
      ) : file.status === 'deleted' ? (
        <p className="px-3 py-3 text-[12.5px] text-fg-dim">{tr('ui.fileChanges.fileDeleted', { count: file.removed })}</p>
      ) : (
        <div className="overflow-x-auto">
          {file.hunks.map((h, i) => (
            <div key={h.id} className={cn(i === 0 && '[&>div:first-child]:border-t-0')}>
              <HunkHead hunk={h} accepted={accepted.has(h.id)} actions={actions} />
              {mode === 'split' ? <SideHunk hunk={h} /> : <InlineHunk hunk={h} />}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
