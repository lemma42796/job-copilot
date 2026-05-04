import type { Route } from 'next';
import Link from 'next/link';

import { Button } from '@/components/ui/button';
import { type JDListResponse, listJds } from '@/lib/api';

import { JdsClient } from './jds-client';

const PAGE_SIZE = 20;

type LoadState = { kind: 'ok'; data: JDListResponse } | { kind: 'error'; message: string };

async function loadFirstPage(): Promise<LoadState> {
  try {
    const data = await listJds({ limit: PAGE_SIZE });
    return { kind: 'ok', data };
  } catch (err) {
    return { kind: 'error', message: err instanceof Error ? err.message : String(err) };
  }
}

export default async function JdsListPage() {
  const state = await loadFirstPage();

  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      <header className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">全部 JD</h1>
          <p className="mt-1 text-sm text-muted">粘贴过的职位描述都在这里。</p>
        </div>
        <Button asChild size="sm">
          <Link href={'/jds/new' as Route}>新建 JD</Link>
        </Button>
      </header>

      {state.kind === 'ok' ? (
        <JdsClient
          initialItems={[...state.data.data]}
          initialCursor={state.data.next_cursor ?? null}
        />
      ) : (
        <div className="rounded-md border border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 p-4 text-sm text-[var(--color-danger)]">
          加载失败:{state.message}
        </div>
      )}
    </div>
  );
}
