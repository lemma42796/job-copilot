// P8 smoke:单用户串行走通完整链路。任何一步非 2xx 就算失败。
// 这个脚本的目的不是压吞吐,而是确认压测环境本身是好的 ——
// 在跑 long_tasks.js 之前先跑它。
import http from 'k6/http';
import { check, fail, sleep } from 'k6';
import { api, jsonHeaders, registerUser, waitForJob } from './lib.js';

export const options = {
  vus: 1,
  iterations: 1,
  thresholds: {
    checks: ['rate==1.0'],
  },
};

export default function () {
  const user = registerUser('smoke');
  if (!user) fail('注册失败,后面的步骤没有意义');
  const token = user.token;
  const headers = jsonHeaders(token);

  // 1. 余额:注册赠额应当已经到账。
  const balance = http.get(api('/billing/balance'), { headers });
  check(balance, {
    'balance 200': (r) => r.status === 200,
    'balance > 0': (r) => Number(r.json('balance_cny')) > 0,
  });

  // 2. 建一条笔记,给出题提供检索素材。
  const note = http.post(
    api('/notes'),
    JSON.stringify({
      title: '压测笔记 - Python GIL',
      folder_path: ['loadtest'],
      content:
        '# GIL\n\nCPython 的全局解释器锁保证同一时刻只有一个线程执行字节码。' +
        'CPU 密集任务因此无法靠多线程加速,需要多进程或把热点下沉到 C 扩展。' +
        'IO 密集任务在等待时会释放 GIL,所以多线程仍然有效。',
    }),
    { headers },
  );
  check(note, { 'note 201': (r) => r.status === 201 });

  // embedding 由 worker 异步补,出题前给它一点时间。
  sleep(15);

  // 3. 出题:202 + job_id,不再是一条长 SSE。
  const created = http.post(
    api('/quiz/sessions'),
    JSON.stringify({ query: 'Python GIL 对并发的影响', mode: 'topic', question_count: 2 }),
    { headers },
  );
  check(created, { 'quiz 202': (r) => r.status === 202 });
  if (created.status !== 202) fail('出题接口未返回 202');

  const sessionId = created.json('resource_id');
  const status = waitForJob(token, created.json('job_id'));
  check({ status }, { '出题任务成功': (o) => o.status === 'succeeded' });

  // 4. 存草稿 + 提交评分。
  const draft = http.put(
    api(`/quiz/sessions/${sessionId}/answers/0`),
    JSON.stringify({ answer_text: 'GIL 让 CPU 密集的多线程无法并行,IO 密集不受影响。' }),
    { headers },
  );
  check(draft, { 'draft 200': (r) => r.status === 200 });

  const submitted = http.post(api(`/quiz/sessions/${sessionId}/submit`), null, { headers });
  check(submitted, { 'submit 202': (r) => r.status === 202 });
  if (submitted.status === 202) {
    const s = waitForJob(token, submitted.json('job_id'));
    check({ s }, { '评分任务成功': (o) => o.s === 'succeeded' });
  }

  // 5. 扣费应当已经产生流水。
  const spend = http.get(api('/billing/spend-summary'), { headers });
  check(spend, {
    'spend 200': (r) => r.status === 200,
    '有扣费流水': (r) => Number(r.json('total_spent_cny')) > 0,
  });
}
