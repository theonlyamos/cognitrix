import { useId, isValidElement, cloneElement, Fragment, type ReactNode, type ReactElement } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/lib/components/ui/button';

export function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: ReactNode;
}) {
  // Associate the label with a single-element control so clicking the label
  // focuses it and screen readers announce them together. Fragments (e.g. an
  // input + its <datalist>) are left unassociated.
  const autoId = useId();
  const isControl = isValidElement(children) && children.type !== Fragment;
  const el = children as ReactElement<{ id?: string }>;
  const controlId = isControl ? el.props.id ?? autoId : undefined;
  const control = isControl ? cloneElement(el, { id: controlId }) : children;

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={controlId} className="font-mono text-[11px] tracking-[0.12em] text-fg-dim">
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
  id,
}: {
  options: { value: string; label: string; sub?: string }[];
  selected: Set<string>;
  onToggle: (v: string) => void;
  empty?: string;
  id?: string;
}) {
  return (
    <div id={id} role="group" className="max-h-64 overflow-y-auto rounded border border-line">
      {options.length === 0 ? (
        <div className="px-3 py-4 font-mono text-[11px] text-fg-dim">{empty || 'nothing available'}</div>
      ) : (
        options.map((o) => (
          <label key={o.value} className="flex cursor-pointer items-center gap-2.5 border-b border-line px-3 py-2 last:border-b-0 hover:bg-panel-2">
            <input type="checkbox" checked={selected.has(o.value)} onChange={() => onToggle(o.value)} className="accent-[var(--accent)]" />
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
  return (
    <div className="flex-1 flex flex-col h-screen min-w-0 overflow-hidden bg-bg text-fg">
      <header className="flex flex-none items-center gap-4 border-b border-line px-6 py-4">
        <Link
          to={backTo}
          className="grid h-8 w-8 place-items-center rounded border border-line text-fg-dim transition-colors hover:border-fg-dim hover:text-fg"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 6l-6 6 6 6" /></svg>
        </Link>
        <div className="min-w-0">
          <p className="font-mono text-[10px] tracking-[0.18em] text-accent-ink">{eyebrow}</p>
          <h1 className="truncate text-lg font-semibold tracking-tight">{title}</h1>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {extraActions}
          {onDelete && (
            <Button variant="ghost" size="sm" onClick={onDelete} className="hover:text-danger-ink">
              Delete
            </Button>
          )}
          <Button asChild variant="outline" size="sm">
            <Link to={backTo}>Cancel</Link>
          </Button>
          <Button size="sm" onClick={onSave} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
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
          {error && <p className="border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[12px] text-danger-ink">{error}</p>}
          {children}
        </form>
      </div>
    </div>
  );
}
