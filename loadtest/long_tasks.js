// P8:压四个 202 长任务接口 + job 完成时延。
//
// 两个指标要分开看:
// - job_accept_duration:POST 到拿到 202 的时间。这是**在线**指标,应当和
//   只读接口同量级;它变大说明长任务还在在线链路里做事。
// - job_complete_duration:202 到 job 进终态的时间。这是 **worker** 指标,
//   靠加 worker 副本 / 提高 job_worker_concurrency 改善,跟在线无关。
//
// 注意:本脚本会真实调用上游模型并真实扣费。跑之前确认测试账号余额,
// 跑之后用 /api/billing/spend-summary 对账。
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import { api, jsonHeaders, registerUser, waitForJob } from './lib.js';

const acceptDuration = new Trend('job_accept_duration', true);
const completeDuration = new Trend('job_complete_duration', true);
const overloaded = new Counter('queue_overloaded_503');
const insufficient = new Counter('insufficient_balance');

export const options = {
  scenarios: {
    long_tasks: {
      executor: 'constant-vus',
      vus: Number(__ENV.VUS || 20),
      duration: __ENV.DURATION || '5m',
    },
  },
  thresholds: {
    // 接收长任务是纯写库操作,不该慢。
    'job_accept_duration': ['p(95)<800'],
  },
};

export function setup() {
  const user = registerUser('longtask');
  if (!user) throw new Error('setup 注册失败');
  const headers = jsonHeaders(user.token);
  http.post(
    api('/notes'),
    JSON.stringify({
      title: '压测素材',
      folder_path: ['loadtest'],
      content: '并发、连接池、限流、熔断、队列水位、幂等、扣费账本。'.repeat(20),
    }),
    { headers },
  );
  sleep(20); // 等 embed worker 补向量
  return { token: user.token };
}

export default function (data) {
  const headers = jsonHeaders(data.token);

  const started = Date.now();
  const res = http.post(
    api('/quiz/sessions'),
    JSON.stringify({ query: '并发改造里的限流与熔断', mode: 'topic', question_count: 2 }),
    { headers, tags: { name: 'quiz_create' } },
  );
  acceptDuration.add(Date.now() - started);

  if (res.status === 503) {
    // 过载保护生效:这是预期行为,不是错误。
    overloaded.add(1);
    check(res, { '503 带 Retry-After': (r) => !!r.headers['Retry-After'] });
    sleep(Number(res.headers['Retry-After'] || 5));
    return;
  }
  check(res, { 'quiz 202': (r) => r.status === 202 });
  if (res.status !== 202) return;

  const acceptedAt = Date.now();
  const status = waitForJob(data.token, res.json('job_id'));
  completeDuration.add(Date.now() - acceptedAt);

  if (status === 'insufficient_balance') {
    insufficient.add(1);
    return;
  }
  check({ status }, { 'job 终态成功': (o) => o.status === 'succeeded' });

  // 顺带压一次提交评分(AnswerJudge 是最重的一条链路)。
  const sessionId = res.json('resource_id');
  http.put(
    api(`/quiz/sessions/${sessionId}/answers/0`),
    JSON.stringify({ answer_text: '限流用信号量,熔断看连续 429,水位到了直接 503。' }),
    { headers },
  );
  const submit = http.post(api(`/quiz/sessions/${sessionId}/submit`), null, {
    headers,
    tags: { name: 'quiz_submit' },
  });
  if (submit.status === 202) {
    waitForJob(data.token, submit.json('job_id'));
  }
}
