// P8:过载保护验收。
//
// 故意把待执行队列压过 `queue_high_watermark`,看系统怎么退化。
// **合格标准不是 0 个 503** —— 恰恰相反,503 是正确行为。要看的是:
//
// 1. 503 比例随负载平滑上升,没有超时、连接被拒、5xx 雪崩。
// 2. 每个 503 都带 `Retry-After`,客户端能据此退避。
// 3. 已经入队的任务照常跑完,不会因为后面的请求被拒而受影响。
//
// 上游持续 429 时还会出现 `upstream_circuit_open`(也是 503),同样带
// Retry-After;两者用响应体里的 `code` 区分。
import http from 'k6/http';
import { check } from 'k6';
import { Counter, Rate } from 'k6/metrics';
import { api, jsonHeaders, registerUser } from './lib.js';

const accepted = new Counter('accepted_202');
const queueRejected = new Counter('rejected_queue_overloaded');
const breakerRejected = new Counter('rejected_upstream_circuit_open');
const unexpected = new Rate('unexpected_status');

export const options = {
  scenarios: {
    burst: {
      executor: 'ramping-arrival-rate',
      startRate: 10,
      timeUnit: '1s',
      preAllocatedVUs: Number(__ENV.VUS || 200),
      stages: [
        { target: 50, duration: '30s' },
        { target: 200, duration: '1m' },
        { target: 400, duration: '1m' },
        { target: 0, duration: '30s' },
      ],
    },
  },
  thresholds: {
    // 只允许 202 和 503 两种结果;出现别的状态码说明退化路径不受控。
    'unexpected_status': ['rate<0.01'],
    // 过载时也不该出现请求超时。
    'http_req_duration{expected_response:true}': ['p(99)<3000'],
  },
};

export function setup() {
  const user = registerUser('overload');
  if (!user) throw new Error('setup 注册失败');
  return { token: user.token };
}

export default function (data) {
  const res = http.post(
    api('/quiz/sessions'),
    JSON.stringify({ query: '过载保护', mode: 'topic', question_count: 1 }),
    { headers: jsonHeaders(data.token), tags: { name: 'quiz_create_burst' } },
  );

  if (res.status === 202) {
    accepted.add(1);
    unexpected.add(false);
    return;
  }
  if (res.status === 503) {
    const code = res.json('code');
    if (code === 'queue_overloaded') queueRejected.add(1);
    else if (code === 'upstream_circuit_open') breakerRejected.add(1);
    check(res, {
      '503 带 Retry-After': (r) => !!r.headers['Retry-After'],
      '503 是 problem+json': (r) => String(r.headers['Content-Type']).includes('problem+json'),
    });
    unexpected.add(false);
    return;
  }
  unexpected.add(true);
}
