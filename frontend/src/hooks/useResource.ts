import { useCallback, useEffect, useState } from 'react';
import { api, errorMessage } from '@/lib/api';

/**
 * Fetch a resource from the API with loading/error/refetch state.
 * Pass `null` as the path to skip fetching.
 */
export function useResource<T = unknown>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  // Which path `data` came from, so callers can tell stale data apart
  // right after the path changes.
  const [loadedPath, setLoadedPath] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(path !== null);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async (opts?: { silent?: boolean }) => {
    if (path === null) return;
    // silent: refresh data without flipping `loading` (for background polls).
    // Explicit === true guard: onClick={refetch} passes a MouseEvent as opts.
    if (!(opts && opts.silent === true)) setLoading(true);
    setError(null);
    try {
      const res = await api.get<T>(path);
      setData(res.data);
      setLoadedPath(path);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    refetch();
  }, [refetch]);

  return { data, loading, error, refetch, setData, loadedPath };
}
