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

JobCopilot v2 是单用户、本地优先的求职准备工作台,当前有两条产品链路:

- Markdown 笔记 → RAG 出题 → AnswerJudge 三层评分 → InterviewCoach 多轮纠偏与总结。
- 文本 JD → 解析 / 聚合 → 岗位要求地图 → 知识库覆盖 → 学习路径和 quiz topics。

当前不做多用户 SaaS、简历上传 / 诊断 / 改写、岗位类三源出题、JD 截图 OCR、SR 或弱点 dashboard。仓库中若仍有 v1 / 已砍方向的未挂载模块,不能据此宣称当前产品支持该能力;活跃产品入口以 `main.py` 挂载的 router 为准。

# 总体架构

```text
Next.js Web
  ├─ /notes:本地 Markdown 导入、树形导航、编辑
  ├─ /quiz:主题出题、答题、纠偏、总结、恢复
  └─ /jds:JD 入库、一键分析、报告查看
        │ REST / SSE
        ▼
FastAPI
  routers → services → agents / llm → models
      │                       │
      │                       └─ DashScope OpenAI-compatible API
      ├─ embed worker
      └─ PostgreSQL 16 + pgvector

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
- `workers/`:与请求生命周期解耦的后台任务;当前只有 embedding worker。
- `infra/`:数据库、日志、request id、prompt 加载等基础设施。

事务原则:service 负责 `flush` 和业务操作,router 或独立 Worker 在边界处 `commit`;异常路径统一 rollback。不要在 Agent 内直接控制 HTTP response 或数据库事务。

# 活跃 API 边界

`main.py` 当前挂载四类 router:`/v1/health`、`/api/notes*`、`/api/quiz*`、`/api/jds*` 与 `/api/jd-analyses*`。端点清单与字段以 router、Pydantic schema 和 OpenAPI 为准,本文不复制。统一约束:

- 普通端点使用 JSON;错误由全局 handler 转为 Problem+JSON。
- 慢链路使用 `text/event-stream`。
- SSE 失败也要发送 `error` 后再发送 `done {ok:false}`;正常链路以 `done {ok:true}` 收尾。
- `started` 必须在资源落库并获得稳定 id 后发送。
- 列表使用 cursor + limit;limit 上限由对应 schema / router 定义。
- 单用户本地部署当前无 auth;这不是可直接外推的 SaaS 接口。

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
- 执行与观察解耦:进程内 task registry 承载任务,SSE 只订阅有界事件缓冲区;断线不取消任务,同 `analysis_id` 可重新订阅,API 重启会重新启动仍为 `in_progress` 的记录。
- 文本生成统一经过进程内 LLM admission gate,`BaseLLMClient` 与 AnswerJudge 工具调用链共享同一并发额度;缓存命中不占额度,实际 Provider 调用及其重试占额度。

当前可靠性边界:单 API 进程 MVP,不是分布式任务系统。没有逐步 progress 持久化、待执行容量、幂等键、lease 或 heartbeat;多 API 实例可能重复执行同一 `in_progress` 分析。进程重启恢复允许重复外部调用,不能表述为 exactly-once。独立 Worker、多实例接管和容量报告不在当前范围。

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

- 当前文本 Agent 统一使用 `qwen3.8-flash`;thinking / temperature 由具体 Agent 显式配置。
- prompt 修改必须 bump version;启动时从仓库 prompt 资产同步到 `prompt_versions`。
- 应用层 response cache 缓完整请求 / 响应;query embedding eval 默认 cache-only。
- Context Cache 只优化 provider 计算和计费,不是会话记忆,当前显式模式默认关闭。
- Langfuse 环境变量必须在 import routers / agents / llm 前完成镜像。
- 文本 LLM Provider 调用必须通过共享 admission gate,避免各 Agent 各自设置互不相干的并发上限。
- embeddings 和 rerank 不在 OpenAI auto-instrument 覆盖范围,需要显式 generation trace。

# 可观测性与评测

- `llm_calls` 记录 feature、model、thinking、tokens、cost、latency、success、trace 和关联实体。
- Langfuse public key 为空时进入 noop;CLI / eval 脚本仍需显式管理 SDK 生命周期。
- 正式评测说明、dataset 和最近可信报告位于 `evals/`;固定 smoke 只能证明覆盖的关键路径,不能外推任意 query 或线上容量。
- AI 助手不主动运行测试 / lint / typecheck / build / Playwright;用户明确要求时才执行。

# 演进原则

- 当前以确定性 workflow 为主,只在语义判断、生成和受控分支处使用 LLM。
- 新增持久化状态必须配 migration;公共接口改变必须同步 schema / OpenAPI 和调用方。
- 跨里程碑且难以撤销的架构决策才新增 ADR;下一编号为 `0007`。
- 不为展示技术名词引入微服务、Kafka、Kubernetes、Redis、ONNX、CUDA 或 LoRA。

# 不在本文档范围

- 项目最新状态 → `STATUS.md`
- 当前与未完成任务 → `TASKS.md`
- 评测规范与组件证据 → `../evals/EVAL_GUIDE.md`
