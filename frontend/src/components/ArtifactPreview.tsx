import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import type { ToolArtifact } from '@/context/SessionContext';
import { api } from '@/lib/api';

const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

const isCanonicalTaskArtifactPath = (value: string | undefined): value is string => {
  if (!value || !/^\/tasks\/[^/?#]+\/runs\/[^/?#]+\/artifacts\/[^/?#]+$/.test(value)) {
    return false;
  }
  try {
    return [2, 4, 6].every((index) => {
      const segment = decodeURIComponent(value.split('/')[index]);
      return segment !== '.' && segment !== '..' && !segment.includes('/') && !segment.includes('\\');
    });
  } catch {
    return false;
  }
};

interface ArtifactPreviewProps {
  artifact: ToolArtifact;
  sourcePath?: string;
  onEditSource?: (artifact: ToolArtifact) => void;
  selected?: boolean;
}

export function ArtifactPreview({
  artifact,
  sourcePath,
  onEditSource,
  selected = false,
}: ArtifactPreviewProps) {
  const isImage = artifact.mime_type.startsWith('image/');
  const taskArtifactPath = isCanonicalTaskArtifactPath(sourcePath) ? sourcePath : null;
  const endpoint = taskArtifactPath ?? `/artifacts/${artifact.id}`;
  const previewEndpoint = taskArtifactPath ? endpoint : `${endpoint}?variant=thumbnail`;
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<'idle' | 'loading' | 'ready' | 'error'>(isImage ? 'loading' : 'idle');
  const [url, setUrl] = useState<string | null>(null);
  const [originalUrl, setOriginalUrl] = useState<string | null>(null);
  const [originalLoading, setOriginalLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const expandTriggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const imageLabel = artifact.origin === 'uploaded' ? 'Attached image' : 'Generated image';

  useEffect(() => {
    if (!isImage) return;
    const controller = new AbortController();
    let active = true;
    let objectUrl: string | null = null;
    setState('loading');
    setUrl(null);
    api.get(previewEndpoint, { responseType: 'blob', signal: controller.signal })
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
  }, [artifact.id, attempt, isImage, previewEndpoint]);

  const downloadFile = async () => {
    setState('loading');
    try {
      const { data } = await api.get(endpoint, { responseType: 'blob' });
      const objectUrl = URL.createObjectURL(data as Blob);
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = artifact.filename || 'artifact';
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
      setState('idle');
    } catch {
      setState('error');
    }
  };

  useEffect(() => () => {
    if (originalUrl) URL.revokeObjectURL(originalUrl);
  }, [originalUrl]);

  const loadOriginal = async () => {
    if (originalUrl) return originalUrl;
    if (taskArtifactPath && url) return url;
    setOriginalLoading(true);
    try {
      const { data } = await api.get(endpoint, { responseType: 'blob' });
      const nextUrl = URL.createObjectURL(data as Blob);
      setOriginalUrl(nextUrl);
      return nextUrl;
    } finally {
      setOriginalLoading(false);
    }
  };

  const expandOriginal = async () => {
    try {
      await loadOriginal();
      setExpanded(true);
    } catch {
      setState('error');
    }
  };

  const downloadOriginal = async (filename: string) => {
    try {
      const downloadUrl = await loadOriginal();
      const link = document.createElement('a');
      link.href = downloadUrl;
      link.download = filename;
      link.click();
    } catch {
      setState('error');
    }
  };

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

  if (!isImage) {
    const filename = artifact.filename || 'artifact';
    return (
      <button
        type="button"
        className="min-h-11 text-accent-ink underline underline-offset-2 sm:min-h-0"
        disabled={state === 'loading'}
        onClick={() => void downloadFile()}
      >
        {state === 'loading'
          ? `Downloading ${filename}...`
          : state === 'error'
            ? `Retry download ${filename}`
            : `Download ${filename}`}
      </button>
    );
  }
  if (state === 'error') {
    return (
      <div className="flex items-center gap-2 text-sm text-danger-ink">
        <span>{imageLabel} unavailable.</span>
        <button type="button" className="underline" onClick={() => setAttempt((n) => n + 1)}>Retry image</button>
      </div>
    );
  }
  if (state !== 'ready' || !url) return <div className="text-sm text-fg-dim">Loading {imageLabel.toLowerCase()}...</div>;
  const filename = artifact.filename || (artifact.origin === 'uploaded' ? 'attached-image.png' : 'generated-image.png');
  return (
    <>
      <div className="relative w-fit max-w-full">
        <img src={url} alt={imageLabel} className="max-h-80 max-w-full rounded border border-line bg-panel object-contain" />
        <div className="absolute bottom-2 right-2 flex gap-1 rounded border border-line bg-bg/80 p-1 shadow-lg backdrop-blur-sm">
          {onEditSource && (
            <button
              type="button"
              aria-label={`Use ${filename} as edit source`}
              aria-pressed={selected}
              title={selected ? 'Selected as edit source' : 'Use as edit source'}
              className={`flex h-11 w-11 items-center justify-center rounded border bg-panel-2/95 transition-colors sm:h-9 sm:w-9 ${selected ? 'border-accent text-accent-ink' : 'border-line text-fg hover:border-fg-dim hover:text-accent-ink'}`}
              onClick={() => onEditSource(artifact)}
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 21h6l11-11-6-6L3 15v6z" /><path d="M12 6l6 6" /></svg>
            </button>
          )}
          <button
            ref={expandTriggerRef}
            type="button"
            aria-label="Expand generated image"
            title="Expand image"
            className="flex h-11 w-11 items-center justify-center rounded border border-line bg-panel-2/95 text-fg transition-colors hover:border-fg-dim hover:text-accent-ink sm:h-9 sm:w-9"
            disabled={originalLoading}
            onClick={() => { void expandOriginal(); }}
          >
            <svg aria-hidden="true" viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5" />
            </svg>
          </button>
          {taskArtifactPath ? (
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
          ) : (
            <button
              type="button"
              aria-label={`Download ${filename}`}
              title={`Download ${filename}`}
              disabled={originalLoading}
              className="flex h-11 w-11 items-center justify-center rounded border border-line bg-panel-2/95 text-fg transition-colors hover:border-fg-dim hover:text-accent-ink sm:h-9 sm:w-9"
              onClick={() => { void downloadOriginal(filename); }}
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 3v12M7 10l5 5 5-5M5 21h14" />
              </svg>
            </button>
          )}
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
            aria-label={`${imageLabel} preview`}
            tabIndex={-1}
            className="fixed inset-0 z-[71] flex items-center justify-center p-4 sm:p-8"
            onKeyDown={handleDialogKeyDown}
            onClick={(event) => {
              if (event.target === event.currentTarget) setExpanded(false);
            }}
          >
            <img src={originalUrl || url} alt={`${imageLabel}, full size`} className="max-h-full max-w-full object-contain" />
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
