---
title: JobCopilot 项目最新状态
owner: lemma42796
last_updated: 2026-09-05
purpose: 只记录项目当前已经实现、已经验证的最新事实。
---

# 当前状态

- 产品功能链路已完成。并发改造 P0–P8 的代码已全部写完,**尚未跑过 migration、未启动过 compose、未压测**。
- 当前分支为 `claude/youthful-liskov-294bdd`。

# 当前已实现能力

- Markdown 笔记导入、树形导航、编辑、heading-aware chunk 和异步 embedding。
- 主题 query → hybrid RAG → QuizGenerator → AnswerJudge 三层评分。
- InterviewCoach 多轮纠偏、补答重评、教练追问、session 恢复和整场总结。
- 文本 JD 入库、批量技术栈聚合、知识库覆盖矩阵、学习路径、quiz topics 和历史报告。
- `/jds` 报告筛选、覆盖证据、"优先补齐"清单和 topic 批量进入 `/quiz`。
- 用户体系:`users` 表 + PBKDF2 口令 + HMAC 自包含 bearer token;11 张业务表带 `user_id` 与索引,service 层每条查询按 `user_id` 过滤;笔记文件按用户分目录。
- 余额账本:`user_balances` / `balance_transactions`,模拟充值(不接支付);LLM / rerank / embedding 三条链路统一落 `llm_calls` 并按 `cost_cny` 实扣;调用前查余额,余额耗尽就地中止并保留已产生结果,job 有独立终态区别于执行失败。
- 长任务异步化:四个 quiz 端点与 JD 分析改为 202 + `job_id`,进度落 `job_events` 表,`GET /api/jobs/{id}/stream` 只读订阅、支持 `after_seq` 断线续看。
- 独立 worker 容器 + Redis Streams(consumer group / XACK / XAUTOCLAIM),job 以条件写领取做幂等,`embed_worker` 用 `FOR UPDATE SKIP LOCKED` 领取 chunk。
- API 走 gunicorn + UvicornWorker 4 进程;`llm_max_concurrency` 默认 32;连接池显式 20 + 20。
- 过载保护:队列水位 503 + `Retry-After`、上游连续 429 熔断、job deadline 与超期回收。
- 语义缓存(按用户隔离,默认关闭)、短 query 跳过 rewrite、候选数 ≤ top_k 跳过 rerank、embed 批处理并发化。
- LLM 与任务生命周期写结构化日志,包含功能、关联 id、延迟、Token、成本、缓存和成功 / 失败终态。
- `loadtest/` 下有 k6 脚本(smoke / 在线端点 / 长任务 / 过载),**尚未执行**。

# 当前边界

- **上述能力全部只经过读码与静态检查,没有任何运行验证。** migration 0025 的存量数据回填、job SSE 空闲超时与前端重连的配合、Redis 消费接管路径都未实跑。
- 不是 exactly-once:worker 崩在 `running` 中途的 job 不会自动重试,由回收器打终态,人工重发。
- 无真实支付链路,充值为模拟接口。
- 语义缓存默认关闭,近似命中阈值未经评测。
- `CONCURRENCY_PLAN.md` 中除上游配额外的数字均为读码推算,未经压测,不得对外引用。上游 qwen3.6-flash 实测 TPM 10,000,000 / RPM 30,000(该模型独享,rerank 与 embedding 各有独立配额池)。

# 相关入口

- 当前与未完成任务 → `TASKS.md`
- 技术架构 → `TECH_DESIGN.md`
- 并发改造方案 → `CONCURRENCY_PLAN.md`
- 评测规范与最新证据 → `../evals/EVAL_GUIDE.md`
