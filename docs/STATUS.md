---
title: JobCopilot 项目最新状态
owner: lemma42796
last_updated: 2026-09-06
purpose: 只记录项目当前已经实现、已经验证的最新事实。
---

# 当前状态

- 产品功能链路已完成。并发改造 P0–P8 已全部落地并合并回 `main`。
- 2026-09-05 完成两轮验证:单进程冒烟(核心链路跑通)+ 全栈 compose 压测(stub 上游,四个 k6 场景全部达标)。已验与未验清单见「当前边界」。

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
- `loadtest/` 下 k6 脚本(smoke / 在线端点 / 长任务 / 过载)已在全栈 compose + stub 上游下执行,四个场景 checks 100%、阈值全部达标;报告见 `../evals/reports/loadtest-p8-20260905-185443.md`(`evals/reports/` 已 gitignore)。
- `StubProvider`(`JOBCOPILOT_LLM_PROVIDER=stub`)覆盖 LLM / rerank / embedding 三条链路,固定延迟与固定并发上限、永远返回合法响应,供压测使用,零真实模型调用、零真实扣费。

# 当前边界

- **已冒烟验证**:`uv sync` 依赖安装;migration 0025/0026 在有存量数据的库上执行成功(128 条笔记回填 legacy 用户);注册 / 登录 / token / 401;双用户隔离(bob 交叉读 / 删 alice 的 note 与 job 均 404,笔记树互不可见);模拟充值;笔记 → chunk → embedding → `llm_calls` 记账 → 余额按 `cost_cny` 实扣;quiz 出题 202 + job_id + SSE 全程事件;`after_seq` 断线续看。
- **冒烟中发现并已修复**:① 注册在 `AUTH_SECRET` 未配置时先 commit 用户后签 token,失败不回滚 → 改为先签后 commit;② compose redis 未映射宿主端口,本地起不了 worker → 补 `6379:6379`;③ quiz_generator 输出违反完整性约束(如 scoring_points 数量越界)时无重试、已计费但 job 直接失败 → 带失败原因重试一次(`GEN_INTEGRITY_ATTEMPTS=2`)。
- **已压测验证(2026-09-05,全栈 compose:gunicorn 4 进程 + worker×2 + postgres + redis,`LLM_PROVIDER=stub`、缓存关)**:长任务与在线只读接口双向互压下互不拖垮(只读 p95=322ms @≈197 QPS,长任务接收 p95=158ms,`http_req_failed`=0%);到达率拉到 400/s 时越过队列水位线平滑返回 503 + `Retry-After` + `problem+json`,非预期状态码 0%、p99=586ms;job 终态数与 k6 iterations 逐条对齐,过载中被拒任务在调模型前短路,扣费为 0。
- **尚未验证**:答题回合 / 结束总结两个 SSE 端点的端到端;session / jd 的跨用户隔离(note / job 已验);worker 副本扩容线性度、强杀接管、多副本 embed 去重;上游 429 熔断(stub 永不 429,该路径未被触发);连接数上限;真实 provider 下的耗时与成本。
- 不是 exactly-once:worker 崩在 `running` 中途的 job 不会自动重试,由回收器打终态,人工重发。
- 无真实支付链路,充值为模拟接口。
- 语义缓存默认关闭,近似命中阈值未经评测。
- 压测结论的证据边界:stub 固定 2 秒延迟、并发上限 256、永不失败,故 `job_complete_duration` 只反映调度开销、成本为按长度折算的模拟值,均不代表真实模型表现;压测未测出容量上限(只读场景每轮带 sleep,是 k6 在限速而非服务端饱和),197 QPS 应读作「在此负载下仍有余量」而非「上限 197 QPS」;k6 与被测栈同机,不能外推为生产容量。
- 单次调用约 15,000 token / 30 秒仍是推算值,stub 模式下测不出,需另行小样本真实调用补测后替换;口径见 `TECH_DESIGN.md`「并发与成本约束」。

# 相关入口

- 当前与未完成任务 → `TASKS.md`
- 技术架构 → `TECH_DESIGN.md`
- 评测规范与最新证据 → `../evals/EVAL_GUIDE.md`
