'use client';

import {
  AlertCircle,
  BarChart3,
  BriefcaseBusiness,
  CheckCircle2,
  ClipboardList,
  ExternalLink,
  FileText,
  ListChecks,
  Loader2,
  PlayCircle,
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
  createJdAnalysis,
  createJdLibraryItem,
  deleteJdLibraryItem,
  getJdAnalysis,
  getJdLibraryItem,
  listJdAnalyses,
  listJdLibrary,
  observeJdAnalysis,
  patchJdLibraryItem,
  type AggregatedRequirement,
  type JdAnalysisFilter,
  type JdAnalysisListItem,
  type JdAnalysisReport,
  type JdAnalysisSseFrame,
  type JdLibraryItem,
  type JdLibraryListItem,
  type JdNoteMatchSummaryItem,
  type JdParsedPayload,
  type JdQuizTopicCandidate,
} from '@/lib/api';
import { formatRelative } from '@/lib/format';
import { cn } from '@/lib/utils';

type AsyncState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'success'; message?: string }
  | { kind: 'error'; message: string };

type ActiveView = 'jd' | 'analysis';
type AnalysisScope = 'all' | 'recent' | 'title' | 'selected';
type AnalysisInput = { filter: JdAnalysisFilter; description: string };
type TopicPriorityFilter = 'all' | 'high' | 'medium' | 'low' | string;
type CoverageStatusFilter = 'all' | 'covered' | 'partial' | 'missing' | 'unknown' | string;

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
  const [activeView, setActiveView] = useState<ActiveView>('jd');
  const [analysisScope, setAnalysisScope] = useState<AnalysisScope>('all');
  const [recentCount, setRecentCount] = useState(50);
  const [analysisItems, setAnalysisItems] = useState<JdAnalysisListItem[]>([]);
  const [selectedAnalysisId, setSelectedAnalysisId] = useState<number | null>(null);
  const [selectedAnalysis, setSelectedAnalysis] = useState<JdAnalysisReport | null>(null);
  const [analysisListState, setAnalysisListState] = useState<AsyncState>({ kind: 'idle' });
  const [analysisDetailState, setAnalysisDetailState] = useState<AsyncState>({ kind: 'idle' });
  const [analysisRunState, setAnalysisRunState] = useState<AsyncState>({ kind: 'idle' });
  const [analysisProgress, setAnalysisProgress] = useState<string[]>([]);

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

  const loadAnalyses = useCallback(async () => {
    setAnalysisListState({ kind: 'loading' });
    try {
      const data = await listJdAnalyses({ limit: 20 });
      setAnalysisItems(data.items);
      setAnalysisListState({ kind: 'idle' });
    } catch (err) {
      setAnalysisListState({ kind: 'error', message: errorMessage(err) });
    }
  }, []);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    void loadAnalyses();
  }, [loadAnalyses]);

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

  useEffect(() => {
    if (selectedAnalysisId == null) {
      setSelectedAnalysis(null);
      setAnalysisDetailState({ kind: 'idle' });
      return;
    }

    const controller = new AbortController();
    setSelectedAnalysis(null);
    setAnalysisDetailState({ kind: 'loading' });
    getJdAnalysis(selectedAnalysisId, controller.signal)
      .then((detail) => {
        setSelectedAnalysis(detail);
        setAnalysisDetailState({ kind: 'idle' });
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setAnalysisDetailState({ kind: 'error', message: errorMessage(err) });
      });
    return () => controller.abort();
  }, [selectedAnalysisId]);

  const parsedPayload = selectedDetail?.parsed_payload;
  const stats = useMemo(() => buildStats(parsedPayload), [parsedPayload]);
  const cleanedRawText = useMemo(() => normalizeJdPaste(rawText), [rawText]);
  const rawTextIsCleanable = rawText.length > 0 && cleanedRawText !== rawText;
  const likelyDuplicate = useMemo(
    () => findLikelyDuplicateJd(cleanedRawText, items),
    [cleanedRawText, items],
  );

  const handleCreate = async () => {
    const text = cleanedRawText.trim();
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
    if (
      likelyDuplicate &&
      !window.confirm(`列表里已有相似 JD「${likelyDuplicate.title}」，仍然继续入库？`)
    ) {
      return;
    }
    setSubmitState({ kind: 'loading' });
    try {
      const created = await createJdLibraryItem({ source: 'text_paste', raw_text: text });
      setRawText('');
      setSubmitState({ kind: 'success', message: `已解析：${created.title}` });
      setSelectedId(created.id);
      setActiveView('jd');
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
      setActiveView('jd');
      setSelectedDetail(null);
      setDeleteState({ kind: 'idle' });
    } catch (err) {
      setDeleteState({ kind: 'error', message: errorMessage(err) });
    }
  };

  const buildAnalysisInput = (): AnalysisInput | null => {
    if (analysisScope === 'all') {
      return { filter: { type: 'all' }, description: '全部 JD' };
    }
    if (analysisScope === 'recent') {
      const n = Math.max(1, Math.min(200, Math.floor(recentCount || 1)));
      return { filter: { type: 'recent', n }, description: `最近 ${n} 条 JD` };
    }
    if (analysisScope === 'title') {
      const value = (appliedTitle || titleFilter).trim();
      if (!value) {
        setAnalysisRunState({ kind: 'error', message: '先输入 title 筛选词' });
        return null;
      }
      return { filter: { type: 'title', value }, description: `title: ${value}` };
    }
    if (selectedId == null) {
      setAnalysisRunState({ kind: 'error', message: '先选中一条 JD' });
      return null;
    }
    return {
      filter: { type: 'ids', ids: [selectedId] },
      description: selectedDetail?.title ? `单条: ${selectedDetail.title}` : `单条 JD #${selectedId}`,
    };
  };

  const appendAnalysisProgress = (message: string) => {
    setAnalysisProgress((current) => [...current.slice(-7), message]);
  };

  const runAnalysis = async () => {
    const input = buildAnalysisInput();
    if (!input) return;
    setAnalysisRunState({ kind: 'loading' });
    setAnalysisProgress([]);
    let resultAnalysisId: number | null = null;

    const consumeAnalysisEvents = async (
      stream: AsyncGenerator<JdAnalysisSseFrame>,
    ) => {
      for await (const frame of stream) {
        if (frame.event === 'started') {
          resultAnalysisId = frame.data.resource_id;
          appendAnalysisProgress(`报告 #${frame.data.resource_id} 已创建`);
        } else if (frame.event === 'progress') {
          appendAnalysisProgress(formatAnalysisPhase(frame.data));
        } else if (frame.event === 'result') {
          resultAnalysisId = frame.data.analysis_id;
          appendAnalysisProgress(
            `${frame.data.requirement_count} 个要求 / ${frame.data.quiz_topic_count} 个 topic`,
          );
        } else if (frame.event === 'error') {
          throw new Error(frame.data.detail || frame.data.code);
        } else if (frame.event === 'done' && !frame.data.ok) {
          throw new Error('分析未完成');
        }
      }
    };

    try {
      try {
        await consumeAnalysisEvents(
          createJdAnalysis({
            filter: input.filter,
            filter_description: input.description,
          }),
        );
      } catch (firstError) {
        if (resultAnalysisId == null) throw firstError;
        appendAnalysisProgress(`连接中断，正在恢复报告 #${resultAnalysisId}`);
        await consumeAnalysisEvents(observeJdAnalysis(resultAnalysisId));
      }
      setAnalysisRunState({ kind: 'success', message: '分析完成' });
      await loadAnalyses();
      if (resultAnalysisId != null) {
        setSelectedAnalysisId(resultAnalysisId);
        setActiveView('analysis');
      }
    } catch (err) {
      setAnalysisRunState({ kind: 'error', message: errorMessage(err) });
      await loadAnalyses();
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

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[minmax(360px,460px)_minmax(0,1fr)]">
        <section className="min-h-0 overflow-y-auto border-b border-border bg-surface p-4 lg:border-r lg:border-b-0">
          <div className="rounded-lg border border-border bg-background p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold">上传 JD</h2>
                <p className="text-[13px] text-muted">文本粘贴</p>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setRawText(cleanedRawText)}
                  disabled={!rawTextIsCleanable}
                >
                  <RefreshCw className="size-4" />
                  清洗
                </Button>
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
            </div>
            <Textarea
              value={rawText}
              onChange={(event) => setRawText(event.target.value)}
              placeholder="岗位：Java 后端工程师..."
              className="mt-4 min-h-[180px] resize-y rounded-lg bg-surface leading-relaxed"
            />
            {rawTextIsCleanable || likelyDuplicate ? (
              <div className="mt-2 space-y-1 text-xs text-muted">
                {rawTextIsCleanable ? <div>可清洗复制格式符和多余空行</div> : null}
                {likelyDuplicate ? <div>可能重复：{likelyDuplicate.title}</div> : null}
              </div>
            ) : null}
            <div className="mt-3 flex items-center justify-between gap-3">
              <span
                className={cn(
                  'text-xs',
                  cleanedRawText.length > JD_TEXT_MAX_LENGTH
                    ? 'text-[var(--color-danger)]'
                    : 'text-muted',
                )}
              >
                {cleanedRawText.length}/{JD_TEXT_MAX_LENGTH}
              </span>
              <Button
                type="button"
                onClick={handleCreate}
                disabled={
                  submitState.kind === 'loading' || cleanedRawText.length > JD_TEXT_MAX_LENGTH
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

          <div className="mt-4 rounded-lg border border-border bg-background p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold">一键分析</h2>
                <p className="text-[13px] text-muted">聚合岗位要求</p>
              </div>
              <Button
                type="button"
                onClick={runAnalysis}
                disabled={analysisRunState.kind === 'loading'}
              >
                {analysisRunState.kind === 'loading' ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <PlayCircle className="size-4" />
                )}
                开始
              </Button>
            </div>

            <div className="mt-4 grid gap-2">
              <label className="text-xs font-medium text-muted" htmlFor="analysis-scope">
                范围
              </label>
              <select
                id="analysis-scope"
                value={analysisScope}
                onChange={(event) => setAnalysisScope(event.target.value as AnalysisScope)}
                className="h-9 rounded-lg border border-input bg-surface px-3 text-sm outline-none"
              >
                <option value="all">全部 JD</option>
                <option value="recent">最近 N 条</option>
                <option value="title">title 筛选</option>
                <option value="selected">当前选中</option>
              </select>
              {analysisScope === 'recent' ? (
                <Input
                  type="number"
                  min={1}
                  max={200}
                  value={recentCount}
                  onChange={(event) => setRecentCount(Number(event.target.value))}
                  className="h-9 rounded-lg bg-surface"
                />
              ) : null}
            </div>

            <StateLine state={analysisRunState} className="mt-3" />
            {analysisProgress.length ? (
              <ul className="mt-3 space-y-1 text-xs text-muted">
                {analysisProgress.map((item, index) => (
                  <li key={`${item}-${index}`} className="flex items-center gap-2">
                    <CheckCircle2 className="size-3" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>

          <div className="mt-4 rounded-lg border border-border bg-background">
            <div className="border-b border-border p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <h2 className="text-base font-semibold">JD 列表</h2>
                <span className="text-xs text-muted">{items.length} 条</span>
              </div>
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

            <div className="max-h-[340px] overflow-y-auto p-2">
              {items.length === 0 && listState.kind !== 'loading' ? (
                <div className="px-3 py-10 text-center text-sm text-muted">暂无 JD</div>
              ) : null}
              <ul className="space-y-1">
                {items.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedId(item.id);
                        setActiveView('jd');
                      }}
                      className={cn(
                        'w-full rounded-lg px-3 py-3 text-left transition-colors',
                        activeView === 'jd' && selectedId === item.id
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
              <div className="border-t border-border p-3">
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

          <div className="mt-4 rounded-lg border border-border bg-background p-4">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-sm font-semibold">
                <ClipboardList className="size-4" />
                历史报告
              </div>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                title="刷新报告"
                onClick={() => void loadAnalyses()}
              >
                <RefreshCw className="size-4" />
              </Button>
            </div>
            <StateLine state={analysisListState} className="mt-2" />
            <div className="mt-2 max-h-[260px] space-y-1 overflow-y-auto">
              {analysisItems.length === 0 && analysisListState.kind !== 'loading' ? (
                <div className="py-4 text-center text-sm text-muted">暂无报告</div>
              ) : null}
              {analysisItems.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => {
                    setSelectedAnalysisId(item.id);
                    setActiveView('analysis');
                  }}
                  className={cn(
                    'w-full rounded-lg px-3 py-2 text-left transition-colors',
                    activeView === 'analysis' && selectedAnalysisId === item.id
                      ? 'bg-[var(--color-selection)] text-[var(--color-selection-fg)]'
                      : 'hover:bg-black/[0.04]',
                  )}
                >
                  <div className="flex items-center justify-between gap-2 text-sm">
                    <span>报告 #{item.id}</span>
                    <StatusPill status={item.status} />
                  </div>
                  <div className="mt-1 truncate text-xs text-muted">
                    {item.filter_description ?? '未命名范围'} · {item.jd_count} 条 JD
                  </div>
                  <div className="mt-1 text-[11px] text-muted">
                    {item.requirement_count} 要求 / {item.quiz_topic_count} topics
                  </div>
                </button>
              ))}
            </div>
          </div>
        </section>

        <section className="min-h-0 overflow-y-auto bg-surface">
            {activeView === 'analysis' && analysisDetailState.kind === 'loading' ? (
              <div className="flex h-full min-h-[320px] items-center justify-center text-sm text-muted">
                <Loader2 className="mr-2 size-4 animate-spin" />
                加载中
              </div>
            ) : null}

            {activeView === 'analysis' && analysisDetailState.kind === 'error' ? (
              <div className="p-6">
                <StateLine state={analysisDetailState} />
              </div>
            ) : null}

            {activeView === 'analysis' &&
            !selectedAnalysis &&
            analysisDetailState.kind !== 'loading' &&
            analysisDetailState.kind !== 'error' ? (
              <div className="flex h-full min-h-[320px] items-center justify-center text-sm text-muted">
                选择一份报告
              </div>
            ) : null}

            {activeView === 'analysis' && selectedAnalysis ? (
              <AnalysisReportView report={selectedAnalysis} />
            ) : null}

            {activeView === 'jd' && detailState.kind === 'loading' ? (
              <div className="flex h-full min-h-[320px] items-center justify-center text-sm text-muted">
                <Loader2 className="mr-2 size-4 animate-spin" />
                加载中
              </div>
            ) : null}

            {activeView === 'jd' && detailState.kind === 'error' ? (
              <div className="p-6">
                <StateLine state={detailState} />
              </div>
            ) : null}

            {activeView === 'jd' &&
            !selectedDetail &&
            detailState.kind !== 'loading' &&
            detailState.kind !== 'error' ? (
              <div className="flex h-full min-h-[320px] items-center justify-center text-sm text-muted">
                选择一条 JD
              </div>
            ) : null}

            {activeView === 'jd' && selectedDetail ? (
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

function StatusPill({ status }: { status: string }) {
  const label =
    status === 'done' ? '完成' : status === 'failed' ? '失败' : status === 'in_progress' ? '进行中' : status;
  const tone =
    status === 'done'
      ? 'border-[var(--color-success-border)] text-[var(--color-success-fg)]'
      : status === 'failed'
        ? 'border-[var(--color-danger)] text-[var(--color-danger)]'
        : 'border-border text-muted';
  return (
    <span className={cn('rounded-md border px-2 py-0.5 text-[11px]', tone)}>
      {label}
    </span>
  );
}

function AnalysisReportView({ report }: { report: JdAnalysisReport }) {
  const [requirementCategory, setRequirementCategory] = useState('all');
  const [requirementQuery, setRequirementQuery] = useState('');
  const [topicPriority, setTopicPriority] = useState<TopicPriorityFilter>('all');

  const requirementCategories = useMemo(
    () => ['all', ...Array.from(new Set(report.aggregated_requirements.map((req) => req.category)))],
    [report.aggregated_requirements],
  );
  const filteredRequirements = useMemo(() => {
    const query = requirementQuery.trim().toLowerCase();
    return report.aggregated_requirements.filter((req) => {
      const categoryMatch = requirementCategory === 'all' || req.category === requirementCategory;
      const queryMatch =
        !query ||
        req.canonical_text.toLowerCase().includes(query) ||
        req.raw_phrases.some((phrase) => phrase.toLowerCase().includes(query));
      return categoryMatch && queryMatch;
    });
  }, [report.aggregated_requirements, requirementCategory, requirementQuery]);
  const topRequirements = filteredRequirements.slice(0, 50);
  const topicPriorities = useMemo(
    () => ['all', ...Array.from(new Set(report.quiz_topic_candidates.map((topic) => topic.priority)))],
    [report.quiz_topic_candidates],
  );
  const filteredTopics = useMemo(
    () =>
      report.quiz_topic_candidates.filter(
        (topic) => topicPriority === 'all' || topic.priority === topicPriority,
      ),
    [report.quiz_topic_candidates, topicPriority],
  );
  const batchTopic = filteredTopics
    .slice(0, 5)
    .map((topic) => topic.topic)
    .join('、');
  return (
    <div className="space-y-4 p-5">
      <div className="rounded-lg border border-border bg-background p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[13px] font-medium text-muted">
              <BarChart3 className="size-4" />
              JD 分析报告
            </div>
            <h2 className="mt-1 truncate text-xl font-semibold">报告 #{report.id}</h2>
            <div className="mt-2 text-sm text-muted">{report.filter_description ?? '未命名范围'}</div>
          </div>
          <StatusPill status={report.status} />
        </div>
        {report.failure_reason ? (
          <div className="mt-3 rounded-lg border border-[var(--color-danger)] px-3 py-2 text-sm text-[var(--color-danger)]">
            {report.failure_reason}
          </div>
        ) : null}
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="JD 数" value={report.jd_count} />
        <Metric label="要求数" value={report.aggregated_requirements.length} />
        <Metric label="Topics" value={report.quiz_topic_candidates.length} />
        <Metric label="成本" value={`${report.total_cost_cny ?? 0} CNY`} />
      </div>

      <CoverageAnalysisSection
        items={report.note_match_summary}
        requirements={report.aggregated_requirements}
      />

      <section className="rounded-lg border border-border bg-background p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <ListChecks className="size-4" />
            岗位要求地图
          </h3>
          <span className="text-xs text-muted">
            {topRequirements.length}/{filteredRequirements.length}
          </span>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <select
            value={requirementCategory}
            onChange={(event) => setRequirementCategory(event.target.value)}
            className="h-9 rounded-lg border border-input bg-surface px-3 text-sm outline-none"
          >
            {requirementCategories.map((category) => (
              <option key={category} value={category}>
                {category === 'all' ? '全部类别' : category}
              </option>
            ))}
          </select>
          <Input
            value={requirementQuery}
            onChange={(event) => setRequirementQuery(event.target.value)}
            placeholder="搜要求或原文短语"
            className="h-9 min-w-[220px] flex-1 rounded-lg bg-surface"
          />
        </div>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[680px] text-left text-sm">
            <thead className="border-b border-border text-xs text-muted">
              <tr>
                <th className="py-2 pr-3 font-medium">要求</th>
                <th className="py-2 pr-3 font-medium">类别</th>
                <th className="py-2 pr-3 font-medium">频次</th>
                <th className="py-2 pr-3 font-medium">证据 JD</th>
                <th className="py-2 font-medium">原文短语</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {topRequirements.length ? (
                topRequirements.map((req) => <RequirementRow key={req.id} req={req} />)
              ) : (
                <tr>
                  <td colSpan={5} className="py-8 text-center text-muted">
                    没有匹配的要求
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="rounded-lg border border-border bg-background p-4">
        <h3 className="text-sm font-semibold">学习路径</h3>
        <pre className="mt-3 max-h-[520px] overflow-auto whitespace-pre-wrap rounded-lg bg-surface p-3 text-[13px] leading-relaxed text-foreground">
          {report.learning_path_md || '暂无'}
        </pre>
      </section>

      <section className="rounded-lg border border-border bg-background p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="text-sm font-semibold">Quiz Topics</h3>
          <div className="flex items-center gap-2">
            <select
              value={topicPriority}
              onChange={(event) => setTopicPriority(event.target.value)}
              className="h-8 rounded-lg border border-input bg-surface px-2 text-xs outline-none"
            >
              {topicPriorities.map((priority) => (
                <option key={priority} value={priority}>
                  {priority === 'all' ? '全部优先级' : priority}
                </option>
              ))}
            </select>
            <a
              href={batchTopic ? `/quiz?topic=${encodeURIComponent(batchTopic)}` : undefined}
              className={cn(
                'inline-flex h-8 items-center gap-1 rounded-md border border-border bg-surface px-2 text-xs hover:bg-black/[0.04]',
                !batchTopic && 'pointer-events-none opacity-50',
              )}
            >
              <ExternalLink className="size-3" />
              批量练习
            </a>
          </div>
        </div>
        <div className="mt-2 text-xs text-muted">{filteredTopics.length} 个 topic</div>
        <div className="mt-3 grid gap-2">
          {filteredTopics.length ? (
            filteredTopics.map((topic, index) => (
              <QuizTopicItem key={`${topic.topic}-${index}`} topic={topic} />
            ))
          ) : (
            <div className="text-sm text-muted">暂无</div>
          )}
        </div>
      </section>
    </div>
  );
}

function CoverageAnalysisSection({
  items,
  requirements,
}: {
  items: JdNoteMatchSummaryItem[];
  requirements: AggregatedRequirement[];
}) {
  const [statusFilter, setStatusFilter] = useState<CoverageStatusFilter>('all');
  const counts = useMemo(() => buildCoverageCounts(items), [items]);
  const requirementById = useMemo(
    () =>
      new Map<string, AggregatedRequirement>(
        requirements.map((req) => [req.id, req] as [string, AggregatedRequirement]),
      ),
    [requirements],
  );
  const statuses = useMemo(
    () => ['all', ...Array.from(new Set(items.map((item) => item.status || 'unknown')))],
    [items],
  );
  const visibleItems = useMemo(
    () =>
      items
        .filter((item) => statusFilter === 'all' || item.status === statusFilter)
        .slice(0, 40),
    [items, statusFilter],
  );

  return (
    <section className="rounded-lg border border-border bg-background p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Search className="size-4" />
          知识库覆盖分析
        </h3>
        <select
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value)}
          className="h-8 rounded-lg border border-input bg-surface px-2 text-xs outline-none"
        >
          {statuses.map((status) => (
            <option key={status} value={status}>
              {status === 'all' ? '全部状态' : coverageStatusLabel(status)}
            </option>
          ))}
        </select>
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-4">
        <CoverageMetric label="已覆盖" value={counts.covered} tone="covered" />
        <CoverageMetric label="部分覆盖" value={counts.partial} tone="partial" />
        <CoverageMetric label="缺口" value={counts.missing} tone="missing" />
        <CoverageMetric label="未知" value={counts.unknown} tone="unknown" />
      </div>

      <CoverageGapSummary items={items} requirementById={requirementById} />

      <div className="mt-3 space-y-2">
        {visibleItems.length ? (
          visibleItems.map((item) => <CoverageItem key={item.req_id} item={item} />)
        ) : (
          <div className="rounded-lg border border-dashed border-border px-3 py-8 text-center text-sm text-muted">
            没有匹配的覆盖项
          </div>
        )}
      </div>
    </section>
  );
}

function CoverageGapSummary({
  items,
  requirementById,
}: {
  items: JdNoteMatchSummaryItem[];
  requirementById: Map<string, AggregatedRequirement>;
}) {
  const gaps = useMemo(
    () =>
      items
        .filter((item) => item.status === 'missing' || item.status === 'partial')
        .map((item) => ({
          item,
          requirement: requirementById.get(item.req_id),
        }))
        .sort((left, right) => {
          const statusDelta = coverageGapRank(left.item.status) - coverageGapRank(right.item.status);
          if (statusDelta !== 0) return statusDelta;
          return (right.requirement?.frequency ?? 0) - (left.requirement?.frequency ?? 0);
        })
        .slice(0, 8),
    [items, requirementById],
  );

  if (!gaps.length) {
    return (
      <div className="mt-3 rounded-lg border border-border bg-surface px-3 py-3 text-sm text-muted">
        暂无显著知识库缺口
      </div>
    );
  }

  return (
    <div className="mt-3 rounded-lg border border-border bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <AlertCircle className="size-4 text-[var(--color-danger)]" />
          优先补齐
        </div>
        <span className="text-xs text-muted">{gaps.length} 项</span>
      </div>
      <div className="divide-y divide-border">
        {gaps.map(({ item, requirement }) => {
          const topic = item.canonical_text ?? requirement?.canonical_text ?? item.req_id;
          const phrases = item.matched_phrases?.length
            ? item.matched_phrases
            : requirement?.raw_phrases ?? [];
          const evidenceCount = item.evidence_chunks?.length ?? 0;
          return (
            <div key={item.req_id} className="flex flex-wrap items-center gap-3 px-3 py-2.5">
              <div className="min-w-[180px] flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <div className="max-w-full truncate text-sm font-medium">{topic}</div>
                  <span
                    className={cn(
                      'rounded-md border px-2 py-0.5 text-[11px]',
                      coverageTone(item.status),
                    )}
                  >
                    {coverageStatusLabel(item.status)}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
                  <span>频次 {formatPercent(requirement?.frequency ?? 0)}</span>
                  <span>证据 {evidenceCount} 段</span>
                  {phrases.length ? <span>{phrases.slice(0, 3).join(' / ')}</span> : null}
                </div>
              </div>
              <a
                href={`/quiz?topic=${encodeURIComponent(topic)}`}
                className="inline-flex h-8 items-center gap-1 rounded-md border border-border bg-background px-2 text-xs hover:bg-black/[0.04]"
              >
                <ExternalLink className="size-3" />
                练习
              </a>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CoverageMetric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: string;
}) {
  return (
    <div className={cn('rounded-lg border px-3 py-2', coverageTone(tone))}>
      <div className="text-xs">{label}</div>
      <div className="mt-1 text-lg font-semibold">{value}</div>
    </div>
  );
}

function CoverageItem({ item }: { item: JdNoteMatchSummaryItem }) {
  const evidence = item.evidence_chunks ?? [];
  const notes = item.matched_notes ?? [];
  const phrases = item.matched_phrases ?? [];
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">{item.canonical_text ?? item.req_id}</div>
          <div className="mt-1 text-xs text-muted">
            {phrases.length ? `命中：${phrases.slice(0, 4).join(' / ')}` : '暂无命中短语'}
          </div>
        </div>
        <span className={cn('rounded-md border px-2 py-0.5 text-[11px]', coverageTone(item.status))}>
          {coverageStatusLabel(item.status)}
        </span>
      </div>
      {evidence.length ? (
        <div className="mt-3 space-y-2">
          {evidence.slice(0, 2).map((chunk) => (
            <div key={chunk.chunk_id} className="rounded-md border border-border bg-background p-2">
              <div className="truncate text-xs font-medium">
                {chunk.note_title}
                {chunk.heading_path.length ? ` / ${chunk.heading_path.join(' / ')}` : ''}
              </div>
              <div className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted">
                {chunk.snippet}
              </div>
            </div>
          ))}
        </div>
      ) : notes.length ? (
        <div className="mt-2 text-xs text-muted">
          匹配笔记：{notes.slice(0, 3).map((note) => note.title).join(' / ')}
        </div>
      ) : null}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-border bg-background p-3">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 truncate text-xl font-semibold">{value}</div>
    </div>
  );
}

function RequirementRow({ req }: { req: AggregatedRequirement }) {
  return (
    <tr className="align-top">
      <td className="max-w-[260px] py-2 pr-3 font-medium">{req.canonical_text}</td>
      <td className="py-2 pr-3 text-muted">{req.category}</td>
      <td className="py-2 pr-3">{formatPercent(req.frequency)}</td>
      <td className="py-2 pr-3 text-muted">
        {req.supporting_jd_ids.slice(0, 8).map((id) => `#${id}`).join(' ')}
        {req.supporting_jd_ids.length > 8 ? ' ...' : ''}
      </td>
      <td className="max-w-[280px] py-2 text-muted">
        {req.raw_phrases.slice(0, 3).join(' / ')}
      </td>
    </tr>
  );
}

function QuizTopicItem({ topic }: { topic: JdQuizTopicCandidate }) {
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{topic.topic}</div>
          <div className="mt-1 text-xs text-muted">
            {topic.priority} · {formatPercent(topic.frequency)} · {topic.note_match_status}
          </div>
        </div>
        <a
          href={`/quiz?topic=${encodeURIComponent(topic.topic)}`}
          className="inline-flex h-8 items-center gap-1 rounded-md border border-border bg-background px-2 text-xs hover:bg-black/[0.04]"
        >
          <ExternalLink className="size-3" />
          练习
        </a>
      </div>
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

function formatAnalysisPhase(data: { phase: string; batch?: number; total?: number }) {
  const labels: Record<string, string> = {
    loading_parsed: '读取 JD 快照',
    reducing_batch: '合并要求',
    merging: '二次合并',
    frequency_recompute: '重算频次',
    learning_path_gen: '生成学习路径',
    note_matching: '匹配笔记',
    quiz_topic_generating: '生成 topic',
  };
  const base = labels[data.phase] ?? data.phase;
  if (data.phase === 'reducing_batch' && data.batch && data.total) {
    return `${base} ${data.batch}/${data.total}`;
  }
  return base;
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function buildCoverageCounts(items: JdNoteMatchSummaryItem[]) {
  return items.reduce(
    (acc, item) => {
      if (item.status === 'covered') acc.covered += 1;
      else if (item.status === 'partial') acc.partial += 1;
      else if (item.status === 'missing') acc.missing += 1;
      else acc.unknown += 1;
      return acc;
    },
    { covered: 0, partial: 0, missing: 0, unknown: 0 },
  );
}

function coverageGapRank(status: string): number {
  if (status === 'missing') return 0;
  if (status === 'partial') return 1;
  return 2;
}

function coverageStatusLabel(status: string): string {
  if (status === 'covered') return '已覆盖';
  if (status === 'partial') return '部分覆盖';
  if (status === 'missing') return '缺口';
  if (status === 'unknown') return '未知';
  return status;
}

function coverageTone(status: string): string {
  if (status === 'covered') {
    return 'border-[var(--color-success-border)] bg-[var(--color-success-bg)] text-[var(--color-success-fg)]';
  }
  if (status === 'partial') {
    return 'border-[var(--color-warning-border)] bg-[var(--color-warning-bg)] text-[var(--color-warning-fg)]';
  }
  if (status === 'missing') {
    return 'border-[var(--color-danger)] bg-background text-[var(--color-danger)]';
  }
  return 'border-border bg-background text-muted';
}

function normalizeJdPaste(value: string): string {
  return value
    .replace(/\r\n?/g, '\n')
    .replace(/\u00a0/g, ' ')
    .split('\n')
    .map((line) => line.trim())
    .filter((line, index, lines) => line || lines[index - 1])
    .join('\n')
    .trim();
}

function normalizeForCompare(value: string): string {
  return normalizeJdPaste(value).replace(/\s+/g, '').toLowerCase();
}

function findLikelyDuplicateJd(
  text: string,
  items: JdLibraryListItem[],
): JdLibraryListItem | null {
  const normalized = normalizeForCompare(text);
  if (normalized.length < 80) return null;
  const prefix = normalized.slice(0, 160);
  return (
    items.find((item) => {
      const preview = normalizeForCompare(item.raw_text_preview);
      return preview.length >= 80 && (prefix.startsWith(preview) || preview.startsWith(prefix));
    }) ?? null
  );
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return err.problem.detail ?? err.problem.title ?? `HTTP ${err.status}`;
  }
  return err instanceof Error ? err.message : String(err);
}
