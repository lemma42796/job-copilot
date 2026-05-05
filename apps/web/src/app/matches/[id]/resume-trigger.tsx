'use client';

import type { Route } from 'next';
import { useRouter } from 'next/navigation';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  ApiError,
  type DrafterPhase,
  type MatchDetail,
  type ResumeNodeName,
  type ResumeSseFrame,
  createResume,
} from '@/lib/api';

type Props = {
  match: MatchDetail;
};

type Phase = 'idle' | 'starting' | 'generating' | 'redirecting';

// W7 graph 5 节点;revise 是条件分支(review 失败才触发),所以进度条
// 主轴只显示 4 个常驻节点,revise 通过 revisionCount badge 单独反映。
const MAIN_NODES = ['retrieve', 'plan', 'draft', 'review'] as const;
type MainNode = (typeof MAIN_NODES)[number];

const NODE_LABELS: Record<ResumeNodeName, string> = {
  retrieve: '检索',
  plan: '规划',
  draft: '起草',
  review: '核查',
  revise: '修订',
};

export function ResumeTrigger({ match }: Props) {
  const router = useRouter();
  const [phase, setPhase] = React.useState<Phase>('idle');
  const [error, setError] = React.useState<string | null>(null);
  const [completedNodes, setCompletedNodes] = React.useState<ReadonlySet<ResumeNodeName>>(
    () => new Set(),
  );
  const [revisionCount, setRevisionCount] = React.useState(0);
  // Streamed drafter preview(W8):每个 drafter_token append 到 streamedDraft;
  // phase 切换(draft → revise)时整段重置 — revise 重写,不追加。
  const [streamedDraft, setStreamedDraft] = React.useState('');
  const [streamedPhase, setStreamedPhase] = React.useState<DrafterPhase | null>(null);
  const streamedPhaseRef = React.useRef<DrafterPhase | null>(null);

  const blocker =
    match.status !== 'scored'
      ? '匹配尚未完成评分,无法基于此次匹配生成简历'
      : match.structured == null
        ? '匹配数据缺失,无法生成简历'
        : null;

  async function start() {
    setPhase('starting');
    setError(null);
    setCompletedNodes(new Set());
    setRevisionCount(0);
    setStreamedDraft('');
    setStreamedPhase(null);
    streamedPhaseRef.current = null;
    try {
      const stream = createResume({
        jd_id: match.jd_id,
        profile_id: match.profile_id,
        match_id: match.id,
      });
      let resourceId: number | null = null;
      for await (const frame of stream) {
        const f = frame as ResumeSseFrame;
        switch (f.event) {
          case 'started':
            resourceId = f.data.resource_id;
            setPhase('generating');
            break;
          case 'drafter_token': {
            const incomingPhase = f.data.phase;
            if (streamedPhaseRef.current !== incomingPhase) {
              streamedPhaseRef.current = incomingPhase;
              setStreamedPhase(incomingPhase);
              setStreamedDraft(f.data.delta);
            } else {
              setStreamedDraft((prev) => prev + f.data.delta);
            }
            break;
          }
          case 'node_completed':
            setCompletedNodes((prev) => {
              const next = new Set(prev);
              next.add(f.data.node);
              return next;
            });
            setRevisionCount(f.data.revision_count);
            break;
          case 'result':
            resourceId = f.data.resource_id;
            break;
          case 'error':
            setError(f.data.detail || f.data.code);
            setPhase('idle');
            return;
          case 'done':
            if (f.data.ok && resourceId != null) {
              setPhase('redirecting');
              router.push(`/resumes/${resourceId}` as Route);
              return;
            }
            if (!f.data.ok) {
              setPhase('idle');
            }
            return;
        }
      }
    } catch (err) {
      setError(messageOf(err));
      setPhase('idle');
    }
  }

  const isWorking = phase === 'generating' || phase === 'redirecting';

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">基于此次匹配生成简历</CardTitle>
        <CardDescription>
          drafter 会以你的个人档案为依据,围绕 JD 重点重写简历;reviewer 做一遍事实核查防幻觉。 通常
          30-90 秒;成本约 ¥0.04-0.06。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {blocker ? (
          <div className="rounded-md border border-[var(--color-warning-fg)]/30 bg-[var(--color-warning-bg)] px-3 py-2 text-sm text-[var(--color-warning-fg)]">
            {blocker}
          </div>
        ) : null}

        {error ? (
          <div className="rounded-md border border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 px-3 py-2 text-sm text-[var(--color-danger)]">
            生成失败:{error}
          </div>
        ) : null}

        <div className="flex items-center gap-3">
          <Button onClick={start} disabled={blocker != null || phase !== 'idle'} size="sm">
            {phase === 'idle'
              ? '生成定制简历'
              : phase === 'starting'
                ? '准备中…'
                : phase === 'generating'
                  ? '生成中…'
                  : '跳转中…'}
          </Button>
          {phase === 'generating' ? (
            <span className="text-xs text-muted">drafter + reviewer 串行,大致 30-90 秒。</span>
          ) : null}
        </div>

        {isWorking ? (
          <NodeProgress completed={completedNodes} revisionCount={revisionCount} />
        ) : null}

        {isWorking && streamedDraft && streamedPhase ? (
          <DrafterPreview phase={streamedPhase} text={streamedDraft} />
        ) : null}
      </CardContent>
    </Card>
  );
}

function DrafterPreview({ phase, text }: { phase: DrafterPhase; text: string }) {
  const label = phase === 'draft' ? '起草中' : '修订中';
  // 只显示尾部最近 ~1.5KB,长简历末尾内容才是 LLM 当前正在写的部分,
  // 同时 viewport 不会被一整篇 markdown 撑爆。final 完整版从 /resumes/{id} 取。
  const TAIL = 1500;
  const tail = text.length > TAIL ? `…${text.slice(-TAIL)}` : text;
  return (
    <div className="rounded-md border border-border bg-black/[0.02]">
      <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
        <span className="text-[11px] font-medium text-muted">{label}(实时预览)</span>
        <span className="text-[10px] text-muted">{text.length} 字</span>
      </div>
      <pre className="max-h-44 overflow-auto whitespace-pre-wrap px-3 py-2 font-mono text-[11px] leading-relaxed text-foreground/80">
        {tail}
      </pre>
    </div>
  );
}

function NodeProgress({
  completed,
  revisionCount,
}: {
  completed: ReadonlySet<ResumeNodeName>;
  revisionCount: number;
}) {
  return (
    <div className="space-y-2 rounded-md border border-border bg-black/[0.02] px-3 py-2.5">
      <ol className="flex items-center gap-1.5">
        {MAIN_NODES.map((node, i) => {
          const isDone = completed.has(node);
          const isNext =
            !isDone &&
            (i === 0 || completed.has(MAIN_NODES[i - 1] as MainNode));
          return (
            <React.Fragment key={node}>
              <li className="flex items-center gap-1.5">
                <span
                  className={`flex h-5 w-5 items-center justify-center rounded-full border text-[10px] font-semibold transition-colors ${
                    isDone
                      ? 'border-[var(--color-success-border)] bg-[var(--color-success-border)] text-white'
                      : isNext
                        ? 'border-[var(--color-accent)] bg-background text-[var(--color-accent)]'
                        : 'border-border bg-background text-muted'
                  }`}
                  aria-current={isNext ? 'step' : undefined}
                >
                  {isDone ? '✓' : i + 1}
                </span>
                <span
                  className={`text-xs ${
                    isDone || isNext ? 'text-foreground' : 'text-muted'
                  }`}
                >
                  {NODE_LABELS[node]}
                </span>
              </li>
              {i < MAIN_NODES.length - 1 ? (
                <span
                  className={`h-px flex-1 ${
                    completed.has(MAIN_NODES[i + 1] as MainNode) || isDone
                      ? 'bg-[var(--color-success-border)]/60'
                      : 'bg-border'
                  }`}
                  aria-hidden
                />
              ) : null}
            </React.Fragment>
          );
        })}
      </ol>
      {revisionCount > 0 ? (
        <p className="text-xs text-[var(--color-warning-fg)]">
          核查未通过 — 已修订 {revisionCount} 次,正在重新核查…
        </p>
      ) : null}
    </div>
  );
}

function messageOf(err: unknown): string {
  if (err instanceof ApiError) {
    return err.problem.detail ?? err.problem.title ?? `HTTP ${err.status}`;
  }
  return err instanceof Error ? err.message : String(err);
}
