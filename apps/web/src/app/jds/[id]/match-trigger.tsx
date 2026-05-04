'use client';

import { MatchDepth } from '@jobcopilot/schemas';
import type { Route } from 'next';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ApiError, type MatchSseFrame, createMatch } from '@/lib/api';

type Props = {
  jdId: number;
  jdParsed: boolean;
  profileId: number | null;
  profileParsed: boolean;
};

type Phase = 'idle' | 'starting' | 'analyzing' | 'redirecting';

export function MatchTrigger({ jdId, jdParsed, profileId, profileParsed }: Props) {
  const router = useRouter();
  const [phase, setPhase] = React.useState<Phase>('idle');
  const [error, setError] = React.useState<string | null>(null);

  const blocker = !jdParsed
    ? 'JD 还未解析完成,无法发起匹配'
    : profileId == null
      ? '还没有简历,先去新建一份'
      : !profileParsed
        ? '简历还未解析完成或解析失败,请打开简历详情页处理'
        : null;

  async function start() {
    if (profileId == null) return;
    setPhase('starting');
    setError(null);
    try {
      const stream = createMatch({
        jd_id: jdId,
        profile_id: profileId,
        depth: MatchDepth.quick,
      });
      let resourceId: number | null = null;
      for await (const frame of stream) {
        const f = frame as MatchSseFrame;
        switch (f.event) {
          case 'started':
            resourceId = f.data.resource_id;
            setPhase('analyzing');
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
              router.push(`/matches/${resourceId}` as Route);
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
        <CardTitle>匹配分析</CardTitle>
        <CardDescription>
          基于这份 JD 与你的简历做一次匹配,给出评分、命中/缺失技能与改进建议。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {blocker ? (
          <div className="rounded-md border border-[var(--color-warning-fg)]/30 bg-[var(--color-warning-bg)] px-3 py-2 text-sm text-[var(--color-warning-fg)]">
            {blocker}
            {profileId == null ? (
              <>
                {' '}
                <Link href="/profiles/new" className="underline hover:opacity-80">
                  去新建简历
                </Link>
              </>
            ) : null}
          </div>
        ) : null}

        {error ? (
          <div className="rounded-md border border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 px-3 py-2 text-sm text-[var(--color-danger)]">
            匹配失败:{error}
          </div>
        ) : null}

        <div className="flex items-center gap-3">
          <Button onClick={start} disabled={blocker != null || phase !== 'idle'} size="sm">
            {phase === 'idle'
              ? '开始匹配'
              : phase === 'starting'
                ? '准备中…'
                : phase === 'analyzing'
                  ? '分析中…'
                  : '跳转中…'}
          </Button>
          {phase === 'analyzing' ? (
            <span className="text-xs text-muted">LLM 正在打分,通常 5-15 秒。</span>
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
