import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';

export type ArtifactVariant = 'thumbnail' | 'original';
export type ArtifactBlobState = 'idle' | 'loading' | 'ready' | 'error';

/** Fetch an artifact variant only while it is needed. The hook owns the URL it
 * creates and releases it whenever its request is replaced or unmounted. */
export function useArtifactBlob(artifactId: string, variant: ArtifactVariant, enabled: boolean, endpoint?: string) {
  const [attempt, setAttempt] = useState(0);
  const [url, setUrl] = useState<string | null>(null);
  const [state, setState] = useState<ArtifactBlobState>(enabled ? 'loading' : 'idle');

  useEffect(() => {
    if (!enabled) {
      setState('idle');
      setUrl(null);
      return;
    }

    const controller = new AbortController();
    let active = true;
    let objectUrl: string | null = null;
    setState('loading');
    setUrl(null);

    api.get(endpoint ?? `/artifacts/${artifactId}?variant=${variant}`, {
      responseType: 'blob',
      signal: controller.signal,
    }).then(({ data }) => {
      objectUrl = URL.createObjectURL(data as Blob);
      if (!active) {
        URL.revokeObjectURL(objectUrl);
        return;
      }
      setUrl(objectUrl);
      setState('ready');
    }).catch((error: unknown) => {
      if (!active || controller.signal.aborted) return;
      if ((error as { name?: string })?.name !== 'CanceledError' && (error as { name?: string })?.name !== 'AbortError') {
        setState('error');
      }
    });

    return () => {
      active = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [artifactId, attempt, enabled, endpoint, variant]);

  const retry = useCallback(() => setAttempt((current) => current + 1), []);
  return { url, state, retry };
}
