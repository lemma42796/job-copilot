'use client';

/**
 * P0 前端登录态。
 *
 * token 是后端 `services/auth_service.py` 签发的自包含串
 * `<user_id>.<expires_at>.<hmac>`,没有服务端 session,前端只负责存和带上。
 * 存在 localStorage:刷新页面要保持登录,而 SSE 请求走 fetch 而不是
 * EventSource,所以不依赖 cookie 也能带 Authorization 头。
 *
 * 过期由后端判定(401)。前端只在收到 401 时清掉本地 token 并跳登录页,
 * 不自己解析过期时间 —— 本地时钟不可信。
 */

const TOKEN_KEY = 'jobcopilot.access_token';
const EMAIL_KEY = 'jobcopilot.email';

export type StoredSession = {
  token: string;
  email: string | null;
};

export function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function getStoredSession(): StoredSession | null {
  const token = getToken();
  if (!token) return null;
  let email: string | null = null;
  try {
    email = window.localStorage.getItem(EMAIL_KEY);
  } catch {
    email = null;
  }
  return { token, email };
}

export function setSession(token: string, email: string | null): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
    if (email) window.localStorage.setItem(EMAIL_KEY, email);
  } catch {
    // 隐私模式下 localStorage 会抛。登录仍然在本次会话内有效。
  }
  notify();
}

export function clearSession(): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(EMAIL_KEY);
  } catch {
    // ignore
  }
  notify();
}

type Listener = () => void;
const listeners = new Set<Listener>();

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function notify(): void {
  for (const listener of listeners) listener();
}

/** 401 的统一处理:清 token,把用户送回登录页。 */
export function handleUnauthorized(): void {
  clearSession();
  if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
  }
}
