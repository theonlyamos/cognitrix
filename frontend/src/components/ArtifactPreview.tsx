import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import type { ToolArtifact } from '@/context/SessionContext';
import { api } from '@/lib/api';

const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function ArtifactPreview({ artifact }: { artifact: ToolArtifact }) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [url, setUrl] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const expandTriggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const isImage = artifact.mime_type.startsWith('image/');

  useEffect(() => {
    if (!isImage) return;
    const controller = new AbortController();
    let active = true;
    let objectUrl: string | null = null;
    setState('loading');
    setUrl(null);
    api.get(`/artifacts/${artifact.id}`, { responseType: 'blob', signal: controller.signal })
      .then(({ data }) => {
        objectUrl = URL.createObjectURL(data as Blob);
        if (!active) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        setUrl(objectUrl);
        setState('ready');
      })
      .catch((error) => {
        if (active && error?.name !== 'CanceledError' && error?.name !== 'AbortError') setState('error');
      });
    return () => {
      active = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [artifact.id, attempt, isImage]);

  useEffect(() => {
    if (!expanded) return;

    const dialog = dialogRef.current;
    const trigger = expandTriggerRef.current;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    dialog?.querySelector<HTMLElement>(FOCUSABLE)?.focus();

    return () => {
      document.body.style.overflow = previousOverflow;
      const activeElement = document.activeElement;
      if (activeElement === document.body || (activeElement && dialog?.contains(activeElement))) {
        trigger?.focus();
      }
    };
  }, [expanded]);

  const handleDialogKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      setExpanded(false);
      return;
    }

    if (event.key !== 'Tab') return;

    const controls = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (!first || !last) {
      event.preventDefault();
      dialogRef.current?.focus();
    } else if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  if (!isImage) return null;
  if (state === 'error') {
    return (
      <div className="flex items-center gap-2 text-sm text-danger-ink">
        <span>Generated image unavailable.</span>
        <button type="button" className="underline" onClick={() => setAttempt((n) => n + 1)}>Retry image</button>
      </div>
    );
  }
  if (state !== 'ready' || !url) return <div className="text-sm text-fg-dim">Loading generated image...</div>;
  const filename = artifact.filename || 'generated-image.png';
  return (
    <>
      <div className="relative w-fit max-w-full">
        <img src={url} alt="Generated image" className="max-h-80 max-w-full rounded border border-line bg-panel object-contain" />
        <div className="absolute bottom-2 right-2 flex gap-1 rounded border border-line bg-bg/80 p-1 shadow-lg backdrop-blur-sm">
          <button
            ref={expandTriggerRef}
            type="button"
            aria-label="Expand generated image"
            title="Expand image"
            className="flex h-11 w-11 items-center justify-center rounded border border-line bg-panel-2/95 text-fg transition-colors hover:border-fg-dim hover:text-accent-ink sm:h-9 sm:w-9"
            onClick={() => setExpanded(true)}
          >
            <svg aria-hidden="true" viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5" />
            </svg>
          </button>
          <a
            href={url}
            download={filename}
            aria-label={`Download ${filename}`}
            title={`Download ${filename}`}
            className="flex h-11 w-11 items-center justify-center rounded border border-line bg-panel-2/95 text-fg transition-colors hover:border-fg-dim hover:text-accent-ink sm:h-9 sm:w-9"
          >
            <svg aria-hidden="true" viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 3v12M7 10l5 5 5-5M5 21h14" />
            </svg>
          </a>
        </div>
      </div>

      {expanded && createPortal(
        <>
          <div
            aria-hidden="true"
            className="fixed inset-0 z-[70] bg-bg/90 backdrop-blur-sm"
            onClick={() => setExpanded(false)}
          />
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-label="Generated image preview"
            tabIndex={-1}
            className="fixed inset-0 z-[71] flex items-center justify-center p-4 sm:p-8"
            onKeyDown={handleDialogKeyDown}
            onClick={(event) => {
              if (event.target === event.currentTarget) setExpanded(false);
            }}
          >
            <img src={url} alt="Generated image, full size" className="max-h-full max-w-full object-contain" />
            <button
              type="button"
              aria-label="Close image preview"
              title="Close image preview"
              className="absolute right-4 top-4 flex h-11 w-11 items-center justify-center rounded border border-line bg-panel-2/95 text-fg shadow-lg transition-colors hover:border-fg-dim hover:text-accent-ink sm:right-6 sm:top-6 sm:h-9 sm:w-9"
              onClick={() => setExpanded(false)}
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </div>
        </>,
        document.body,
      )}
    </>
  );
}
