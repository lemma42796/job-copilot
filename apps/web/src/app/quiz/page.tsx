'use client';

import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  Folder,
  Loader2,
  MessageCircleQuestion,
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
  finishQuizSession,
  getQuizSession,
  listQuizSessions,
  saveQuizAnswer,
  submitQuizAnswerTurn,
  submitQuizSession,
  type QuizAnswerTurn,
  type QuizCoachTurn,
  type QuizEvidence,
  type QuizJudgeTurn,
  type QuizNullableScores,
  type QuizNextAction,
  type QuizProgress,
  type QuizQuestionReady,
  type QuizRemediationPrompt,
  type QuizScores,
  type QuizSessionDetail,
  type QuizSessionListItem,
  type QuizSessionSummary,
  type QuizTypeMix,
} from '@/lib/api';
import { cn } from '@/lib/utils';

type Stage = 'idle' | 'generating' | 'answering' | 'submitting' | 'submitted';
type TurnIntent = 'answer' | 'coach_question';
type SaveState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'saved' }
  | { kind: 'error'; message: string };

type ProgressItem = {
  id: string;
  group: 'quiz' | 'judge';
  label: string;
  detail?: string;
};

type QuestionResult = {
  scores: QuizScores;
  evidence?: QuizEvidence | null;
  coachMessage?: string | null;
};

type QuestionTurnState = {
  status: 'running' | 'done' | 'error';
  phase?: string;
  roundIndex?: number;
  scores?: QuizScores;
  nextAction?: QuizNextAction | string;
  triggeredBy?: string;
  decisionReason?: string;
  exitReason?: string | null;
  remediationPrompt?: QuizRemediationPrompt | null;
  coachMessage?: string | null;
  unresolvedGaps?: unknown[];
  error?: string;
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
    summary?: QuizSessionSummary | null;
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
  supporting_chunk_ids?: number[];
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

const QUESTION_COUNTS = [1, 3, 5] as const;

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
        evidence_chunk_ids: [6, 10],
      },
    },
    {
      order_index: 1,
      question: {
        id: 9002,
        type: 'open_ended',
        prompt:
          '在使用 Langfuse 进行 Prompt 版本管理时，如果直接在 UI 修改 Prompt 并更新 production 标签，会带来什么风险？笔记中提到了哪些解法或最佳实践来规避这些风险？',
        evidence_chunk_ids: [6, 11],
      },
    },
    {
      order_index: 2,
      question: {
        id: 9003,
        type: 'definition',
        prompt:
          '根据笔记内容，Langfuse 视角下 Prompt 被定义为什么类型的数据？这与传统观点有何不同？',
        evidence_chunk_ids: [6],
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
              supporting_chunk_ids: [6],
            },
            {
              text: 'Langfuse 可以在线版本化、快速切换和回滚。',
              label: 'supported',
              supporting_chunk_ids: [10],
            },
            {
              text: '配置漂移是 Langfuse 风险之一。',
              label: 'inferred',
              supporting_chunk_ids: [10],
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
              supporting_chunk_ids: [11],
            },
            {
              text: '固定具体 prompt version 可以降低漂移风险。',
              label: 'supported',
              supporting_chunk_ids: [6],
            },
            {
              text: 'Git 保留基线有助于审计。',
              label: 'inferred',
              supporting_chunk_ids: [6],
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
              supporting_chunk_ids: [6],
            },
            {
              text: '传统方式把 Prompt 放在代码静态字符串中。',
              label: 'supported',
              supporting_chunk_ids: [6],
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
    summary: {
      headline: '这场已经能答出主线，下一步要集中补齐反复出现的漏点和深度维度。',
      strengths: ['答案基本能回到笔记证据，凭空发挥较少。'],
      recurring_gaps: [
        { key: 'coverage', label: '采分点遗漏或只答到部分', count: 4 },
        { key: 'depth:boundary', label: 'Depth 缺少边界', count: 2 },
      ],
      remediation_wins: ['第 1 题补答后总分提升 12 分'],
      review_suggestions: [
        '复盘每题采分点，把漏答项整理成 3-5 条短句再口述一遍。',
        '每个概念补一层 why / trade-off / boundary，避免只给定义。',
      ],
    },
  },
};

const PHASE_LABELS: Record<string, string> = {
  query_rewriting: '理解主题',
  query_rewriting_done: '扩展检索词',
  hybrid_searching: '全库召回',
  reranking: '重排候选',
  context_selecting: '选择证据',
  parent_doc_expanding: '选择证据',
  generating: '生成题目',
  type_mix_decided: '确定题型',
  judging: '评分中',
  context_pack_built: '整理本题上下文',
  coach_question_started: '追问教练',
  coach_context_built: '整理追问上下文',
  coach_done: '教练已回复',
  summarizing: '生成整场总结',
};

const NEXT_ACTION_LABELS: Record<string, string> = {
  ask_next: '进入下一题',
  remediate: '继续补答',
  summarize: '进入总结',
  finish: '完成',
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
  const group = progress.phase === 'judging' ? 'judge' : 'quiz';
  const details: string[] = [];
  if (progress.expanded_queries?.length) details.push(progress.expanded_queries.join(' / '));
  if (typeof progress.candidate_count === 'number') details.push(`${progress.candidate_count} 个候选`);
  if (typeof progress.chunk_count === 'number') details.push(`${progress.chunk_count} 个 chunks`);
  if (progress.model) details.push(progress.model);
  if (typeof progress.order_index === 'number') details.push(`第 ${progress.order_index + 1} 题`);
  return {
    id: `${progress.phase}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    group,
    label,
    detail: details.join(' · ') || undefined,
  };
}

function turnPhaseLabel(phase?: string): string {
  if (!phase) return '推进中';
  return PHASE_LABELS[phase] ?? phase;
}

function appendProgress(items: ProgressItem[], next: ProgressItem): ProgressItem[] {
  return [...items.slice(-16), next];
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
            supporting_chunk_ids: numberArray(record.supporting_chunk_ids),
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

function nextActionLabel(action?: string | null): string {
  if (!action) return '等待';
  return NEXT_ACTION_LABELS[action] ?? action;
}

function remediationText(prompt?: QuizRemediationPrompt | null): string | null {
  const text = prompt?.text;
  return typeof text === 'string' && text.trim() ? text : null;
}

function coachFeedbackText({
  coachMessage,
  scores,
  remediationPrompt,
  turnState,
}: {
  coachMessage?: string | null;
  scores?: QuizScores;
  remediationPrompt?: QuizRemediationPrompt | null;
  turnState?: QuestionTurnState;
}): string {
  if (typeof coachMessage === 'string' && coachMessage.trim()) {
    return coachMessage.trim();
  }
  const promptText = remediationText(remediationPrompt);
  if (!scores) {
    return (
      promptText ??
      turnState?.decisionReason ??
      '我会先看你的答案覆盖了什么，再看有没有依据和深度。'
    );
  }
  return promptText ?? turnState?.decisionReason ?? '评分已完成，展开下面的评分细节可以查看证据。';
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

function toScores(scores: QuizNullableScores | null | undefined): QuizScores | null {
  if (
    !scores ||
    scores.coverage === null ||
    scores.fidelity === null ||
    scores.depth === null ||
    scores.total === null
  ) {
    return null;
  }
  return {
    coverage: scores.coverage,
    fidelity: scores.fidelity,
    depth: scores.depth,
    total: scores.total,
  };
}

function typeMixFromQuestions(questions: QuizQuestionReady[]): QuizTypeMix {
  return {
    open_ended: questions.filter((item) => item.question.type === 'open_ended').length,
    definition: questions.filter((item) => item.question.type === 'definition').length,
  };
}

function allowedQuestionCount(count: number): number {
  return QUESTION_COUNTS.includes(count as (typeof QUESTION_COUNTS)[number]) ? count : 3;
}

function updateSessionUrl(sessionId: number | null): void {
  if (typeof window === 'undefined') return;
  const url = new URL(window.location.href);
  if (sessionId === null) url.searchParams.delete('session');
  else url.searchParams.set('session', String(sessionId));
  window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
}

function sessionStatusLabel(status: QuizSessionListItem['status']): string {
  if (status === 'submitted') return '已评分';
  if (status === 'in_progress') return '答题中';
  return '已放弃';
}

function sessionStatusTone(status: QuizSessionListItem['status']): string {
  if (status === 'submitted') return 'text-[var(--color-success-fg)]';
  if (status === 'in_progress') return 'text-accent';
  return 'text-muted';
}

function shortDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

export default function QuizPage() {
  const [query, setQuery] = useState('');
  const [questionCount, setQuestionCount] = useState<number>(3);
  const [stage, setStage] = useState<Stage>('idle');
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [questions, setQuestions] = useState<QuizQuestionReady[]>([]);
  const [activeQuestionOrder, setActiveQuestionOrder] = useState<number | null>(null);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [progressItems, setProgressItems] = useState<ProgressItem[]>([]);
  const [typeMix, setTypeMix] = useState<QuizTypeMix | null>(null);
  const [questionResults, setQuestionResults] = useState<Record<number, QuestionResult>>({});
  const [answerTurns, setAnswerTurns] = useState<Record<number, QuizAnswerTurn[]>>({});
  const [judgeTurns, setJudgeTurns] = useState<Record<number, QuizJudgeTurn[]>>({});
  const [coachTurns, setCoachTurns] = useState<Record<number, QuizCoachTurn[]>>({});
  const [remediationPrompts, setRemediationPrompts] = useState<
    Record<number, QuizRemediationPrompt | null>
  >({});
  const [questionActions, setQuestionActions] = useState<Record<number, string | null>>({});
  const [turnStates, setTurnStates] = useState<Record<number, QuestionTurnState>>({});
  const [activeTurnOrder, setActiveTurnOrder] = useState<number | null>(null);
  const [finalResult, setFinalResult] = useState<{
    scores: QuizScores;
    recallMdPath?: string | null;
    summary?: QuizSessionSummary | null;
  } | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>({ kind: 'idle' });
  const [recentSessions, setRecentSessions] = useState<QuizSessionListItem[]>([]);
  const [recentError, setRecentError] = useState<string | null>(null);
  const savedAnswersRef = useRef<Record<number, string>>({});

  const answeredCount = useMemo(
    () =>
      questions.filter((q) => {
        const turns = answerTurns[q.order_index] ?? [];
        return turns.length > 0 || (answers[q.order_index] ?? '').trim().length > 0;
      }).length,
    [answerTurns, answers, questions],
  );
  const quizProgressItems = useMemo(
    () => progressItems.filter((item) => item.group === 'quiz'),
    [progressItems],
  );
  const judgeProgressItems = useMemo(
    () => progressItems.filter((item) => item.group === 'judge'),
    [progressItems],
  );

  const canStart = query.trim().length > 0 && (stage === 'idle' || stage === 'submitted');
  const controlsLocked = stage !== 'idle' && stage !== 'submitted';
  const canSubmit =
    stage === 'answering' &&
    questions.length > 0 &&
    answeredCount === questions.length &&
    sessionId !== null &&
    activeTurnOrder === null;
  const allQuestionsJudged = useMemo(
    () =>
      questions.length > 0 &&
      questions.every((q) => {
        const orderIndex = q.order_index;
        return Boolean(
          questionResults[orderIndex]?.scores ||
            turnStates[orderIndex]?.scores ||
            (judgeTurns[orderIndex] ?? []).some((turn) => turn.scores),
        );
      }),
    [judgeTurns, questionResults, questions, turnStates],
  );
  const canFinish =
    stage === 'answering' &&
    sessionId !== null &&
    activeTurnOrder === null &&
    allQuestionsJudged;
  const activeQuestion = useMemo(() => {
    if (questions.length === 0) return null;
    return questions.find((item) => item.order_index === activeQuestionOrder) ?? questions[0];
  }, [activeQuestionOrder, questions]);

  const reloadRecent = useCallback(async (signal?: AbortSignal) => {
    try {
      const data = await listQuizSessions({ limit: 8, signal });
      setRecentSessions(data.items);
      setRecentError(null);
    } catch (err) {
      if (!signal?.aborted) setRecentError(problemMessage(err));
    }
  }, []);

  const resetSession = useCallback(() => {
    setStage('idle');
    setSessionId(null);
    setQuestions([]);
    setActiveQuestionOrder(null);
    setAnswers({});
    setProgressItems([]);
    setTypeMix(null);
    setQuestionResults({});
    setAnswerTurns({});
    setJudgeTurns({});
    setCoachTurns({});
    setRemediationPrompts({});
    setQuestionActions({});
    setTurnStates({});
    setActiveTurnOrder(null);
    setFinalResult(null);
    setRunError(null);
    setSaveState({ kind: 'idle' });
    savedAnswersRef.current = {};
    updateSessionUrl(null);
  }, []);

  const hydrateSession = useCallback((detail: QuizSessionDetail) => {
    const restoredQuestions: QuizQuestionReady[] = detail.questions
      .map((item) => ({
        order_index: item.order_index,
        question: item.question,
      }))
      .sort((a, b) => a.order_index - b.order_index);
    const restoredAnswers: Record<number, string> = {};
    const restoredResults: Record<number, QuestionResult> = {};
    const restoredTurns: Record<number, QuizAnswerTurn[]> = {};
    const restoredJudgeTurns: Record<number, QuizJudgeTurn[]> = {};
    const restoredCoachTurns: Record<number, QuizCoachTurn[]> = {};
    const restoredPrompts: Record<number, QuizRemediationPrompt | null> = {};
    const restoredActions: Record<number, string | null> = {};
    const restoredTurnStates: Record<number, QuestionTurnState> = {};
    for (const item of detail.questions) {
      const turns = item.answer_turns ?? [];
      restoredTurns[item.order_index] = turns;
      restoredJudgeTurns[item.order_index] = item.judge_turns ?? [];
      restoredCoachTurns[item.order_index] = item.coach_turns ?? [];
      restoredAnswers[item.order_index] = turns.length > 0 ? '' : item.user_answer ?? '';
      const scores = toScores(item.scores);
      const prompt = item.remediation_prompt ?? item.remediation_state?.remediation_prompt ?? null;
      const action = item.next_action ?? item.remediation_state?.last_decision ?? null;
      restoredPrompts[item.order_index] = prompt;
      restoredActions[item.order_index] = action;
      if (scores) {
        restoredResults[item.order_index] = {
          scores,
          evidence: item.evidence,
          coachMessage: item.coach_message ?? null,
        };
      }
      if (scores || prompt || action) {
        restoredTurnStates[item.order_index] = {
          status: 'done',
          scores: scores ?? undefined,
          nextAction: action ?? undefined,
          triggeredBy: item.remediation_state?.triggered_by,
          decisionReason: item.remediation_state?.decision_reason,
          exitReason: item.remediation_state?.exit_reason,
          remediationPrompt: prompt,
          coachMessage: item.coach_message ?? null,
          unresolvedGaps: item.remediation_state?.unresolved_gaps,
        };
      }
    }
    const finalScores = toScores(detail.scores);
    const agentState = asRecord(detail.agent_state);
    const currentQuestionIndex =
      typeof agentState?.current_question_index === 'number'
        ? agentState.current_question_index
        : restoredQuestions[0]?.order_index ?? null;

    setQuery(detail.query);
    setQuestionCount(allowedQuestionCount(restoredQuestions.length));
    setStage(
      detail.status === 'submitted'
        ? 'submitted'
        : detail.status === 'in_progress'
          ? 'answering'
          : 'idle',
    );
    setSessionId(detail.id);
    setQuestions(restoredQuestions);
    setActiveQuestionOrder(currentQuestionIndex);
    setAnswers(restoredAnswers);
    setProgressItems([
      { id: `restored-${detail.id}`, group: 'quiz', label: '题目已恢复', detail: `Session #${detail.id}` },
      ...(finalScores
        ? [
            {
              id: `restored-score-${detail.id}`,
              group: 'judge' as const,
              label: '评分已恢复',
              detail: `总分 ${roundedScore(finalScores.total)}`,
            },
          ]
        : []),
    ]);
    setTypeMix(restoredQuestions.length ? typeMixFromQuestions(restoredQuestions) : null);
    setQuestionResults(restoredResults);
    setAnswerTurns(restoredTurns);
    setJudgeTurns(restoredJudgeTurns);
    setCoachTurns(restoredCoachTurns);
    setRemediationPrompts(restoredPrompts);
    setQuestionActions(restoredActions);
    setTurnStates(restoredTurnStates);
    setActiveTurnOrder(null);
    setFinalResult(
      finalScores
        ? {
            scores: finalScores,
            recallMdPath: detail.recall_md_path,
            summary: detail.summary ?? null,
          }
        : null,
    );
    setRunError(detail.status === 'abandoned' ? '这个 session 已放弃，不能继续答题' : null);
    setSaveState({ kind: 'idle' });
    savedAnswersRef.current = restoredAnswers;
    updateSessionUrl(detail.id);
  }, []);

  const loadSample = useCallback(() => {
    const sampleTurns: Record<number, QuizAnswerTurn[]> = {};
    for (const [orderIndex, text] of Object.entries(SAMPLE_QUIZ.answers)) {
      sampleTurns[Number(orderIndex)] = [
        {
          round_index: 0,
          turn_type: 'initial',
          text,
          submitted_at: new Date().toISOString(),
        },
      ];
    }

    setQuery(SAMPLE_QUIZ.query);
    setQuestionCount(SAMPLE_QUIZ.questionCount);
    setStage('submitted');
    setSessionId(null);
    setQuestions(SAMPLE_QUIZ.questions);
    setActiveQuestionOrder(SAMPLE_QUIZ.questions[0]?.order_index ?? null);
    setAnswers({});
    setProgressItems([
      { id: 'sample-session', group: 'quiz', label: '样例题目', detail: SAMPLE_QUIZ.query },
      { id: 'sample-result', group: 'judge', label: '样例评分', detail: '本地样例' },
    ]);
    setTypeMix(SAMPLE_QUIZ.typeMix);
    setQuestionResults(SAMPLE_QUIZ.results);
    setAnswerTurns(sampleTurns);
    setJudgeTurns({});
    setCoachTurns({});
    setRemediationPrompts({});
    setQuestionActions({});
    setTurnStates({});
    setActiveTurnOrder(null);
    setFinalResult(SAMPLE_QUIZ.finalResult);
    setRunError(null);
    setSaveState({ kind: 'idle' });
    savedAnswersRef.current = {};
    updateSessionUrl(null);
  }, []);

  const loadSession = useCallback(
    async (id: number, signal?: AbortSignal) => {
      try {
        const detail = await getQuizSession(id, signal);
        hydrateSession(detail);
      } catch (err) {
        if (!signal?.aborted) setRunError(problemMessage(err));
      }
    },
    [hydrateSession],
  );

  const mergeQuestionDetails = useCallback((detail: QuizSessionDetail) => {
    const nextAnswers: Record<number, string> = {};
    const nextTurns: Record<number, QuizAnswerTurn[]> = {};
    const nextJudgeTurns: Record<number, QuizJudgeTurn[]> = {};
    const nextCoachTurns: Record<number, QuizCoachTurn[]> = {};
    const nextPrompts: Record<number, QuizRemediationPrompt | null> = {};
    const nextActions: Record<number, string | null> = {};
    const nextResults: Record<number, QuestionResult> = {};
    const nextTurnStates: Record<number, QuestionTurnState> = {};

    for (const item of detail.questions) {
      const turns = item.answer_turns ?? [];
      nextTurns[item.order_index] = turns;
      nextJudgeTurns[item.order_index] = item.judge_turns ?? [];
      nextCoachTurns[item.order_index] = item.coach_turns ?? [];
      nextAnswers[item.order_index] = turns.length > 0 ? '' : item.user_answer ?? '';
      const scores = toScores(item.scores);
      const prompt = item.remediation_prompt ?? item.remediation_state?.remediation_prompt ?? null;
      const action = item.next_action ?? item.remediation_state?.last_decision ?? null;

      nextPrompts[item.order_index] = prompt;
      nextActions[item.order_index] = action;
      if (scores) {
        nextResults[item.order_index] = {
          scores,
          evidence: item.evidence,
          coachMessage: item.coach_message ?? null,
        };
      }
      if (scores || prompt || action) {
        nextTurnStates[item.order_index] = {
          status: 'done',
          scores: scores ?? undefined,
          nextAction: action ?? undefined,
          triggeredBy: item.remediation_state?.triggered_by,
          decisionReason: item.remediation_state?.decision_reason,
          exitReason: item.remediation_state?.exit_reason,
          remediationPrompt: prompt,
          coachMessage: item.coach_message ?? null,
          unresolvedGaps: item.remediation_state?.unresolved_gaps,
        };
      }
    }

    setAnswers(nextAnswers);
    setAnswerTurns(nextTurns);
    setJudgeTurns(nextJudgeTurns);
    setCoachTurns(nextCoachTurns);
    setRemediationPrompts(nextPrompts);
    setQuestionActions(nextActions);
    setQuestionResults(nextResults);
    setTurnStates((prev) => ({ ...prev, ...nextTurnStates }));
    savedAnswersRef.current = nextAnswers;
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void reloadRecent(controller.signal);
    return () => controller.abort();
  }, [reloadRecent]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const raw = new URLSearchParams(window.location.search).get('session');
    if (!raw) return;
    const id = Number(raw);
    if (!Number.isInteger(id) || id <= 0) return;

    const controller = new AbortController();
    void loadSession(id, controller.signal);
    return () => controller.abort();
  }, [loadSession]);

  const saveAllAnswers = useCallback(async () => {
    if (sessionId === null) return;
    for (const q of questions) {
      if ((answerTurns[q.order_index] ?? []).length > 0) continue;
      const text = answers[q.order_index] ?? '';
      await saveQuizAnswer(sessionId, q.order_index, text);
      savedAnswersRef.current[q.order_index] = text;
    }
    setSaveState({ kind: 'saved' });
  }, [answerTurns, answers, questions, sessionId]);

  useEffect(() => {
    if (stage !== 'answering' || sessionId === null || questions.length === 0) return;
    if (activeTurnOrder !== null) return;
    const pending = questions.filter((q) => {
      if ((answerTurns[q.order_index] ?? []).length > 0) return false;
      return (answers[q.order_index] ?? '') !== savedAnswersRef.current[q.order_index];
    });
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
  }, [activeTurnOrder, answerTurns, answers, questions, sessionId, stage]);

  const handleSubmitTurn = useCallback(
    async (orderIndex: number, intent: TurnIntent = 'answer') => {
      if (stage !== 'answering' || sessionId === null || activeTurnOrder !== null) return;
      const text = (answers[orderIndex] ?? '').trim();
      if (!text) {
        setRunError(
          intent === 'coach_question'
            ? `第 ${orderIndex + 1} 题追问不能为空`
            : `第 ${orderIndex + 1} 题答案不能为空`,
        );
        return;
      }

      const priorTurns = answerTurns[orderIndex] ?? [];
      const hasPriorCoachContext = Boolean(
        questionResults[orderIndex]?.coachMessage ||
          remediationPrompts[orderIndex] ||
          turnStates[orderIndex]?.coachMessage ||
          turnStates[orderIndex]?.remediationPrompt,
      );
      if (intent === 'coach_question' && !hasPriorCoachContext) {
        setRunError(`第 ${orderIndex + 1} 题先等教练反馈，再追问教练`);
        return;
      }
      const turnType =
        intent === 'coach_question'
          ? 'coach_question'
          : priorTurns.length > 0
            ? 'remediation'
            : 'initial';
      const clientTurnId = `web-${sessionId}-${orderIndex}-${Date.now()}`;

      setRunError(null);
      setActiveTurnOrder(orderIndex);
      setTurnStates((prev) => ({
        ...prev,
        [orderIndex]: {
          status: 'running',
          phase: 'started',
          nextAction: questionActions[orderIndex] ?? undefined,
          remediationPrompt: remediationPrompts[orderIndex] ?? null,
        },
      }));
      setProgressItems((items) =>
        appendProgress(items, {
          id: `turn-start-${orderIndex}-${Date.now()}`,
          group: 'judge',
          label:
            intent === 'coach_question'
              ? `第 ${orderIndex + 1} 题追问教练`
              : `第 ${orderIndex + 1} 题提交`,
          detail:
            turnType === 'coach_question'
              ? '追问'
              : turnType === 'initial'
                ? '初答'
                : '补答',
        }),
      );

      let ok = false;
      try {
        for await (const frame of submitQuizAnswerTurn(sessionId, orderIndex, {
          text,
          turn_type: turnType,
          client_turn_id: clientTurnId,
        })) {
          if (frame.event === 'started') {
            setTurnStates((prev) => ({
              ...prev,
              [orderIndex]: {
                ...(prev[orderIndex] ?? { status: 'running' as const }),
                status: 'running',
                phase:
                  turnType === 'coach_question' ? 'coach_question_started' : 'started',
                roundIndex: frame.data.round_index,
              },
            }));
          } else if (frame.event === 'progress') {
            setTurnStates((prev) => ({
              ...prev,
              [orderIndex]: {
                ...(prev[orderIndex] ?? { status: 'running' as const }),
                status: 'running',
                phase: frame.data.phase,
              },
            }));
            setProgressItems((items) =>
              appendProgress(items, {
                id: `turn-progress-${orderIndex}-${frame.data.phase}-${Date.now()}`,
                group: 'judge',
                label: turnPhaseLabel(frame.data.phase),
                detail: `第 ${orderIndex + 1} 题`,
              }),
            );
          } else if (frame.event === 'judge_done') {
            setQuestionResults((prev) => ({
              ...prev,
              [orderIndex]: {
                scores: frame.data.scores,
                evidence: prev[orderIndex]?.evidence ?? null,
                coachMessage:
                  frame.data.coach_message ?? prev[orderIndex]?.coachMessage ?? null,
              },
            }));
            setTurnStates((prev) => ({
              ...prev,
              [orderIndex]: {
                ...(prev[orderIndex] ?? { status: 'running' as const }),
                status: 'running',
                phase: 'judge_done',
                roundIndex: frame.data.round_index,
                scores: frame.data.scores,
                coachMessage: frame.data.coach_message ?? null,
                unresolvedGaps: frame.data.unresolved_gaps,
              },
            }));
            setProgressItems((items) =>
              appendProgress(items, {
                id: `turn-judge-${orderIndex}-${Date.now()}`,
                group: 'judge',
                label: `第 ${orderIndex + 1} 题评分完成`,
                detail: `总分 ${roundedScore(frame.data.scores.total)}`,
              }),
            );
          } else if (frame.event === 'coach_done') {
            const turn: QuizCoachTurn = {
              round_index: frame.data.round_index,
              turn_type: 'coach_question',
              text: frame.data.text,
              client_turn_id: frame.data.client_turn_id ?? clientTurnId,
              submitted_at: frame.data.submitted_at,
              coach_message: frame.data.coach_message,
            };
            setAnswers((prev) => ({
              ...prev,
              [orderIndex]: '',
            }));
            savedAnswersRef.current[orderIndex] = '';
            setCoachTurns((prev) => ({
              ...prev,
              [orderIndex]: [...(prev[orderIndex] ?? []), turn],
            }));
            setTurnStates((prev) => ({
              ...prev,
              [orderIndex]: {
                ...(prev[orderIndex] ?? { status: 'running' as const }),
                status: 'done',
                phase: 'coach_done',
                roundIndex: frame.data.round_index,
              },
            }));
            setProgressItems((items) =>
              appendProgress(items, {
                id: `turn-coach-${orderIndex}-${Date.now()}`,
                group: 'judge',
                label: `第 ${orderIndex + 1} 题教练已回复`,
                detail: '不重评',
              }),
            );
          } else if (frame.event === 'decision_done') {
            setQuestionActions((prev) => ({
              ...prev,
              [orderIndex]: frame.data.next_action,
            }));
            setTurnStates((prev) => ({
              ...prev,
              [orderIndex]: {
                ...(prev[orderIndex] ?? { status: 'running' as const }),
                status: 'running',
                phase: 'decision_done',
                nextAction: frame.data.next_action,
                triggeredBy: frame.data.triggered_by,
                decisionReason: frame.data.decision_reason,
                exitReason: frame.data.exit_reason,
              },
            }));
            setProgressItems((items) =>
              appendProgress(items, {
                id: `turn-decision-${orderIndex}-${Date.now()}`,
                group: 'judge',
                label: nextActionLabel(frame.data.next_action),
                detail: frame.data.decision_reason,
              }),
            );
          } else if (frame.event === 'result') {
            const prompt = frame.data.remediation_prompt;
            setAnswers((prev) => ({
              ...prev,
              [orderIndex]: '',
            }));
            savedAnswersRef.current[orderIndex] = '';
            setQuestionResults((prev) => ({
              ...prev,
              [orderIndex]: {
                scores: frame.data.scores,
                evidence: prev[orderIndex]?.evidence ?? null,
                coachMessage:
                  frame.data.coach_message ?? prev[orderIndex]?.coachMessage ?? null,
              },
            }));
            setRemediationPrompts((prev) => ({ ...prev, [orderIndex]: prompt }));
            setQuestionActions((prev) => ({
              ...prev,
              [orderIndex]: frame.data.next_action,
            }));
            setTurnStates((prev) => ({
              ...prev,
              [orderIndex]: {
                ...(prev[orderIndex] ?? { status: 'running' as const }),
                status: 'done',
                phase: 'result',
                roundIndex: frame.data.round_index,
                scores: frame.data.scores,
                nextAction: frame.data.next_action,
                remediationPrompt: prompt,
                coachMessage:
                  frame.data.coach_message ?? prev[orderIndex]?.coachMessage ?? null,
              },
            }));
            if (frame.data.next_action === 'ask_next') {
              const nextQuestion = questions.find((q) => q.order_index === orderIndex + 1);
              if (nextQuestion) setActiveQuestionOrder(nextQuestion.order_index);
            }
          } else if (frame.event === 'error') {
            const message = `${frame.data.code}: ${frame.data.detail}`;
            setRunError(message);
            setTurnStates((prev) => ({
              ...prev,
              [orderIndex]: {
                ...(prev[orderIndex] ?? { status: 'error' as const }),
                status: 'error',
                error: message,
              },
            }));
          } else if (frame.event === 'done') {
            ok = frame.data.ok;
          }
        }

        if (ok) {
          try {
            const detail = await getQuizSession(sessionId);
            mergeQuestionDetails(detail);
          } catch (err) {
            setSaveState({ kind: 'error', message: problemMessage(err) });
          }
          void reloadRecent();
        }
      } catch (err) {
        const message = problemMessage(err);
        setRunError(message);
        setTurnStates((prev) => ({
          ...prev,
          [orderIndex]: {
            ...(prev[orderIndex] ?? { status: 'error' as const }),
            status: 'error',
            error: message,
          },
        }));
      } finally {
        setActiveTurnOrder(null);
      }
    },
    [
      activeTurnOrder,
      answerTurns,
      answers,
      mergeQuestionDetails,
      questionActions,
      questionResults,
      questions,
      reloadRecent,
      remediationPrompts,
      sessionId,
      stage,
    ],
  );

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
    setActiveQuestionOrder(null);
    setAnswers({});
    setTypeMix(null);
    setQuestionResults({});
    setAnswerTurns({});
    setJudgeTurns({});
    setCoachTurns({});
    setRemediationPrompts({});
    setQuestionActions({});
    setTurnStates({});
    setActiveTurnOrder(null);
    setFinalResult(null);
    setSaveState({ kind: 'idle' });
    setProgressItems([{ id: 'start', group: 'quiz', label: '准备出题' }]);
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
          updateSessionUrl(frame.data.resource_id);
          setProgressItems((items) =>
            appendProgress(items, {
              id: `session-${frame.data.resource_id}`,
              group: 'quiz',
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
          setActiveQuestionOrder((current) => current ?? received[0]?.order_index ?? null);
          setProgressItems((items) =>
            appendProgress(items, {
              id: `question-${frame.data.order_index}`,
              group: 'quiz',
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
        void reloadRecent();
      } else {
        setStage('idle');
        if (!streamError) setRunError('出题没有返回可答题目');
        void reloadRecent();
      }
    } catch (err) {
      setStage('idle');
      setRunError(problemMessage(err));
      void reloadRecent();
    }
  }, [questionCount, query, reloadRecent]);

  const handleSubmit = useCallback(async () => {
    if (!canSubmit || sessionId === null) return;
    const missing = questions
      .filter((q) => {
        const turns = answerTurns[q.order_index] ?? [];
        return turns.length === 0 && !(answers[q.order_index] ?? '').trim();
      })
      .map((q) => q.order_index + 1);
    if (missing.length) {
      setRunError(`还有题目未作答:${missing.join(', ')}`);
      return;
    }

    setStage('submitting');
    setRunError(null);
    setQuestionResults({});
    setFinalResult(null);
    setProgressItems((items) =>
      appendProgress(items, { id: 'submit-start', group: 'judge', label: '提交评分' }),
    );

    let ok = false;
    try {
      await saveAllAnswers();
      for await (const frame of submitQuizSession(sessionId)) {
        if (frame.event === 'started') {
          setProgressItems((items) =>
            appendProgress(items, {
              id: 'judge-started',
              group: 'judge',
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
              coachMessage: frame.data.coach_message ?? null,
            },
          }));
          setProgressItems((items) =>
            appendProgress(items, {
              id: `judge-done-${frame.data.order_index}`,
              group: 'judge',
              label: `第 ${frame.data.order_index + 1} 题评分完成`,
              detail: `总分 ${roundedScore(frame.data.scores.total)}`,
            }),
          );
        } else if (frame.event === 'result') {
          setFinalResult({
            scores: frame.data.scores,
            recallMdPath: frame.data.recall_md_path,
            summary: null,
          });
        } else if (frame.event === 'error') {
          setRunError(`${frame.data.code}: ${frame.data.detail}`);
        } else if (frame.event === 'done') {
          ok = frame.data.ok;
        }
      }
      setStage(ok ? 'submitted' : 'answering');
      void reloadRecent();
    } catch (err) {
      setStage('answering');
      setRunError(problemMessage(err));
      void reloadRecent();
    }
  }, [answerTurns, answers, canSubmit, questions, reloadRecent, saveAllAnswers, sessionId]);

  const handleFinishSession = useCallback(async () => {
    if (!canFinish || sessionId === null) return;

    setStage('submitting');
    setRunError(null);
    setProgressItems((items) =>
      appendProgress(items, { id: 'finish-start', group: 'judge', label: '生成整场总结' }),
    );

    let ok = false;
    try {
      for await (const frame of finishQuizSession(sessionId)) {
        if (frame.event === 'started') {
          setProgressItems((items) =>
            appendProgress(items, {
              id: 'finish-started',
              group: 'judge',
              label: '总结已开始',
              detail: `${frame.data.total_questions} 题`,
            }),
          );
        } else if (frame.event === 'progress') {
          setProgressItems((items) =>
            appendProgress(items, {
              id: `finish-progress-${frame.data.phase}-${Date.now()}`,
              group: 'judge',
              label: turnPhaseLabel(frame.data.phase),
              detail: frame.data.compacted ? '已压缩为 session summary' : undefined,
            }),
          );
        } else if (frame.event === 'result') {
          setFinalResult({
            scores: frame.data.scores,
            recallMdPath: frame.data.recall_md_path,
            summary: frame.data.summary ?? null,
          });
        } else if (frame.event === 'error') {
          setRunError(`${frame.data.code}: ${frame.data.detail}`);
        } else if (frame.event === 'done') {
          ok = frame.data.ok;
        }
      }

      if (ok) {
        const detail = await getQuizSession(sessionId);
        hydrateSession(detail);
        void reloadRecent();
      } else {
        setStage('answering');
      }
    } catch (err) {
      setStage('answering');
      setRunError(problemMessage(err));
      void reloadRecent();
    }
  }, [canFinish, hydrateSession, reloadRecent, sessionId]);

  const handleAbandon = useCallback(async () => {
    if (sessionId === null) {
      resetSession();
      return;
    }
    if (!window.confirm(`放弃 session #${sessionId}?`)) return;
    try {
      await abandonQuizSession(sessionId);
      resetSession();
      void reloadRecent();
    } catch (err) {
      setRunError(problemMessage(err));
    }
  }, [reloadRecent, resetSession, sessionId]);

  return (
    <div className="grid h-full grid-cols-1 bg-background lg:grid-cols-[280px_1fr]">
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
            <div className="relative">
              <select
                value={questionCount}
                disabled={controlsLocked}
                onChange={(event) => setQuestionCount(Number(event.target.value))}
                className="h-11 w-full appearance-none rounded-xl border border-border bg-[var(--color-system-gray-6)] px-3 pr-10 text-sm font-medium text-foreground shadow-inner outline-none transition-colors focus:border-accent"
              >
                {QUESTION_COUNTS.map((count) => (
                  <option key={count} value={count}>
                    {count} 题
                  </option>
                ))}
              </select>
              <ChevronDown className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-muted" />
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

          {questions.length ? (
            <QuestionGroup
              topic={query}
              questions={questions}
              activeOrder={activeQuestion?.order_index ?? null}
              activeTurnOrder={activeTurnOrder}
              answerTurns={answerTurns}
              results={questionResults}
              actions={questionActions}
              turnStates={turnStates}
              onSelect={setActiveQuestionOrder}
            />
          ) : null}

          <div className="border-t border-border pt-4">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs font-medium text-muted">最近练习</span>
              <button
                type="button"
                onClick={() => void reloadRecent()}
                className="text-xs text-muted hover:text-foreground"
              >
                刷新
              </button>
            </div>
            {recentError ? (
              <p className="rounded-lg bg-[var(--color-warning-bg)] px-3 py-2 text-xs text-[var(--color-warning-fg)]">
                {recentError}
              </p>
            ) : recentSessions.length === 0 ? (
              <p className="text-xs text-muted">暂无 session</p>
            ) : (
              <ul className="space-y-1">
                {recentSessions.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => void loadSession(item.id)}
                      disabled={stage === 'generating' || stage === 'submitting'}
                      className={cn(
                        'w-full rounded-lg px-3 py-2 text-left transition-colors',
                        sessionId === item.id
                          ? 'bg-[var(--color-selection)]'
                          : 'hover:bg-[var(--color-system-gray-6)]',
                      )}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="min-w-0 truncate text-xs font-medium text-foreground">
                          {item.query || '未命名练习'}
                        </span>
                        <span
                          className={cn(
                            'shrink-0 text-[11px] font-medium',
                            sessionStatusTone(item.status),
                          )}
                        >
                          {sessionStatusLabel(item.status)}
                        </span>
                      </div>
                      <div className="mt-1 flex items-center justify-between gap-2 text-[11px] text-muted">
                        <span>#{item.id} · {item.question_count} 题</span>
                        <span>
                          {item.total_score === null
                            ? shortDateTime(item.started_at)
                            : `总分 ${roundedScore(item.total_score)}`}
                        </span>
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="border-t border-border pt-4">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs font-medium text-muted">进度</span>
              {sessionId ? <span className="text-xs text-muted">#{sessionId}</span> : null}
            </div>
            <div className="space-y-2">
              <ProgressSummary
                title="出题流程"
                items={quizProgressItems}
                active={stage === 'generating'}
                done={questions.length > 0}
                meta={
                  questions.length
                    ? `${questions.length} 题 · ${typeMix ? `开放 ${typeMix.open_ended} / 八股 ${typeMix.definition}` : '已生成'}`
                    : undefined
                }
              />
              <ProgressSummary
                title="评分流程"
                items={judgeProgressItems}
                active={stage === 'submitting' || activeTurnOrder !== null}
                done={stage === 'submitted'}
                meta={finalResult ? `总分 ${roundedScore(finalResult.scores.total)}` : undefined}
              />
            </div>
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
                    ? activeTurnOrder !== null
                      ? `第 ${activeTurnOrder + 1} 题推进中`
                      : `${answeredCount}/${questions.length} 已答`
                    : stage === 'submitting'
                      ? allQuestionsJudged
                        ? '正在总结'
                        : '正在评分'
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
              <Button
                className="rounded-lg"
                onClick={canFinish ? handleFinishSession : handleSubmit}
                disabled={stage === 'submitting' || (!canFinish && !canSubmit)}
              >
                {stage === 'submitting' ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : canFinish ? (
                  <CheckCircle2 className="size-4" />
                ) : (
                  <Send className="size-4" />
                )}
                {stage === 'submitting'
                  ? allQuestionsJudged
                    ? '总结中'
                    : '评分中'
                  : canFinish
                    ? '生成总结'
                    : '提交评分'}
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
            <div className="mx-auto flex max-w-[960px] flex-col gap-5">
              {typeMix ? <TypeMixBar typeMix={typeMix} /> : null}
              {finalResult ? <FinalScore result={finalResult} /> : null}
              {activeQuestion ? (
                <QuestionPanel
                  key={activeQuestion.question.id}
                  item={activeQuestion}
                  answer={answers[activeQuestion.order_index] ?? ''}
                  disabled={
                    stage === 'submitting' ||
                    stage === 'submitted' ||
                    (activeTurnOrder !== null && activeTurnOrder !== activeQuestion.order_index)
                  }
                  result={questionResults[activeQuestion.order_index]}
                  answerTurns={answerTurns[activeQuestion.order_index] ?? []}
                  judgeTurns={judgeTurns[activeQuestion.order_index] ?? []}
                  coachTurns={coachTurns[activeQuestion.order_index] ?? []}
                  remediationPrompt={remediationPrompts[activeQuestion.order_index]}
                  nextAction={questionActions[activeQuestion.order_index]}
                  turnState={turnStates[activeQuestion.order_index]}
                  turnBusy={activeTurnOrder === activeQuestion.order_index}
                  canSubmitTurn={
                    stage === 'answering' &&
                    sessionId !== null &&
                    activeTurnOrder === null &&
                    (answers[activeQuestion.order_index] ?? '').trim().length > 0
                  }
                  canAskCoach={
                    stage === 'answering' &&
                    sessionId !== null &&
                    activeTurnOrder === null &&
                    (answers[activeQuestion.order_index] ?? '').trim().length > 0 &&
                    Boolean(
                      questionResults[activeQuestion.order_index]?.coachMessage ||
                        remediationPrompts[activeQuestion.order_index] ||
                        turnStates[activeQuestion.order_index]?.coachMessage ||
                        turnStates[activeQuestion.order_index]?.remediationPrompt,
                    )
                  }
                  onAnswer={(value) =>
                    setAnswers((prev) => ({ ...prev, [activeQuestion.order_index]: value }))
                  }
                  onSubmitTurn={() => void handleSubmitTurn(activeQuestion.order_index, 'answer')}
                  onAskCoach={() =>
                    void handleSubmitTurn(activeQuestion.order_index, 'coach_question')
                  }
                />
              ) : null}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

function QuestionGroup({
  topic,
  questions,
  activeOrder,
  activeTurnOrder,
  answerTurns,
  results,
  actions,
  turnStates,
  onSelect,
}: {
  topic: string;
  questions: QuizQuestionReady[];
  activeOrder: number | null;
  activeTurnOrder: number | null;
  answerTurns: Record<number, QuizAnswerTurn[]>;
  results: Record<number, QuestionResult>;
  actions: Record<number, string | null>;
  turnStates: Record<number, QuestionTurnState>;
  onSelect: (orderIndex: number) => void;
}) {
  const topicTitle = topic.trim() || '未命名练习';

  return (
    <section className="border-t border-border pt-4">
      <div className="mb-2 flex items-center gap-2 rounded-xl px-1.5 py-1.5 text-muted">
        <Folder className="size-4 shrink-0" strokeWidth={1.8} />
        <span className="min-w-0 truncate text-[15px] font-medium" title={topicTitle}>
          {topicTitle}
        </span>
        <span className="ml-auto shrink-0 text-xs">{questions.length} 题</span>
      </div>
      <div className="space-y-0.5">
        {questions.map((item) => {
          const orderIndex = item.order_index;
          const selected = activeOrder === orderIndex;
          const turns = answerTurns[orderIndex] ?? [];
          const action = actions[orderIndex] ?? turnStates[orderIndex]?.nextAction ?? null;
          const result = results[orderIndex] ?? (turnStates[orderIndex]?.scores ? { scores: turnStates[orderIndex].scores } : null);
          const running = activeTurnOrder === orderIndex;
          const status = running
            ? '推进中'
            : action === 'remediate'
              ? '待补答'
              : action === 'summarize'
                ? '可总结'
              : result
                ? '已评分'
                : turns.length > 0
                  ? '已提交'
                  : '开放题';

          return (
            <button
              key={item.question.id}
              type="button"
              onClick={() => onSelect(orderIndex)}
              className={cn(
                'w-full rounded-2xl py-2.5 pr-3 pl-8 text-left transition-colors duration-150 ease-apple',
                selected
                  ? 'bg-[var(--color-selection)]'
                  : 'hover:bg-[var(--color-system-gray-6)]',
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-[13px] font-semibold text-foreground">第 {orderIndex + 1} 题</span>
                <span
                  className={cn(
                    'shrink-0 text-[11px] font-medium',
                    action === 'remediate'
                      ? 'text-[var(--color-warning-fg)]'
                      : result
                        ? 'text-[var(--color-success-fg)]'
                        : running
                          ? 'text-accent'
                          : 'text-muted',
                  )}
                >
                  {status}
                </span>
              </div>
              <div className="mt-1 line-clamp-2 text-[13px] leading-5 text-muted">
                {item.question.prompt}
              </div>
              <div className="mt-1 flex items-center justify-between gap-2 text-[11px] text-muted">
                <span>{item.question.type === 'definition' ? '八股' : '开放'}</span>
                {turns.length ? <span>{turns.length} 轮</span> : null}
              </div>
            </button>
          );
        })}
      </div>
    </section>
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

function ProgressSummary({
  title,
  items,
  active,
  done,
  meta,
}: {
  title: string;
  items: ProgressItem[];
  active: boolean;
  done: boolean;
  meta?: string;
}) {
  const visibleItems = items.slice(-2);
  const status = active ? '进行中' : done ? '完成' : '等待';
  return (
    <section className="rounded-lg border border-border bg-[var(--color-system-gray-6)] px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-foreground">{title}</span>
        <span
          className={cn(
            'text-[11px] font-medium',
            active
              ? 'text-accent'
              : done
                ? 'text-[var(--color-success-fg)]'
                : 'text-muted',
          )}
        >
          {status}
        </span>
      </div>
      {meta ? <div className="mt-1 truncate text-[11px] text-muted">{meta}</div> : null}
      {visibleItems.length ? (
        <ol className="mt-2 space-y-1">
          {visibleItems.map((item) => (
            <li key={item.id} className="flex gap-2 text-[11px]">
              <span className="mt-[6px] size-1 rounded-full bg-accent" aria-hidden="true" />
              <span className="min-w-0">
                <span className="block truncate text-foreground">{item.label}</span>
                {item.detail ? <span className="block truncate text-muted">{item.detail}</span> : null}
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <div className="mt-2 text-[11px] text-muted">还没有开始</div>
      )}
    </section>
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
  result: {
    scores: QuizScores;
    recallMdPath?: string | null;
    summary?: QuizSessionSummary | null;
  };
}) {
  const summary = result.summary;
  const strengths = summary?.strengths ?? [];
  const gaps = summary?.recurring_gaps ?? [];
  const wins = summary?.remediation_wins ?? [];
  const suggestions = summary?.review_suggestions ?? [];

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
      {summary ? (
        <div className="mt-4 space-y-3 border-t border-[var(--color-success-border)] pt-3 text-[var(--color-success-fg)]">
          {summary.headline ? <p className="text-sm leading-6">{summary.headline}</p> : null}
          {strengths.length ? (
            <SummaryLine title="做得好" items={strengths} />
          ) : null}
          {gaps.length ? (
            <SummaryLine
              title="反复缺口"
              items={gaps.map((gap) => `${gap.label ?? gap.key ?? '缺口'} ${gap.count ?? 0} 处`)}
            />
          ) : null}
          {wins.length ? <SummaryLine title="补答修正" items={wins} /> : null}
          {suggestions.length ? <SummaryLine title="复习建议" items={suggestions} /> : null}
          {result.recallMdPath ? (
            <div className="text-[11px] opacity-80">沉淀: {result.recallMdPath}</div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function SummaryLine({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="grid gap-1 text-xs leading-5 sm:grid-cols-[72px_1fr]">
      <div className="font-semibold">{title}</div>
      <div className="space-y-0.5">
        {items.map((item, index) => (
          <div key={`${title}-${index}`}>{item}</div>
        ))}
      </div>
    </div>
  );
}

function QuestionPanel({
  item,
  answer,
  disabled,
  result,
  answerTurns,
  judgeTurns,
  coachTurns,
  remediationPrompt,
  nextAction,
  turnState,
  turnBusy,
  canSubmitTurn,
  canAskCoach,
  onAnswer,
  onSubmitTurn,
  onAskCoach,
}: {
  item: QuizQuestionReady;
  answer: string;
  disabled: boolean;
  result?: QuestionResult;
  answerTurns: QuizAnswerTurn[];
  judgeTurns: QuizJudgeTurn[];
  coachTurns: QuizCoachTurn[];
  remediationPrompt?: QuizRemediationPrompt | null;
  nextAction?: string | null;
  turnState?: QuestionTurnState;
  turnBusy: boolean;
  canSubmitTurn: boolean;
  canAskCoach: boolean;
  onAnswer: (value: string) => void;
  onSubmitTurn: () => void;
  onAskCoach: () => void;
}) {
  const promptText = remediationText(remediationPrompt ?? turnState?.remediationPrompt);
  const currentAction = nextAction ?? turnState?.nextAction ?? null;
  const submitLabel = answerTurns.length > 0 || promptText ? '发送补答' : '发送回答';
  const scores = turnState?.scores ?? result?.scores;
  const coachMessage = turnState?.coachMessage ?? result?.coachMessage ?? null;
  const judgeByRound = new Map<number, QuizJudgeTurn>();
  let latestJudgeRound = -1;
  for (const judgeTurn of judgeTurns) {
    const roundIndex = typeof judgeTurn.round_index === 'number' ? judgeTurn.round_index : 0;
    judgeByRound.set(roundIndex, judgeTurn);
    latestJudgeRound = Math.max(latestJudgeRound, roundIndex);
  }
  const canShowFeedbackFallback = judgeTurns.length === 0 && Boolean(
    coachMessage || scores || promptText || turnState?.decisionReason,
  );

  return (
    <article className="overflow-hidden rounded-[28px] border border-border bg-[#f5f5f7]">
      <div className="border-b border-border/70 bg-white/70 px-5 py-4 backdrop-blur-xl">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <span className="rounded-full bg-[var(--color-system-gray-6)] px-2.5 py-1 text-xs font-medium text-muted">
              第 {item.order_index + 1} 题
            </span>
            <span className="rounded-full bg-[var(--color-system-gray-6)] px-2.5 py-1 text-xs text-muted">
              {item.question.type === 'definition' ? '八股' : '开放题'}
            </span>
            <span
              className="truncate text-xs text-muted"
              title={`chunks ${item.question.evidence_chunk_ids.join(', ')}`}
            >
              来源 {item.question.evidence_chunk_ids.length} 段
            </span>
          </div>
          {currentAction ? (
            <span
              className={cn(
                'rounded-full px-2.5 py-1 text-xs font-medium',
                currentAction === 'remediate'
                  ? 'bg-[var(--color-warning-bg)] text-[var(--color-warning-fg)]'
                  : 'bg-[var(--color-success-bg)] text-[var(--color-success-fg)]',
              )}
            >
              {nextActionLabel(currentAction)}
            </span>
          ) : null}
        </div>
      </div>

      <div className="space-y-4 px-4 py-5 sm:px-6">
        <ChatBubble role="coach">
          <div className="space-y-2">
            <p className="text-base font-semibold leading-7 tracking-tight text-foreground">
              {item.question.prompt}
            </p>
          </div>
        </ChatBubble>

        {answerTurns.map((turn, index) => {
          const roundIndex = typeof turn.round_index === 'number' ? turn.round_index : index;
          const judgeTurn = judgeByRound.get(roundIndex);
          return (
            <div
              key={`${turn.client_turn_id ?? index}-${turn.submitted_at ?? ''}`}
              className="space-y-3"
            >
              <ChatBubble role="user">
                <div className="mb-1 text-[11px] font-medium opacity-75">
                  {turn.turn_type === 'remediation' ? `第 ${index + 1} 轮补答` : '初答'}
                </div>
                <p className="whitespace-pre-wrap text-sm leading-7">
                  {turn.text || '（空答案）'}
                </p>
              </ChatBubble>
              {judgeTurn ? (
                <CoachFeedbackBubble
                  label={turn.turn_type === 'remediation' ? `第 ${index + 1} 轮评分` : '初答评分'}
                  result={roundIndex === latestJudgeRound ? result : undefined}
                  scores={judgeTurn.scores}
                  remediationPrompt={judgeTurn.remediation_prompt ?? null}
                  coachMessage={judgeTurn.coach_message ?? null}
                  turnState={{
                    status: 'done',
                    nextAction: judgeTurn.next_action ?? undefined,
                    triggeredBy: judgeTurn.triggered_by ?? undefined,
                    decisionReason: judgeTurn.decision_reason ?? undefined,
                    exitReason: judgeTurn.exit_reason ?? undefined,
                    unresolvedGaps: judgeTurn.unresolved_gaps,
                  }}
                />
              ) : null}
            </div>
          );
        })}

        {turnBusy || turnState?.status === 'running' ? (
          <ChatBubble role="coach" muted>
            <div className="flex items-center gap-2 text-sm text-muted">
              <Loader2 className="size-4 animate-spin" />
              {turnPhaseLabel(turnState?.phase)}
            </div>
          </ChatBubble>
        ) : null}

        {canShowFeedbackFallback ? (
          <CoachFeedbackBubble
            result={result}
            scores={scores}
            remediationPrompt={remediationPrompt ?? turnState?.remediationPrompt ?? null}
            coachMessage={coachMessage}
            turnState={turnState}
          />
        ) : answerTurns.length === 0 ? (
          <ChatBubble role="coach" muted>
            <p className="text-sm leading-6 text-muted">
              直接像面试一样回答就好。需要补答时，你只发新增内容。
            </p>
          </ChatBubble>
        ) : null}

        {coachTurns.map((turn, index) => (
          <div
            key={`coach-${turn.client_turn_id ?? index}-${
              turn.answered_at ?? turn.submitted_at ?? ''
            }`}
            className="space-y-2"
          >
            <ChatBubble role="user">
              <div className="mb-1 text-[11px] font-medium opacity-75">追问教练</div>
              <p className="whitespace-pre-wrap text-sm leading-7">{turn.text || '（空追问）'}</p>
            </ChatBubble>
            <ChatBubble role="coach">
              <div className="mb-1 text-[11px] font-semibold tracking-wide text-muted uppercase">
                Coach
              </div>
              <p className="whitespace-pre-wrap text-sm leading-7">
                {turn.coach_message || '教练还没有回复'}
              </p>
            </ChatBubble>
          </div>
        ))}

        <div className="flex items-end gap-2 rounded-[26px] border border-[var(--color-system-gray-4)] bg-white px-3 py-2 shadow-[var(--shadow-apple-sm)]">
          <Textarea
            value={answer}
            onChange={(event) => onAnswer(event.target.value)}
            disabled={disabled || turnBusy}
            className="min-h-11 flex-1 resize-none border-0 bg-transparent px-1 py-2 leading-6 shadow-none focus-visible:ring-0"
            placeholder={answerTurns.length > 0 ? '只写这一轮补充，不需要重复前文' : '在这里作答'}
          />
          <Button
            variant="ghost"
            className="mb-1 size-8 shrink-0 rounded-full p-0"
            size="icon"
            onClick={onAskCoach}
            disabled={disabled || turnBusy || !canAskCoach}
            title="问教练"
            aria-label="问教练"
          >
            <MessageCircleQuestion className="size-4" />
          </Button>
          <Button
            className="mb-1 size-8 shrink-0 rounded-full p-0"
            size="icon"
            onClick={onSubmitTurn}
            disabled={disabled || turnBusy || !canSubmitTurn}
            title={submitLabel}
            aria-label={submitLabel}
          >
            {turnBusy ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
          </Button>
        </div>
        <div className="px-2 text-xs text-muted">
          {answerTurns.length > 0 ? `${answerTurns.length} 轮已提交` : '尚未提交本题'}
          {coachTurns.length > 0 ? ` · ${coachTurns.length} 次追问` : null}
          {turnState?.status === 'error' && turnState.error ? (
            <span className="text-[var(--color-danger)]"> · {turnState.error}</span>
          ) : null}
        </div>
      </div>
    </article>
  );
}

function ChatBubble({
  role,
  muted = false,
  children,
}: {
  role: 'coach' | 'user';
  muted?: boolean;
  children: ReactNode;
}) {
  const isUser = role === 'user';
  return (
    <div className={cn('flex w-full', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'max-w-[84%] rounded-[22px] px-4 py-2.5 text-sm lg:max-w-[620px]',
          isUser
            ? 'rounded-br-md bg-[#007aff] text-white'
            : muted
              ? 'rounded-bl-md bg-[#e9e9eb] text-muted'
              : 'rounded-bl-md bg-[#e9e9eb] text-foreground',
        )}
      >
        {children}
      </div>
    </div>
  );
}

function CoachFeedbackBubble({
  label = 'Feedback',
  result,
  scores,
  remediationPrompt,
  coachMessage,
  turnState,
}: {
  label?: string;
  result?: QuestionResult;
  scores?: QuizScores;
  remediationPrompt?: QuizRemediationPrompt | null;
  coachMessage?: string | null;
  turnState?: QuestionTurnState;
}) {
  const feedback = coachFeedbackText({
    coachMessage,
    scores,
    remediationPrompt,
    turnState,
  });

  return (
    <ChatBubble role="coach">
      <div className="space-y-3">
        <div>
          <div className="mb-1 text-[11px] font-semibold tracking-wide text-muted uppercase">
            {label}
          </div>
          <p className="whitespace-pre-wrap text-sm leading-7 text-foreground">{feedback}</p>
        </div>
        {scores ? (
          <div className="flex flex-wrap gap-2">
            <MiniScore label="Coverage" value={scores.coverage} />
            <MiniScore label="Fidelity" value={scores.fidelity} />
            <MiniScore label="Depth" value={scores.depth} />
            <MiniScore label="Total" value={scores.total} strong />
          </div>
        ) : null}
        {result ? (
          <details className="group rounded-2xl border border-border bg-[var(--color-system-gray-6)]/70 px-3 py-2">
            <summary className="cursor-pointer list-none text-xs font-medium text-muted group-open:text-foreground">
              查看评分细节
            </summary>
            <QuestionScore result={result} compact />
          </details>
        ) : scores ? (
          <details className="group rounded-2xl border border-border bg-[var(--color-system-gray-6)]/70 px-3 py-2">
            <summary className="cursor-pointer list-none text-xs font-medium text-muted group-open:text-foreground">
              查看分数
            </summary>
            <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
              <ScorePill label="Coverage" value={scores.coverage} />
              <ScorePill label="Fidelity" value={scores.fidelity} />
              <ScorePill label="Depth" value={scores.depth} />
              <ScorePill label="Total" value={scores.total} strong />
            </div>
          </details>
        ) : null}
      </div>
    </ChatBubble>
  );
}

function QuestionScore({ result, compact = false }: { result: QuestionResult; compact?: boolean }) {
  if (!result.evidence) {
    return (
      <div className={cn('grid grid-cols-2 gap-2 md:grid-cols-4', compact ? 'mt-3' : 'mt-4')}>
        <ScorePill label="Coverage" value={result.scores.coverage} />
        <ScorePill label="Fidelity" value={result.scores.fidelity} />
        <ScorePill label="Depth" value={result.scores.depth} />
        <ScorePill label="Total" value={result.scores.total} strong />
      </div>
    );
  }

  const coverage = coverageEvidence(result.evidence);
  const fidelity = fidelityEvidence(result.evidence);
  const depth = depthEvidence(result.evidence);

  return (
    <div className={cn('space-y-4', compact ? 'mt-3' : 'mt-4')}>
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
              {claim.supporting_chunk_ids?.length ? (
                <span className="truncate text-[11px] text-muted">
                  chunks {claim.supporting_chunk_ids.join(', ')}
                </span>
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

function MiniScore({
  label,
  value,
  strong = false,
}: {
  label: string;
  value: number;
  strong?: boolean;
}) {
  return (
    <span
      className={cn(
        'rounded-full px-2.5 py-1 text-xs font-medium',
        strong
          ? 'bg-accent text-white'
          : 'bg-[var(--color-system-gray-6)] text-foreground',
      )}
    >
      {label} {roundedScore(value)}
    </span>
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
