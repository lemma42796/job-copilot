import type { Route } from 'next';
import Link from 'next/link';

import { Button } from '@/components/ui/button';
import { type ProfileListResponse, listProfiles } from '@/lib/api';

import { ProfilesClient } from './profiles-client';

const PAGE_SIZE = 20;

type LoadState =
  | { kind: 'ok'; data: ProfileListResponse }
  | { kind: 'error'; message: string };

async function loadFirstPage(): Promise<LoadState> {
  try {
    const data = await listProfiles({ limit: PAGE_SIZE });
    return { kind: 'ok', data };
  } catch (err) {
    return { kind: 'error', message: err instanceof Error ? err.message : String(err) };
  }
}

export default async function ProfilesListPage() {
  const state = await loadFirstPage();

  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      <header className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">全部简历</h1>
          <p className="mt-1 text-sm text-muted">每个用户当前只允许一份;后续 M3 起放开多份。</p>
        </div>
        <Button asChild size="sm">
          <Link href={'/profiles/new' as Route}>新建简历</Link>
        </Button>
      </header>

      {state.kind === 'ok' ? (
        <ProfilesClient
          initialItems={state.data.data}
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
