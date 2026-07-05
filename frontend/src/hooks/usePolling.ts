import { useEffect, useRef } from 'react';

/** Call `fn` every `intervalMs` while `enabled`. The fn ref is kept fresh so
 *  callers can pass inline closures without resetting the interval. */
export function usePolling(fn: () => void | Promise<void>, intervalMs: number, enabled: boolean) {
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => {
      void fnRef.current();
    }, intervalMs);
    return () => clearInterval(id);
  }, [intervalMs, enabled]);
}
