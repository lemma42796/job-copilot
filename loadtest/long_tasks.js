// P8:压四个 202 长任务接口 + job 完成时延。
//
// 两个指标要分开看:
// - job_accept_duration:POST 到拿到 202 的时间。这是**在线**指标,应当和
//   只读接口同量级;它变大说明长任务还在在线链路里做事。
// - job_complete_duration:202 到 job 进终态的时间。这是 **worker** 指标,
//   靠加 worker 副本 / 提高 job_worker_concurrency 改善,跟在线无关。
//
// 前置:后端以 LLM_PROVIDER=stub 启动(零真实模型调用,记账走模拟余额,
// 见 docs/TASKS.md「压测 stub provider」)。跑之前确认 stub 参数
// (STUB_LATENCY_S / STUB_MAX_CONCURRENCY)与预期档位一致。
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import { api, jsonHeaders, registerUser, waitForJob } from './lib.js';

const acceptDuration = new Trend('job_accept_duration', true);
const completeDuration = new Trend('job_complete_duration', true);
const overloaded = new Counter('queue_overloaded_503');
const insufficient = new Counter('insufficient_balance');

// 每轮出题数;draft 要给每一题都存答案,否则 submit 会因“未作答”失败。
const QUESTION_COUNT = 2;

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

  // 所有 VU 共用这一个账号,5 元注册赠额扛不住整场压测;先模拟充值到用不完,
  // 否则中途余额耗尽会让任务走 insufficient_balance 短路,污染吞吐数据。
  const topup = http.post(
    api('/billing/topup'),
    JSON.stringify({ amount_cny: 100000, note: 'loadtest' }),
    { headers },
  );
  check(topup, { 'topup 200': (r) => r.status === 200 });

  // 笔记要切出 ≥3 个 chunk 才过得了出题的 0 命中守门(MIN_CHUNKS_FOR_QUIZ=3);
  // 无标题单段只切 1 块,所以按 H1 + 3×H2 分节写。
  http.post(
    api('/notes'),
    JSON.stringify({
      title: '压测素材',
      folder_path: ['loadtest'],
      content_md:
        '# 并发改造\n\n' +
        '## 限流\n\n' +
        '用信号量给 LLM 调用设并发上限,超过上限的请求排队等待而不是直接失败。\n\n' +
        '## 熔断\n\n' +
        '上游连续返回 429 时打开熔断,暂停转发并让请求快速失败,避免雪崩。\n\n' +
        '## 队列水位\n\n' +
        '待执行队列超过高水位线时,入口直接返回 503 并带 Retry-After,让客户端退避。',
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
    JSON.stringify({ query: '并发改造里的限流与熔断', mode: 'topic', question_count: QUESTION_COUNT }),
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
  // submit 要求每一题的 user_answer 都非空,所以给 QUESTION_COUNT 题都存草稿。
  const sessionId = res.json('resource_id');
  for (let i = 0; i < QUESTION_COUNT; i++) {
    http.put(
      api(`/quiz/sessions/${sessionId}/answers/${i}`),
      JSON.stringify({ user_answer: '限流用信号量,熔断看连续 429,水位到了直接 503。' }),
      { headers },
    );
  }
  const submit = http.post(api(`/quiz/sessions/${sessionId}/submit`), null, {
    headers,
    tags: { name: 'quiz_submit' },
  });
  if (submit.status === 202) {
    waitForJob(data.token, submit.json('job_id'));
  }
}
