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
    <div className="mx-auto max-w-3xl px-6 py-16">
      <h1 className="text-5xl font-semibold tracking-tight">JobCopilot</h1>
      <p className="mt-3 text-xl text-muted">笔记即题库 — AI 面试陪练 + JD 分析 + 简历诊断</p>
      <p className="mt-6 max-w-xl text-[15px] leading-relaxed text-muted">
        v2 重构中。M1 起逐步上线:笔记入库 + 树形导航 → 出题 + 三层评分 → JD 累积分析 →
        简历两方锚点诊断。
      </p>

      <h2 className="mt-16 text-base font-semibold tracking-tight text-muted uppercase">
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
    </div>
  );
}
