# 压测脚本(P8)

工具选 **k6**:脚本是 JS,单进程能压出足够并发,`--out json` 的结果可以直接
喂给下面的对账脚本。本目录只放脚本,**不在 CI 里自动跑** —— 每次跑都会真实
调用上游模型并真实扣余额。

## 前置

```bash
brew install k6
```

被压环境需要:

1. `docker compose up -d` 起全栈,`alembic upgrade head` 已跑到 0026。
2. 用 `smoke.js` 里的注册流程建测试账号,或提前准备好 `USERS` 环境变量。
3. 测试账号余额充足 —— 余额耗尽时任务会进 `insufficient_balance` 终态,
   那是被测行为之一,但混在吞吐压测里会污染结果。

## 场景

| 脚本 | 压什么 | 关注指标 |
| --- | --- | --- |
| `smoke.js` | 单用户串行走通注册→建笔记→出题→答题→提交 | 功能是否成立,任何非 2xx 直接失败 |
| `online_endpoints.js` | 只压在线只读/写接口(登录、笔记树、会话列表、job 状态) | p95 延迟、错误率;这些接口不碰 LLM,应当稳定在几十毫秒 |
| `long_tasks.js` | 压四个 202 长任务接口 + job 事件订阅 | 202 接收速率、队列水位 503 比例、端到端完成时间 |
| `overload.js` | 故意超过 `queue_high_watermark` | 是否稳定返回 503 + `Retry-After`,而不是超时或 5xx 雪崩 |

## 跑法

```bash
k6 run -e BASE_URL=http://localhost:8000 loadtest/smoke.js
```

```bash
k6 run -e BASE_URL=http://localhost:8000 -e VUS=50 -e DURATION=3m loadtest/online_endpoints.js
```

```bash
k6 run -e BASE_URL=http://localhost:8000 -e VUS=20 loadtest/long_tasks.js
```

```bash
k6 run -e BASE_URL=http://localhost:8000 -e VUS=200 loadtest/overload.js
```

## 判读

- **在线接口**:`http_req_duration` p95 是主指标。P3 之后长任务不再占用在线
  连接,所以在线接口的 p95 不应该随长任务并发上升而劣化 —— 如果劣化了,
  说明还有长任务残留在 API 进程里。
- **长任务**:`job_accept_duration`(POST 到 202 的时间)应当与在线接口同量级;
  `job_complete_duration` 才反映 worker 侧吞吐,靠加 worker 副本改善。
- **过载**:503 是**正确**结果。要看的是 503 比例随负载平滑上升,以及响应里
  有没有 `Retry-After` 头;出现超时或连接被拒说明水位设得太高。
- **扣费**:压测后查 `GET /api/billing/spend-summary`,把总扣费和 `llm_calls`
  的 `sum(cost_cny)` 对一遍。两者不一致说明有调用漏记账。
