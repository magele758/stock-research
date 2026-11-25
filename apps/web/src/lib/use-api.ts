'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { isAbort, QuantApiError } from './quant-api';

export interface ApiState<T> {
  data: T | undefined;
  error: QuantApiError | Error | undefined;
  loading: boolean;
  /** Wall-clock of the last settled request (ms). */
  elapsedMs: number | undefined;
  /** Key of the request that produced `data`. */
  dataKey: string | null;
  reload: () => void;
}

/**
 * Keyed async loader with abort-on-change and stale-while-revalidate.
 * `key === null` disables the request (e.g. API offline) and keeps previous data.
 * `debounceMs` delays the request after key changes (slider drags).
 */
export function useApi<T>(key: string | null, fetcher: (signal: AbortSignal) => Promise<T>, debounceMs = 0): ApiState<T> {
  const [data, setData] = useState<T>();
  const [dataKey, setDataKey] = useState<string | null>(null);
  const [error, setError] = useState<QuantApiError | Error>();
  const [loading, setLoading] = useState(false);
  const [elapsedMs, setElapsedMs] = useState<number>();
  const [nonce, setNonce] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    if (key === null) {
      setLoading(false);
      return;
    }
    const ctrl = new AbortController();
    let cancelled = false;
    setLoading(true);
    const run = async () => {
      const t0 = performance.now();
      try {
        const out = await fetcherRef.current(ctrl.signal);
        if (cancelled) return;
        setData(out);
        setDataKey(key);
        setError(undefined);
      } catch (err) {
        if (cancelled || isAbort(err)) return;
        setError(err instanceof Error ? err : new Error(String(err)));
      } finally {
        if (!cancelled) {
          setElapsedMs(performance.now() - t0);
          setLoading(false);
        }
      }
    };
    const timer = debounceMs > 0 ? setTimeout(run, debounceMs) : (run(), undefined);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      ctrl.abort();
    };
  }, [key, nonce, debounceMs]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, elapsedMs, dataKey, reload };
}
