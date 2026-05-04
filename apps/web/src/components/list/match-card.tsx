'use client';

import type { Route } from 'next';
import Link from 'next/link';

import { Card } from '@/components/ui/card';
import type { JDListItem, MatchListItem } from '@/lib/api';
import { formatRelative } from '@/lib/format';

export function MatchCard({
  match,
  jd,
  onDelete,
  deleting,
}: {
  match: MatchListItem;
  jd: JDListItem | null;
  onDelete: (id: number) => void;
  deleting: boolean;
}) {
  const jdTitle = jd?.title?.trim() || `JD #${match.jd_id}`;
  const jdMeta = [jd?.company?.trim(), jd?.location?.trim()].filter(Boolean).join(' · ');

  return (
    <Card className="group relative px-5 py-4 transition-colors hover:bg-black/[0.02]">
      <Link
        href={`/matches/${match.id}` as Route}
        className="absolute inset-0 rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        aria-label={`查看匹配 #${match.id}`}
      />
      <div className="pointer-events-none relative flex items-center gap-4">
        <ScoreBadge score={match.score} status={match.status} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-[15px] font-semibold tracking-tight">{jdTitle}</h3>
            {match.status !== 'scored' ? <StatusPill status={match.status} /> : null}
          </div>
          {jdMeta ? <p className="mt-1 truncate text-[13px] text-muted">{jdMeta}</p> : null}
          <p className="mt-1 text-xs text-muted">
            #{match.id} · 命中 {match.matched_skills_count} · 缺失 {match.missing_skills_count} ·{' '}
            {formatRelative(match.created_at)}
          </p>
        </div>
        <div className="flex items-center">
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              if (deleting) return;
              if (window.confirm(`删除匹配 #${match.id}?此操作不可撤销。`)) onDelete(match.id);
            }}
            disabled={deleting}
            className="pointer-events-auto rounded-md px-2 py-1 text-xs text-muted opacity-0 transition-opacity hover:bg-[var(--color-danger)]/10 hover:text-[var(--color-danger)] focus-visible:opacity-100 group-hover:opacity-100 disabled:pointer-events-none disabled:opacity-30"
          >
            {deleting ? '删除中…' : '删除'}
          </button>
        </div>
      </div>
    </Card>
  );
}

function ScoreBadge({
  score,
  status,
}: {
  score: number | null;
  status: MatchListItem['status'];
}) {
  if (status !== 'scored' || score == null) {
    return (
      <div className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-black/5 text-xs text-muted">
        —
      </div>
    );
  }
  const tone = score >= 75 ? 'good' : score >= 50 ? 'warn' : 'bad';
  const cls =
    tone === 'good'
      ? 'bg-[var(--color-success-bg)] text-[var(--color-success-fg)]'
      : tone === 'warn'
        ? 'bg-[var(--color-warning-bg)] text-[var(--color-warning-fg)]'
        : 'bg-[var(--color-danger)]/10 text-[var(--color-danger)]';
  return (
    <div className={`flex size-12 shrink-0 flex-col items-center justify-center rounded-xl ${cls}`}>
      <span className="text-[15px] font-semibold leading-none">{score}</span>
      <span className="mt-0.5 text-[9px] tracking-wider opacity-70">SCORE</span>
    </div>
  );
}

function StatusPill({ status }: { status: MatchListItem['status'] }) {
  if (status === 'pending') {
    return (
      <span className="rounded-full bg-[var(--color-warning-bg)] px-2 py-0.5 text-[10px] font-medium text-[var(--color-warning-fg)]">
        分析中
      </span>
    );
  }
  return (
    <span className="rounded-full bg-[var(--color-danger)]/10 px-2 py-0.5 text-[10px] font-medium text-[var(--color-danger)]">
      分析失败
    </span>
  );
}
