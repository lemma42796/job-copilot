'use client';

import type { Route } from 'next';
import Link from 'next/link';

import { Card } from '@/components/ui/card';
import type { ProfileListItem } from '@/lib/api';
import { formatRelative } from '@/lib/format';

export function ProfileCard({
  profile,
  onDelete,
  deleting,
}: {
  profile: ProfileListItem;
  onDelete: (id: number) => void;
  deleting: boolean;
}) {
  const name = profile.full_name?.trim() || '(未识别姓名)';
  const meta = profile.location?.trim() ?? '';

  return (
    <Card className="group relative px-5 py-4 transition-colors hover:bg-black/[0.02]">
      <Link
        href={`/profiles/${profile.id}` as Route}
        className="absolute inset-0 rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        aria-label={`查看简历 #${profile.id} ${name}`}
      />
      <div className="pointer-events-none relative flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-[15px] font-semibold tracking-tight">{name}</h3>
            {profile.status !== 'parsed' ? <StatusPill status={profile.status} /> : null}
          </div>
          {meta ? <p className="mt-1 truncate text-[13px] text-muted">{meta}</p> : null}
          <p className="mt-1 text-xs text-muted">
            #{profile.id} · 更新于 {formatRelative(profile.updated_at)}
          </p>
        </div>
        <div className="flex items-center">
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              if (deleting) return;
              if (window.confirm(`删除简历 #${profile.id}?此操作不可撤销。`)) onDelete(profile.id);
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

function StatusPill({ status }: { status: ProfileListItem['status'] }) {
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
