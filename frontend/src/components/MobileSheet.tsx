import { useEffect, useRef, type KeyboardEvent, type ReactNode, type RefObject } from 'react';

interface MobileSheetProps {
  id: string;
  label: string;
  open: boolean;
  onClose: () => void;
  triggerRef: RefObject<HTMLButtonElement>;
  children: ReactNode;
}

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export function MobileSheet({ id, label, open, onClose, triggerRef, children }: MobileSheetProps) {
  const dialogRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!open) return;

    const dialog = dialogRef.current;
    const trigger = triggerRef.current;
    const firstControl = dialog?.querySelector<HTMLElement>(FOCUSABLE);
    (firstControl ?? dialog)?.focus();

    return () => {
      const activeElement = document.activeElement;
      if (activeElement === document.body || (activeElement && dialog?.contains(activeElement))) {
        trigger?.focus();
      }
    };
  }, [open, triggerRef]);

  if (!open) return null;

  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
      return;
    }

    if (event.key !== 'Tab') return;

    const controls = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (!first || !last) {
      event.preventDefault();
      dialogRef.current?.focus();
      return;
    }

    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <>
      <div
        data-testid="mobile-sheet-backdrop"
        aria-hidden="true"
        tabIndex={-1}
        className="fixed inset-0 z-40 bg-bg/70 md:hidden"
        onClick={onClose}
      />
      <aside
        ref={dialogRef}
        id={id}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        className="fixed inset-y-0 right-0 z-50 flex w-[min(88vw,320px)] flex-col border-l border-line bg-panel shadow-xl md:hidden"
        onKeyDown={handleKeyDown}
      >
        {children}
      </aside>
    </>
  );
}
