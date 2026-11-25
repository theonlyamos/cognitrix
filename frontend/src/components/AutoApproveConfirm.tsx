import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { tr } from '@/lib/i18n';

/**
 * Asked before "Auto-approve everything" is turned on: it answers every
 * approval card Yes, which can do damage that can't be undone.
 */
export default function AutoApproveConfirm({ onConfirm, onClose }: { onConfirm: () => Promise<unknown>; onClose: () => void }) {
  const titleId = useId();
  const bodyId = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    cancelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const confirm = async () => {
    setBusy(true);
    setError('');
    try {
      await onConfirm();
      onClose();
    } catch {
      setError(tr('ui.autoApprove.couldNotSave'));
      setBusy(false);
    }
  };

  return createPortal(
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-[rgba(5,6,7,.6)] p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        className="w-[480px] max-w-full rounded-2xl border border-danger/50 bg-panel p-6 shadow-panel"
      >
        <div className="mb-3 flex items-center gap-2 font-mono text-[10.5px] font-semibold tracking-[0.14em] text-danger-ink">
          <span aria-hidden>⚠</span>{tr('ui.autoApprove.dangerous')}
        </div>
        <h2 id={titleId} className="text-[19px] font-semibold leading-tight">{tr('ui.autoApprove.confirmTitle')}</h2>
        <div id={bodyId} className="mt-3 space-y-2 text-[13.5px] leading-relaxed text-fg-dim">
          <p>{tr('ui.autoApprove.confirmWhat')}</p>
          <p className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-danger-ink">{tr('ui.autoApprove.confirmRisk')}</p>
          <p>{tr('ui.autoApprove.confirmOff')}</p>
        </div>
        {error && <p role="alert" className="mt-3 text-[13px] text-danger-ink">{error}</p>}
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button ref={cancelRef} type="button" onClick={onClose} className="rounded-lg border border-line px-3.5 py-2 text-[13.5px] hover:bg-panel-2">
            {tr('ui.autoApprove.cancel')}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => { void confirm(); }}
            className="rounded-lg bg-danger px-3.5 py-2 text-[13.5px] font-medium text-white hover:opacity-90 disabled:opacity-60"
          >
            {tr('ui.autoApprove.turnOn')}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
