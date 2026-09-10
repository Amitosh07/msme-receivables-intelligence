import { useCallback, useEffect, useRef, useState } from "react";
export function useAsync<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<unknown>();
  const [loading, setLoading] = useState(true);
  const request = useRef(0);
  const refresh = useCallback(async (showLoading = true) => {
    const requestId = ++request.current;
    if (showLoading) setLoading(true);
    setError(undefined);
    try { const next = await loader(); if (requestId === request.current) setData(next); }
    catch (e) { if (requestId === request.current) setError(e); }
    finally { if (requestId === request.current && showLoading) setLoading(false); }
  }, deps);
  useEffect(() => { void refresh(); return () => { request.current += 1; }; }, [refresh]);
  return { data, error, loading, refresh };
}
