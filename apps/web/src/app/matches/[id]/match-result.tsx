'use client';

import type { Route } from 'next';
import Link from 'next/link';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import type { MatchDetail, MatchedSkill, MissingSkill } from '@/lib/api';
import { formatRelative } from '@/lib/format';

const SEVERITY_LABELS: Record<MissingSkill['severity'], string> = {
  critical: '硬性缺失',
  major: '重要短板',
  minor: '加分项缺失',
};

const SEVERITY_TONES: Record<MissingSkill['severity'], string> = {
  critical:
    'bg-[var(--color-danger)]/10 text-[var(--color-danger)] border-[var(--color-danger)]/30',
  major:
    'bg-[var(--color-warning-bg)] text-[var(--color-warning-fg)] border-[var(--color-warning-fg)]/30',
  minor: 'bg-black/[0.04] text-foreground border-border',
};

export function MatchResultView({ match }: { match: MatchDetail }) {
  const structured = match.structured;

  return (
    <div className="space-y-6">
      <Header match={match} />

      {match.status === 'pending' ? <PendingBanner /> : null}
      {match.status === 'failed' ? <FailedBanner /> : null}

      {structured ? (
        <>
          <ScoreCard score={structured.score} match={match} />
          <SummaryCards advantage={structured.advantage_summary} gap={structured.gap_summary} />
          <MatchedSkillsCard skills={structured.matched_skills ?? []} />
          <MissingSkillsCard skills={structured.missing_skills ?? []} />
          <SuggestionsCard suggestions={structured.suggestions ?? []} />
        </>
      ) : null}
    </div>
  );
}

function Header({ match }: { match: MatchDetail }) {
  return (
    <div className="flex items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">匹配 #{match.id}</h1>
        <p className="mt-1 text-sm text-muted">
          基于{' '}
          <Link href={`/jds/${match.jd_id}` as Route} className="underline hover:text-foreground">
            JD #{match.jd_id}
          </Link>{' '}
          + 简历 #{match.profile_id} · {formatRelative(match.created_at)}
        </p>
      </div>
    </div>
  );
}

function PendingBanner() {
  return (
    <div className="rounded-md border border-[var(--color-warning-fg)]/30 bg-[var(--color-warning-bg)] px-4 py-3 text-sm text-[var(--color-warning-fg)]">
      正在分析中,稍候刷新页面查看结果。
    </div>
  );
}

function FailedBanner() {
  return (
    <div className="rounded-md border border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 px-4 py-3 text-sm text-[var(--color-danger)]">
      本次匹配分析失败 — 可以删除这条记录,在 JD 详情页重新发起匹配。
    </div>
  );
}

function ScoreCard({ score, match }: { score: number; match: MatchDetail }) {
  const tone =
    score >= 75
      ? { ring: 'var(--color-success-fg)', text: 'text-[var(--color-success-fg)]' }
      : score >= 50
        ? { ring: 'var(--color-warning-fg)', text: 'text-[var(--color-warning-fg)]' }
        : { ring: 'var(--color-danger)', text: 'text-[var(--color-danger)]' };
  const angle = Math.max(0, Math.min(score, 100)) * 3.6;
  return (
    <Card>
      <CardContent className="flex items-center gap-6 py-6">
        <div
          className="relative size-32 rounded-full"
          style={{
            background: `conic-gradient(${tone.ring} ${angle}deg, var(--color-border) 0)`,
          }}
        >
          <div className="absolute inset-[6px] flex flex-col items-center justify-center rounded-full bg-surface">
            <span className={`text-4xl font-bold leading-none ${tone.text}`}>{score}</span>
            <span className="mt-1 text-[10px] tracking-widest text-muted uppercase">SCORE</span>
          </div>
        </div>
        <div className="flex-1 space-y-1 text-sm text-muted">
          <p>
            模型:<span className="text-foreground">{match.model ?? '-'}</span>
          </p>
          <p>
            tokens:
            <span className="text-foreground">
              {match.tokens?.input ?? 0} / {match.tokens?.output ?? 0}
            </span>
          </p>
          <p>
            成本:<span className="text-foreground">¥ {match.cost_cny ?? '0'}</span>
          </p>
          <p>
            耗时:<span className="text-foreground">{match.latency_ms ?? 0} ms</span>
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

function SummaryCards({ advantage, gap }: { advantage: string; gap: string }) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">优势</CardTitle>
          <CardDescription>简历中可以重点突出的亮点</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm leading-6 whitespace-pre-wrap text-foreground">
            {advantage || '(LLM 未给出优势分析)'}
          </p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">差距</CardTitle>
          <CardDescription>距离这份 JD 还差什么</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm leading-6 whitespace-pre-wrap text-foreground">
            {gap || '(LLM 未给出差距分析)'}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function MatchedSkillsCard({ skills }: { skills: readonly MatchedSkill[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">命中技能 · {skills.length}</CardTitle>
        <CardDescription>JD 要求里你已经具备的能力(数字为强度自评)</CardDescription>
      </CardHeader>
      <CardContent>
        {skills.length === 0 ? (
          <p className="text-sm text-muted">(无)</p>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {skills.map((s, i) => (
              <li
                key={`${s.name}-${i}`}
                className="flex items-center gap-1.5 rounded-full border border-[var(--color-success-fg)]/30 bg-[var(--color-success-bg)] px-3 py-1 text-xs text-[var(--color-success-fg)]"
                title={
                  s.evidence_chunk_ids && s.evidence_chunk_ids.length > 0
                    ? `证据 chunk:#${[...s.evidence_chunk_ids].join(', #')}`
                    : '无证据 chunk(LLM 未给出引用)'
                }
              >
                <span className="font-medium">{s.name}</span>
                <span className="opacity-70">{Math.round(s.strength * 100)}</span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function MissingSkillsCard({ skills }: { skills: readonly MissingSkill[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">缺失技能 · {skills.length}</CardTitle>
        <CardDescription>JD 提到但简历里未体现的能力</CardDescription>
      </CardHeader>
      <CardContent>
        {skills.length === 0 ? (
          <p className="text-sm text-muted">(无)</p>
        ) : (
          <ul className="space-y-2">
            {skills.map((s, i) => (
              <li
                key={`${s.name}-${i}`}
                className={`rounded-md border px-3 py-2 ${SEVERITY_TONES[s.severity]}`}
              >
                <div className="flex items-center gap-2">
                  <span className="font-medium">{s.name}</span>
                  <span className="rounded-full bg-black/10 px-1.5 py-0.5 text-[10px] tracking-wider uppercase">
                    {SEVERITY_LABELS[s.severity]}
                  </span>
                </div>
                <p className="mt-1 text-sm leading-5 opacity-90">{s.suggestion}</p>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function SuggestionsCard({ suggestions }: { suggestions: readonly string[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">改进建议 · {suggestions.length}</CardTitle>
      </CardHeader>
      <CardContent>
        {suggestions.length === 0 ? (
          <p className="text-sm text-muted">(无)</p>
        ) : (
          <ol className="list-decimal space-y-2 pl-5 text-sm leading-6">
            {suggestions.map((s, i) => (
              <li key={`${i}-${s.slice(0, 16)}`}>{s}</li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
