// P8:只压不碰 LLM 的在线接口。
//
// 这是 P3 的验收脚本。改造前四个长任务接口会长时间占住 worker,连带把
// 同进程的只读接口拖慢;改造后长任务只写一行 job 就返回,这些接口的 p95
// 不应该随长任务并发上升而劣化。跑法:先起 long_tasks.js 制造背景负载,
// 再跑本脚本对比 p95。
import http from 'k6/http';
import { check, group, sleep } from 'k6';
import { Trend } from 'k6/metrics';
import { api, jsonHeaders, registerUser } from './lib.js';

const readLatency = new Trend('online_read_duration', true);

export const options = {
  scenarios: {
    online: {
      executor: 'constant-vus',
      vus: Number(__ENV.VUS || 50),
      duration: __ENV.DURATION || '3m',
    },
  },
  thresholds: {
    // 这些接口只查索引,不该有 LLM 级别的延迟。
    'online_read_duration': ['p(95)<500'],
    'http_req_failed': ['rate<0.01'],
  },
};

export function setup() {
  const user = registerUser('online');
  if (!user) throw new Error('setup 注册失败');
  return { token: user.token };
}

export default function (data) {
  const headers = jsonHeaders(data.token);

  group('read', () => {
    const endpoints = [
      '/auth/me',
      '/billing/balance',
      '/notes/tree',
      '/quiz/sessions?limit=20',
      '/jds?limit=20',
      '/jd-analyses?limit=20',
    ];
    for (const path of endpoints) {
      const res = http.get(api(path), { headers, tags: { name: path } });
      readLatency.add(res.timings.duration);
      check(res, { [`${path} 200`]: (r) => r.status === 200 });
    }
  });

  sleep(1);
}
