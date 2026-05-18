'use client';

import {
  AlertCircle,
  BriefcaseBusiness,
  CheckCircle2,
  FileText,
  Loader2,
  RefreshCw,
  Save,
  Search,
  Send,
  Tag,
  Trash2,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  ApiError,
  createJdLibraryItem,
  deleteJdLibraryItem,
  getJdLibraryItem,
  listJdLibrary,
  patchJdLibraryItem,
  type JdLibraryItem,
  type JdLibraryListItem,
  type JdParsedPayload,
} from '@/lib/api';
import { formatRelative } from '@/lib/format';
import { cn } from '@/lib/utils';

type AsyncState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'success'; message?: string }
  | { kind: 'error'; message: string };

const SAMPLE_JD = `岗位：Java 后端工程师
职责：
1. 负责核心交易链路的服务设计、开发和性能优化。
2. 参与微服务架构治理、接口稳定性建设和线上问题排查。
要求：
1. 熟悉 Java、Spring Boot、MySQL、Redis、消息队列。
2. 理解 JVM、并发编程、分布式事务或服务治理。
3. 有良好的沟通协作能力。`;
const JD_TEXT_MAX_LENGTH = 10_000;

export default function JdsPage() {
  const [rawText, setRawText] = useState('');
  const [titleFilter, setTitleFilter] = useState('');
  const [appliedTitle, setAppliedTitle] = useState('');
  const [items, setItems] = useState<JdLibraryListItem[]>([]);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [listState, setListState] = useState<AsyncState>({ kind: 'idle' });
  const [submitState, setSubmitState] = useState<AsyncState>({ kind: 'idle' });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<JdLibraryItem | null>(null);
  const [detailState, setDetailState] = useState<AsyncState>({ kind: 'idle' });
  const [editingTitle, setEditingTitle] = useState('');
  const [titleState, setTitleState] = useState<AsyncState>({ kind: 'idle' });
  const [deleteState, setDeleteState] = useState<AsyncState>({ kind: 'idle' });

  const loadList = useCallback(
    async (cursor: number | null = null, append = false) => {
      setListState({ kind: 'loading' });
      try {
        const data = await listJdLibrary({
          title: appliedTitle || undefined,
          cursor,
          limit: 20,
        });
        setItems((current) => (append ? [...current, ...data.items] : data.items));
        setNextCursor(data.next_cursor);
        setHasMore(data.has_more);
        setListState({ kind: 'idle' });
        const first = data.items[0];
        if (!append && first) {
          setSelectedId(first.id);
        }
        if (!append && !first) {
          setSelectedId(null);
          setSelectedDetail(null);
        }
      } catch (err) {
        setListState({ kind: 'error', message: errorMessage(err) });
      }
    },
    [appliedTitle],
  );

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    if (selectedId == null) {
      setSelectedDetail(null);
      setEditingTitle('');
      setDetailState({ kind: 'idle' });
      return;
    }

    const controller = new AbortController();
    setDetailState({ kind: 'loading' });
    getJdLibraryItem(selectedId, controller.signal)
      .then((detail) => {
        setSelectedDetail(detail);
        setEditingTitle(detail.title);
        setDetailState({ kind: 'idle' });
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setDetailState({ kind: 'error', message: errorMessage(err) });
      });
    return () => controller.abort();
  }, [selectedId]);

  const parsedPayload = selectedDetail?.parsed_payload;
  const stats = useMemo(() => buildStats(parsedPayload), [parsedPayload]);

  const handleCreate = async () => {
    const text = rawText.trim();
    if (!text) {
      setSubmitState({ kind: 'error', message: 'JD 原文不能为空' });
      return;
    }
    if (text.length > JD_TEXT_MAX_LENGTH) {
      setSubmitState({
        kind: 'error',
        message: `JD 原文超过 ${JD_TEXT_MAX_LENGTH} 字符`,
      });
      return;
    }
    setSubmitState({ kind: 'loading' });
    try {
      const created = await createJdLibraryItem({ source: 'text_paste', raw_text: text });
      setRawText('');
      setSubmitState({ kind: 'success', message: `已解析：${created.title}` });
      setSelectedId(created.id);
      setSelectedDetail(created);
      setEditingTitle(created.title);
      setItems((current) => [
        toListItem(created),
        ...current.filter((item) => item.id !== created.id),
      ]);
    } catch (err) {
      setSubmitState({ kind: 'error', message: errorMessage(err) });
    }
  };

  const applySearch = () => {
    setAppliedTitle(titleFilter.trim());
  };

  const clearSearch = () => {
    setTitleFilter('');
    setAppliedTitle('');
  };

  const saveTitle = async () => {
    if (!selectedDetail) return;
    setTitleState({ kind: 'loading' });
    try {
      const updated = await patchJdLibraryItem(selectedDetail.id, { title: editingTitle });
      setSelectedDetail(updated);
      setEditingTitle(updated.title);
      setItems((current) =>
        current.map((item) => (item.id === updated.id ? { ...item, title: updated.title } : item)),
      );
      setTitleState({ kind: 'success', message: '已保存' });
    } catch (err) {
      setTitleState({ kind: 'error', message: errorMessage(err) });
    }
  };

  const deleteSelected = async () => {
    if (!selectedDetail) return;
    const ok = window.confirm(`删除「${selectedDetail.title}」？`);
    if (!ok) return;
    setDeleteState({ kind: 'loading' });
    try {
      await deleteJdLibraryItem(selectedDetail.id);
      const remaining = items.filter((item) => item.id !== selectedDetail.id);
      setItems(remaining);
      setSelectedId(remaining[0]?.id ?? null);
      setSelectedDetail(null);
      setDeleteState({ kind: 'idle' });
    } catch (err) {
      setDeleteState({ kind: 'error', message: errorMessage(err) });
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border bg-surface px-5 py-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[13px] font-medium text-muted">
            <BriefcaseBusiness className="size-4" />
            JD Intelligence
          </div>
          <h1 className="mt-1 text-2xl font-semibold">我的 JD 库</h1>
        </div>
        <div className="flex items-center gap-2 text-[13px] text-muted">
          <span>{items.length} 条</span>
          {appliedTitle ? <span>title: {appliedTitle}</span> : null}
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[minmax(320px,420px)_minmax(0,1fr)]">
        <section className="min-h-0 overflow-y-auto border-b border-border bg-surface p-4 lg:border-r lg:border-b-0">
          <div className="rounded-lg border border-border bg-background p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold">上传 JD</h2>
                <p className="text-[13px] text-muted">文本粘贴</p>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setRawText(SAMPLE_JD)}
              >
                <FileText className="size-4" />
                样例
              </Button>
            </div>
            <Textarea
              value={rawText}
              onChange={(event) => setRawText(event.target.value)}
              placeholder="岗位：Java 后端工程师..."
              className="mt-4 min-h-[360px] resize-none rounded-lg bg-surface leading-relaxed"
            />
            <div className="mt-3 flex items-center justify-between gap-3">
              <span
                className={cn(
                  'text-xs',
                  rawText.trim().length > JD_TEXT_MAX_LENGTH
                    ? 'text-[var(--color-danger)]'
                    : 'text-muted',
                )}
              >
                {rawText.trim().length}/{JD_TEXT_MAX_LENGTH}
              </span>
              <Button
                type="button"
                onClick={handleCreate}
                disabled={
                  submitState.kind === 'loading' || rawText.trim().length > JD_TEXT_MAX_LENGTH
                }
              >
                {submitState.kind === 'loading' ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Send className="size-4" />
                )}
                解析入库
              </Button>
            </div>
            <StateLine state={submitState} className="mt-3" />
          </div>
        </section>

        <section className="grid min-h-0 grid-cols-1 overflow-hidden xl:grid-cols-[360px_minmax(0,1fr)]">
          <div className="flex min-h-0 flex-col border-b border-border bg-background xl:border-r xl:border-b-0">
            <div className="shrink-0 border-b border-border p-4">
              <div className="flex gap-2">
                <Input
                  value={titleFilter}
                  onChange={(event) => setTitleFilter(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') applySearch();
                  }}
                  placeholder="筛 title"
                  className="h-9 rounded-lg bg-surface"
                />
                <Button
                  type="button"
                  size="icon"
                  variant="outline"
                  onClick={applySearch}
                  title="筛选"
                >
                  <Search className="size-4" />
                </Button>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  onClick={clearSearch}
                  title="清空"
                >
                  <RefreshCw className="size-4" />
                </Button>
              </div>
              <StateLine state={listState} className="mt-2" />
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-2">
              {items.length === 0 && listState.kind !== 'loading' ? (
                <div className="px-3 py-10 text-center text-sm text-muted">暂无 JD</div>
              ) : null}
              <ul className="space-y-1">
                {items.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => setSelectedId(item.id)}
                      className={cn(
                        'w-full rounded-lg px-3 py-3 text-left transition-colors',
                        selectedId === item.id
                          ? 'bg-[var(--color-selection)] text-[var(--color-selection-fg)]'
                          : 'hover:bg-black/[0.04]',
                      )}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="truncate text-sm font-medium">{item.title}</div>
                          <div className="mt-1 line-clamp-2 text-xs text-muted">
                            {item.raw_text_preview}
                          </div>
                        </div>
                        <span className="shrink-0 text-[11px] text-muted">
                          {formatRelative(item.created_at)}
                        </span>
                      </div>
                      <div className="mt-2 flex items-center gap-2 text-[11px] text-muted">
                        <Tag className="size-3" />
                        <span>{item.hard_skills_count} hard skills</span>
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            </div>

            {hasMore ? (
              <div className="shrink-0 border-t border-border p-3">
                <Button
                  type="button"
                  variant="outline"
                  className="w-full"
                  disabled={listState.kind === 'loading'}
                  onClick={() => void loadList(nextCursor, true)}
                >
                  {listState.kind === 'loading' ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : null}
                  加载更多
                </Button>
              </div>
            ) : null}
          </div>

          <div className="min-h-0 overflow-y-auto bg-surface">
            {detailState.kind === 'loading' ? (
              <div className="flex h-full min-h-[320px] items-center justify-center text-sm text-muted">
                <Loader2 className="mr-2 size-4 animate-spin" />
                加载中
              </div>
            ) : null}

            {detailState.kind === 'error' ? (
              <div className="p-6">
                <StateLine state={detailState} />
              </div>
            ) : null}

            {!selectedDetail && detailState.kind !== 'loading' && detailState.kind !== 'error' ? (
              <div className="flex h-full min-h-[320px] items-center justify-center text-sm text-muted">
                选择一条 JD
              </div>
            ) : null}

            {selectedDetail ? (
              <div className="space-y-4 p-5">
                <div className="rounded-lg border border-border bg-background p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Input
                      value={editingTitle}
                      onChange={(event) => setEditingTitle(event.target.value)}
                      className="min-w-[220px] flex-1 rounded-lg bg-surface text-base font-medium"
                    />
                    <Button
                      type="button"
                      variant="outline"
                      onClick={saveTitle}
                      disabled={titleState.kind === 'loading'}
                    >
                      {titleState.kind === 'loading' ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <Save className="size-4" />
                      )}
                      保存
                    </Button>
                    <Button
                      type="button"
                      variant="destructive"
                      onClick={deleteSelected}
                      disabled={deleteState.kind === 'loading'}
                    >
                      {deleteState.kind === 'loading' ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <Trash2 className="size-4" />
                      )}
                      删除
                    </Button>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted">
                    <span>#{selectedDetail.id}</span>
                    <span>{selectedDetail.parse_model ?? 'model pending'}</span>
                    <span>{selectedDetail.parse_cost_cny ?? '0'} CNY</span>
                    <span>{formatRelative(selectedDetail.created_at)}</span>
                  </div>
                  <StateLine state={titleState} className="mt-2" />
                  <StateLine state={deleteState} className="mt-2" />
                </div>

                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  {stats.map((stat) => (
                    <div
                      key={stat.label}
                      className="rounded-lg border border-border bg-background p-3"
                    >
                      <div className="text-xs text-muted">{stat.label}</div>
                      <div className="mt-1 text-xl font-semibold">{stat.value}</div>
                    </div>
                  ))}
                </div>

                <ParsedSection title="硬技能" values={parsedPayload?.hard_skills} />
                <ParsedSection title="职责" values={parsedPayload?.responsibilities} />
                <ParsedSection title="软技能" values={parsedPayload?.soft_skills} />

                <div className="rounded-lg border border-border bg-background p-4">
                  <h3 className="text-sm font-semibold">其他字段</h3>
                  <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
                    <MetaItem label="经验" value={parsedPayload?.experience_years ?? null} />
                    <MetaItem label="学历" value={parsedPayload?.education ?? null} />
                    {Object.entries(parsedPayload?.extras ?? {}).map(([key, value]) => (
                      <MetaItem key={key} label={key} value={formatUnknown(value)} />
                    ))}
                  </dl>
                </div>

                <div className="rounded-lg border border-border bg-background p-4">
                  <h3 className="text-sm font-semibold">原文</h3>
                  <pre className="mt-3 max-h-[420px] overflow-auto whitespace-pre-wrap rounded-lg bg-surface p-3 text-[13px] leading-relaxed text-foreground">
                    {selectedDetail.raw_text}
                  </pre>
                </div>
              </div>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}

function StateLine({ state, className }: { state: AsyncState; className?: string }) {
  if (state.kind === 'idle') return null;
  if (state.kind === 'loading') {
    return (
      <div className={cn('flex items-center gap-2 text-xs text-muted', className)}>
        <Loader2 className="size-3 animate-spin" />
        处理中
      </div>
    );
  }
  if (state.kind === 'success') {
    return (
      <div
        className={cn(
          'flex items-center gap-2 text-xs text-[var(--color-success-fg)]',
          className,
        )}
      >
        <CheckCircle2 className="size-3" />
        {state.message ?? '完成'}
      </div>
    );
  }
  return (
    <div
      className={cn(
        'flex items-center gap-2 text-xs text-[var(--color-danger)]',
        className,
      )}
    >
      <AlertCircle className="size-3" />
      {state.message}
    </div>
  );
}

function ParsedSection({ title, values }: { title: string; values?: string[] }) {
  const visible = values?.filter(Boolean) ?? [];
  return (
    <section className="rounded-lg border border-border bg-background p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        <span className="text-xs text-muted">{visible.length}</span>
      </div>
      {visible.length ? (
        <ul className="mt-3 flex flex-wrap gap-2">
          {visible.map((item, index) => (
            <li
              key={`${item}-${index}`}
              className="rounded-md border border-border bg-surface px-2.5 py-1 text-[13px]"
            >
              {item}
            </li>
          ))}
        </ul>
      ) : (
        <div className="mt-3 text-sm text-muted">无</div>
      )}
    </section>
  );
}

function MetaItem({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="rounded-md bg-surface px-3 py-2">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="mt-1 break-words text-sm">{value || '无'}</dd>
    </div>
  );
}

function buildStats(payload?: JdParsedPayload) {
  return [
    { label: '硬技能', value: payload?.hard_skills?.length ?? 0 },
    { label: '职责', value: payload?.responsibilities?.length ?? 0 },
    { label: '软技能', value: payload?.soft_skills?.length ?? 0 },
    { label: '经验', value: payload?.experience_years || '无' },
  ];
}

function toListItem(item: JdLibraryItem): JdLibraryListItem {
  return {
    id: item.id,
    title: item.title,
    source: item.source,
    raw_text_preview: item.raw_text.replace(/\s+/g, ' ').slice(0, 200),
    hard_skills_count: item.parsed_payload.hard_skills?.length ?? 0,
    created_at: item.created_at,
  };
}

function formatUnknown(value: unknown): string {
  if (value == null) return '';
  if (Array.isArray(value)) return value.map(formatUnknown).filter(Boolean).join('、');
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return err.problem.detail ?? err.problem.title ?? `HTTP ${err.status}`;
  }
  return err instanceof Error ? err.message : String(err);
}
