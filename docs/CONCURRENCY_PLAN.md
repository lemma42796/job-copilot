---
title: JobCopilot 高并发改造方案
owner: lemma42796
last_updated: 2026-09-05
purpose: 记录从单用户本地形态改造为可承载并发访问的分阶段方案,含瓶颈依据、改动点与验收标准。
---

# 事实边界

本文是**尚未实施**的改造方案,不描述当前系统事实。当前系统事实以 `docs/STATUS.md` 和代码为准。

本文中的瓶颈数字均为**读代码推算,未经压测**。P7 完成前不得在任何对外材料中引用这些数字。

# 与现有文档的冲突

`docs/TECH_DESIGN.md` 当前定义系统范围为"单用户、本地优先的求职准备工作台",并明确"当前不做多用户 SaaS"。

本方案改变这一定位。实施 P1 之前必须先更新 `TECH_DESIGN.md` 的系统范围章节,否则两份文档会互相矛盾。

# 目标重新定义

模型固定为 DashScope 的 qwen3.6-flash / qwen3.6-plus,上游配额是不可突破的硬天花板。因此"抗高并发"不等于无限扩容,而是三条:

1. **接入层无压力** — 提交任务、查询进度这类在线接口不触碰 LLM,可承载高 QPS。
2. **处理层吃满配额** — LLM 任务吞吐等于上游给的配额,自身代码不成为额外瓶颈。
3. **过载不雪崩** — 超出承载能力时排队或明确拒绝,不出现连锁超时和重试风暴。

做不到"高并发 LLM 推理",能做到"高并发接入 + 有界吞吐处理 + 过载可控"。

# 现状瓶颈

按撞墙先后排序,均为读码推算。

| 序号 | 瓶颈 | 依据 | 推算影响 |
| --- | --- | --- | --- |
| 1 | 上游配额 | DashScope QPS / TPM 限制 | 硬天花板 |
| 2 | 进程内并发闸门 | `llm/client.py` 中 `asyncio.Semaphore`,默认 `JOBCOPILOT_LLM_MAX_CONCURRENCY=4` | 同时仅 4 个 LLM 调用在飞;一次出题走 rewrite → rerank → 生成至少 3 次调用,实际并发用户为个位数 |
| 3 | 单进程 uvicorn | `docker/api.Dockerfile` CMD 无 `--workers` | 仅使用单核 |
| 4 | 连接池默认值 | `infra/db.py` 仅设 `pool_pre_ping`,未设 `pool_size` | SQLAlchemy 默认 5 + overflow 10 = 15;SSE 长请求全程持有 session,约 15 个并发会话打满 |
| 5 | worker 在 API 进程内 | `main.py` lifespan 中 `asyncio.create_task(embed_worker.run_forever)` | 多进程部署会重复消费队列、重复计费,是横向扩展的直接阻碍 |
| 6 | 无限流与过载保护 | 全仓无 ratelimit / 熔断实现 | 过载时重试放大,可能反向打爆上游 |

已做对、无需改动的部分:`profile_chunks` 与 `note_chunks` 的 HNSW 与 GIN 索引已建(migration `0005`、`0016`),检索层不是瓶颈。

# 分阶段方案

阶段间存在强依赖,不可跳序。建议先只做 P1 到 P3。

## P1:长任务从在线请求中剥离

**目标**
在线接口不再承载 LLM 全流程,SSE 仅用于订阅进度,断线可重连续看。

**依赖**
先更新 `TECH_DESIGN.md` 系统范围。

**改动点**
- `routers/quiz.py`、`routers/jd.py`:`POST` 改为落库建 job 后立即返回 202 与 job_id,不触碰 LLM。
- 新增 `GET /v1/quiz/jobs/{id}/events`,SSE 只推进度事件,支持从中间接入。
- `services/quiz_service.py`、`services/jd_service.py`:生成器逻辑迁移到 worker 侧执行,进度写入 job 事件表。
- 新增 job 与 job event 数据模型及 Alembic migration。

**设计参考**
Paper Copilot 仓库已实现同形态模型(Job / Attempt / 事件订阅 / 断点恢复),直接复用其状态机设计,不重新设计。

**验收标准**
- `POST /v1/quiz/sessions` P99 响应时间低于 200ms,且调用期间无 LLM 请求发出。
- 客户端在任务执行中断开 SSE 后重连,能收到断开期间产生的事件,任务不中断。
- 单个在线请求持有数据库 session 的时间不超过单次查询时长。

## P2:拆出独立 worker 与真队列

**目标**
任务处理能力可通过增加 worker 副本线性扩展。

**依赖**
P1。

**改动点**
- `main.py`:移除 lifespan 中的 embed_worker 启动逻辑。
- 新增独立 worker 入口与 `docker/worker.Dockerfile`,`docker-compose.yml` 新增 worker 服务并支持副本数配置。
- 队列选型 Redis Streams:需要 consumer group、ack 与 pending 列表,消费者崩溃后任务可被接管。备选 pgmq(compose 注释中的 M2 原计划),但会让 PostgreSQL 同时承担业务库与队列两个角色,高负载下压力集中。
- 任务幂等:以 job_id 为去重键,worker 重启后重复消费不得重复调用 LLM 或重复计费。

**注意**
本阶段引入 Redis,`docker-compose.yml` 服务数将突破 README 与 AGENTS.md 中记录的"6 服务"约束,需同步更新那两处。

**验收标准**
- worker 副本从 1 增至 N,任务吞吐近似线性提升(在未触及上游配额前提下)。
- 强杀一个 worker 进程,其未 ack 的任务被其他副本接管完成,无任务丢失、无重复计费。
- API 容器不再包含任何后台常驻任务。

## P3:全局并发闸门改为 Redis 实现

**目标**
多进程多副本下仍能全局控制对上游的并发,不超配额。

**依赖**
P2(Redis 已引入)。

**改动点**
- 实现 Redis 令牌桶,以 Lua 脚本保证原子性。
- 通过 `llm/client.py` 已预留的 `concurrency_gate_factory` 注入,`LLMClient` 接口不变。
- 必须双维度限制:DashScope 同时约束 QPS 与 TPM,仅控请求数会在长文本请求上超出 TPM。

**验收标准**
- 启动 N 个 API 进程与 M 个 worker 副本,实际发往上游的并发请求数不超过配置值,与进程数无关。
- 压测期间上游 429 比例低于 1%。

## P4:多进程与连接池显式配置

**目标**
吃满多核,同时不打爆数据库连接数。

**依赖**
P2(否则多进程会重复消费队列)。

**改动点**
- `docker/api.Dockerfile`:改为 `gunicorn -k uvicorn.workers.UvicornWorker -w N`。
- `infra/db.py`:显式配置 `pool_size` 与 `max_overflow`。
- 校验总连接数:`API 进程数 × (pool_size + max_overflow) + worker 副本数 × 池大小` 必须小于 PostgreSQL `max_connections`(默认 100)。超出则引入 PgBouncer 做连接复用。

**验收标准**
- 压测中无 `QueuePool limit` 或 `too many connections` 错误。
- CPU 利用率随进程数增加而提升,不再单核饱和。

## P5:减少上游调用

**目标**
配额固定的前提下,省下的每次调用都是净增吞吐。

**依赖**
无强依赖,可与 P4 并行。

**改动点**
- 语义缓存:`llm/cache_key.py` 当前为精确哈希,增加基于 embedding 相似度的近似命中路径,复用现有 embedding 链路。需要保留 `llm_response_cache` 表的成本审计能力,不得因改造丢失成本归因。
- 跳过不必要的调用:短 query 跳过 rewrite;候选集小于 top_k 时跳过 rerank。
- 批处理:`workers/embed_worker.py` 当前一轮一 batch、上限 10(对齐百炼 EMBED_BATCH_LIMIT),队列积压时改为一轮并发多 batch。

**验收标准**
- 相同压测负载下,发往上游的调用次数较改造前下降,且出题质量评测指标不下降。
- 成本归因能力保持可用。

## P6:过载保护

**目标**
超出承载能力时行为可预期,不雪崩。

**依赖**
P2(Redis)。

**改动点**
- 限流:Redis 计数器,用户级与全局两层。
- 队列水位:超过阈值直接返回 503 与 `Retry-After`,不允许队列无限增长。
- 熔断:上游连续返回 429 时短暂停止发送。当前 `llm/client.py` 的 tenacity 重试在过载时会放大压力,必须有熔断兜底。
- 任务 deadline:超期任务直接判死并释放 worker。

**验收标准**
- 以 10 倍于承载能力的负载压测,系统返回明确的 429 / 503,不出现连锁超时,已入队任务仍能正常完成。
- 人为使上游持续返回 429,熔断在阈值内触发,恢复后自动放行。

## P7:压测与基线

**目标**
用实测数字替换本文所有推算值。

**依赖**
P1 到 P6。

**改动点**
- 引入 k6 或 locust,脚本纳入仓库。
- 分两组指标:在线接口(提交、查进度)的 QPS 与 P99;端到端任务完成率、排队时长与吞吐。
- 结果写入 `docs/STATUS.md`,并在 Langfuse 中补充队列深度、等待时长、上游 429 率的观测。

**验收标准**
- 产出可复现的压测报告,包含硬件规格、上游配额假设与完整指标。
- 本文瓶颈表中的推算值全部替换为实测值。

# 预期结果

- 在线接入层:可承载高 QPS,因其只读写数据库与 Redis。
- 任务处理吞吐:等于上游配额,不多不少。
- 用户体验:从"等待数十秒或超时"变为"立即返回 + 进度可见 + 断线可续"。
- 过载行为:排队或明确拒绝,不雪崩。

# 风险

- **范围膨胀**:P1 与 P2 触及核心数据流,改造期间产品功能可能不稳定。建议在独立分支进行,合并前跑通现有全部单测与评测集。
- **成本失控**:并发上去后 token 消耗同比例上升。P6 的限流必须与 P1 到 P3 同期上线,不可后置。
- **文档漂移**:本方案会使 `TECH_DESIGN.md` 的系统范围、README 与 AGENTS.md 的服务数约束失效,每阶段完成后同步更新。
