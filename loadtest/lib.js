// 压测脚本共用的辅助函数(P8)。
import http from 'k6/http';
import { check, sleep } from 'k6';

export const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export function api(path) {
  return `${BASE_URL}/api${path}`;
}

export function jsonHeaders(token) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

// 注册一个压测专用账号并返回 token。邮箱带 VU/迭代号,保证互不冲突。
export function registerUser(tag) {
  const email = `loadtest+${tag}-${__VU}-${Date.now()}@jobcopilot.local`;
  const res = http.post(
    api('/auth/register'),
    JSON.stringify({ email, password: 'LoadTest!2345', name: `lt-${tag}` }),
    { headers: jsonHeaders(), tags: { name: 'auth_register' } },
  );
  check(res, { 'register 201': (r) => r.status === 201 });
  if (res.status !== 201) return null;
  return { email, token: res.json('access_token') };
}

export function login(email, password) {
  const res = http.post(
    api('/auth/login'),
    JSON.stringify({ email, password }),
    { headers: jsonHeaders(), tags: { name: 'auth_login' } },
  );
  check(res, { 'login 200': (r) => r.status === 200 });
  return res.status === 200 ? res.json('access_token') : null;
}

// 轮询 job 直到终态。返回最终 status,超时返回 'timeout'。
// 用轮询而不是 SSE:k6 没有原生 EventSource,而 /jobs/{id} 与
// /jobs/{id}/events 走的是同一批索引查询,压出来的库压力等价。
export function waitForJob(token, jobId, timeoutMs = 180000, intervalMs = 1000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const res = http.get(api(`/jobs/${jobId}`), {
      headers: jsonHeaders(token),
      tags: { name: 'job_poll' },
    });
    if (res.status !== 200) return `http_${res.status}`;
    const status = res.json('status');
    if (
      ['succeeded', 'failed', 'insufficient_balance', 'deadline_exceeded'].includes(
        status,
      )
    ) {
      return status;
    }
    sleep(intervalMs / 1000);
  }
  return 'timeout';
}
