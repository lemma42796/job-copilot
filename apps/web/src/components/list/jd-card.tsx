'use client';

import type { Route } from 'next';
import Link from 'next/link';

import { Card } from '@/components/ui/card';
import type { JDListItem } from '@/lib/api';
import { formatRelative, formatSalary } from '@/lib/format';

export function JdCard({
  jd,
  onDelete,
  deleting,
}: {
  jd: JDListItem;
  onDelete: (id: number) => void;
  deleting: boolean;
}) {
  const title = jd.title?.trim() || '(未抽出职位)';
  const meta = [jd.company?.trim(), jd.location?.trim()].filter(Boolean).join(' · ');
  const salary = formatSalary(jd.salary_min, jd.salary_max, jd.salary_currency, null);

  return (
    <Card className="group relative px-5 py-4 transition-colors hover:bg-black/[0.02]">
      <Link
        href={`/jds/${jd.id}` as Route}
        className="absolute inset-0 rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        aria-label={`查看 JD #${jd.id} ${title}`}
      />
      <div className="pointer-events-none relative flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-[15px] font-semibold tracking-tight">{title}</h3>
            {jd.status !== 'parsed' ? <StatusPill status={jd.status} /> : null}
          </div>
          {meta || salary ? (
            <p className="mt-1 truncate text-[13px] text-muted">
              {meta}
              {meta && salary !== '面议' ? ' · ' : ''}
              {salary !== '面议' ? salary : meta ? '' : '面议'}
            </p>
          ) : null}
          <p className="mt-1 text-xs text-muted">
            #{jd.id} · {formatRelative(jd.created_at)}
          </p>
        </div>
        <div className="flex items-center">
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              if (deleting) return;
              if (window.confirm(`删除 JD #${jd.id}?此操作不可撤销。`)) onDelete(jd.id);
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

function StatusPill({ status }: { status: JDListItem['status'] }) {
  if (status === 'parsing') {
    return (
      <span className="rounded-full bg-[var(--color-warning-bg)] px-2 py-0.5 text-[10px] font-medium text-[var(--color-warning-fg)]">
        解析中
      </span>
    );
  }
  return (
    <span className="rounded-full bg-[var(--color-danger)]/10 px-2 py-0.5 text-[10px] font-medium text-[var(--color-danger)]">
      解析失败
    </span>
  );
}
