import type { components } from '@jobcopilot/schemas';

// Server components fetch via the internal Docker network (`http://api:8000`),
// while the browser hits the host-mapped port. Pick whichever is set first.
const API_BASE_URL =
  (typeof window === 'undefined' ? process.env.INTERNAL_API_BASE_URL : undefined) ??
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  'http://localhost:8000';

export type HealthResponse = components['schemas']['HealthResponse'];

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const res = await fetch(`${API_BASE_URL}/v1/health`, {
    cache: 'no-store',
    signal,
  });
  if (!res.ok) {
    throw new Error(`Health check failed: HTTP ${res.status}`);
  }
  return (await res.json()) as HealthResponse;
}

export { API_BASE_URL };
