import type { components } from '@jobcopilot/schemas';

const API_BASE_URL =
  (typeof window === 'undefined' ? process.env.INTERNAL_API_BASE_URL : undefined) ??
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  'http://localhost:8000';

const USER_ID = process.env.NEXT_PUBLIC_USER_ID ?? '1';

export type HealthResponse = components['schemas']['HealthResponse'];
export type JDStructured = components['schemas']['JDStructured'];
export type JDSkill = components['schemas']['JDSkill'];
export type JDDetail = components['schemas']['JDDetail'];
export type JDParseInput = components['schemas']['JDParseInput'];
export type JDParseResponse = components['schemas']['JDParseResponse'];
export type JDPatchInput = components['schemas']['JDPatchInput'];
export type JdStatus = components['schemas']['JdStatus'];

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

export async function patchJd(id: number, patch: JDPatchInput): Promise<JDDetail> {
  return jsonFetch<JDDetail>(`/v1/jds/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

export { API_BASE_URL };
