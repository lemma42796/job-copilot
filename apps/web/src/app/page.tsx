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
    <main className="mx-auto max-w-3xl px-6 py-24">
      <h1 className="text-5xl font-semibold tracking-tight">JobCopilot</h1>
      <p className="mt-3 text-xl text-muted">AI 求职助手</p>
      <p className="mt-6 max-w-xl text-[15px] leading-relaxed text-muted">
        粘贴感兴趣的岗位 JD,再上传你的简历,即可得到匹配分析、能力差距与改进建议。
      </p>

      <div className="mt-10 flex flex-wrap gap-3">
        <Button asChild size="lg">
          <Link href="/jds/new">粘贴 JD</Link>
        </Button>
        <Button asChild size="lg" variant="outline">
          <Link href="/profiles/new">上传简历</Link>
        </Button>
      </div>

      <h2 className="mt-20 text-base font-semibold tracking-tight text-muted uppercase">
        API 状态
      </h2>
      <div className="mt-2 text-sm">
        {state.kind === 'ok' ? (
          <p className="text-muted">
            <span className="font-medium text-accent">{state.data.status}</span> · v
            {state.data.version} · env {state.data.env} · {state.data.timestamp}
          </p>
        ) : (
          <p className="text-muted">
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
