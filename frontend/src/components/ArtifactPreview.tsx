import { useEffect, useState } from 'react';
import type { ToolArtifact } from '@/context/SessionContext';
import { api } from '@/lib/api';

export function ArtifactPreview({ artifact }: { artifact: ToolArtifact }) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [url, setUrl] = useState<string | null>(null);
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
    <div className="space-y-1.5">
      <a href={url} download={filename} className="block w-fit">
        <img src={url} alt="Generated image" className="max-h-80 max-w-full rounded border border-line bg-panel object-contain" />
      </a>
      <a href={url} download={filename} className="inline-block font-mono text-[11px] text-accent-ink underline">Download {filename}</a>
    </div>
  );
}
