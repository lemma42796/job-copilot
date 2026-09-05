---
title: JobCopilot 技术与架构设计
owner: lemma42796
last_updated: 2026-08-15
purpose: 记录稳定技术架构、模块边界、核心数据流和跨模块约束。
---

# 事实边界

本文解释系统为什么这样设计,不复制完整字段或端点 schema。发生冲突时按以下顺序判断:

1. 当前代码、Alembic migration、配置和可复现运行结果。
2. Pydantic schema / OpenAPI、prompt / Agent 输出 schema、评测 dataset / script / report。
3. 本文的架构语义。

运行 API 文档位于开发环境 `/v1/docs`,OpenAPI 位于 `/v1/openapi.json`。数据字段以 `models/` 和 `apps/api/alembic/versions/` 为准;Agent 输入输出以 `schemas/agents/`、prompt 和实现代码为准。

# 系统范围

JobCopilot v2 是**多用户线上服务**,当前有两条产品链路:

- Markdown 笔记 → RAG 出题 → AnswerJudge 三层评分 → InterviewCoach 多轮纠偏与总结。
- 文本 JD → 解析 / 聚合 → 岗位要求地图 → 知识库覆盖 → 学习路径和 quiz topics。

多用户带来三条贯穿全仓的约束:

- **数据隔离**:所有业务表带 `user_id`,service 层每条查询都按它过滤;别人的资源返回 404 而不是 403(不泄露 id 是否存在)。笔记文件按 `<notes_fs_root>/users/<user_id>/` 分目录。
- **成本归属**:每次上游调用前查余额、调用后按实际 `cost_cny` 扣费。生成、rerank、embedding 三条链路都进 `llm_calls` + `balance_transactions`。余额中途耗尽就地中止、保留已产生结果、不回滚。
- **执行与在线解耦**:会调用 LLM 的长任务一律 202 + job_id,执行在独立 worker 进程。

当前不做支付接入(充值是账本上的模拟操作)、简历上传 / 诊断 / 改写、岗位类三源出题、JD 截图 OCR、SR 或弱点 dashboard。仓库中若仍有 v1 / 已砍方向的未挂载模块,不能据此宣称当前产品支持该能力;活跃产品入口以 `main.py` 挂载的 router 为准。

# 总体架构

```text
Next.js Web
  ├─ /notes:本地 Markdown 导入、树形导航、编辑
  ├─ /quiz:主题出题、答题、纠偏、总结、恢复
  └─ /jds:JD 入库、一键分析、报告查看
        │ REST / SSE
        ▼
FastAPI(gunicorn + UvicornWorker,多进程)
  routers → services → models
      │  在线请求只做:鉴权、校验、写库、读库
      │  长任务:写一行 jobs → 202 + job_id
      ▼
Redis Streams(consumer group + XACK + XAUTOCLAIM)
      ▼
worker 进程(N 副本,`python -m jobcopilot_api.workers.main`)
      ├─ job_worker  → services → agents / llm → DashScope OpenAI-compatible API
      ├─ embed_worker(FOR UPDATE SKIP LOCKED)
      └─ job reaper(deadline 判死)
      ▼
PostgreSQL 16 + pgvector

Observability:Langfuse + llm_calls
Evaluation:evals/suites + apps/api/scripts/eval_*.py
```

主要技术栈:

| 层 | 选型 |
|----|------|
| Web | Next.js App Router、React、Tailwind、Monaco |
| API | FastAPI、Pydantic、SQLAlchemy async、asyncpg |
| 数据 | PostgreSQL 16、pgvector、tsvector / char n-grams |
| LLM | 阿里云百炼 OpenAI 兼容接口、`qwen3.8-flash` |
| Embedding / rerank | `text-embedding-v4`、`qwen3-rerank` |
| 编排 | 确定性 service workflow + 受控 Agent 节点 |
| 观测 | Langfuse、`llm_calls`、结构化日志 |
| 包管理 | Python uv workspace、pnpm workspace |

# 模块边界

后端目录以 `apps/api/src/jobcopilot_api/` 为根:

- `routers/`:HTTP / SSE 边界、依赖注入和事务收尾。
- `services/`:业务编排、状态转换、检索治理和持久化协调。
- `agents/`:渲染 prompt、调用 LLM、Pydantic 校验;不负责公共 HTTP 契约。
- `schemas/`:REST、SSE 和 Agent 输入输出的运行契约。
- `models/`:SQLAlchemy 表模型;迁移由 Alembic 单 head 管理。
- `llm/`:provider、缓存、重试、价格和调用审计。
- `workers/`:独立 worker 进程里的后台任务 —— `job_worker`(长任务执行)、`embed_worker`(补 embedding)、`main`(进程入口)。**不由 API 的 lifespan 启动**:API 多进程 / 多副本后,挂在 lifespan 里的后台任务会被每个进程各跑一份,既重复调用上游也重复扣费。
- `infra/`:数据库、日志、request id、prompt 加载等基础设施。

事务原则:service 负责 `flush` 和业务操作,router 或独立 Worker 在边界处 `commit`;异常路径统一 rollback。不要在 Agent 内直接控制 HTTP response 或数据库事务。

# 活跃 API 边界

`main.py` 当前挂载四类 router:`/v1/health`、`/api/notes*`、`/api/quiz*`、`/api/jds*` 与 `/api/jd-analyses*`。端点清单与字段以 router、Pydantic schema 和 OpenAPI 为准,本文不复制。统一约束:

- 普通端点使用 JSON;错误由全局 handler 转为 Problem+JSON。
- 慢链路使用 `text/event-stream`。
- SSE 失败也要发送 `error` 后再发送 `done {ok:false}`;正常链路以 `done {ok:true}` 收尾。
- `started` 必须在资源落库并获得稳定 id 后发送。
- 列表使用 cursor + limit;limit 上限由对应 schema / router 定义。
- 除 `/v1/health` 与 `/api/auth/register` / `/api/auth/login` 外,所有端点需要 `Authorization: Bearer <token>`;`X-User-Id` 兜底只在 `JOBCOPILOT_ENV=dev` 下接受。
- 会调用 LLM 的长任务返回 **202 + job_id**,进度经 `GET /api/jobs/{id}/stream` 订阅;订阅带 `after_seq` 支持断线续读。SSE 的 `error` / `done` 契约不变,只是事件源从进程内内存换成 `job_events` 表。
- 待执行任务超过水位返回 503 + `Retry-After`;上游连续 429 触发熔断,同样是 503 + `Retry-After`,两者用响应体 `code` 区分。

# 核心数据模型

只记录实体关系和关键不变量,字段细节看 model / migration。

```text
notes 1 ── N note_chunks
quiz_sessions 1 ── N session_answers N ── 1 questions
quiz_sessions 1 ── N session_events
jds N ── N jd_analyses  (jd_ids JSONB snapshot)
prompt_versions / llm_calls / llm_response_cache 记录 LLM 配置、审计和缓存
```

关键不变量:

- `note_chunks` 保存 `folder_path`、`heading_path`、全文检索字段和 1024 维 embedding。
- `questions.evidence_chunk_ids`、`reference_answer_chunk_ids` 和 `scoring_points` 最终存真实 `note_chunks.id`。
- prompt 中的 `[N]` 只是本次 final context 的局部编号,入库前由后端映射;重复引用字段由后端派生。
- `quiz_sessions.final_context_chunk_ids` 固定出题上下文;评分沿用同一批上下文。
- `session_answers` 分离正式答案 turn、评分 evidence、教练消息和 remediation state。
- `session_events` 用于状态机回放与恢复,不以 SSE 连接作为唯一状态源。
- `jds.parsed_payload` 是单 JD 解析快照;`jd_analyses` 保存一次选定集合的聚合报告快照。
- JSONB schema 的运行事实由 Pydantic schema 和消费代码共同约束,不能只改文档。

# 核心数据流

只记录模块顺序与边界;具体步骤以代码、migration 和 OpenAPI 为准。

## 笔记入库

`/api/notes/batch-import` → heading-aware chunk → `notes` / `note_chunks` 落库(embedding=NULL)→ embed worker 异步补齐。

- 不接 zip 或第三方笔记同步。
- 负载压力按总字数 / token 判断,不按文件数判断。

## 主题出题与检索

主题 query → QueryRewriter → hybrid retrieval + weighted RRF → provider rerank challenger → deterministic governance / blend → zero-hit guard → QuizGenerator → `quiz_sessions` 落库。

- 只支持主题 query;聊天框是唯一出题入口。
- 用户原话权重大于改写,项目私有实体不能被泛化。
- reranker 只是 challenger source,最终成员必须再过 deterministic governance。
- parent-doc 默认关闭,不进入引用 id。
- 命中不足时直接返回“笔记里没有该主题”,不让 LLM 用常识兜底编题。

## 答题、评分与纠偏

答案 / 补答 → AnswerJudge 产出 coverage / fidelity / depth evidence → Python 计算稳定总分并校验引用 → InterviewCoach 决定 remediate / ask_next / finish → 状态与 event 落库 → SSE 通知前端。

- LLM 给 evidence 和 label,不直接决定产品总分。
- 补答并入累计答案后重评;教练追问不修改正式答案或分数。
- 纠偏必须 evidence-bound,不能引入 final context 外的新标准答案来源。
- 多轮上下文使用 context pack 和摘要,不把全量 transcript 塞回 prompt。
- 退出条件包括达标、用户跳过、无明显提升、偏题和 token budget。
- recall 文件路径由后端固定为 `notes/_recall/{session_id}.md`。

## JD 分析

文本 JD → JdParser 即时解析存 `parsed_payload` → 用户选定范围 → JdAggregator raw-JD reduce / merge → Python 重算 supporting_jd_ids 与频次 → note coverage matching → learning path + quiz topics → `jd_analyses` 报告快照。

- JD 聚合是对已选集合的有界归纳,不是 RAG;RAG 只在报告 topic 进入 `/quiz` 后发生。
- 执行与观察解耦:任务由 worker 进程执行,事件逐条写 `job_events`;SSE 订阅只读这张表。断线不取消任务,带 `after_seq` 重新订阅即可补齐断开期间的事件。
- 文本生成统一经过进程内 LLM admission gate,`BaseLLMClient` 与 AnswerJudge 工具调用链共享同一并发额度;缓存命中不占额度,实际 Provider 调用及其重试占额度。

当前可靠性边界:进度持久化(`job_events`)、待执行容量(队列水位 503)、幂等键(`jobs.dedupe_key` + `status='queued'` 条件写领取)、heartbeat 与 deadline 都已具备,worker 可多副本并崩溃接管(Redis XAUTOCLAIM)。仍**不是** exactly-once:条件写保证同一 job 不被两个消费者同时执行,但一次执行内部若在扣费之后崩溃,重试会重复消耗上游 —— 当前策略是不自动重试已 `running` 的 job,由 reaper 判死后人工重发。

# Agent 与 LLM 约束

| 组件 | 职责 | 关键边界 |
|------|------|----------|
| QueryRewriter | 生成可检索改写和权重 | 保留原始实体与否定条件 |
| QuizGenerator | 基于 final context 出题和采分点 | 只能引用本次 context |
| AnswerJudge | 输出三层 label / evidence / coach message | Python 算分;必要时受控查笔记 |
| InterviewCoach | 状态机分支、纠偏、恢复和总结 | 不是自由多 Agent 对话 |
| JdParser | 单 JD 结构化抽取 | 找不到的信息留空,不补全 |
| JdAggregator | 多 JD 技术栈合并、学习路径 | LLM 合并,Python 重算频次 |

Prompt 和模型规则:

- 当前文本 Agent 统一使用 `qwen3.8-flash`;temperature 由具体 Agent 显式配置。
- thinking 默认开启,力度为 `reasoning_effort=medium`(思维链预算 16384 token),三个 tier 一致。上游要求 `reasoning_effort` 与 `thinking_budget` 不可同设,故全仓只下发前者。例外:AnswerJudge 在强制指定工具的轮次单独关闭 thinking,因为上游不支持思考模式下强制调用某个工具。
- prompt 修改必须 bump version;启动时从仓库 prompt 资产同步到 `prompt_versions`。
- 应用层 response cache 缓完整请求 / 响应;query embedding eval 默认 cache-only。缓存键包含 `user_id`,**跨用户不共享** —— 缓存的 `request` 里是用户原始输入(笔记片段、query),跨用户复用等于内容泄露。语义近似缓存(`llm/semantic_cache.py`)同样按用户隔离,默认关闭。
- Context Cache 只优化 provider 计算和计费,不是会话记忆,当前显式模式默认关闭。
- Langfuse 环境变量必须在 import routers / agents / llm 前完成镜像。
- 文本 LLM Provider 调用必须通过共享 admission gate,避免各 Agent 各自设置互不相干的并发上限。
- embeddings 和 rerank 不在 OpenAI auto-instrument 覆盖范围,需要显式 generation trace。

# 并发与成本约束

上游 `qwen3.8-flash` 控制台配额(**该模型独享,`qwen3-rerank` 与 embedding 各有独立配额池**):TPM 5,000,000(额度有效期 2026-08-15~2026-09-15);控制台未公布该模型 RPM,按 TPM 单维度推算。配额到期后需重新确认档位,本节所有并发上限随之重算。

本项目单次生成调用约 15,000 token(`RERANK_TOP_K=10` 个片段 × `MAX_CHUNK_TOKENS=1000`,加 prompt 与输出),因此 TPM 允许约 333 次调用/分。按单次调用 30 秒估算,允许**约 166 个 LLM 调用同时在飞**。

由此确定的两条边界:

- **上游配额不是瓶颈,成本是。** 上游不会通过限流帮我们刹车,打多少收多少。余额扣费是唯一刹车,任何提高并发的改动都必须在扣费链路完整之后。
- **全局并发闸门暂不做 Redis 实现。** 当前 4 个 API 进程 × `llm_max_concurrency=32` = 128,仍在 166 余量内。触发条件是 `进程数 × llm_max_concurrency` 接近 166;届时用 Redis 令牌桶 + Lua 原子脚本,通过 `llm/client.py` 已预留的 `concurrency_gate_factory` 注入(`LLMClient` 接口不变),必须双维度限制 QPS 与 TPM,并同时接管 `services/reranker.py`。

15,000 token / 30 秒这两个数字是读代码推算,未经压测 —— 2026-09-05 的 k6 压测跑在 stub 上游下,测不出真实 token 分布与真实耗时,这两个值仍待真实 provider 小样本补测。任一偏离一个数量级则上述结论全部要重算,补测前只用于内部决策,不得对外引用。

# 可观测性与评测

- `llm_calls` 记录 feature、model、thinking、tokens、cost、latency、success、trace 和关联实体。
- Langfuse public key 为空时进入 noop;CLI / eval 脚本仍需显式管理 SDK 生命周期。
- 正式评测说明、dataset 和最近可信报告位于 `evals/`;固定 smoke 只能证明覆盖的关键路径,不能外推任意 query 或线上容量。
- AI 助手不主动运行测试 / lint / typecheck / build / Playwright;用户明确要求时才执行。

# 演进原则

- 当前以确定性 workflow 为主,只在语义判断、生成和受控分支处使用 LLM。
- 新增持久化状态必须配 migration;公共接口改变必须同步 schema / OpenAPI 和调用方。
- 跨里程碑且难以撤销的架构决策才新增 ADR;下一编号为 `0007`。
- 不为展示技术名词引入微服务、Kafka、Kubernetes、ONNX、CUDA 或 LoRA。Redis 已引入,用途单一:作为长任务队列(consumer group + ack + pending 接管),不当缓存也不当会话存储。

# 不在本文档范围

- 项目最新状态 → `STATUS.md`
- 当前与未完成任务 → `TASKS.md`
- 评测规范与组件证据 → `../evals/EVAL_GUIDE.md`
