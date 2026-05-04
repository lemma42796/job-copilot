'use client';

import type { Route } from 'next';
import Link from 'next/link';

import { Card } from '@/components/ui/card';
import type { JDListItem, ResumeListItem } from '@/lib/api';
import { formatRelative } from '@/lib/format';

export function ResumeCard({
  resume,
  jd,
  onDelete,
  deleting,
}: {
  resume: ResumeListItem;
  jd: JDListItem | null;
  onDelete: (id: number) => void;
  deleting: boolean;
}) {
  const fallbackTitle = jd?.title?.trim() || `JD #${resume.jd_id}`;
  const title = resume.title?.trim() || fallbackTitle;
  const meta = [jd?.company?.trim(), jd?.location?.trim()].filter(Boolean).join(' · ');

  return (
    <Card className="group relative px-5 py-4 transition-colors hover:bg-black/[0.02]">
      <Link
        href={`/resumes/${resume.id}` as Route}
        className="absolute inset-0 rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        aria-label={`查看简历 #${resume.id}`}
      />
      <div className="pointer-events-none relative flex items-center gap-4">
        <StatusBadge status={resume.status} reviewPassed={resume.review_passed} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-[15px] font-semibold tracking-tight">{title}</h3>
            {resume.status !== 'ready' ? <StatusPill status={resume.status} /> : null}
          </div>
          {meta ? <p className="mt-1 truncate text-[13px] text-muted">{meta}</p> : null}
          <p className="mt-1 text-xs text-muted">
            #{resume.id} ·{' '}
            {resume.review_findings_count > 0
              ? `${resume.review_findings_count} 条核查标记`
              : '通过事实核查'}
            {resume.cost_cny != null ? ` · ¥${resume.cost_cny}` : ''} ·{' '}
            {formatRelative(resume.created_at)}
          </p>
        </div>
        <div className="flex items-center">
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              if (deleting) return;
              if (window.confirm(`删除简历 #${resume.id}?此操作不可撤销。`)) onDelete(resume.id);
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

function StatusBadge({
  status,
  reviewPassed,
}: {
  status: ResumeListItem['status'];
  reviewPassed: boolean | null;
}) {
  if (status === 'ready' && reviewPassed) {
    return (
      <div className="flex size-12 shrink-0 flex-col items-center justify-center rounded-xl bg-[var(--color-success-bg)] text-[var(--color-success-fg)]">
        <span className="text-[15px] leading-none font-semibold">✓</span>
        <span className="mt-0.5 text-[9px] tracking-wider opacity-70">READY</span>
      </div>
    );
  }
  if (status === 'review_failed') {
    return (
      <div className="flex size-12 shrink-0 flex-col items-center justify-center rounded-xl bg-[var(--color-warning-bg)] text-[var(--color-warning-fg)]">
        <span className="text-[15px] leading-none font-semibold">!</span>
        <span className="mt-0.5 text-[9px] tracking-wider opacity-70">REVIEW</span>
      </div>
    );
  }
  if (status === 'failed') {
    return (
      <div className="flex size-12 shrink-0 flex-col items-center justify-center rounded-xl bg-[var(--color-danger)]/10 text-[var(--color-danger)]">
        <span className="text-[15px] leading-none font-semibold">×</span>
        <span className="mt-0.5 text-[9px] tracking-wider opacity-70">FAILED</span>
      </div>
    );
  }
  return (
    <div className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-black/5 text-xs text-muted">
      …
    </div>
  );
}

function StatusPill({ status }: { status: ResumeListItem['status'] }) {
  if (status === 'generating') {
    return (
      <span className="rounded-full bg-[var(--color-warning-bg)] px-2 py-0.5 text-[10px] font-medium text-[var(--color-warning-fg)]">
        生成中
      </span>
    );
  }
  if (status === 'review_failed') {
    return (
      <span className="rounded-full bg-[var(--color-warning-bg)] px-2 py-0.5 text-[10px] font-medium text-[var(--color-warning-fg)]">
        待人工核查
      </span>
    );
  }
  return (
    <span className="rounded-full bg-[var(--color-danger)]/10 px-2 py-0.5 text-[10px] font-medium text-[var(--color-danger)]">
      生成失败
    </span>
  );
}
