'use client';

import * as React from 'react';

const DEFAULT_TTL_MS = 60 * 60 * 1000;

export function useSessionDraft(
  key: string,
  ttlMs: number = DEFAULT_TTL_MS,
): [string, (v: string) => void, () => void] {
  const [value, setValueState] = React.useState('');

  React.useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      const raw = window.sessionStorage.getItem(key);
      if (!raw) return;
      const parsed = JSON.parse(raw) as { v: string; t: number };
      if (Date.now() - parsed.t < ttlMs && typeof parsed.v === 'string') {
        setValueState(parsed.v);
      } else {
        window.sessionStorage.removeItem(key);
      }
    } catch {
      // corrupted entry — ignore
    }
  }, [key, ttlMs]);

  const setValue = React.useCallback(
    (v: string) => {
      setValueState(v);
      if (typeof window === 'undefined') return;
      try {
        if (v === '') window.sessionStorage.removeItem(key);
        else window.sessionStorage.setItem(key, JSON.stringify({ v, t: Date.now() }));
      } catch {
        // quota / private mode — ignore
      }
    },
    [key],
  );

  const clear = React.useCallback(() => {
    setValueState('');
    if (typeof window === 'undefined') return;
    try {
      window.sessionStorage.removeItem(key);
    } catch {
      // ignore
    }
  }, [key]);

  return [value, setValue, clear];
}
