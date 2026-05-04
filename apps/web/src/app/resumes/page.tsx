import { type JDListItem, type ResumeListResponse, listJds, listResumes } from '@/lib/api';

import { ResumesClient } from './resumes-client';

const PAGE_SIZE = 20;
const JD_LOOKUP_LIMIT = 100;

type LoadState =
  | {
      kind: 'ok';
      data: ResumeListResponse;
      jdLookup: Record<number, JDListItem>;
    }
  | { kind: 'error'; message: string };

async function loadFirstPage(): Promise<LoadState> {
  try {
    // 同 matches 列表页模式:并发拉简历首页 + 一份 JD 列表用作 jdLookup。
    // 100 条 JD 对单 user dogfood 量级足够;miss 卡片回落到 "JD #id"。
    const [resumes, jds] = await Promise.all([
      listResumes({ limit: PAGE_SIZE }),
      listJds({ limit: JD_LOOKUP_LIMIT }).catch(() => null),
    ]);
    const jdLookup: Record<number, JDListItem> = {};
    for (const j of jds?.data ?? []) jdLookup[j.id] = j;
    return { kind: 'ok', data: resumes, jdLookup };
  } catch (err) {
    return { kind: 'error', message: err instanceof Error ? err.message : String(err) };
  }
}

export default async function ResumesListPage() {
  const state = await loadFirstPage();

  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">全部定制简历</h1>
        <p className="mt-1 text-sm text-muted">
          基于匹配结果生成的针对性简历;待人工核查 / 失败的也会列出。
        </p>
      </header>

      {state.kind === 'ok' ? (
        <ResumesClient
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
