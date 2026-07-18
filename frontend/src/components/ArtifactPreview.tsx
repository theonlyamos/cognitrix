import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import type { ToolArtifact } from '@/context/SessionContext';
import { useArtifactBlob } from '@/hooks/useArtifactBlob';
import { api } from '@/lib/api';

const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

const isCanonicalTaskArtifactPath = (value: string | undefined): value is string => {
  if (!value || !/^\/tasks\/[^/?#]+\/runs\/[^/?#]+\/artifacts\/[^/?#]+$/.test(value)) return false;
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

const fileNameFor = (artifact: ToolArtifact) => Array.from((
  artifact.filename || (artifact.origin === 'uploaded' ? 'attached-image.png' : 'generated-image.png')
).replace(/[\\/:*?"<>|]/g, '_'), (character) => (
  character.charCodeAt(0) < 32 ? '_' : character
)).join('');

export function ArtifactPreview({ artifact, sourcePath, onEditSource, selected = false }: ArtifactPreviewProps) {
  const isImage = artifact.mime_type.startsWith('image/');
  const taskArtifactPath = isCanonicalTaskArtifactPath(sourcePath) ? sourcePath : undefined;
  const [inViewport, setInViewport] = useState(false);
  const placeholderRef = useRef<HTMLDivElement>(null);
  const { url: thumbnailUrl, state: thumbnailState, retry } = useArtifactBlob(artifact.id, 'thumbnail', isImage && inViewport, taskArtifactPath);
  const [originalUrl, setOriginalUrl] = useState<string | null>(null);
  const originalUrlRef = useRef<string | null>(null);
  const originalRequestRef = useRef<Promise<string> | null>(null);
  const originalControllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const [dialogError, setDialogError] = useState(false);
  const [downloadError, setDownloadError] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const expandTriggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const imageLabel = artifact.origin === 'uploaded' ? 'Attached image' : 'Generated image';
  const filename = fileNameFor(artifact);

  useEffect(() => {
    if (!isImage) return;
    const target = placeholderRef.current;
    if (!target) return;
    if (typeof IntersectionObserver === 'undefined') {
      setInViewport(true);
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setInViewport(true);
        observer.disconnect();
      }
    }, { rootMargin: '400px 0px' });
    observer.observe(target);
    return () => observer.disconnect();
  }, [isImage]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      originalControllerRef.current?.abort();
      if (originalUrlRef.current) URL.revokeObjectURL(originalUrlRef.current);
    };
  }, []);

  const loadOriginal = () => {
    if (originalUrlRef.current) return Promise.resolve(originalUrlRef.current);
    // Durable task-run artifacts use a distinct ACL-protected route without
    // variants; their viewport URL is therefore the retained original.
    if (taskArtifactPath && thumbnailUrl) return Promise.resolve(thumbnailUrl);
    if (originalRequestRef.current) return originalRequestRef.current;
    const controller = new AbortController();
    originalControllerRef.current = controller;
    const request = api.get(taskArtifactPath ?? `/artifacts/${artifact.id}?variant=original`, {
      responseType: 'blob',
      signal: controller.signal,
    }).then(({ data }) => {
      const nextUrl = URL.createObjectURL(data as Blob);
      if (!mountedRef.current) {
        URL.revokeObjectURL(nextUrl);
        throw new DOMException('Unmounted', 'AbortError');
      }
      originalUrlRef.current = nextUrl;
      setOriginalUrl(nextUrl);
      return nextUrl;
    }).finally(() => {
      originalRequestRef.current = null;
      originalControllerRef.current = null;
    });
    originalRequestRef.current = request;
    return request;
  };

  const requestOriginal = (surface: 'dialog' | 'download') => {
    if (surface === 'dialog') setDialogError(false);
    else setDownloadError(false);
    return loadOriginal().catch((error: unknown) => {
      if ((error as { name?: string })?.name !== 'AbortError') {
        if (surface === 'dialog') setDialogError(true);
        else setDownloadError(true);
      }
      throw error;
    });
  };

  const downloadOriginal = async () => {
    try {
      const downloadUrl = await requestOriginal('download');
      const link = document.createElement('a');
      link.href = downloadUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch {
      // Preserve the thumbnail and let the user try the explicit action again.
    }
  };

  const openExpanded = () => {
    setExpanded(true);
    void requestOriginal('dialog').catch(() => undefined);
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
      if (activeElement === document.body || (activeElement && dialog?.contains(activeElement))) trigger?.focus();
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
    return <button type="button" className="min-h-11 text-accent-ink underline underline-offset-2 sm:min-h-0" onClick={() => { void downloadOriginal(); }}>Download {filename}</button>;
  }

  const hasDimensions = (
    Number.isFinite(artifact.width) && Number.isFinite(artifact.height)
    && Number(artifact.width) > 0 && Number(artifact.height) > 0
  );
  const reservedHeight = hasDimensions ? Math.min(Number(artifact.height), 320) : 0;
  const reservedWidth = hasDimensions
    ? Number(artifact.width) * reservedHeight / Number(artifact.height)
    : 0;
  const reserveStyle = hasDimensions ? {
    aspectRatio: `${artifact.width} / ${artifact.height}`,
    width: `min(100%, ${reservedWidth}px)`,
  } : undefined;
  return (
    <>
      <div ref={placeholderRef} data-testid="artifact-placeholder" className="relative w-fit max-w-full" style={reserveStyle}>
        {thumbnailState === 'error' ? (
          <div className="flex items-center gap-2 text-sm text-danger-ink"><span>{imageLabel} unavailable.</span><button type="button" className="underline" onClick={retry}>Retry image</button></div>
        ) : thumbnailState !== 'ready' || !thumbnailUrl ? (
          <div className="text-sm text-fg-dim">Loading {imageLabel.toLowerCase()}...</div>
        ) : (
          <>
            <img src={thumbnailUrl} alt={imageLabel} className="max-h-80 max-w-full rounded border border-line bg-panel object-contain" />
            <div className="absolute bottom-2 right-2 flex gap-1 rounded border border-line bg-bg/80 p-1 shadow-lg backdrop-blur-sm">
              {onEditSource && <button type="button" aria-label={`Use ${filename} as edit source`} aria-pressed={selected} title={selected ? 'Selected as edit source' : 'Use as edit source'} className={`flex h-11 w-11 items-center justify-center rounded border bg-panel-2/95 transition-colors sm:h-9 sm:w-9 ${selected ? 'border-accent text-accent-ink' : 'border-line text-fg hover:border-fg-dim hover:text-accent-ink'}`} onClick={() => onEditSource(artifact)}>Edit</button>}
              <button ref={expandTriggerRef} type="button" aria-label={`Expand ${imageLabel.toLowerCase()}`} title="Expand image" className="flex h-11 w-11 items-center justify-center rounded border border-line bg-panel-2/95 text-fg transition-colors hover:border-fg-dim hover:text-accent-ink sm:h-9 sm:w-9" onClick={openExpanded}>Expand</button>
              {taskArtifactPath ? (
                <a href={thumbnailUrl} download={filename} aria-label={`Download ${filename}`} title={`Download ${filename}`} className="flex h-11 w-11 items-center justify-center rounded border border-line bg-panel-2/95 text-fg transition-colors hover:border-fg-dim hover:text-accent-ink sm:h-9 sm:w-9">Download</a>
              ) : (
                <button type="button" aria-label={`Download ${filename}`} title={`Download ${filename}`} className="flex h-11 w-11 items-center justify-center rounded border border-line bg-panel-2/95 text-fg transition-colors hover:border-fg-dim hover:text-accent-ink sm:h-9 sm:w-9" onClick={() => { void downloadOriginal(); }}>Download</button>
              )}
            </div>
          </>
        )}
      </div>
      {downloadError && (
        <div role="alert" className="mt-2 flex items-center gap-2 text-sm text-danger-ink">
          <span>Download {filename} failed.</span>
          <button type="button" className="underline" onClick={() => { void downloadOriginal(); }}>Retry download {filename}</button>
        </div>
      )}
      {expanded && createPortal(
        <>
          <div aria-hidden="true" className="fixed inset-0 z-[70] bg-bg/90 backdrop-blur-sm" onClick={() => setExpanded(false)} />
          <div ref={dialogRef} role="dialog" aria-modal="true" aria-label={`${imageLabel} preview`} tabIndex={-1} className="fixed inset-0 z-[71] flex items-center justify-center p-4 sm:p-8" onKeyDown={handleDialogKeyDown} onClick={(event) => { if (event.target === event.currentTarget) setExpanded(false); }}>
            {originalUrl ? <img src={originalUrl} alt={`${imageLabel}, full size`} className="max-h-full max-w-full object-contain" /> : dialogError ? (
              <div role="alert" className="text-danger-ink">Full image unavailable. <button type="button" className="underline" onClick={() => { void requestOriginal('dialog').catch(() => undefined); }}>Retry full image</button></div>
            ) : <span className="text-fg-dim">Loading full image...</span>}
            <button type="button" aria-label="Close image preview" title="Close image preview" className="absolute right-4 top-4 flex h-11 w-11 items-center justify-center rounded border border-line bg-panel-2/95 text-fg shadow-lg transition-colors hover:border-fg-dim hover:text-accent-ink sm:right-6 sm:top-6 sm:h-9 sm:w-9" onClick={() => setExpanded(false)}>Close</button>
          </div>
        </>, document.body,
      )}
    </>
  );
}
