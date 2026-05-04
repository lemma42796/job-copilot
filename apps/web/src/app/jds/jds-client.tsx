'use client';

import type { Route } from 'next';
import Link from 'next/link';
import * as React from 'react';

import { JdCard } from '@/components/list/jd-card';
import { Button } from '@/components/ui/button';
import { ApiError, type JDListItem, deleteJd, listJds } from '@/lib/api';

const PAGE_SIZE = 20;

type Props = {
  initialItems: JDListItem[];
  initialCursor: string | null;
};

export function JdsClient({ initialItems, initialCursor }: Props) {
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
      const res = await listJds({ cursor, limit: PAGE_SIZE });
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
      await deleteJd(id);
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
        <p className="text-sm text-muted">还没有 JD</p>
        <Button asChild className="mt-4" size="sm">
          <Link href={'/jds/new' as Route}>新建 JD</Link>
        </Button>
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

      {items.map((jd) => (
        <JdCard key={jd.id} jd={jd} onDelete={onDelete} deleting={deletingId === jd.id} />
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
