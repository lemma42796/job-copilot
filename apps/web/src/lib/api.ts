import type { components } from '@jobcopilot/schemas';

import { type SseFrame, streamSse } from './sse';

const API_BASE_URL =
  (typeof window === 'undefined' ? process.env.INTERNAL_API_BASE_URL : undefined) ??
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  'http://localhost:8000';

const USER_ID = process.env.NEXT_PUBLIC_USER_ID ?? '1';

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
  const headers = new Headers(init.headers);
  headers.set('X-User-Id', USER_ID);
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
    headers: { 'X-User-Id': USER_ID },
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
    headers: {
      'Content-Type': 'application/json',
      'X-User-Id': USER_ID,
    },
    body: JSON.stringify(input),
  });
}

export function rechunkProfile(profileId: number): AsyncGenerator<ProfileParseSseFrame> {
  return streamSse<ProfileParseSseFrame>(`${API_BASE_URL}/v1/profiles/${profileId}/rechunk`, {
    method: 'POST',
    headers: { 'X-User-Id': USER_ID },
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

export { API_BASE_URL };
