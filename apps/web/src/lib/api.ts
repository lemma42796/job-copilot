import type { components } from '@jobcopilot/schemas';

import { getToken, handleUnauthorized } from './auth';
import { type SseFrame, streamSse } from './sse';

const API_BASE_URL =
  (typeof window === 'undefined' ? process.env.INTERNAL_API_BASE_URL : undefined) ??
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  (typeof window !== 'undefined'
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : 'http://127.0.0.1:8000');

/**
 * P0:所有业务请求都要带身份。生产走 `Authorization: Bearer <token>`;
 * 后端只在 `JOBCOPILOT_ENV=dev` 时才接受 `X-User-Id` 兜底,便于本地调试。
 */
const DEV_USER_ID = process.env.NEXT_PUBLIC_USER_ID ?? '1';

function authHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  const token = getToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  else if (process.env.NEXT_PUBLIC_DEV_AUTH === 'true') {
    headers.set('X-User-Id', DEV_USER_ID);
  }
  return headers;
}

export { authHeaders };

export type HealthResponse = components['schemas']['HealthResponse'];
export type JDStructured = components['schemas']['JDStructured'];
export type JDSkill = components['schemas']['JDSkill'];
export type JDDetail = components['schemas']['JDDetail'];
export type JDListItem = components['schemas']['JDListItem'];
export type JDListResponse = components['schemas']['JDListResponse'];
export type JDParseInput = components['schemas']['JDParseInput'];
export type JDParseResponse = components['schemas']['JDParseResponse'];
export type JDPatchInput = components['schemas']['JDPatchInput'];
export type JdStatus = components['schemas']['JdStatus'];

export type FileUploadResponse = components['schemas']['FileUploadResponse'];
export type ProfileDetail = components['schemas']['ProfileDetail'];
export type ProfileListItem = components['schemas']['ProfileListItem'];
export type ProfileListResponse = components['schemas']['ProfileListResponse'];
export type ProfileStructured = components['schemas']['ProfileStructured'];
export type ProfileExperienceItem = components['schemas']['ProfileExperienceItem'];
export type ProfileProjectItem = components['schemas']['ProfileProjectItem'];
export type ProfileSkillItem = components['schemas']['ProfileSkillItem'];
export type ProfileEducationItem = components['schemas']['ProfileEducationItem'];
export type ProfileStats = components['schemas']['ProfileStats'];
export type ProfileParseInput = components['schemas']['ProfileParseInput'];
export type ProfilePatchInput = components['schemas']['ProfilePatchInput'];
export type ProfileChunkItem = components['schemas']['ProfileChunkItem'];
export type ProfileChunksResponse = components['schemas']['ProfileChunksResponse'];

export type MatchCreateInput = components['schemas']['MatchCreateInput'];
export type MatchDetail = components['schemas']['MatchDetail'];
export type MatchListItem = components['schemas']['MatchListItem'];
export type MatchListResponse = components['schemas']['MatchListResponse'];
export type MatchResult = components['schemas']['MatchResult'];
export type MatchedSkill = components['schemas']['MatchedSkill'];
export type MissingSkill = components['schemas']['MissingSkill'];
export type MatchStatus = components['schemas']['MatchStatus'];

export type ResumeCreateInput = components['schemas']['ResumeCreateInput'];
export type ResumeDetail = components['schemas']['ResumeDetail'];
export type ResumeListItem = components['schemas']['ResumeListItem'];
export type ResumeListResponse = components['schemas']['ResumeListResponse'];
export type ResumeStatus = components['schemas']['ResumeStatus'];
export type ReviewFinding = components['schemas']['ReviewFinding'];

type Problem = {
  type?: string;
  title?: string;
  detail?: string;
  status?: number;
  code?: string;
};

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly problem: Problem,
  ) {
    super(problem.detail ?? problem.title ?? `HTTP ${status}`);
    this.name = 'ApiError';
  }
}

async function jsonFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = authHeaders(init.headers);
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const res = await fetch(`${API_BASE_URL}${path}`, {
    cache: 'no-store',
    ...init,
    headers,
  });
  if (!res.ok) {
    const problem = (await res.json().catch(() => ({}))) as Problem;
    if (res.status === 401) handleUnauthorized();
    throw new ApiError(res.status, problem);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return jsonFetch<HealthResponse>('/v1/health', { signal });
}

export async function parseJd(input: JDParseInput): Promise<JDParseResponse> {
  return jsonFetch<JDParseResponse>('/v1/jds/parse', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export async function getJd(id: number, signal?: AbortSignal): Promise<JDDetail> {
  return jsonFetch<JDDetail>(`/v1/jds/${id}`, { signal });
}

export async function listJds(
  opts: { cursor?: string | null; limit?: number; signal?: AbortSignal } = {},
): Promise<JDListResponse> {
  const params = new URLSearchParams();
  if (opts.cursor) params.set('cursor', opts.cursor);
  if (opts.limit) params.set('limit', String(opts.limit));
  const qs = params.toString();
  return jsonFetch<JDListResponse>(`/v1/jds${qs ? `?${qs}` : ''}`, { signal: opts.signal });
}

export async function patchJd(id: number, patch: JDPatchInput): Promise<JDDetail> {
  return jsonFetch<JDDetail>(`/v1/jds/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

export async function deleteJd(id: number): Promise<void> {
  await jsonFetch<void>(`/v1/jds/${id}`, { method: 'DELETE' });
}

// ---------------------------------------------------------------------------
// JD Intelligence(M2.5 — JD 库 + 上传即解析)
// ---------------------------------------------------------------------------
//
// schema 暂时手写,后续 pnpm gen:api 之后可以替换为
// components['schemas']['JdOut'] 等生成版本。

// Output keeps image_upload for possible legacy rows; create input is text-only.
export type JdLibrarySource = 'text_paste' | 'image_upload';
export type JdLibraryCreateSource = 'text_paste';

export type JdLibraryCreateInput = {
  source?: JdLibraryCreateSource;
  raw_text: string;
};

export type JdLibraryPatchInput = {
  title?: string | null;
};

export type JdParsedPayload = {
  title?: string;
  responsibilities?: string[];
  hard_skills?: string[];
  soft_skills?: string[];
  experience_years?: string | null;
  education?: string | null;
  extras?: Record<string, unknown>;
};

export type JdLibraryItem = {
  id: number;
  source: JdLibrarySource;
  title: string;
  raw_text: string;
  parsed_payload: JdParsedPayload & Record<string, unknown>;
  parse_model?: string | null;
  parse_prompt_version?: string | null;
  parse_tokens_in?: number | null;
  parse_tokens_out?: number | null;
  parse_cost_cny?: string | number | null;
  created_at: string;
  updated_at: string;
};

export type JdLibraryListItem = {
  id: number;
  title: string;
  source: JdLibrarySource;
  raw_text_preview: string;
  hard_skills_count: number;
  created_at: string;
};

export type JdLibraryListResponse = {
  items: JdLibraryListItem[];
  next_cursor: number | null;
  has_more: boolean;
};

export type JdAnalysisFilter =
  | { type: 'all'; value?: null; ids?: null; n?: null }
  | { type: 'title'; value: string; ids?: null; n?: null }
  | { type: 'ids'; ids: number[]; value?: null; n?: null }
  | { type: 'recent'; n: number; value?: null; ids?: null };

export type JdAnalysisCreateInput = {
  filter: JdAnalysisFilter;
  filter_description?: string | null;
};

export type AggregatedRequirement = {
  id: string;
  canonical_text: string;
  category: string;
  frequency: number;
  raw_phrases: string[];
  supporting_jd_ids: number[];
};

export type JdQuizTopicCandidate = {
  topic: string;
  priority: 'high' | 'medium' | 'low' | string;
  source_req_ids: string[];
  frequency: number;
  note_match_status: 'covered' | 'partial' | 'missing' | 'unknown' | string;
  category?: string;
};

export type JdNoteMatchSummaryItem = {
  req_id: string;
  canonical_text?: string;
  status: 'covered' | 'partial' | 'missing' | 'unknown' | string;
  matched_note_ids: number[];
  coverage_score?: number;
  matched_phrases?: string[];
  evidence_chunks?: JdCoverageEvidenceChunk[];
  matched_notes?: JdCoverageMatchedNote[];
};

export type JdCoverageEvidenceChunk = {
  chunk_id: number;
  note_id: number;
  note_title: string;
  folder_path: string[];
  heading_path: string[];
  matched_phrases: string[];
  match_type: 'canonical' | 'phrase' | string;
  snippet: string;
};

export type JdCoverageMatchedNote = {
  note_id: number;
  title: string;
  folder_path: string[];
  matched_phrases: string[];
  match_type: 'canonical' | 'phrase' | string;
};

export type JdAnalysisListItem = {
  id: number;
  jd_count: number;
  filter_description?: string | null;
  status: string;
  requirement_count: number;
  quiz_topic_count: number;
  started_at: string;
  completed_at?: string | null;
  failed_at?: string | null;
};

export type JdAnalysisListResponse = {
  items: JdAnalysisListItem[];
  next_cursor: number | null;
  has_more: boolean;
};

export type JdAnalysisReport = {
  id: number;
  jd_ids: number[];
  jd_count: number;
  filter_description?: string | null;
  status: string;
  aggregated_requirements: AggregatedRequirement[];
  learning_path_md?: string | null;
  quiz_topic_candidates: JdQuizTopicCandidate[];
  note_match_summary: JdNoteMatchSummaryItem[];
  total_tokens_in?: number | null;
  total_tokens_out?: number | null;
  total_cost_cny?: string | number | null;
  cache_hit_rate?: string | number | null;
  started_at: string;
  completed_at?: string | null;
  failed_at?: string | null;
  failure_reason?: string | null;
};

export type JdAnalysisSseFrame =
  // P3:POST 返回 202 后由客户端合成的第一帧,携带 job_id 供断线续读。
  | SseFrame<'accepted', { job_id: number; analysis_id: number | null }>
  | SseFrame<'started', { job_id: string; resource_id: number; jd_count: number }>
  | SseFrame<'progress', { phase: string; jd_count?: number; batch?: number; total?: number }>
  | SseFrame<
      'result',
      { analysis_id: number; requirement_count: number; quiz_topic_count: number; url: string }
    >
  | SseFrame<'error', { code: string; detail: string }>
  | SseFrame<'done', { ok: boolean }>;

export async function createJdLibraryItem(
  input: JdLibraryCreateInput,
): Promise<JdLibraryItem> {
  return jsonFetch<JdLibraryItem>('/api/jds', {
    method: 'POST',
    body: JSON.stringify({
      source: input.source ?? 'text_paste',
      raw_text: input.raw_text,
    }),
  });
}

export async function listJdLibrary(
  opts: {
    title?: string;
    cursor?: number | null;
    limit?: number;
    signal?: AbortSignal;
  } = {},
): Promise<JdLibraryListResponse> {
  const params = new URLSearchParams();
  if (opts.title) params.set('title', opts.title);
  if (opts.cursor) params.set('cursor', String(opts.cursor));
  if (opts.limit) params.set('limit', String(opts.limit));
  const qs = params.toString();
  return jsonFetch<JdLibraryListResponse>(`/api/jds${qs ? `?${qs}` : ''}`, {
    signal: opts.signal,
  });
}

export async function getJdLibraryItem(
  id: number,
  signal?: AbortSignal,
): Promise<JdLibraryItem> {
  return jsonFetch<JdLibraryItem>(`/api/jds/${id}`, { signal });
}

export async function patchJdLibraryItem(
  id: number,
  input: JdLibraryPatchInput,
): Promise<JdLibraryItem> {
  return jsonFetch<JdLibraryItem>(`/api/jds/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  });
}

export async function deleteJdLibraryItem(id: number): Promise<void> {
  await jsonFetch<void>(`/api/jds/${id}`, { method: 'DELETE' });
}

export function createJdAnalysis(
  input: JdAnalysisCreateInput,
): AsyncGenerator<JdAnalysisSseFrame> {
  // 先合成一帧把 job_id / analysis_id 交给页面 —— 断线后要靠 job_id 续读。
  return enqueueAndStream<JdAnalysisSseFrame>(
    '/api/jd-analyses',
    input,
    (accepted) =>
      ({
        event: 'accepted',
        data: { job_id: accepted.job_id, analysis_id: accepted.resource_id },
      }) as JdAnalysisSseFrame,
  );
}

/**
 * 恢复观察:analysis 页刷新后用 job_id 续读。
 *
 * 旧接口 `/api/jd-analyses/{id}/events` 已删除 —— 它依赖 API 进程内存里的
 * 订阅队列,多副本下订阅不到别的副本正在跑的任务。
 */
export function observeJdAnalysisJob(
  jobId: number,
  afterSeq = 0,
): AsyncGenerator<JdAnalysisSseFrame> {
  return streamJobEvents<JdAnalysisSseFrame>(jobId, afterSeq);
}

export async function listJdAnalyses(
  opts: { cursor?: number | null; limit?: number; signal?: AbortSignal } = {},
): Promise<JdAnalysisListResponse> {
  const params = new URLSearchParams();
  if (opts.cursor) params.set('cursor', String(opts.cursor));
  if (opts.limit) params.set('limit', String(opts.limit));
  const qs = params.toString();
  return jsonFetch<JdAnalysisListResponse>(`/api/jd-analyses${qs ? `?${qs}` : ''}`, {
    signal: opts.signal,
  });
}

export async function getJdAnalysis(
  id: number,
  signal?: AbortSignal,
): Promise<JdAnalysisReport> {
  return jsonFetch<JdAnalysisReport>(`/api/jd-analyses/${id}`, { signal });
}

// ---------------------------------------------------------------------------
// Files
// ---------------------------------------------------------------------------

export type FilePurposeValue =
  | 'jd_pdf'
  | 'jd_image'
  | 'profile_pdf'
  | 'profile_docx'
  | 'resume_pdf'
  | 'other';

export async function uploadFile(
  file: File,
  purpose: FilePurposeValue,
): Promise<FileUploadResponse> {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('purpose', purpose);
  const res = await fetch(`${API_BASE_URL}/v1/files/upload`, {
    method: 'POST',
    body: fd,
    headers: authHeaders(),
    cache: 'no-store',
  });
  if (!res.ok) {
    const problem = (await res.json().catch(() => ({}))) as Problem;
    throw new ApiError(res.status, problem);
  }
  return (await res.json()) as FileUploadResponse;
}

// ---------------------------------------------------------------------------
// Profiles
// ---------------------------------------------------------------------------

export type ProfileParseSseFrame =
  | SseFrame<'started', { job_id: string; resource_id: number }>
  | SseFrame<
      'chunking_embedding',
      | {
          ok: true;
          chunks_written: number;
          embed_model: string;
          tokens_in: number;
          cost_cny: string;
        }
      | { ok: false; error: string }
    >
  | SseFrame<'result', { resource_id: number; url: string }>
  | SseFrame<'error', { code: string; detail: string }>
  | SseFrame<'done', { ok: boolean }>;

export function parseProfile(input: ProfileParseInput): AsyncGenerator<ProfileParseSseFrame> {
  return streamSse<ProfileParseSseFrame>(`${API_BASE_URL}/v1/profiles/parse`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(input),
  });
}

export function rechunkProfile(profileId: number): AsyncGenerator<ProfileParseSseFrame> {
  return streamSse<ProfileParseSseFrame>(`${API_BASE_URL}/v1/profiles/${profileId}/rechunk`, {
    method: 'POST',
    headers: authHeaders(),
  });
}

export async function getProfile(id: number, signal?: AbortSignal): Promise<ProfileDetail> {
  return jsonFetch<ProfileDetail>(`/v1/profiles/${id}`, { signal });
}

export async function listProfiles(
  opts: { cursor?: string | null; limit?: number; signal?: AbortSignal } = {},
): Promise<ProfileListResponse> {
  const params = new URLSearchParams();
  if (opts.cursor) params.set('cursor', opts.cursor);
  if (opts.limit) params.set('limit', String(opts.limit));
  const qs = params.toString();
  return jsonFetch<ProfileListResponse>(`/v1/profiles${qs ? `?${qs}` : ''}`, {
    signal: opts.signal,
  });
}

export async function patchProfile(id: number, patch: ProfilePatchInput): Promise<ProfileDetail> {
  return jsonFetch<ProfileDetail>(`/v1/profiles/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

export async function deleteProfile(id: number): Promise<void> {
  await jsonFetch<void>(`/v1/profiles/${id}`, { method: 'DELETE' });
}

export async function listProfileChunks(
  id: number,
  signal?: AbortSignal,
): Promise<ProfileChunksResponse> {
  return jsonFetch<ProfileChunksResponse>(`/v1/profiles/${id}/chunks`, { signal });
}

// ---------------------------------------------------------------------------
// Matches
// ---------------------------------------------------------------------------

export type MatchSseFrame =
  | SseFrame<'started', { job_id: string; resource_id: number }>
  | SseFrame<'result', { resource_id: number; url: string; score: number | null }>
  | SseFrame<'error', { code: string; detail: string }>
  | SseFrame<'done', { ok: boolean }>;

export function createMatch(input: MatchCreateInput): AsyncGenerator<MatchSseFrame> {
  return streamSse<MatchSseFrame>(`${API_BASE_URL}/v1/matches`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(input),
  });
}

export async function getMatch(id: number, signal?: AbortSignal): Promise<MatchDetail> {
  return jsonFetch<MatchDetail>(`/v1/matches/${id}`, { signal });
}

export async function listMatches(
  opts: {
    cursor?: string | null;
    limit?: number;
    jdId?: number;
    profileId?: number;
    signal?: AbortSignal;
  } = {},
): Promise<MatchListResponse> {
  const params = new URLSearchParams();
  if (opts.cursor) params.set('cursor', opts.cursor);
  if (opts.limit) params.set('limit', String(opts.limit));
  if (opts.jdId) params.set('jd_id', String(opts.jdId));
  if (opts.profileId) params.set('profile_id', String(opts.profileId));
  const qs = params.toString();
  return jsonFetch<MatchListResponse>(`/v1/matches${qs ? `?${qs}` : ''}`, {
    signal: opts.signal,
  });
}

export async function deleteMatch(id: number): Promise<void> {
  await jsonFetch<void>(`/v1/matches/${id}`, { method: 'DELETE' });
}

// ---------------------------------------------------------------------------
// Resumes(简历定制)
// ---------------------------------------------------------------------------

export type ResumeNodeName = 'retrieve' | 'plan' | 'draft' | 'review' | 'revise';

export type DrafterPhase = 'draft' | 'revise';

export type ResumeSseFrame =
  | SseFrame<'started', { job_id: string; resource_id: number }>
  | SseFrame<'drafter_token', { phase: DrafterPhase; delta: string }>
  | SseFrame<'node_completed', { node: ResumeNodeName; revision_count: number }>
  | SseFrame<
      'result',
      {
        resource_id: number;
        url: string;
        status: ResumeStatus;
        review_passed: boolean | null;
        revisions: number;
      }
    >
  | SseFrame<'error', { code: string; detail: string }>
  | SseFrame<'done', { ok: boolean }>;

export function createResume(input: ResumeCreateInput): AsyncGenerator<ResumeSseFrame> {
  return streamSse<ResumeSseFrame>(`${API_BASE_URL}/v1/resumes/generate`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(input),
  });
}

export async function getResume(id: number, signal?: AbortSignal): Promise<ResumeDetail> {
  return jsonFetch<ResumeDetail>(`/v1/resumes/${id}`, { signal });
}

export async function listResumes(
  opts: {
    cursor?: string | null;
    limit?: number;
    jdId?: number;
    profileId?: number;
    matchId?: number;
    signal?: AbortSignal;
  } = {},
): Promise<ResumeListResponse> {
  const params = new URLSearchParams();
  if (opts.cursor) params.set('cursor', opts.cursor);
  if (opts.limit) params.set('limit', String(opts.limit));
  if (opts.jdId) params.set('jd_id', String(opts.jdId));
  if (opts.profileId) params.set('profile_id', String(opts.profileId));
  if (opts.matchId) params.set('match_id', String(opts.matchId));
  const qs = params.toString();
  return jsonFetch<ResumeListResponse>(`/v1/resumes${qs ? `?${qs}` : ''}`, {
    signal: opts.signal,
  });
}

export async function deleteResume(id: number): Promise<void> {
  await jsonFetch<void>(`/v1/resumes/${id}`, { method: 'DELETE' });
}

// ---------------------------------------------------------------------------
// Resume versions(W8 — monaco 编辑器 + 版本 diff)
// ---------------------------------------------------------------------------
//
// 这些类型暂时手写,等用户跑 `pnpm gen:api` 后可以替换为
// `components['schemas']['ResumeVersionItem']` 等生成版,与本文件其他 wire
// 类型保持一致(jds/profiles/matches 等)。

export type ResumeVersionEditType = 'generated' | 'edited' | 'regenerated';

export type ResumeVersionItem = {
  id: number;
  version_number: number;
  markdown: string;
  edit_type: ResumeVersionEditType | null;
  edit_note: string | null;
  created_at: string;
};

export type ResumeVersionListResponse = {
  data: ResumeVersionItem[];
};

export type ResumeVersionCreateInput = {
  markdown: string;
  note?: string | null;
};

export async function listResumeVersions(
  id: number,
  signal?: AbortSignal,
): Promise<ResumeVersionListResponse> {
  return jsonFetch<ResumeVersionListResponse>(`/v1/resumes/${id}/versions`, { signal });
}

export async function createResumeVersion(
  id: number,
  body: ResumeVersionCreateInput,
): Promise<ResumeVersionItem> {
  return jsonFetch<ResumeVersionItem>(`/v1/resumes/${id}/versions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// Notes(M1 — 树形导航 + Monaco 编辑器 + 本地目录批量导入)
// ---------------------------------------------------------------------------
//
// schema 暂时手写,后续 pnpm gen:api 之后可以替换为
// components['schemas']['NoteOut'] 等生成版本。

export type NoteOut = {
  id: number;
  folder_path: string[];
  title: string;
  content_md: string;
  created_at: string;
  updated_at: string;
};

export type TreeNode = {
  folder_path: string[];
  notes: NoteOut[];
  children: TreeNode[];
};

export type NoteCreateInput = {
  folder_path: string[];
  title: string;
  content_md: string;
};

export type NoteUpdateInput = {
  title?: string | null;
  content_md?: string | null;
  folder_path?: string[] | null;
};

export type NoteBatchImportItem = {
  folder_path: string[];
  title: string;
  content_md: string;
};

export type NoteBatchImportInput = {
  items: NoteBatchImportItem[];
  root_folder?: string | null;
  overwrite?: boolean;
};

export type BatchImportReport = {
  imported: number;
  skipped: number;
  skipped_reasons: { path: string; reason: string }[];
  note_ids: number[];
};

export async function listNotesTree(signal?: AbortSignal): Promise<TreeNode[]> {
  return jsonFetch<TreeNode[]>('/api/notes/tree', { signal });
}

export async function getNote(id: number, signal?: AbortSignal): Promise<NoteOut> {
  return jsonFetch<NoteOut>(`/api/notes/${id}`, { signal });
}

export async function createNote(input: NoteCreateInput): Promise<NoteOut> {
  return jsonFetch<NoteOut>('/api/notes', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export async function updateNote(id: number, input: NoteUpdateInput): Promise<NoteOut> {
  return jsonFetch<NoteOut>(`/api/notes/${id}`, {
    method: 'PUT',
    body: JSON.stringify(input),
  });
}

export async function deleteNote(id: number): Promise<void> {
  await jsonFetch<void>(`/api/notes/${id}`, { method: 'DELETE' });
}

export async function moveNote(id: number, newFolderPath: string[]): Promise<NoteOut> {
  return jsonFetch<NoteOut>(`/api/notes/${id}/move`, {
    method: 'POST',
    body: JSON.stringify({ new_folder_path: newFolderPath }),
  });
}

export async function batchImportNotes(
  input: NoteBatchImportInput,
): Promise<BatchImportReport> {
  return jsonFetch<BatchImportReport>('/api/notes/batch-import', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

// ---------------------------------------------------------------------------
// Quiz(M2 — 聊天框 query → 全库 RAG → 出题 + Judge 三层评分)
// ---------------------------------------------------------------------------

export type QuizMode = 'topic' | 'job' | 'auto';
export type QuizQuestionType = 'open_ended' | 'definition';

export type QuizSessionCreateInput = {
  query: string;
  mode: QuizMode;
  question_count: number;
  jd_ids?: number[] | null;
};

export type QuizQuestionPublic = {
  id: number;
  type: QuizQuestionType;
  prompt: string;
  evidence_chunk_ids: number[];
};

export type QuizQuestionReady = {
  order_index: number;
  question: QuizQuestionPublic;
};

export type QuizTypeMix = {
  open_ended: number;
  definition: number;
  rationale?: string;
};

export type QuizProgress = {
  phase: string;
  expanded_queries?: string[];
  candidate_count?: number;
  chunk_count?: number;
  model?: string;
  type_mix?: QuizTypeMix;
  order_index?: number;
};

export type QuizScores = {
  coverage: number;
  fidelity: number;
  depth: number;
  total: number;
};

export type QuizNullableScores = {
  coverage: number | null;
  fidelity: number | null;
  depth: number | null;
  total: number | null;
};

export type QuizEvidence = {
  coverage_evidence?: unknown;
  fidelity_evidence?: unknown;
  depth_evidence?: unknown;
};

export type QuizNextAction = 'ask_next' | 'remediate' | 'summarize' | 'finish';

export type QuizRemediationPrompt = {
  text?: string;
  triggered_by?: string;
  missing_scoring_point_ids?: string[];
  fabricated_claim_ids?: number[];
  missing_depth_dimensions?: string[];
  supporting_chunk_ids?: number[];
  unresolved_gaps?: unknown[];
};

export type QuizAnswerTurn = {
  round_index?: number;
  turn_type?: 'initial' | 'remediation' | string;
  text?: string;
  client_turn_id?: string | null;
  submitted_at?: string;
};

export type QuizCoachTurn = {
  round_index?: number;
  turn_type?: 'coach_question' | string;
  text?: string;
  client_turn_id?: string | null;
  submitted_at?: string;
  answered_at?: string;
  coach_message?: string | null;
};

export type QuizJudgeTurn = {
  round_index?: number;
  turn_type?: 'judge_feedback' | string;
  answer_turn_type?: 'initial' | 'remediation' | string | null;
  judged_at?: string;
  scores?: QuizScores;
  coach_message?: string | null;
  next_action?: QuizNextAction | string | null;
  triggered_by?: string | null;
  decision_reason?: string | null;
  exit_reason?: string | null;
  remediation_prompt?: QuizRemediationPrompt | null;
  unresolved_gaps?: unknown[];
};

export type QuizSessionSummaryGap = {
  type?: string;
  key?: string;
  label?: string;
  count?: number;
  examples?: string[];
};

export type QuizSessionQuestionSummary = {
  order_index?: number;
  question_id?: number;
  prompt?: string;
  scores?: QuizScores;
  round_count?: number;
  improved_by_remediation?: boolean;
  score_delta?: number;
  coverage_gaps?: unknown[];
  fabricated_claims?: string[];
  missing_depth_dimensions?: string[];
  coach_message?: string | null;
  status?: string;
};

export type QuizSessionSummary = {
  session_id?: number;
  query?: string;
  mode?: QuizMode | string;
  finished_at?: string;
  headline?: string;
  scores?: QuizScores;
  strengths?: string[];
  recurring_gaps?: QuizSessionSummaryGap[];
  remediation_wins?: string[];
  review_suggestions?: string[];
  question_summaries?: QuizSessionQuestionSummary[];
  context_pack?: Record<string, unknown>;
  markdown?: string;
};

export type QuizRemediationState = {
  last_decision?: QuizNextAction | string;
  triggered_by?: string;
  decision_reason?: string;
  exit_reason?: string | null;
  remediation_prompt?: QuizRemediationPrompt | null;
  unresolved_gaps?: unknown[];
};

export type QuizSessionQuestionDetail = {
  order_index: number;
  question: QuizQuestionPublic;
  user_answer: string | null;
  answer_turns?: QuizAnswerTurn[];
  judge_turns?: QuizJudgeTurn[];
  coach_turns?: QuizCoachTurn[];
  answer_submitted_at: string | null;
  judged: boolean;
  scores: QuizNullableScores | null;
  evidence: QuizEvidence | null;
  remediation_state?: QuizRemediationState | null;
  next_action?: QuizNextAction | string | null;
  remediation_prompt?: QuizRemediationPrompt | null;
  coach_message?: string | null;
  reference_answer?: string | null;
  scoring_points?: unknown[] | null;
};

export type QuizSessionDetail = {
  id: number;
  query: string;
  mode: QuizMode;
  jd_ids: number[] | null;
  status: 'in_progress' | 'submitted' | 'abandoned';
  agent_state?: Record<string, unknown> | null;
  started_at: string;
  submitted_at: string | null;
  abandoned_at: string | null;
  scores: QuizNullableScores | null;
  recall_md_path: string | null;
  summary?: QuizSessionSummary | null;
  questions: QuizSessionQuestionDetail[];
};

export type QuizSessionListItem = {
  id: number;
  query: string;
  mode: QuizMode;
  status: 'in_progress' | 'submitted' | 'abandoned';
  started_at: string;
  submitted_at: string | null;
  total_score: number | null;
  question_count: number;
};

export type QuizSessionListResponse = {
  items: QuizSessionListItem[];
  next_cursor: number | null;
  has_more: boolean;
};

export type QuizCreateSseFrame =
  // P3:POST 返回 202 后由客户端合成的第一帧。session 行此时已建好。
  | SseFrame<'accepted', { job_id: number; session_id: number | null }>
  | SseFrame<
      'started',
      { job_id?: string; resource_id: number; query: string; mode: QuizMode }
    >
  | SseFrame<'progress', QuizProgress>
  | SseFrame<'question_ready', QuizQuestionReady>
  | SseFrame<'error', { code: string; detail: string }>
  | SseFrame<'done', { ok: boolean }>;

export type QuizSubmitSseFrame =
  | SseFrame<
      'started',
      { job_id: string; resource_id: number; session_id?: number; total_questions: number }
    >
  | SseFrame<'progress', QuizProgress>
  | SseFrame<
      'question_done',
      {
        order_index: number;
        scores: QuizScores;
        coach_message?: string | null;
        evidence: QuizEvidence;
      }
    >
  | SseFrame<'result', { session_id: number; scores: QuizScores; recall_md_path?: string | null }>
  | SseFrame<'error', { code: string; detail: string; order_index?: number }>
  | SseFrame<'done', { ok: boolean }>;

export type QuizAnswerTurnSubmitInput = {
  text: string;
  turn_type: 'auto' | 'initial' | 'remediation' | 'coach_question';
  client_turn_id?: string | null;
};

export type QuizAnswerTurnSseFrame =
  | SseFrame<
      'started',
      {
        job_id: string;
        resource_id: number;
        session_id?: number;
        order_index: number;
        round_index: number;
        turn_type?: 'initial' | 'remediation' | 'coach_question';
      }
    >
  | SseFrame<
      'progress',
      {
        phase: string;
        included?: string[];
        compacted?: boolean;
      }
    >
  | SseFrame<
      'judge_done',
      {
        order_index: number;
        round_index: number;
        scores: QuizScores;
        coach_message?: string | null;
        unresolved_gaps?: unknown[];
      }
    >
  | SseFrame<
      'coach_done',
      {
        order_index: number;
        round_index: number;
        turn_type: 'coach_question';
        text: string;
        client_turn_id?: string | null;
        submitted_at?: string;
        coach_message: string;
      }
    >
  | SseFrame<
      'decision_done',
      {
        next_action: QuizNextAction;
        triggered_by: string;
        decision_reason: string;
        exit_reason?: string | null;
      }
    >
  | SseFrame<
      'result',
      {
        session_id: number;
        order_index: number;
        round_index: number;
        next_action: QuizNextAction;
        cumulative_answer: string;
        scores: QuizScores;
        remediation_prompt: QuizRemediationPrompt | null;
        coach_message?: string | null;
      }
    >
  | SseFrame<'error', { code: string; detail: string; order_index?: number }>
  | SseFrame<'done', { ok: boolean }>;

export type QuizFinishSseFrame =
  | SseFrame<
      'started',
      {
        job_id: string;
        resource_id: number;
        session_id?: number;
        total_questions: number;
      }
    >
  | SseFrame<
      'progress',
      {
        phase: string;
        included?: string[];
        compacted?: boolean;
      }
    >
  | SseFrame<
      'result',
      {
        session_id: number;
        scores: QuizScores;
        summary?: QuizSessionSummary | null;
        recall_md_path?: string | null;
      }
    >
  | SseFrame<'error', { code: string; detail: string }>
  | SseFrame<'done', { ok: boolean }>;


// ---------------------------------------------------------------------------
// P3:长任务从"一条长 SSE"改成"202 + job_id,再订阅 job 事件"。
//
// 对页面组件而言签名没变 —— 下面这些函数仍然返回同一套 SseFrame 的
// AsyncGenerator。变化在内部:先 POST 拿 job_id(连接立刻结束),再连
// `/api/jobs/{id}/stream` 读事件。好处是刷新页面 / 断线后能用同一个
// job_id 从 `after_seq` 续读,不像旧实现那样一断就丢进度。
// ---------------------------------------------------------------------------

export type JobAccepted = {
  job_id: number;
  status: string;
  kind: string;
  resource_kind: string | null;
  resource_id: number | null;
};

export async function enqueueJob(
  path: string,
  body?: unknown,
): Promise<JobAccepted> {
  return jsonFetch<JobAccepted>(path, {
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

/** 订阅一个 job 的事件流。`afterSeq` 用于断线续读。 */
export function streamJobEvents<TFrame extends SseFrame>(
  jobId: number,
  afterSeq = 0,
): AsyncGenerator<TFrame> {
  const qs = afterSeq > 0 ? `?after_seq=${afterSeq}` : '';
  return streamSse<TFrame>(`${API_BASE_URL}/api/jobs/${jobId}/stream${qs}`, {
    headers: authHeaders(),
  });
}

/** POST 入队 + 立刻订阅,把两步拼成调用方眼里的一条流。 */
async function* enqueueAndStream<TFrame extends SseFrame>(
  path: string,
  body: unknown,
  onAccepted?: (accepted: JobAccepted) => TFrame | null,
): AsyncGenerator<TFrame> {
  const accepted = await enqueueJob(path, body);
  const prelude = onAccepted?.(accepted);
  if (prelude) yield prelude;
  yield* streamJobEvents<TFrame>(accepted.job_id);
}

export function createQuizSession(
  input: QuizSessionCreateInput,
): AsyncGenerator<QuizCreateSseFrame> {
  // session 行在 202 阶段就已建好,先合成一帧把 session_id 交给页面,
  // 页面可以立刻跳转,不用等第一条 worker 事件。
  return enqueueAndStream<QuizCreateSseFrame>(
    '/api/quiz/sessions',
    input,
    (accepted) =>
      ({
        event: 'accepted',
        data: { job_id: accepted.job_id, session_id: accepted.resource_id },
      }) as QuizCreateSseFrame,
  );
}

export async function saveQuizAnswer(
  sessionId: number,
  orderIndex: number,
  userAnswer: string,
): Promise<{ ok: boolean }> {
  return jsonFetch<{ ok: boolean }>(`/api/quiz/sessions/${sessionId}/answers/${orderIndex}`, {
    method: 'PUT',
    body: JSON.stringify({ user_answer: userAnswer }),
  });
}

export async function getQuizSession(
  sessionId: number,
  signal?: AbortSignal,
): Promise<QuizSessionDetail> {
  return jsonFetch<QuizSessionDetail>(`/api/quiz/sessions/${sessionId}`, { signal });
}

export async function listQuizSessions(
  opts: {
    status?: 'in_progress' | 'submitted' | 'abandoned';
    cursor?: number | null;
    limit?: number;
    signal?: AbortSignal;
  } = {},
): Promise<QuizSessionListResponse> {
  const params = new URLSearchParams();
  if (opts.status) params.set('status', opts.status);
  if (opts.cursor) params.set('cursor', String(opts.cursor));
  if (opts.limit) params.set('limit', String(opts.limit));
  const qs = params.toString();
  return jsonFetch<QuizSessionListResponse>(`/api/quiz/sessions${qs ? `?${qs}` : ''}`, {
    signal: opts.signal,
  });
}

export function submitQuizSession(sessionId: number): AsyncGenerator<QuizSubmitSseFrame> {
  return enqueueAndStream<QuizSubmitSseFrame>(
    `/api/quiz/sessions/${sessionId}/submit`,
    undefined,
  );
}

export function finishQuizSession(sessionId: number): AsyncGenerator<QuizFinishSseFrame> {
  return enqueueAndStream<QuizFinishSseFrame>(
    `/api/quiz/sessions/${sessionId}/finish`,
    undefined,
  );
}

export function submitQuizAnswerTurn(
  sessionId: number,
  orderIndex: number,
  input: QuizAnswerTurnSubmitInput,
): AsyncGenerator<QuizAnswerTurnSseFrame> {
  return enqueueAndStream<QuizAnswerTurnSseFrame>(
    `/api/quiz/sessions/${sessionId}/answers/${orderIndex}/turns`,
    input,
  );
}

export async function abandonQuizSession(
  sessionId: number,
): Promise<{ id: number; status: 'abandoned'; abandoned_at: string }> {
  return jsonFetch<{ id: number; status: 'abandoned'; abandoned_at: string }>(
    `/api/quiz/sessions/${sessionId}/abandon`,
    { method: 'POST' },
  );
}


// ---------------------------------------------------------------------------
// P0/P1:认证与余额
// ---------------------------------------------------------------------------

export type AuthTokenResponse = {
  access_token: string;
  token_type: string;
  expires_at: string;
};

export type CurrentUser = {
  id: number;
  email: string;
  name: string | null;
  locale: string;
  created_at: string;
};

export type BalanceResponse = {
  user_id: number;
  balance_cny: string;
  total_topup_cny: string;
  total_spent_cny: string;
};

export type SpendSummaryResponse = {
  total_spent_cny: string;
  items: { channel: string; feature: string; spent_cny: string; calls: number }[];
};

export async function register(input: {
  email: string;
  password: string;
  name?: string;
}): Promise<AuthTokenResponse> {
  return jsonFetch<AuthTokenResponse>('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export async function login(input: {
  email: string;
  password: string;
}): Promise<AuthTokenResponse> {
  return jsonFetch<AuthTokenResponse>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export async function getCurrentUser(signal?: AbortSignal): Promise<CurrentUser> {
  return jsonFetch<CurrentUser>('/api/auth/me', { signal });
}

export async function getBalance(signal?: AbortSignal): Promise<BalanceResponse> {
  return jsonFetch<BalanceResponse>('/api/billing/balance', { signal });
}

/** 模拟充值 —— 没有接支付,只是往账本里记一笔 topup。 */
export async function topupBalance(amountCny: string): Promise<BalanceResponse> {
  return jsonFetch<BalanceResponse>('/api/billing/topup', {
    method: 'POST',
    body: JSON.stringify({ amount_cny: amountCny }),
  });
}

export async function getSpendSummary(
  signal?: AbortSignal,
): Promise<SpendSummaryResponse> {
  return jsonFetch<SpendSummaryResponse>('/api/billing/spend-summary', { signal });
}

export { API_BASE_URL };
