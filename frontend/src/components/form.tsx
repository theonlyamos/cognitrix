import { useId, isValidElement, cloneElement, Fragment, type ReactNode, type ReactElement } from 'react';
import { Link } from 'react-router-dom';
import { MobileHeaderActions, type MobileHeaderAction } from '@/components/mobile-header-actions';
import { Button } from '@/lib/components/ui/button';

export function Field({
  label,
  hint,
  required,
  composite,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  composite?: boolean;
  children: ReactNode;
}) {
  // Associate the label with a single-element control so clicking the label
  // focuses it and screen readers announce them together. Fragments (e.g. an
  // input + its <datalist>) are left unassociated.
  const autoId = useId();
  const isControl = isValidElement(children) && children.type !== Fragment;
  const el = children as ReactElement<{ id?: string; 'aria-labelledby'?: string }>;
  const controlId = isControl && !composite ? el.props.id ?? autoId : undefined;
  const labelId = composite ? autoId : undefined;
  const control = isControl
    ? cloneElement(el, composite ? { 'aria-labelledby': labelId } : { id: controlId })
    : children;

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <label id={labelId} htmlFor={controlId} className="font-mono text-[11px] tracking-[0.12em] text-fg-dim">
          {label}
          {required && <span className="text-accent-ink"> *</span>}
        </label>
        {hint && <span className="font-mono text-[10.5px] text-fg-dim/70">{hint}</span>}
      </div>
      {control}
    </div>
  );
}

export function CheckList({
  options,
  selected,
  onToggle,
  empty,
  disabled = false,
  id,
  'aria-labelledby': ariaLabelledBy,
}: {
  options: { value: string; label: string; sub?: string }[];
  selected: Set<string>;
  onToggle: (v: string) => void;
  empty?: string;
  disabled?: boolean;
  id?: string;
  'aria-labelledby'?: string;
}) {
  return (
    <div id={id} role="group" aria-labelledby={ariaLabelledBy} className="max-h-64 overflow-y-auto rounded border border-line">
      {options.length === 0 ? (
        <div className="px-3 py-4 font-mono text-[11px] text-fg-dim">{empty || 'nothing available'}</div>
      ) : (
        options.map((o) => (
          <label key={o.value} className={`flex min-h-11 items-center gap-2.5 border-b border-line px-3 py-2 last:border-b-0 md:min-h-0 ${disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:bg-panel-2'}`}>
            <input type="checkbox" checked={selected.has(o.value)} onChange={() => onToggle(o.value)} disabled={disabled} className="accent-[var(--accent)]" />
            <div className="min-w-0">
              <div className="truncate text-[13px]">{o.label}</div>
              {o.sub && <div className="truncate font-mono text-[10.5px] text-fg-dim">{o.sub}</div>}
            </div>
          </label>
        ))
      )}
    </div>
  );
}

export function PageForm({
  eyebrow,
  title,
  backTo,
  error,
  onSave,
  saving,
  onDelete,
  extraActions,
  children,
}: {
  eyebrow: string;
  title: string;
  backTo: string;
  error?: string;
  onSave: () => void;
  saving?: boolean;
  onDelete?: () => void;
  extraActions?: ReactNode;
  children: ReactNode;
}) {
  const entityLabel = eyebrow.toLowerCase().replace(/^(edit|new)\s+/, '');
  const mobileActions: MobileHeaderAction[] = [
    ...(onDelete ? [{ key: 'delete', label: `Delete ${entityLabel}`, destructive: true, onSelect: onDelete }] : []),
  ];

  return (
    <div className="flex-1 flex flex-col h-screen min-w-0 overflow-hidden bg-bg text-fg">
      <header className="app-page-header flex min-h-14 flex-none flex-row flex-nowrap items-center gap-2 border-b border-line py-2 pl-16 pr-4 md:px-6">
        <div className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
          <Link
            to={backTo}
            aria-label={`Back to ${entityLabel}s`}
            className="grid h-11 w-11 flex-none place-items-center rounded border border-line text-fg-dim transition-colors hover:border-fg-dim hover:text-fg md:h-8 md:w-8"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 6l-6 6 6 6" /></svg>
          </Link>
          <div className="min-w-0 overflow-hidden">
            <p className="sr-only font-mono text-[10px] tracking-[0.18em] text-accent-ink md:not-sr-only">{eyebrow}</p>
            <h1 className="truncate text-lg font-semibold tracking-tight">{title}</h1>
          </div>
        </div>
        <div className="flex flex-none items-center gap-1 md:ml-auto md:gap-2">
          <div className="hidden items-center gap-2 md:flex">
            {extraActions}
            {onDelete && (
              <Button variant="ghost" size="sm" onClick={onDelete} className="hover:text-danger-ink">
                Delete
              </Button>
            )}
            <Button asChild variant="outline" size="sm">
              <Link to={backTo}>Cancel</Link>
            </Button>
          </div>
          {mobileActions.length > 0 && <MobileHeaderActions id="page-form-actions" actions={mobileActions} />}
          <Button
            size="sm"
            className="w-11 px-0 md:w-auto md:px-3"
            aria-label={saving ? 'Saving…' : 'Save changes'}
            onClick={onSave}
            disabled={saving}
          >
            <svg aria-hidden="true" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12l4 4L19 6" /></svg>
            <span className="sr-only md:not-sr-only">{saving ? 'Saving…' : 'Save'}</span>
          </Button>
          {saving && <span role="status" className="sr-only">Saving…</span>}
        </div>
      </header>

      <div className="flex-1 overflow-y-auto">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onSave();
          }}
          className="mx-auto max-w-2xl space-y-6 px-6 py-8"
        >
          {error && <p role="alert" className="border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[12px] text-danger-ink">{error}</p>}
          {children}
        </form>
      </div>
    </div>
  );
}
