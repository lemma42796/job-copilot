import Link from 'next/link';

import { Button } from '@/components/ui/button';
import { API_BASE_URL, type HealthResponse, fetchHealth } from '@/lib/api';

type HealthState = { kind: 'ok'; data: HealthResponse } | { kind: 'error'; message: string };

async function loadHealth(): Promise<HealthState> {
  try {
    const data = await fetchHealth();
    return { kind: 'ok', data };
  } catch (err) {
    return { kind: 'error', message: err instanceof Error ? err.message : String(err) };
  }
}

export default async function HomePage() {
  const state = await loadHealth();

  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <h1 className="text-3xl font-semibold">JobCopilot</h1>
      <p className="mt-2 text-muted">AI 求职副驾</p>

      <div className="mt-8">
        <Button asChild>
          <Link href="/jds/new">粘贴 JD</Link>
        </Button>
      </div>

      <h2 className="mt-12 text-xl font-semibold">API 状态</h2>
      <div className="mt-2 text-sm text-muted">
        {state.kind === 'ok' ? (
          <p>
            <span className="font-medium text-accent">{state.data.status}</span> v
            {state.data.version} · env {state.data.env} · {state.data.timestamp}
          </p>
        ) : (
          <p>
            <span className="font-medium text-[var(--color-danger)]">down</span> · {state.message}
          </p>
        )}
      </div>

      <p className="mt-8 text-xs text-muted">
        backend: <code>{API_BASE_URL}/v1/health</code>
      </p>
    </main>
  );
}
