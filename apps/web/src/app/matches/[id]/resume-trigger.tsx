'use client';

import type { Route } from 'next';
import { useRouter } from 'next/navigation';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ApiError, type MatchDetail, type ResumeSseFrame, createResume } from '@/lib/api';

type Props = {
  match: MatchDetail;
};

type Phase = 'idle' | 'starting' | 'generating' | 'redirecting';

export function ResumeTrigger({ match }: Props) {
  const router = useRouter();
  const [phase, setPhase] = React.useState<Phase>('idle');
  const [error, setError] = React.useState<string | null>(null);

  const blocker =
    match.status !== 'scored'
      ? '匹配尚未完成评分,无法基于此次匹配生成简历'
      : match.structured == null
        ? '匹配数据缺失,无法生成简历'
        : null;

  async function start() {
    setPhase('starting');
    setError(null);
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
      </CardContent>
    </Card>
  );
}

function messageOf(err: unknown): string {
  if (err instanceof ApiError) {
    return err.problem.detail ?? err.problem.title ?? `HTTP ${err.status}`;
  }
  return err instanceof Error ? err.message : String(err);
}
