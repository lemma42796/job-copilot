import { type JDListItem, type MatchListResponse, listJds, listMatches } from '@/lib/api';

import { MatchesClient } from './matches-client';

const PAGE_SIZE = 20;
const JD_LOOKUP_LIMIT = 100;

type LoadState =
  | {
      kind: 'ok';
      data: MatchListResponse;
      jdLookup: Record<number, JDListItem>;
    }
  | { kind: 'error'; message: string };

async function loadFirstPage(): Promise<LoadState> {
  try {
    // 并发拉 matches 首页 + 一份 JD 列表用作 jdLookup(显示公司/岗位/title)。
    // 100 条 JD 对单 user 的 dogfood 量级足够;miss 的卡片回落到 "JD #id" 文案。
    const [matches, jds] = await Promise.all([
      listMatches({ limit: PAGE_SIZE }),
      listJds({ limit: JD_LOOKUP_LIMIT }).catch(() => null),
    ]);
    const jdLookup: Record<number, JDListItem> = {};
    for (const j of jds?.data ?? []) jdLookup[j.id] = j;
    return { kind: 'ok', data: matches, jdLookup };
  } catch (err) {
    return { kind: 'error', message: err instanceof Error ? err.message : String(err) };
  }
}

export default async function MatchesListPage() {
  const state = await loadFirstPage();

  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">全部匹配</h1>
        <p className="mt-1 text-sm text-muted">每次发起匹配的评分与命中/缺失分析。</p>
      </header>

      {state.kind === 'ok' ? (
        <MatchesClient
          initialItems={[...state.data.data]}
          initialCursor={state.data.next_cursor ?? null}
          jdLookup={state.jdLookup}
        />
      ) : (
        <div className="rounded-md border border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 p-4 text-sm text-[var(--color-danger)]">
          加载失败:{state.message}
        </div>
      )}
    </div>
  );
}
