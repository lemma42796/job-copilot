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
    <main>
      <h1>JobCopilot</h1>
      <p style={{ color: 'var(--color-muted)' }}>AI 求职副驾 — M0 仓库骨架已就绪。</p>

      <h2 style={{ marginTop: 32 }}>API 状态</h2>
      {state.kind === 'ok' ? (
        <p>
          <strong style={{ color: 'var(--color-accent)' }}>API: {state.data.status}</strong>{' '}
          <code>v{state.data.version}</code> · env <code>{state.data.env}</code>
          <br />
          <small style={{ color: 'var(--color-muted)' }}>server time: {state.data.timestamp}</small>
        </p>
      ) : (
        <p>
          <strong style={{ color: '#f85149' }}>API: down</strong>
          <br />
          <small style={{ color: 'var(--color-muted)' }}>{state.message}</small>
        </p>
      )}

      <p style={{ marginTop: 32, color: 'var(--color-muted)' }}>
        backend: <code>{API_BASE_URL}/v1/health</code>
      </p>
    </main>
  );
}
