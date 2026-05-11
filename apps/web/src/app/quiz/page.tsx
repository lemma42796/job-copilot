'use client';

import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Play,
  RotateCcw,
  Send,
  Sparkles,
  X,
} from 'lucide-react';
import type { ReactNode } from 'react';
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

type SampleQuiz = {
  query: string;
  questionCount: number;
  typeMix: QuizTypeMix;
  questions: QuizQuestionReady[];
  answers: Record<number, string>;
  results: Record<number, QuestionResult>;
  finalResult: {
    scores: QuizScores;
    recallMdPath?: string | null;
  };
};

type CoveragePoint = {
  id?: string;
  label?: string;
  user_excerpt?: string | null;
};

type CoverageEvidenceView = {
  points?: CoveragePoint[];
  score_raw?: number;
  reasoning?: string;
};

type FidelityClaim = {
  text?: string;
  label?: string;
  chunk_ids?: number[];
};

type FidelityEvidenceView = {
  claims?: FidelityClaim[];
  score_raw?: number;
  reasoning?: string;
};

type DepthDimension = {
  covered?: boolean;
  excerpt?: string | null;
};

type DepthEvidenceView = {
  dimensions?: Record<string, DepthDimension>;
  score_raw?: number;
  reasoning?: string;
};

const QUESTION_COUNTS = [3, 5, 7, 10] as const;

const SAMPLE_QUIZ: SampleQuiz = {
  query: 'Langfuse Prompt 版本管理',
  questionCount: 3,
  typeMix: { open_ended: 2, definition: 1 },
  questions: [
    {
      order_index: 0,
      question: {
        id: 9001,
        type: 'open_ended',
        prompt:
          '在 JobCopilot 项目中，针对 Prompt 版本管理采取了哪两种主要路径？请对比它们的优缺点，并说明项目最终采用了什么策略来平衡两者？',
        source_chunk_ids: [6, 10],
      },
    },
    {
      order_index: 1,
      question: {
        id: 9002,
        type: 'open_ended',
        prompt:
          '在使用 Langfuse 进行 Prompt 版本管理时，如果直接在 UI 修改 Prompt 并更新 production 标签，会带来什么风险？笔记中提到了哪些解法或最佳实践来规避这些风险？',
        source_chunk_ids: [6, 11],
      },
    },
    {
      order_index: 2,
      question: {
        id: 9003,
        type: 'definition',
        prompt:
          '根据笔记内容，Langfuse 视角下 Prompt 被定义为什么类型的数据？这与传统观点有何不同？',
        source_chunk_ids: [6],
      },
    },
  ],
  answers: {
    0: 'JobCopilot 针对 Prompt 版本管理主要考虑了两条路径：Git 管理 Prompt 和 Langfuse Prompts 管理 Prompt。Git 的优点是可 review、可审计、能和代码一起发布，契约稳定；缺点是每次调 prompt 都要走代码提交和部署，迭代慢。Langfuse 的优点是可以在线版本化、快速切换、回滚，并且能和 trace、token、cost 关联；缺点是如果缺少流程约束，容易出现线上 prompt 和 git 代码不一致的配置漂移。最终策略是 Git 管基线和代码契约，Langfuse 管线上版本和实验；生产环境固定具体版本，不随意漂移。',
    1: '直接在 Langfuse UI 修改 Prompt 并更新 production 标签的风险是：线上行为会立刻变化，但 git 代码没有同步，导致代码和 prompt 不一致；如果没人 review，也可能把未验证的 prompt 推到生产，排查和回滚都会变复杂。规避方式包括生产不要只依赖浮动 production 标签，尽量固定具体 prompt version；发布前做 review；Git 中保留基线 prompt 或同步记录；Langfuse 用于实验、版本切换和回滚，但生产版本要有明确发布流程。',
    2: '在 Langfuse 视角下，Prompt 更像是可版本化的配置数据，可以通过名称和版本读取、发布、回滚，并和运行 trace、成本、效果关联。这和传统观点不同：传统方式通常把 Prompt 当成代码里的静态字符串，跟随代码提交和部署；Langfuse 则把 Prompt 抽出来，作为运行时可管理、可观测、可实验的配置资产。',
  },
  results: {
    0: {
      scores: { coverage: 65, fidelity: 92, depth: 67, total: 76 },
      evidence: {
        coverage_evidence: {
          score_raw: 0.65,
          points: [
            {
              id: 'p1',
              label: 'hit',
              user_excerpt: 'Git 的优点是可 review、可审计、能和代码一起发布',
            },
            {
              id: 'p2',
              label: 'partial',
              user_excerpt: '缺点是如果缺少流程约束，容易出现配置漂移',
            },
            {
              id: 'p3',
              label: 'partial',
              user_excerpt: 'Git 管基线和代码契约，Langfuse 管线上版本和实验',
            },
          ],
          reasoning:
            '覆盖了 Git 路径的优缺点和混合策略，但 Langfuse 风险未提到服务不可达与 fallback。',
        },
        fidelity_evidence: {
          score_raw: 0.92,
          claims: [
            {
              text: 'Git 管理 Prompt 可 review、可审计、能和代码一起发布。',
              label: 'supported',
              chunk_ids: [6],
            },
            {
              text: 'Langfuse 可以在线版本化、快速切换和回滚。',
              label: 'supported',
              chunk_ids: [10],
            },
            {
              text: '配置漂移是 Langfuse 风险之一。',
              label: 'inferred',
              chunk_ids: [10],
            },
          ],
          reasoning: '主要声明均有笔记依据或可由笔记合理推断。',
        },
        depth_evidence: {
          score_raw: 0.67,
          dimensions: {
            tradeoff: { covered: true, excerpt: '对比了 Git 与 Langfuse 的迭代速度和审计能力。' },
            why: { covered: true, excerpt: '解释了为什么采用 Git 管基线、Langfuse 管实验。' },
            boundary: { covered: false, excerpt: null },
          },
          reasoning: '取舍和动机清楚，但缺少 fallback 等边界条件。',
        },
      },
    },
    1: {
      scores: { coverage: 30, fidelity: 93, depth: 67, total: 59 },
      evidence: {
        coverage_evidence: {
          score_raw: 0.3,
          points: [
            {
              id: 'p1',
              label: 'hit',
              user_excerpt: '线上行为会立刻变化，但 git 代码没有同步',
            },
            {
              id: 'p2',
              label: 'partial',
              user_excerpt: '发布前做 review；生产版本要有明确发布流程',
            },
            { id: 'p3', label: 'miss', user_excerpt: null },
          ],
          reasoning:
            '命中了直接改 production 的风险和部分流程解法，但漏掉自动备份与 trace 关联 version 审计。',
        },
        fidelity_evidence: {
          score_raw: 0.93,
          claims: [
            {
              text: '直接更新 production 标签会导致线上行为立即变化。',
              label: 'supported',
              chunk_ids: [11],
            },
            {
              text: '固定具体 prompt version 可以降低漂移风险。',
              label: 'supported',
              chunk_ids: [6],
            },
            {
              text: 'Git 保留基线有助于审计。',
              label: 'inferred',
              chunk_ids: [6],
            },
          ],
          reasoning: '答案中的风险和解法基本都能被笔记支撑或合理推断。',
        },
        depth_evidence: {
          score_raw: 0.67,
          dimensions: {
            tradeoff: { covered: true, excerpt: '区分实验便利性和生产稳定性。' },
            why: { covered: true, excerpt: '解释了不一致、不可回滚和排查复杂度。' },
            boundary: { covered: false, excerpt: null },
          },
          reasoning: '说明了生产流程的必要性，但没有展开核心 prompt / 实验 prompt 的边界。',
        },
      },
    },
    2: {
      scores: { coverage: 85, fidelity: 100, depth: 33, total: 86 },
      evidence: {
        coverage_evidence: {
          score_raw: 0.85,
          points: [
            {
              id: 'p1',
              label: 'hit',
              user_excerpt: 'Prompt 更像是可版本化的配置数据',
            },
            {
              id: 'p2',
              label: 'hit',
              user_excerpt: '传统方式通常把 Prompt 当成代码里的静态字符串',
            },
            {
              id: 'p3',
              label: 'partial',
              user_excerpt: '通过名称和版本读取、发布、回滚',
            },
          ],
          reasoning: '核心定义和传统观点区别都准确，但未明确提到 UI 编辑和无需 redeploy。',
        },
        fidelity_evidence: {
          score_raw: 1,
          claims: [
            {
              text: 'Langfuse 将 Prompt 视为可版本化配置数据。',
              label: 'supported',
              chunk_ids: [6],
            },
            {
              text: '传统方式把 Prompt 放在代码静态字符串中。',
              label: 'supported',
              chunk_ids: [6],
            },
          ],
          reasoning: '所有声明均能在笔记中找到直接支持。',
        },
        depth_evidence: {
          score_raw: 0.33,
          dimensions: {
            tradeoff: { covered: true, excerpt: '对比了运行时配置和代码部署。' },
            why: { covered: false, excerpt: null },
            boundary: { covered: false, excerpt: null },
          },
          reasoning: '回答了是什么，但没有深入说明为什么这样设计以及适用边界。',
        },
      },
    },
  },
  finalResult: {
    scores: { coverage: 60, fidelity: 95, depth: 56, total: 74 },
    recallMdPath: 'notes/_recall/sample.md',
  },
};

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

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function textOrNull(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

function numberArray(value: unknown): number[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const out = value.filter((item): item is number => typeof item === 'number');
  return out.length ? out : undefined;
}

function coverageEvidence(evidence: QuizEvidence): CoverageEvidenceView {
  const raw = asRecord(evidence.coverage_evidence);
  if (!raw) return {};
  const points = Array.isArray(raw.points)
    ? raw.points
        .map((point) => {
          const record = asRecord(point);
          if (!record) return null;
          return {
            id: textOrNull(record.id) ?? undefined,
            label: textOrNull(record.label) ?? undefined,
            user_excerpt: textOrNull(record.user_excerpt),
          };
        })
        .filter((point): point is CoveragePoint => point !== null)
    : undefined;
  return {
    points,
    score_raw: typeof raw.score_raw === 'number' ? raw.score_raw : undefined,
    reasoning: textOrNull(raw.reasoning) ?? undefined,
  };
}

function fidelityEvidence(evidence: QuizEvidence): FidelityEvidenceView {
  const raw = asRecord(evidence.fidelity_evidence);
  if (!raw) return {};
  const claims = Array.isArray(raw.claims)
    ? raw.claims
        .map((claim) => {
          const record = asRecord(claim);
          if (!record) return null;
          return {
            text: textOrNull(record.text) ?? undefined,
            label: textOrNull(record.label) ?? undefined,
            chunk_ids: numberArray(record.chunk_ids),
          };
        })
        .filter((claim): claim is FidelityClaim => claim !== null)
    : undefined;
  return {
    claims,
    score_raw: typeof raw.score_raw === 'number' ? raw.score_raw : undefined,
    reasoning: textOrNull(raw.reasoning) ?? undefined,
  };
}

function depthEvidence(evidence: QuizEvidence): DepthEvidenceView {
  const raw = asRecord(evidence.depth_evidence);
  const rawDimensions = asRecord(raw?.dimensions);
  if (!raw) return {};
  const dimensions: Record<string, DepthDimension> = {};
  if (rawDimensions) {
    for (const [key, value] of Object.entries(rawDimensions)) {
      const record = asRecord(value);
      if (!record) continue;
      dimensions[key] = {
        covered: typeof record.covered === 'boolean' ? record.covered : undefined,
        excerpt: textOrNull(record.excerpt),
      };
    }
  }
  return {
    dimensions,
    score_raw: typeof raw.score_raw === 'number' ? raw.score_raw : undefined,
    reasoning: textOrNull(raw.reasoning) ?? undefined,
  };
}

function coverageLabel(label?: string): string {
  if (label === 'hit') return '命中';
  if (label === 'partial') return '部分';
  if (label === 'miss') return '漏掉';
  return label ?? '未知';
}

function fidelityLabel(label?: string): string {
  if (label === 'supported') return '有依据';
  if (label === 'inferred') return '推断';
  if (label === 'fabricated') return '编造';
  return label ?? '未知';
}

function depthLabel(key: string): string {
  if (key === 'tradeoff') return '取舍';
  if (key === 'why') return '原因';
  if (key === 'boundary') return '边界';
  return key;
}

function badgeTone(label?: string): string {
  if (label === 'hit' || label === 'supported') {
    return 'bg-[var(--color-success-bg)] text-[var(--color-success-fg)]';
  }
  if (label === 'partial' || label === 'inferred') {
    return 'bg-[var(--color-warning-bg)] text-[var(--color-warning-fg)]';
  }
  if (label === 'miss' || label === 'fabricated') {
    return 'bg-red-50 text-[var(--color-danger)]';
  }
  return 'bg-[var(--color-system-gray-6)] text-muted';
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

  const loadSample = useCallback(() => {
    setQuery(SAMPLE_QUIZ.query);
    setQuestionCount(SAMPLE_QUIZ.questionCount);
    setStage('submitted');
    setSessionId(null);
    setQuestions(SAMPLE_QUIZ.questions);
    setAnswers(SAMPLE_QUIZ.answers);
    setProgressItems([
      { id: 'sample-session', label: '样例 Session', detail: SAMPLE_QUIZ.query },
      { id: 'sample-result', label: '评分完成', detail: '本地样例' },
    ]);
    setTypeMix(SAMPLE_QUIZ.typeMix);
    setQuestionResults(SAMPLE_QUIZ.results);
    setFinalResult(SAMPLE_QUIZ.finalResult);
    setRunError(null);
    setSaveState({ kind: 'idle' });
    savedAnswersRef.current = SAMPLE_QUIZ.answers;
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
          <Button
            className="w-full rounded-lg"
            variant="outline"
            onClick={loadSample}
            disabled={stage === 'generating' || stage === 'submitting'}
          >
            <Sparkles className="size-4" />
            样例
          </Button>

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
            {stage === 'submitted' ? (
              <Button className="rounded-lg" variant="outline" onClick={resetSession}>
                <RotateCcw className="size-4" />
                再来一轮
              </Button>
            ) : (
              <Button className="rounded-lg" onClick={handleSubmit} disabled={!canSubmit}>
                {stage === 'submitting' ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Send className="size-4" />
                )}
                {stage === 'submitting' ? '评分中' : '提交评分'}
              </Button>
            )}
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
        <div className="mt-2 truncate text-xs text-muted" title={`chunks ${item.question.source_chunk_ids.join(', ')}`}>
          来源 {item.question.source_chunk_ids.length} 段
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
  const coverage = coverageEvidence(result.evidence);
  const fidelity = fidelityEvidence(result.evidence);
  const depth = depthEvidence(result.evidence);

  return (
    <div className="mt-4 space-y-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <ScorePill label="Coverage" value={result.scores.coverage} />
        <ScorePill label="Fidelity" value={result.scores.fidelity} />
        <ScorePill label="Depth" value={result.scores.depth} />
        <ScorePill label="Total" value={result.scores.total} strong />
      </div>
      <div className="grid gap-3 lg:grid-cols-3">
        <CoveragePanel evidence={coverage} />
        <FidelityPanel evidence={fidelity} />
        <DepthPanel evidence={depth} />
      </div>
    </div>
  );
}

function EvidencePanel({
  title,
  score,
  children,
  reasoning,
}: {
  title: string;
  score?: number;
  children: ReactNode;
  reasoning?: string;
}) {
  return (
    <section className="rounded-lg border border-border bg-[var(--color-system-gray-6)] p-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-xs font-semibold text-foreground">{title}</h3>
        {typeof score === 'number' ? (
          <span className="text-xs font-medium text-muted">{Math.round(score * 100)}%</span>
        ) : null}
      </div>
      <div className="mt-3 space-y-2">{children}</div>
      {reasoning ? (
        <p className="mt-3 border-t border-border pt-2 text-xs leading-5 text-muted">{reasoning}</p>
      ) : null}
    </section>
  );
}

function CoveragePanel({ evidence }: { evidence: CoverageEvidenceView }) {
  const points = evidence.points ?? [];
  return (
    <EvidencePanel title="Coverage" score={evidence.score_raw} reasoning={evidence.reasoning}>
      {points.length ? (
        points.map((point, index) => (
          <div key={`${point.id ?? index}-${point.label}`} className="rounded-md bg-surface px-3 py-2">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-xs font-medium text-foreground">{point.id ?? `p${index + 1}`}</span>
              <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium', badgeTone(point.label))}>
                {coverageLabel(point.label)}
              </span>
            </div>
            {point.user_excerpt ? (
              <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted">{point.user_excerpt}</p>
            ) : null}
          </div>
        ))
      ) : (
        <p className="text-xs text-muted">暂无命中点明细</p>
      )}
    </EvidencePanel>
  );
}

function FidelityPanel({ evidence }: { evidence: FidelityEvidenceView }) {
  const claims = evidence.claims ?? [];
  return (
    <EvidencePanel title="Fidelity" score={evidence.score_raw} reasoning={evidence.reasoning}>
      {claims.length ? (
        claims.map((claim, index) => (
          <div key={`${index}-${claim.text ?? claim.label}`} className="rounded-md bg-surface px-3 py-2">
            <div className="mb-1 flex items-center justify-between gap-2">
              <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium', badgeTone(claim.label))}>
                {fidelityLabel(claim.label)}
              </span>
              {claim.chunk_ids?.length ? (
                <span className="truncate text-[11px] text-muted">chunks {claim.chunk_ids.join(', ')}</span>
              ) : null}
            </div>
            <p className="line-clamp-3 text-xs leading-5 text-foreground">{claim.text ?? '未返回 claim 文本'}</p>
          </div>
        ))
      ) : (
        <p className="text-xs text-muted">暂无声明明细</p>
      )}
    </EvidencePanel>
  );
}

function DepthPanel({ evidence }: { evidence: DepthEvidenceView }) {
  const dimensions = Object.entries(evidence.dimensions ?? {});
  return (
    <EvidencePanel title="Depth" score={evidence.score_raw} reasoning={evidence.reasoning}>
      {dimensions.length ? (
        dimensions.map(([key, value]) => (
          <div key={key} className="rounded-md bg-surface px-3 py-2">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-xs font-medium text-foreground">{depthLabel(key)}</span>
              <span
                className={cn(
                  'rounded-full px-2 py-0.5 text-[11px] font-medium',
                  value.covered
                    ? 'bg-[var(--color-success-bg)] text-[var(--color-success-fg)]'
                    : 'bg-[var(--color-system-gray-6)] text-muted',
                )}
              >
                {value.covered ? '讲到' : '缺失'}
              </span>
            </div>
            {value.excerpt ? (
              <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted">{value.excerpt}</p>
            ) : null}
          </div>
        ))
      ) : (
        <p className="text-xs text-muted">暂无深度维度明细</p>
      )}
    </EvidencePanel>
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
