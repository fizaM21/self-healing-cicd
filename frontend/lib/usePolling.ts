"use client";
import { useCallback, useEffect, useState } from "react";

export function usePolling<T>(load: () => Promise<T>, ms = 5000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try { setData(await load()); setError(null); } catch (e) { setError((e as Error).message); }
  }, [load]);
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, ms);
    return () => clearInterval(t);
  }, [refresh, ms]);
  return { data, error, refresh };
}
