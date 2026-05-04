'use client';

import * as React from 'react';

import { ResumeCard } from '@/components/list/resume-card';
import { Button } from '@/components/ui/button';
import {
  ApiError,
  type JDListItem,
  type ResumeListItem,
  deleteResume,
  listResumes,
} from '@/lib/api';

const PAGE_SIZE = 20;

type Props = {
  initialItems: ResumeListItem[];
  initialCursor: string | null;
  jdLookup: Record<number, JDListItem>;
};

export function ResumesClient({ initialItems, initialCursor, jdLookup }: Props) {
  const [items, setItems] = React.useState(initialItems);
  const [cursor, setCursor] = React.useState<string | null>(initialCursor);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [deletingId, setDeletingId] = React.useState<number | null>(null);

  async function loadMore() {
    if (loading || !cursor) return;
    setLoading(true);
    setError(null);
    try {
      const res = await listResumes({ cursor, limit: PAGE_SIZE });
      setItems((prev) => [...prev, ...res.data]);
      setCursor(res.next_cursor ?? null);
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setLoading(false);
    }
  }

  async function onDelete(id: number) {
    setDeletingId(id);
    setError(null);
    try {
      await deleteResume(id);
      setItems((prev) => prev.filter((i) => i.id !== id));
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setDeletingId(null);
    }
  }

  if (items.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-border bg-surface p-12 text-center">
        <p className="text-sm text-muted">还没有定制简历</p>
        <p className="mt-2 text-xs text-muted">
          打开任意一次匹配的详情页,点"基于此次匹配生成简历"。
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {error ? (
        <div className="flex items-center justify-between rounded-md border border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 px-3 py-2 text-sm text-[var(--color-danger)]">
          <span>{error}</span>
          <button
            type="button"
            onClick={() => setError(null)}
            className="text-xs underline opacity-70 hover:opacity-100"
          >
            关闭
          </button>
        </div>
      ) : null}

      {items.map((r) => (
        <ResumeCard
          key={r.id}
          resume={r}
          jd={jdLookup[r.jd_id] ?? null}
          onDelete={onDelete}
          deleting={deletingId === r.id}
        />
      ))}

      {cursor ? (
        <div className="flex justify-center pt-2">
          <Button variant="outline" size="sm" onClick={loadMore} disabled={loading}>
            {loading ? '加载中…' : '加载更多'}
          </Button>
        </div>
      ) : items.length > PAGE_SIZE ? (
        <p className="pt-2 text-center text-xs text-muted">已到底部</p>
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
