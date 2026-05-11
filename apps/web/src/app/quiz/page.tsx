'use client';

import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Play,
  RotateCcw,
  Send,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import {
  ApiError,
  abandonQuizSession,
  createQuizSession,
  saveQuizAnswer,
  submitQuizSession,
  type QuizEvidence,
  type QuizProgress,
  type QuizQuestionReady,
  type QuizScores,
  type QuizTypeMix,
} from '@/lib/api';
import { cn } from '@/lib/utils';

type Stage = 'idle' | 'generating' | 'answering' | 'submitting' | 'submitted';
type SaveState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'saved' }
  | { kind: 'error'; message: string };

type ProgressItem = {
  id: string;
  label: string;
  detail?: string;
};

type QuestionResult = {
  scores: QuizScores;
  evidence: QuizEvidence;
};

const QUESTION_COUNTS = [3, 5, 7, 10] as const;

const PHASE_LABELS: Record<string, string> = {
  query_rewriting: '理解主题',
  query_rewriting_done: '扩展检索词',
  hybrid_searching: '全库召回',
  reranking: '重排候选',
  parent_doc_expanding: '展开上下文',
  generating: '生成题目',
  type_mix_decided: '确定题型',
  judging: '评分中',
};

function problemMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const code = err.problem.code ?? err.status;
    return `${code}: ${err.problem.detail ?? err.message}`;
  }
  return err instanceof Error ? err.message : String(err);
}

function progressLabel(progress: QuizProgress): ProgressItem {
  const label = PHASE_LABELS[progress.phase] ?? progress.phase;
  const details: string[] = [];
  if (progress.expanded_queries?.length) details.push(progress.expanded_queries.join(' / '));
  if (typeof progress.candidate_count === 'number') details.push(`${progress.candidate_count} 个候选`);
  if (typeof progress.chunk_count === 'number') details.push(`${progress.chunk_count} 个 chunks`);
  if (progress.model) details.push(progress.model);
  if (typeof progress.order_index === 'number') details.push(`第 ${progress.order_index + 1} 题`);
  return {
    id: `${progress.phase}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    label,
    detail: details.join(' · ') || undefined,
  };
}

function appendProgress(items: ProgressItem[], next: ProgressItem): ProgressItem[] {
  return [...items.slice(-8), next];
}

function scoreTone(score: number): string {
  if (score >= 80) return 'bg-[var(--color-success-fg)]';
  if (score >= 60) return 'bg-accent';
  return 'bg-[var(--color-warning-fg)]';
}

function roundedScore(score: number | undefined): string {
  if (typeof score !== 'number' || Number.isNaN(score)) return '-';
  return String(Math.round(score));
}

function evidenceReasoning(evidence: QuizEvidence, key: keyof QuizEvidence): string | null {
  const value = evidence[key];
  if (!value || typeof value !== 'object') return null;
  const reasoning = (value as { reasoning?: unknown }).reasoning;
  return typeof reasoning === 'string' && reasoning.trim() ? reasoning : null;
}

export default function QuizPage() {
  const [query, setQuery] = useState('');
  const [questionCount, setQuestionCount] = useState<number>(5);
  const [stage, setStage] = useState<Stage>('idle');
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [questions, setQuestions] = useState<QuizQuestionReady[]>([]);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [progressItems, setProgressItems] = useState<ProgressItem[]>([]);
  const [typeMix, setTypeMix] = useState<QuizTypeMix | null>(null);
  const [questionResults, setQuestionResults] = useState<Record<number, QuestionResult>>({});
  const [finalResult, setFinalResult] = useState<{
    scores: QuizScores;
    recallMdPath?: string | null;
  } | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>({ kind: 'idle' });
  const savedAnswersRef = useRef<Record<number, string>>({});

  const answeredCount = useMemo(
    () => questions.filter((q) => (answers[q.order_index] ?? '').trim()).length,
    [answers, questions],
  );

  const canStart = query.trim().length > 0 && (stage === 'idle' || stage === 'submitted');
  const controlsLocked = stage !== 'idle' && stage !== 'submitted';
  const canSubmit =
    stage === 'answering' &&
    questions.length > 0 &&
    answeredCount === questions.length &&
    sessionId !== null;

  const resetSession = useCallback(() => {
    setStage('idle');
    setSessionId(null);
    setQuestions([]);
    setAnswers({});
    setProgressItems([]);
    setTypeMix(null);
    setQuestionResults({});
    setFinalResult(null);
    setRunError(null);
    setSaveState({ kind: 'idle' });
    savedAnswersRef.current = {};
  }, []);

  const saveAllAnswers = useCallback(async () => {
    if (sessionId === null) return;
    for (const q of questions) {
      const text = answers[q.order_index] ?? '';
      await saveQuizAnswer(sessionId, q.order_index, text);
      savedAnswersRef.current[q.order_index] = text;
    }
    setSaveState({ kind: 'saved' });
  }, [answers, questions, sessionId]);

  useEffect(() => {
    if (stage !== 'answering' || sessionId === null || questions.length === 0) return;
    const pending = questions.filter(
      (q) => (answers[q.order_index] ?? '') !== savedAnswersRef.current[q.order_index],
    );
    if (pending.length === 0) return;

    let cancelled = false;
    const timer = window.setTimeout(() => {
      void (async () => {
        setSaveState({ kind: 'saving' });
        try {
          for (const q of pending) {
            const text = answers[q.order_index] ?? '';
            await saveQuizAnswer(sessionId, q.order_index, text);
            savedAnswersRef.current[q.order_index] = text;
          }
          if (!cancelled) setSaveState({ kind: 'saved' });
        } catch (err) {
          if (!cancelled) setSaveState({ kind: 'error', message: problemMessage(err) });
        }
      })();
    }, 900);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [answers, questions, sessionId, stage]);

  const handleStart = useCallback(async () => {
    const trimmed = query.trim();
    if (!trimmed) {
      setRunError('请输入一个主题');
      return;
    }

    setStage('generating');
    setRunError(null);
    setSessionId(null);
    setQuestions([]);
    setAnswers({});
    setTypeMix(null);
    setQuestionResults({});
    setFinalResult(null);
    setSaveState({ kind: 'idle' });
    setProgressItems([{ id: 'start', label: '准备出题' }]);
    savedAnswersRef.current = {};

    let ok = false;
    let streamError: string | null = null;
    let received: QuizQuestionReady[] = [];
    try {
      for await (const frame of createQuizSession({
        query: trimmed,
        mode: 'topic',
        question_count: questionCount,
      })) {
        if (frame.event === 'started') {
          setSessionId(frame.data.resource_id);
          setProgressItems((items) =>
            appendProgress(items, {
              id: `session-${frame.data.resource_id}`,
              label: `Session #${frame.data.resource_id}`,
              detail: frame.data.query,
            }),
          );
        } else if (frame.event === 'progress') {
          if (frame.data.type_mix) setTypeMix(frame.data.type_mix);
          setProgressItems((items) => appendProgress(items, progressLabel(frame.data)));
        } else if (frame.event === 'question_ready') {
          received = [...received, frame.data].sort((a, b) => a.order_index - b.order_index);
          setQuestions(received);
          setProgressItems((items) =>
            appendProgress(items, {
              id: `question-${frame.data.order_index}`,
              label: `第 ${frame.data.order_index + 1} 题已就绪`,
              detail: frame.data.question.type === 'definition' ? '八股' : '开放题',
            }),
          );
        } else if (frame.event === 'error') {
          streamError = `${frame.data.code}: ${frame.data.detail}`;
          setRunError(streamError);
        } else if (frame.event === 'done') {
          ok = frame.data.ok;
        }
      }

      if (ok && received.length > 0) {
        const initialSaved: Record<number, string> = {};
        for (const item of received) initialSaved[item.order_index] = '';
        savedAnswersRef.current = initialSaved;
        setStage('answering');
        setSaveState({ kind: 'idle' });
      } else {
        setStage('idle');
        if (!streamError) setRunError('出题没有返回可答题目');
      }
    } catch (err) {
      setStage('idle');
      setRunError(problemMessage(err));
    }
  }, [questionCount, query]);

  const handleSubmit = useCallback(async () => {
    if (!canSubmit || sessionId === null) return;
    const missing = questions
      .filter((q) => !(answers[q.order_index] ?? '').trim())
      .map((q) => q.order_index + 1);
    if (missing.length) {
      setRunError(`还有题目未作答:${missing.join(', ')}`);
      return;
    }

    setStage('submitting');
    setRunError(null);
    setQuestionResults({});
    setFinalResult(null);
    setProgressItems((items) => appendProgress(items, { id: 'submit-start', label: '提交评分' }));

    let ok = false;
    try {
      await saveAllAnswers();
      for await (const frame of submitQuizSession(sessionId)) {
        if (frame.event === 'started') {
          setProgressItems((items) =>
            appendProgress(items, {
              id: 'judge-started',
              label: 'Judge 已开始',
              detail: `${frame.data.total_questions} 题`,
            }),
          );
        } else if (frame.event === 'progress') {
          setProgressItems((items) => appendProgress(items, progressLabel(frame.data)));
        } else if (frame.event === 'question_done') {
          setQuestionResults((prev) => ({
            ...prev,
            [frame.data.order_index]: {
              scores: frame.data.scores,
              evidence: frame.data.evidence,
            },
          }));
          setProgressItems((items) =>
            appendProgress(items, {
              id: `judge-done-${frame.data.order_index}`,
              label: `第 ${frame.data.order_index + 1} 题评分完成`,
              detail: `总分 ${roundedScore(frame.data.scores.total)}`,
            }),
          );
        } else if (frame.event === 'result') {
          setFinalResult({
            scores: frame.data.scores,
            recallMdPath: frame.data.recall_md_path,
          });
        } else if (frame.event === 'error') {
          setRunError(`${frame.data.code}: ${frame.data.detail}`);
        } else if (frame.event === 'done') {
          ok = frame.data.ok;
        }
      }
      setStage(ok ? 'submitted' : 'answering');
    } catch (err) {
      setStage('answering');
      setRunError(problemMessage(err));
    }
  }, [answers, canSubmit, questions, saveAllAnswers, sessionId]);

  const handleAbandon = useCallback(async () => {
    if (sessionId === null) {
      resetSession();
      return;
    }
    if (!window.confirm(`放弃 session #${sessionId}?`)) return;
    try {
      await abandonQuizSession(sessionId);
      resetSession();
    } catch (err) {
      setRunError(problemMessage(err));
    }
  }, [resetSession, sessionId]);

  return (
    <div className="grid h-full grid-cols-1 bg-background lg:grid-cols-[320px_1fr]">
      <aside className="flex h-full flex-col border-b border-border bg-surface lg:border-r lg:border-b-0">
        <div className="border-b border-border px-5 py-4">
          <div className="text-[11px] font-semibold tracking-wider text-muted uppercase">
            Recall
          </div>
          <h1 className="mt-1 text-xl font-semibold tracking-tight">面试练习</h1>
        </div>

        <div className="flex flex-1 flex-col gap-5 overflow-y-auto px-5 py-5">
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted" htmlFor="quiz-query">
              主题
            </label>
            <Textarea
              id="quiz-query"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              disabled={controlsLocked}
              className="min-h-[112px] resize-none rounded-lg bg-[var(--color-system-gray-6)]"
              placeholder="考考我多线程"
            />
          </div>

          <div className="space-y-2">
            <div className="text-xs font-medium text-muted">题数</div>
            <div className="grid grid-cols-4 rounded-lg border border-border bg-[var(--color-system-gray-6)] p-1">
              {QUESTION_COUNTS.map((count) => (
                <button
                  key={count}
                  type="button"
                  disabled={controlsLocked}
                  onClick={() => setQuestionCount(count)}
                  className={cn(
                    'h-8 rounded-md text-sm font-medium transition-colors',
                    questionCount === count
                      ? 'bg-surface text-accent shadow-[var(--shadow-apple-sm)]'
                      : 'text-muted hover:text-foreground',
                  )}
                >
                  {count}
                </button>
              ))}
            </div>
          </div>

          <div className="flex gap-2">
            <Button className="flex-1 rounded-lg" onClick={handleStart} disabled={!canStart}>
              {stage === 'generating' ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Play className="size-4" />
              )}
              出题
            </Button>
            <Button
              className="rounded-lg"
              variant="outline"
              size="icon"
              onClick={sessionId && stage === 'answering' ? handleAbandon : resetSession}
              disabled={stage === 'generating' || stage === 'submitting'}
              title={sessionId && stage === 'answering' ? '放弃并重置' : '重置'}
              aria-label={sessionId && stage === 'answering' ? '放弃并重置' : '重置'}
            >
              <RotateCcw className="size-4" />
            </Button>
          </div>

          <div className="border-t border-border pt-4">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs font-medium text-muted">进度</span>
              {sessionId ? <span className="text-xs text-muted">#{sessionId}</span> : null}
            </div>
            <ol className="space-y-2">
              {progressItems.length === 0 ? (
                <li className="text-xs text-muted">等待开始</li>
              ) : (
                progressItems.map((item) => (
                  <li key={item.id} className="flex gap-2 text-xs">
                    <span className="mt-[5px] size-1.5 rounded-full bg-accent" aria-hidden="true" />
                    <span className="min-w-0">
                      <span className="block truncate text-foreground">{item.label}</span>
                      {item.detail ? (
                        <span className="block truncate text-muted">{item.detail}</span>
                      ) : null}
                    </span>
                  </li>
                ))
              )}
            </ol>
          </div>
        </div>
      </aside>

      <main className="flex h-full flex-col">
        <header className="flex min-h-16 flex-wrap items-center justify-between gap-4 border-b border-border bg-background px-5 py-3 lg:px-8">
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-muted">
              {stage === 'idle'
                ? '准备开始'
                : stage === 'generating'
                  ? '正在出题'
                  : stage === 'answering'
                    ? `${answeredCount}/${questions.length} 已答`
                    : stage === 'submitting'
                      ? '正在评分'
                      : '评分完成'}
            </div>
            <div className="mt-0.5 truncate text-lg font-semibold tracking-tight">
              {query.trim() || '主题面试'}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {saveState.kind === 'saving' ? (
              <span className="flex items-center gap-1.5 text-xs text-muted">
                <Loader2 className="size-3.5 animate-spin" />
                保存中
              </span>
            ) : saveState.kind === 'saved' ? (
              <span className="flex items-center gap-1.5 text-xs text-[var(--color-success-fg)]">
                <CheckCircle2 className="size-3.5" />
                已保存
              </span>
            ) : saveState.kind === 'error' ? (
              <span className="flex max-w-[240px] items-center gap-1.5 truncate text-xs text-[var(--color-danger)]">
                <AlertCircle className="size-3.5 shrink-0" />
                <span className="truncate">{saveState.message}</span>
              </span>
            ) : null}
            {stage === 'answering' ? (
              <Button variant="outline" className="rounded-lg" onClick={handleAbandon}>
                <X className="size-4" />
                放弃
              </Button>
            ) : null}
            <Button className="rounded-lg" onClick={handleSubmit} disabled={!canSubmit}>
              {stage === 'submitting' ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Send className="size-4" />
              )}
              提交评分
            </Button>
          </div>
        </header>

        {runError ? (
          <div className="border-b border-[var(--color-warning-border)] bg-[var(--color-warning-bg)] px-8 py-3 text-sm text-[var(--color-warning-fg)]">
            {runError}
          </div>
        ) : null}

        <div className="flex-1 overflow-y-auto px-5 py-6 lg:px-8 lg:py-7">
          {questions.length === 0 ? (
            <EmptyState stage={stage} />
          ) : (
            <div className="mx-auto flex max-w-5xl flex-col gap-4">
              {typeMix ? <TypeMixBar typeMix={typeMix} /> : null}
              {finalResult ? <FinalScore result={finalResult} /> : null}
              {questions.map((item) => (
                <QuestionPanel
                  key={item.question.id}
                  item={item}
                  answer={answers[item.order_index] ?? ''}
                  disabled={stage === 'submitting' || stage === 'submitted'}
                  result={questionResults[item.order_index]}
                  onAnswer={(value) =>
                    setAnswers((prev) => ({ ...prev, [item.order_index]: value }))
                  }
                />
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

function EmptyState({ stage }: { stage: Stage }) {
  const busy = stage === 'generating';
  return (
    <div className="flex h-full items-center justify-center">
      <div className="text-center">
        <div className="mx-auto flex size-10 items-center justify-center rounded-full bg-[var(--color-system-gray-6)] text-muted">
          {busy ? <Loader2 className="size-5 animate-spin" /> : <Play className="size-5" />}
        </div>
        <p className="mt-3 text-sm text-muted">{busy ? '正在整理笔记上下文…' : '等待主题'}</p>
      </div>
    </div>
  );
}

function TypeMixBar({ typeMix }: { typeMix: QuizTypeMix }) {
  const total = Math.max(typeMix.open_ended + typeMix.definition, 1);
  const openPct = (typeMix.open_ended / total) * 100;
  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3">
      <div className="flex items-center justify-between gap-4 text-sm">
        <span className="font-medium">题型配比</span>
        <span className="text-muted">
          开放题 {typeMix.open_ended} · 八股 {typeMix.definition}
        </span>
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-[var(--color-system-gray-6)]">
        <div className="h-full bg-accent" style={{ width: `${openPct}%` }} />
      </div>
    </div>
  );
}

function FinalScore({
  result,
}: {
  result: { scores: QuizScores; recallMdPath?: string | null };
}) {
  return (
    <div className="rounded-lg border border-[var(--color-success-border)] bg-[var(--color-success-bg)] px-4 py-3">
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="text-sm font-semibold text-[var(--color-success-fg)]">总分</div>
          <div className="mt-1 text-xs text-[var(--color-success-fg)]">
            Coverage {roundedScore(result.scores.coverage)} · Fidelity{' '}
            {roundedScore(result.scores.fidelity)} · Depth {roundedScore(result.scores.depth)}
          </div>
        </div>
        <div className="text-3xl font-semibold tracking-tight text-[var(--color-success-fg)]">
          {roundedScore(result.scores.total)}
        </div>
      </div>
    </div>
  );
}

function QuestionPanel({
  item,
  answer,
  disabled,
  result,
  onAnswer,
}: {
  item: QuizQuestionReady;
  answer: string;
  disabled: boolean;
  result?: QuestionResult;
  onAnswer: (value: string) => void;
}) {
  return (
    <article className="rounded-lg border border-border bg-surface shadow-[var(--shadow-apple-sm)]">
      <div className="border-b border-border px-5 py-4">
        <div className="mb-2 flex items-center justify-between gap-3">
          <span className="text-xs font-medium text-muted">第 {item.order_index + 1} 题</span>
          <span className="rounded-full bg-[var(--color-system-gray-6)] px-2.5 py-1 text-xs text-muted">
            {item.question.type === 'definition' ? '八股' : '开放题'}
          </span>
        </div>
        <h2 className="text-base font-semibold leading-relaxed tracking-tight">
          {item.question.prompt}
        </h2>
        <div className="mt-2 truncate text-xs text-muted">
          chunks {item.question.source_chunk_ids.join(', ')}
        </div>
      </div>
      <div className="px-5 py-4">
        <Textarea
          value={answer}
          onChange={(event) => onAnswer(event.target.value)}
          disabled={disabled}
          className="min-h-[156px] resize-y rounded-lg bg-[var(--color-system-gray-6)] leading-7"
          placeholder="在这里作答"
        />
        {result ? <QuestionScore result={result} /> : null}
      </div>
    </article>
  );
}

function QuestionScore({ result }: { result: QuestionResult }) {
  const coverage = evidenceReasoning(result.evidence, 'coverage_evidence');
  const fidelity = evidenceReasoning(result.evidence, 'fidelity_evidence');
  const depth = evidenceReasoning(result.evidence, 'depth_evidence');

  return (
    <div className="mt-4 space-y-3">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <ScorePill label="Coverage" value={result.scores.coverage} />
        <ScorePill label="Fidelity" value={result.scores.fidelity} />
        <ScorePill label="Depth" value={result.scores.depth} />
        <ScorePill label="Total" value={result.scores.total} strong />
      </div>
      {[coverage, fidelity, depth].filter(Boolean).map((text, index) => (
        <p
          key={`${index}-${text}`}
          className="rounded-lg bg-[var(--color-system-gray-6)] px-3 py-2 text-xs leading-5 text-muted"
        >
          {text}
        </p>
      ))}
    </div>
  );
}

function ScorePill({
  label,
  value,
  strong = false,
}: {
  label: string;
  value: number;
  strong?: boolean;
}) {
  const width = Math.max(0, Math.min(100, value));
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2">
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="truncate text-muted">{label}</span>
        <span className={cn('font-semibold', strong ? 'text-accent' : 'text-foreground')}>
          {roundedScore(value)}
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[var(--color-system-gray-6)]">
        <div className={cn('h-full', scoreTone(value))} style={{ width: `${width}%` }} />
      </div>
    </div>
  );
}
