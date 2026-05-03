import { ApiError } from './api';

export type SseFrame<TName extends string = string, TData = unknown> = {
  event: TName;
  data: TData;
};

export async function* streamSse<TFrame extends SseFrame>(
  input: RequestInfo,
  init: RequestInit = {},
): AsyncGenerator<TFrame, void, void> {
  const headers = new Headers(init.headers);
  if (!headers.has('Accept')) headers.set('Accept', 'text/event-stream');

  const res = await fetch(input, { ...init, headers, cache: 'no-store' });
  if (!res.ok) {
    const problem = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    throw new ApiError(res.status, problem);
  }
  if (!res.body) throw new Error('SSE response has no body');

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        if (buf.trim().length > 0) {
          const frame = parseFrame(buf);
          if (frame) yield frame as TFrame;
        }
        return;
      }
      buf += decoder.decode(value, { stream: true });
      while (true) {
        const sep = findFrameEnd(buf);
        if (sep == null) break;
        const raw = buf.slice(0, sep.end);
        buf = buf.slice(sep.next);
        const frame = parseFrame(raw);
        if (frame) yield frame as TFrame;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function findFrameEnd(buf: string): { end: number; next: number } | null {
  const a = buf.indexOf('\n\n');
  const b = buf.indexOf('\r\n\r\n');
  if (a < 0 && b < 0) return null;
  if (a < 0) return { end: b, next: b + 4 };
  if (b < 0) return { end: a, next: a + 2 };
  return a < b ? { end: a, next: a + 2 } : { end: b, next: b + 4 };
}

function parseFrame(raw: string): SseFrame | null {
  let event = 'message';
  const dataLines: string[] = [];
  for (const line of raw.split(/\r?\n/)) {
    if (line === '' || line.startsWith(':')) continue;
    const colon = line.indexOf(':');
    const field = colon >= 0 ? line.slice(0, colon) : line;
    const value = colon >= 0 ? line.slice(colon + 1).replace(/^ /, '') : '';
    if (field === 'event') event = value;
    else if (field === 'data') dataLines.push(value);
  }
  if (dataLines.length === 0) return null;
  const text = dataLines.join('\n');
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    data = text;
  }
  return { event, data };
}
