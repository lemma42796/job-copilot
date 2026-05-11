---
title: TECH DESIGN - JobCopilot v2(架构 / 模块分层 / 数据流 / 错误处理)
owner: lemma42796
last_updated: 2026-05-11
purpose: 锁系统架构、技术栈、模块边界、核心数据流、错误处理分层、v1 沿用 / 砍除清单
---

# 1. 一句话总览

monorepo:**FastAPI + asyncpg + pgvector** 后端,**Next.js App Router + Tailwind + Monaco** 前端,**LLM 走阿里云百炼 OpenAI 兼容接口 + qwen3.6-flash 多模态**(thinking 按 agent 决定),M2.1 起用 LangGraph 编排 InterviewCoachAgent 状态机。慢请求(出题 / 评分 / JD 一键分析 / 简历诊断)走 SSE,embedder 走后台 worker。

# 2. 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                  Next.js 14 App Router (apps/web)                │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐    │
│  │ TreeNav      │  │ Monaco       │  │ QuizRunner +       │    │
│  │ (笔记导航)   │  │ Editor       │  │ ScoreCard (SSE)    │    │
│  └──────────────┘  └──────────────┘  └────────────────────┘    │
│                          │ lib/api.ts + lib/sse.ts             │
└──────────────────────────┼──────────────────────────────────────┘
                           │ HTTP / SSE  (localhost:8000)
┌──────────────────────────┼──────────────────────────────────────┐
│              FastAPI (apps/api/src/jobcopilot_api/)             │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐    │
│  │ routers/     │  │ services/    │  │ agents/            │    │
│  │  notes       │←→│  notes_svc   │←→│  quiz_generator    │    │
│  │  quiz        │  │  chunk_svc   │  │  answer_judge      │    │
│  │  dashboard   │  │  quiz_svc    │  │  embedder          │    │
│  │  (SSE)       │  │  answer_svc  │  │  interview_coach   │    │
│  └──────────────┘  │  search_svc  │  └─────────┬──────────┘    │
│                    │  gap_svc(M3) │            │ llm/          │
│                    └──────┬───────┘            ▼               │
│                           │            ┌────────────────┐     │
│                           │            │ DashScope SDK  │     │
│                           │            │ (qwen3.6-flash │     │
│                           │            │  + emb-v4)     │     │
│                           ▼            └────────────────┘     │
│                    ┌────────────┐                              │
│                    │ models/ +  │                              │
│                    │ asyncpg    │                              │
│                    └─────┬──────┘                              │
│       ┌───────────────────┼────────────────────┐               │
│       ▼                                        ▼               │
│  ┌──────────────────────────────┐  ┌──────────────────────┐    │
│  │ workers/embed_worker          │  │ scripts/eval_*.py    │    │
│  │ (后台异步,补 embedding)      │  │ (CI 评测套件)        │    │
│  └──────────────┬────────────────┘  └──────────────────────┘    │
└─────────────────┼───────────────────────────────────────────────┘
                  ▼
       ┌─────────────────────────┐
       │ Postgres 16 + pgvector  │
       │  notes / note_chunks    │
       │  questions / sessions   │
       │  llm_response_cache     │
       │  prompt_versions        │
       └─────────────────────────┘
```

# 3. 技术栈

| 类别 | 选型 | 备注 |
|------|------|------|
| 语言 | Python 3.13 / TypeScript 5 | 沿用 v1 |
| 包管理 | uv workspace(Python)/ pnpm(JS)| 沿用 v1;workspace member 装依赖必须 `uv sync --all-packages`(LESSONS §7.2) |
| 后端框架 | FastAPI + uvicorn(`--reload` dev / gunicorn prod) | 沿用 v1 |
| ORM | SQLAlchemy 2.x async + asyncpg | 沿用 v1;**不写 `relationship()`**(ADR-0005 D1) |
| 迁移 | Alembic(单 head)| 沿用 v1;v2 切片 0016 砍 v1 表 + 建 v2 表 |
| DB | Postgres 16 + pgvector 0.7 | 沿用 v1 |
| 全文搜索 | tsvector + char_ngrams SQL 函数 | 沿用 v1 alembic 0014 |
| LLM SDK | OpenAI Python SDK 走百炼 OpenAI 兼容接口 | base_url=`https://dashscope.aliyuncs.com/compatible-mode/v1`;`from langfuse.openai import OpenAI` 自动 instrument(只覆盖 chat/completions/responses;embeddings 要手动包 generation,见 STATUS 永久约束) |
| LLM 模型 | qwen3.6-flash(多模态:文本 + 图像 + tool use 一把抓);thinking 按 agent 决定 | 详见 5-AGENT §2.1;qwen3.6 系列整体是视觉模型 |
| LLM cache | `llm_response_cache` 表 + 4-B cache layer | 沿用 v1 alembic 0015 |
| Embedding | text-embedding-v4(1024 维) | 沿用 v1 |
| Agent 编排 | M2 仍是 service 直接编排;M2.1 起上 LangGraph `InterviewCoachAgent` 状态机;M3 扩 SR / 三源岗位流 | LangGraph checkpointer 序列化坑见 LESSONS §2.1 |
| SSE | `sse-starlette.EventSourceResponse` | 沿用 v1;前端走 `web/lib/sse.ts`(永久约束 #21)|
| 前端框架 | Next.js 14 App Router + React 18 | 沿用 v1 |
| 前端样式 | Tailwind 自己写,无组件库 | macOS 风(PRD §9)|
| 编辑器 | Monaco(client-side dynamic import) | 笔记 .md 编辑(US-2) |
| 评测 | pytest + Cohen's kappa(`evals/kappa.py`)| 沿用 v1 |
| 可观测性 | Langfuse 自部署(docker compose)| LLM-native trace + cost / token / latency 视图;详见 §6 |
| Tool use | DashScope `function_call` API(AnswerJudge 反假阳性工具)| 详见 5-AGENT §4.7 |
| 部署 | docker compose(本地)| MVP 单用户;M4+ 再考虑 SaaS |

# 4. 后端模块分层

## 4.1 目录骨架

```
apps/api/src/jobcopilot_api/
├── main.py                         # FastAPI app + 启动钩子(挂 embed worker)
├── settings.py                     # env config(BYOK key 从 .env)
├── errors.py                       # JobCopilotError 全局异常
├── routers/                        # HTTP / SSE 出口层
│   ├── notes.py                    # POST/GET/PUT/DELETE /api/notes/*
│   ├── quiz.py                     # POST /api/quiz/sessions(SSE)等
│   ├── jd.py                       # M2.5:POST/GET /api/jds + jd-analyses(SSE)
│   ├── resume.py                   # M3:POST/GET /api/resumes + resume-analyses(SSE)
│   ├── dashboard.py                # M3
│   └── health.py
├── services/                       # 业务逻辑层(无 LLM 调用)
│   ├── notes_service.py            # CRUD + zip unpack
│   ├── chunk_service.py            # heading-aware markdown chunker(v1 改造)
│   ├── tokenize.py                 # char_ngrams Python 实现(沿用 v1)
│   ├── search_service.py           # 全库 hybrid search RRF(沿用 v1) + global_hybrid_search(M2 lookup tool)
│   ├── retrieval_pipeline.py       # M2:出题前 retrieval 编排(query_rewrite → hybrid → rerank → parent-doc 扩展);0 命中守门
│   ├── query_rewriter.py           # M2:LLM 改写 query(短词 → 同义/相邻概念集),走 llm.client + cache
│   ├── reranker.py                 # M2:cross-encoder rerank(top 50 → top 10),选型见 PRD Q-07
│   ├── quiz_service.py             # 出题编排(调 quiz_generator agent + 落库)
│   ├── interview_service.py        # M2.1:InterviewCoachAgent session 编排 / checkpoint / SSE
│   ├── answer_service.py           # 答题落库 + 算分 + finalize session
│   ├── jd_service.py               # M2.5:JD 上传(立即调 jd_parser)+ 一键分析编排(map-reduce + Python 重算频次)
│   ├── resume_service.py           # M3:简历入库 + 段落 chunker
│   └── knowledge_gap_service.py    # M3:upsert + SR 队列
├── agents/                         # LLM 调用层
│   ├── embedder/                   # 沿用 v1
│   ├── quiz_generator/
│   │   ├── agent.py                # 编排:渲染 USER + 调 llm.complete + Pydantic 校验
│   │   └── prompts.py              # SYSTEM 常量 + render_user(...)
│   ├── answer_judge/
│   │   ├── agent.py
│   │   ├── prompts.py
│   │   └── scoring.py              # Python SSoT 算分(5-AGENT_DESIGN §4.5)
│   ├── jd_parser/                  # M2.5:单 JD 抽要求(thinking off)
│   │   ├── agent.py
│   │   └── prompts.py
│   ├── jd_aggregator/              # M2.5:hierarchical reduce + 学习路径生成
│   │   ├── agent.py                # 三阶段编排(batch reduce / merge / learning_path)
│   │   ├── prompts.py
│   │   └── frequency.py            # Python 重算频次 SSoT
│   ├── resume_advisor/             # M3:两方锚点严格诊断
│   │   ├── agent.py                # 包含 forbidden_pattern 后处理校验
│   │   ├── prompts.py
│   │   └── forbidden_patterns.py   # 替写文案漏洞检测正则集
│   └── interview_coach/            # M2.1 LangGraph:状态机 + 工具 + 追问分支
├── llm/                            # LLM 客户端(改造:DashScope SDK → OpenAI Python SDK)
│   ├── client.py                   # LLMClient + cache + retry(走 base_url 兼容接口)
│   ├── cache.py / cache_key.py / cache_store.py
│   ├── providers/                  # OpenAI 兼容 adapter(走 dashscope.../compatible-mode/v1)
│   ├── pricing.py / tiers.py
│   └── errors.py
├── infra/                          # 基础设施(沿用 v1 大部分)
│   ├── db.py                       # async sessionmaker
│   ├── embedder.py                 # Embedder wrapper(同 v1)
│   ├── logging.py / request_id.py
│   └── upload.py                   # 改造 v1(原 PDF → 现 zip 处理)
├── models/                         # SQLAlchemy ORM(详见 3-DATA_MODEL §5)
│   ├── note.py / note_chunk.py
│   ├── question.py
│   ├── quiz_session.py / session_answer.py
│   ├── knowledge_gap.py
│   ├── jd.py / jd_analysis.py                       # M2.5
│   ├── resume.py / resume_analysis.py               # M3
│   ├── prompt_version.py / llm_call.py / llm_response_cache.py  # 沿用 v1
│   └── base.py                     # Base / IDMixin / TimestampMixin(沿用 v1)
├── schemas/                        # Pydantic IO 校验(REST + SSE 事件 + agent IO)
│   ├── notes.py / quiz.py / dashboard.py
│   ├── jd.py / resume.py
│   ├── agents/{quiz_generator, answer_judge, interview_coach, jd_parser, jd_aggregator, resume_advisor}.py
│   └── sse.py                      # SSE 事件 schema(详见 4-API_SPEC §2.3)
├── prompts/                        # Prompt 加载器(沿用 v1 LoadedPrompt)
├── evals/                          # 评测框架(沿用 v1)
│   ├── judge.py / kappa.py
│   └── (suite-specific 算法在 scripts/eval_*.py)
├── workers/                        # 后台异步任务
│   └── embed_worker.py             # 轮询 embedding=NULL 的 chunks 批量算
└── scripts/                        # CLI 入口
    ├── eval_hybrid_search.py
    ├── eval_quiz_generator.py
    ├── eval_answer_judge.py
    ├── eval_jd_aggregator.py       # M2.5
    ├── eval_resume_advisor.py      # M3
    └── seed.py                     # dev 灌测试数据
```

## 4.2 前端目录骨架

```
apps/web/src/
├── app/                            # Next.js App Router
│   ├── notes/                      # 树形导航 + 笔记编辑
│   │   ├── page.tsx                # 树 + 编辑器双栏
│   │   └── import/page.tsx         # 本地目录 / 单篇导入(File System Access API)
│   ├── (quiz)/
│   │   ├── new/page.tsx            # **聊天框输 query** + 启动 session(M3 加岗位类多源 / 空 query SR 自选)
│   │   ├── [sessionId]/page.tsx    # 答题页(笔记面板隐藏)
│   │   └── [sessionId]/score/page.tsx   # 评分页(笔记面板恢复)
│   ├── (jd)/                       # M2.5:JD 上传 + 我的 JD 库 + 一键分析
│   │   ├── upload/page.tsx
│   │   ├── library/page.tsx        # 列表 + 筛选 + 选范围一键分析按钮
│   │   └── analyses/[id]/page.tsx  # 分析报告详情(频次表 + 学习路径 markdown)
│   ├── (resume)/                   # M3:简历上传 + 诊断
│   │   ├── upload/page.tsx
│   │   └── diagnose/[id]/page.tsx  # 三方对照视图(JD 要求 / 简历段落 / 建议主题)
│   ├── (dashboard)/page.tsx        # M3:弱点 + 今日复习
│   └── layout.tsx
├── lib/
│   ├── api.ts                      # fetch 封装 + 错误格式解析
│   ├── sse.ts                      # SSE 客户端(沿用 v1 永久约束 #21)
│   └── monaco-loader.ts
└── components/
    ├── tree-nav/
    ├── editor/
    ├── quiz-runner/
    ├── score-card/                 # 三层 evidence 渲染
    └── markdown-renderer/
```

## 4.3 模块职责边界

| 层 | 干什么 | 不干什么 |
|----|------|--------|
| `routers/` | 解析请求 / 调用 service / 转 SSE 事件或 HTTP 响应 | **不**直接调 LLM、**不**写复杂业务逻辑 |
| `services/` | 业务编排 / 事务 / 数据库读写 / 调 agents | **不**直接调 DashScope SDK(走 `llm.client`)|
| `agents/` | 渲染 prompt / 调 LLM / Pydantic 校验 / Map output | **不**写 DB(返回结构化结果给 service 层落) |
| `llm/` | DashScope 调用 / 缓存 / 重试 / 计价 | **不**关心业务语义(只关心 prompt → completion) |
| `models/` | ORM 类(BIGSERIAL / TimestampMixin / 字段映射) | **不**写 `relationship()`(ADR-0005 D1) |
| `workers/` | 后台异步队列(embedding) | **不**走 router 路径,启动钩子挂在 main.py |

# 5. 五条核心数据流

## 5.1 笔记本地目录直读

```
FE (showDirectoryPicker / showOpenFilePicker — Chromium 系浏览器)
  → 浏览器递归遍历选中目录,每个 .md await file.text()
  → 按相对路径解析 folder_path,内存里组装 NoteBatchImportItem[]
  → 分批(默认 50/批)POST /api/notes/batch-import (application/json)
  → routers/notes.batch_import
  → services/notes_service.batch_import()
      ├─ for each item:
      │    ├─ 查重 (folder_path, title) WHERE deleted_at IS NULL
      │    │     ├─ 已存在 + overwrite=False → 跳过 + 加 skipped_reasons
      │    │     └─ 已存在 + overwrite=True  → 覆盖 content_md + 重切
      │    ├─ services/chunk_service.rechunk_note(note.id)
      │    │     → list[NoteChunk](按 H2/H3 切,heading_path 元数据)
      │    └─ INSERT notes(source='local_md')+ note_chunks(embedding=NULL)
      └─ 单事务 commit
  → 返回 {imported, skipped, skipped_reasons, note_ids}
```

后台 embed worker 异步算 embedding(详见 §5.5)。**API 不等**。

## 5.2 出题 SSE(M2 主题类 query → 全库 RAG)

```
FE → POST /api/quiz/sessions {query, count}        # query 例:"考考我多线程"
  → routers/quiz.create_session (EventSourceResponse)
  → 校验:query 非空且长度合理(否则 query_required / query_too_long)
  → INSERT quiz_sessions(status=in_progress, query=...) → emit started{session_id}

  → emit progress{phase=query_rewriting}
      └─ services/query_rewriter.rewrite(query)
            → list[expanded_query]  例:["并发", "多线程", "锁", "死锁"]
            (LLM 调用,小温度,走 llm.client + cache;失败回退原 query)

  → emit progress{phase=hybrid_searching}
      └─ services/search_service.global_hybrid_search(expanded_queries, top_k=50)
            ├─ vector 路:pgvector HNSW(WHERE embedding IS NOT NULL)
            ├─ lex 路:tsvector char_ngrams
            ├─ RRF 融合 → top 50 chunks
            └─ 任一路异常另一路兜底(沿用 M1 service 契约)

  → emit progress{phase=reranking}
      └─ services/reranker.rerank(query, chunks_50) → top 10
            (cross-encoder 模型,本地或百炼 reranker 接口,见 PRD Q-07)

  → emit progress{phase=parent_doc_expanding}
      └─ services/retrieval_pipeline.expand_to_parent(top_10_chunks)
            → 命中 chunk 扩展回同 H2/H3 父段全文(粒度见 PRD Q-08)

  → 0 命中守门:expanded chunks < 阈值(PRD Q-10) → emit error{code='no_chunks_for_query'} + done(false)
                                                  + 标 quiz_sessions.status=abandoned

  → emit progress{phase=generating, chunk_count=N}
  → agents/quiz_generator.run(query, chunks, metadata=heading_paths, count)
      ├─ render USER(chunks 用 [N] 编号 + query 当 hint + heading_path 当主题锚点)
      ├─ llm.client.complete(SYSTEM + USER) [+ cache 命中可能]
      ├─ Pydantic 校验 / retry ≤1
      └─ map [N] → DB chunk_id
  → emit progress{phase=type_mix_decided, type_mix}
  → INSERT questions × N → INSERT session_answers × N(user_answer=NULL)
  → emit question_ready × N(按 order_index 升序)
  → emit done(ok=true)
```

任意阶段炸 → `error{code,detail}` + `done(ok=false)`,session 行标 abandoned_at。

**M3 扩展(本文档 M2 阶段不实施,占位说明)**:
- **岗位类 query**(US-5b):入参增 `mode='job'` + `jd_ids[]`;`retrieval_pipeline` 内多走两路并入(简历全文段落 / JD 子集职责+要求聚合),最终三源 chunks 合并喂 quiz_generator。详见 7-ROADMAP M3
- **空 query / 系统自选**(US-5c):入参 `query` 留空 + `mode='auto'`;routers 调 `services/knowledge_gap_service.pick_next_topic()` → SR 选 heading_path → 复用主题类 pipeline

## 5.3 答题草稿(同步 PUT)

```
FE (typing 防抖 1s)
  → PUT /api/quiz/sessions/{id}/answers/{order} {user_answer}
  → routers/quiz.save_draft
  → services/answer_service.save_draft(session_id, order, user_answer)
      └─ UPDATE session_answers SET user_answer=?, answer_submitted_at=now()
         WHERE session_id=? AND order_index=?
  → 200 OK
```

冲突场景:`status != 'in_progress'` → 409 session_not_in_progress。

## 5.4 评分 SSE

```
FE → POST /api/quiz/sessions/{id}/submit
  → routers/quiz.submit_session (EventSourceResponse)
  → 校验:所有 session_answers.user_answer 非空 → 否则 unanswered_questions
  → emit started{session_id, total_questions}

  → for each session_answer (按 order_index):
      ├─ load question + chunks(question.source_chunk_ids → DB)
      ├─ emit progress{phase=judging, order_index=i}
      ├─ agents/answer_judge.run(question, chunks, user_answer)
      │     ├─ render USER
      │     ├─ llm.client.complete(SYSTEM + USER, tools=[lookup_in_notes_global])
      │     │     ├─ LLM 想标 fabricated → 调 tool
      │     │     │     ├─ tool: services/search_service.global_hybrid_search(claim)
      │     │     │     ├─ tool 返回 Top-3 chunks 摘要 → 喂回 LLM
      │     │     │     └─ Langfuse trace 嵌套 span(2-TECH §6)
      │     │     └─ Multi-turn:LLM 看到 tool 结果继续输出 evidence(每 user_answer ≤5 次)
      │     ├─ Pydantic 校验 / retry ≤1
      │     ├─ map [N] → DB chunk_id
      │     └─ post-check:fabricated claim 是否对应至少一次 trace lookup;无 → 强制重跑 1 次
      ├─ services/answer_service.compute_scores(evidence, reference_points)
      │     → coverage / fidelity / depth / total(Python SSoT,5-AGENT §4.5)
      ├─ UPDATE session_answers SET evidence + scores
      └─ emit question_done{order_index, scores, evidence}

  → services/answer_service.finalize_session(session_id)
      ├─ 算 session 三层均值 + total
      ├─ UPDATE quiz_sessions SET status=submitted, scores, recall_md_path
      ├─ 写 notes/_recall/{session_id}.md(含题 / 答 / 评 / reference)
      └─ services/knowledge_gap_service.upsert_from_session(session_id)
          → for each (folder_path, heading_path) hit:
            UPSERT knowledge_gaps(attempt_count++,
              error_count += (total < 60 ? 1 : 0),
              last_score, last_attempt_at,
              SR 算法更新 prev_interval_days + next_review_at)
  → emit result{session_id, scores, recall_md_path}
  → emit done(ok=true)
```

## 5.5 Embed worker(后台异步)

```
main.py 启动钩子
  → asyncio.create_task(embed_worker.run_loop())

run_loop():
  while True:
    rows = SELECT id, content FROM note_chunks
           WHERE embedding IS NULL ORDER BY id LIMIT 10
    if not rows:
      await sleep(2s)
      continue
    embs = await agents/embedder.embed_batch([r.content for r in rows])
    UPDATE note_chunks SET embedding = ? WHERE id IN (...)
```

**为什么不上专门的队列**(redis / celery):MVP 单进程足够,LIMIT 10 + 轮询 2s 的延迟可接受。M4+ SaaS 化才考虑。

**hybrid search 跳过未算**:`search_service` 在 RRF 融合时 `WHERE embedding IS NOT NULL`,半成品 chunk 走 lexical 路径不掉队。

## 5.6 JD 单条上传(M2.5,立即解析)

```
FE 粘 JD 文本 / 上传截图 → POST /api/jds (json 或 multipart)
  → routers/jd.upload_jd
  → services/jd_service.upload_one()
      ├─ if source='image_upload':
      │     ├─ infra/upload.read_image_base64
      │     └─ llm.client.complete(qwen3.6-flash, image_url + prompt)
      │           → raw_text(OCR 结果)
      ├─ agents/jd_parser.run(raw_text)
      │     → JdParseOutput(title / responsibilities / hard_skills / ...)
      └─ INSERT jds(parsed_payload, parse_cost, ...)
  → 返回 jd_row(立即可见)
```

## 5.7 JD 一键分析 SSE(M2.5,hierarchical map-reduce)

```
FE 选范围 + 点"一键分析" → POST /api/jd-analyses {filter} (SSE)
  → routers/jd.create_analysis
  → 校验:filter 解析后 jd_count ≤ 200,否则 422
  → INSERT jd_analyses(status=in_progress, jd_ids=[...])
  → emit started{analysis_id, jd_count}

  → emit progress{phase=loading_parsed}
      └─ services/jd_service.batch_load_parsed_jds(jd_ids)

  → 抽 raw_skills(平均 30/条 → ≤6000 项)
  → 分 batch(每 batch 600 项)
  → for batch in batches: (并发)
      emit progress{phase=reducing_batch, batch=N/total}
      agents/jd_aggregator.batch_reduce(batch)  → partial canonical list

  → emit progress{phase=merging}
      agents/jd_aggregator.merge(all_partial)   → unified canonical list

  → emit progress{phase=frequency_recompute}
      agents/jd_aggregator.frequency.recompute(unified, parsed_jds)  # Python SSoT

  → emit progress{phase=learning_path_gen}
      agents/jd_aggregator.gen_learning_path(unified)  → markdown

  → UPDATE jd_analyses SET status=done, aggregated_requirements, learning_path_md, total_cost_cny
  → emit result{analysis_id, requirement_count}
  → emit done(ok=true)
```

任意阶段炸 → emit error + done(ok=false),jd_analyses.status=failed + failure_reason 落库。

## 5.8 简历诊断 SSE(M3,两方锚点严格)

```
FE 选 JD 报告 + 简历 → POST /api/resume-analyses {jd_analysis_id, resume_id} (SSE)
  → routers/resume.diagnose
  → INSERT resume_analyses(status=in_progress)
  → emit started{resource_id, jd_count, resume_chunk_count}

  → emit progress{phase=loading_inputs}
      ├─ load jd_analyses.aggregated_requirements
      └─ load resumes.parsed_chunks

  → emit progress{phase=diagnosing}
      └─ agents/resume_advisor.run(requirements, resume_chunks)
            ├─ thinking on,temperature 0.2
            └─ Pydantic 校验 + retry ≤1

  → emit progress{phase=anchor_validation}
      └─ for each suggestion:
            tag = anchored if (req_id and resume_position) else unanchored
            forbidden_pattern 检测(suggestion_topic)→ 越界 trace warning + 截断

  → UPDATE resume_analyses SET suggestions, anchored_count, ...
  → emit result{analysis_id, anchored_count, unanchored_count, coverage_summary}
  → emit done(ok=true)
```

# 6. 可观测性 / Tracing(Langfuse 自部署)

## 6.1 选型

**Langfuse 自部署**(docker compose 加一个服务,跟 api / web / postgres 并存),不上 LangSmith / OpenTelemetry。理由:

| 候选 | 选择理由 / 排除原因 |
|------|------------------|
| **Langfuse 自部署** ✅ | LLM-native(LLM call / cost / cache hit / token 现成可视化);自部署符合 PRD §6 NFR "本地优先 / 数据不出机器" |
| LangSmith | SaaS-only,数据出本地,**违反 PRD §6 NFR**,排除 |
| OpenTelemetry + Jaeger | 行业标准但 LLM-specific 视图(成本 / token / cache hit panel)要自己写,重复造轮子 |
| Phoenix(Arize)| 偏 ML observability,LLM agent trace 不如 Langfuse 直观 |

## 6.2 集成方式

```python
from langfuse.decorators import observe, langfuse_context

@observe(as_type='generation')                  # LLM call → generation span
async def llm_complete(prompt, model, ...):
    ...

@observe()                                      # service / agent → trace span
async def quiz_generator_run(chunks, count):
    ...

@observe()                                      # 顶层 root trace
async def submit_session(session_id):
    langfuse_context.update_current_trace(
        user_id=...,                            # MVP 单用户固定 'local'
        session_id=session_id,
        tags=['quiz', f'session:{session_id}']
    )
    ...
```

每条 LLM call 自动收集:input prompt / output / model / tokens_in / tokens_out / cost_cny / cache_hit / latency_ms。每个 agent run 收集:输入 chunk_ids / 输出 schema / retry 次数 / 失败原因。每个 SSE session 收集:总耗时 / 总成本 / 总 token。

## 6.3 Trace 在产品里能解决什么(具体场景)

| 场景 | 没 trace 时怎么排查 | 有 trace 后怎么排查 |
|------|------------------|------------------|
| Cohen's kappa 不达标(6-EVAL) | 看日志 stdout,grep prompt + output,人脑对照 | Langfuse UI 按 fixture id 过滤 trace,直接看每条 evidence 怎么生出来的 |
| Judge 调用 `lookup_in_notes_global` 异常 | 不知道工具调了几次 / 返回了啥 | trace 里嵌套 span 一目了然(Judge 调用 → tool span → tool 内部 hybrid search span) |
| LLM cost 超预期 | 翻 `llm_calls` 表自己 group by | Langfuse cost dashboard 按 prompt_version / model / agent 自动聚合 |
| 出题慢 P95 > 15s(PRD §6 NFR) | 加 print 测各阶段耗时 | trace 里 retrieving_chunks / generating / pydantic_validating 三段 latency 自动分割 |
| Session 失败追溯 | 翻日志找 request_id | 按 `session_id` tag 过滤,所有相关 span 一棵树 |

## 6.4 数据流改动(覆盖 §5)

§5 各条数据流不变,只是每个步骤外面套一层 `@observe`。**注意**:trace 跟业务异步发到 Langfuse 服务(走 SDK 内置队列 + 后台 flush),不阻塞主路径,Langfuse 服务挂了也不影响产品功能。

## 6.5 docker-compose 加服务

```yaml
services:
  langfuse:
    image: langfuse/langfuse:latest
    ports: ["3001:3000"]              # web UI
    environment:
      DATABASE_URL: postgresql://langfuse:...@langfuse-db:5432/langfuse
      NEXTAUTH_URL: http://localhost:3001
      NEXTAUTH_SECRET: ...            # 生成一次 .env
      SALT: ...
    depends_on: [langfuse-db]

  langfuse-db:
    image: postgres:16
    environment:
      POSTGRES_DB: langfuse
      POSTGRES_USER: langfuse
      POSTGRES_PASSWORD: ...
    volumes: ['langfuse_data:/var/lib/postgresql/data']

  api:                                 # 加环境变量
    environment:
      LANGFUSE_HOST: http://langfuse:3000
      LANGFUSE_PUBLIC_KEY: ...
      LANGFUSE_SECRET_KEY: ...
```

业务 postgres 跟 Langfuse postgres **分实例**(简化 schema 隔离 / 备份策略 / 单点失败不互相牵连)。

# 7. 错误处理分层(沿用 v1 LESSONS §8.6)

```
┌─────────────────┐
│ routers/ (出口) │  捕获 JobCopilotError → SSE error 事件 / HTTP code
│                 │  其他异常 → 500 + log + traceback
└────────┬────────┘
         │
┌────────▼────────┐
│ services/ (集中)│  抛 JobCopilotError(code='...', detail='...')
│                 │  by class 分发 — 业务校验 / 状态冲突 / 外部依赖失败
└────────┬────────┘
         │
┌────────▼────────┐
│ agents/(推进者) │  LLM call / parse 失败 → raise 不吞
│                 │  retry ≤1 后仍失败 → raise LLMCallFailed
└─────────────────┘
```

**禁止跨层吞异常**(LESSONS §2.2):agents 不 `try/except` 然后 `return None` 假装成功;services 不 `except: pass`。

错误码命名空间(沿用 v1 风格):

| code | HTTP / SSE | 抛在哪层 |
|------|------------|--------|
| `note_not_found` | 404 | services/notes_service |
| `duplicate_folder_title` | 409 | services/notes_service |
| `invalid_zip` | 400 | services/notes_service |
| `query_required` | 422 | routers/quiz(M2 主题类 query 为空且非 auto 模式)|
| `query_too_long` | 422 | routers/quiz(query 超长,例如 > 200 字符)|
| `no_chunks_for_query` | SSE error | services/retrieval_pipeline(0 命中,守门后报"笔记里没这主题",见 PRD Q-10 阈值) |
| `query_rewrite_failed` | trace warning(回退原 query,不抛错) | services/query_rewriter |
| `rerank_failed` | trace warning(回退 hybrid top-K,不抛错) | services/reranker |
| `insufficient_chunks` | SSE error | services/quiz_service(M2 后 deprecated,经 retrieval_pipeline 命中 < count 时不再单独报)|
| `llm_call_failed` | SSE error | agents/* |
| `session_not_in_progress` | 409 | services/answer_service |
| `unanswered_questions` | 409 | services/answer_service |
| `invalid_image_format` | 400 | services/jd_service / services/resume_service |
| `image_too_large` | 413 | services/jd_service / services/resume_service(> 7MB) |
| `ocr_failed` | SSE error | services/jd_service / services/resume_service |
| `jd_parse_failed` | SSE error | agents/jd_parser |
| `jd_count_exceeds_limit` | 422 | services/jd_service(filter 命中 > 200) |
| `jd_count_zero` | 422 | services/jd_service(filter 命中 0) |
| `aggregator_call_failed` | SSE error | agents/jd_aggregator |
| `resume_advisor_call_failed` | SSE error | agents/resume_advisor |
| `forbidden_pattern_persists` | trace warning(不抛错) | service 层后处理(LLM 越界写文案) |

# 8. v1 沿用清单(M0 不动)

| 模块 | 用途 | 改动 |
|------|------|------|
| `llm/`(全部)| LLMClient / cache / providers / pricing / tiers | 不动 |
| `agents/embedder/` | text-embedding-v4 调用 | 不动 |
| `services/tokenize.py` | char_ngrams Python 一致性 | 不动 |
| `services/chunk_service.py` | chunker | 改造 — 原按 profile 段落切,改 heading-aware markdown |
| `infra/db.py` | async sessionmaker | 不动 |
| `infra/embedder.py` | Embedder wrapper | 不动 |
| `infra/logging.py / request_id.py` | 日志 + request id | 不动 |
| `infra/upload.py` | 上传校验 | 改造 — 原 PDF / pdf2text,改 zip / unzip |
| `models/base.py` | Base + IDMixin + TimestampMixin | 不动 |
| `models/{prompt_version, llm_call, llm_response_cache}.py` | LLM cost / cache 表 | 不动 |
| `evals/judge.py` | JudgeClient 框架 | 不动(prompt 全换) |
| `evals/kappa.py` | Cohen's kappa | 不动 |
| `prompts/` | LoadedPrompt 加载器 | 不动 |
| `errors.py` | JobCopilotError | 不动 |
| `alembic/0014` | char_ngrams SQL 函数 | 不动(v2 note_chunks 复用) |
| `alembic/0015` | llm_response_cache 表 | 不动 |
| 前端 `lib/sse.ts` | SSE 客户端 | 不动(永久约束 #21)|
| 前端 `lib/api.ts` | 错误解析 | 不动 |

# 9. v1 砍除清单(M0 改造)

```
apps/api/src/jobcopilot_api/
├── agents/
│   ├── jd_parser/         ✗
│   ├── profile_parser/    ✗
│   ├── match_analyst/     ✗
│   ├── resume_planner/    ✗
│   ├── resume_drafter/    ✗
│   ├── resume_reviewer/   ✗
│   ├── chunker.py         ✗  (功能并入 services/chunk_service)
│   └── resume_graph.py    ✗
├── services/
│   ├── file_service.py    ✗
│   ├── jd_service.py      ✗
│   ├── match_service.py   ✗
│   ├── profile_service.py ✗
│   ├── resume_service.py  ✗
│   └── retrieval_service.py  ✗  (功能并入 search_service,RRF 不变)
├── routers/
│   ├── files.py / jds.py / profiles.py / matches.py / resumes.py  ✗
├── models/
│   ├── file.py / jd.py / profile.py / match.py / resume.py / resume_version.py / user.py  ✗

apps/web/src/app/
├── (jds) / (profiles) / (matches) / (resumes) / 关联组件  ✗

apps/api/alembic/versions/
├── 0001-0013                ✗  (通过 0016 一并 DROP TABLE,文件保留 git history)

evals/suites/
├── jd_parser / profile_parser / match_analysis / resume_*  ✗
└── (新建 hybrid_search / quiz_generator / answer_judge)
```

砍法:**新建 `0016_v2_schema.py` migration** 一次性 DROP v1 表 + 建 v2 表;Python 端 `git rm -r` 模块文件后跑 `pytest` 看哪些 import 断了一并清。

# 10. 已锁定的关键决策

| 项 | 决策 | 备注 |
|----|------|------|
| 仓库结构 | monorepo:apps/api + apps/web + alembic + evals + docs | 沿用 v1 |
| 后端语言 / 包管理 | Python 3.13 / uv workspace | LESSONS §7.2:装新依赖必须 `--all-packages` |
| 前端 | Next.js 14 App Router + Tailwind 自写 + Monaco | 不引组件库 |
| 模块分层 | routers / services / agents / llm / models / schemas | services 不直接调 LLM,走 agents/ |
| ORM | SQLAlchemy 2.x async,**不写 relationship** | ADR-0005 D1 |
| Embed worker | 后台 asyncio 单进程轮询;不上 redis / celery | MVP 量小;M4+ SaaS 再说 |
| LangGraph | M2.1 起用于 `InterviewCoachAgent` 状态机;M3 扩 SR / 三源岗位流 | M2 仍由 service 直接编排,先把 RAG/Judge 闭环打稳 |
| 错误分层 | routers 转协议 / services 集中分发 / agents 不吞 | 沿用 LESSONS §8.6 |
| 错误码命名 | snake_case + 出处明确 | 同 v1 JobCopilotError |
| SSE 实现 | sse-starlette + 前端 lib/sse.ts | 永久约束 #21 |
| LLM cache | 全 agent 经过 llm_response_cache | 评测路径不禁(EVAL_PLAN §2.4)|
| 部署 | docker compose 本地 | postgres / api / web / caddy / langfuse / langfuse-db 六服务 |
| Tracing 选型 | Langfuse 自部署 | LLM-native + 数据不出本地;详见 §6 |
| Tool use 范围 | 仅 AnswerJudge 用 `lookup_in_notes_global`;Quiz / Embedder / JdParser / JdAggregator / ResumeAdvisor 不用 | 直击 LESSONS §1.1 假阳性,精准不滥用 |
| 出题入口 | **聊天框 query**(M2 主题类 / M3 岗位类 + 空 query 自选);**笔记面板不再触发出题** | 笔记面板降级为查看 / 编辑 / 导航;PRD §6 锁定 |
| M2 retrieval pipeline | **query_rewrite → hybrid + RRF → reranker → parent-doc 扩展** 四件;每段独立可观测进 Langfuse | 见 §5.2 数据流;0 命中守门 < 3 chunks → 返"笔记里没这主题"不兜底 |
| Reranker 选型 | 百炼 **`qwen3-rerank`**(`/compatible-api/v1/reranks`,¥0.0005/k token);本地 bge-reranker-v2-m3 作 fallback 不实施 | 详见 5-AGENT §2.7.5 + memory `reference_aliyun_dashscope_rerank.md`;langfuse 不自动 instrument 要手动 generation 包 |
| Parent-doc 扩展粒度 | **自适应**:命中段 < 200 字 → 扩到同 H2 父段;≥ 200 字 → 不扩 | 阈值在 M2 实施时按 dogfood 命中分布调 |
| 简历存储模型 | 单条记录(全库一行 resumes);无"简历库 / 多份切换" | 一个人就一份简历;岗位类 query 拼"这一份 + 选定 JD 子集"已够 |
| M3 岗位类三源融合 | retrieval_pipeline 多走两路:简历单条全文 + 用户选定 JD 子集职责/要求,合并喂 quiz_generator | 重点考"简历写了 JD 也要"交集 + "JD 强要 简历没写"缺口 |
| LLM SDK | OpenAI Python SDK(via 百炼兼容接口)| Langfuse OpenAI wrapper(chat 自动 instrument,embedding 手动);langfuse SDK 锁 <3.0(server v2 不支持 OTLP);env mirror 必须早于 routers import — 见 STATUS 永久约束 |
| qwen3.6-flash 多模态 | 文本 / 图像 / tool use 一把抓,JD 截图 + 简历 PDF 共用 | 简化模型路由 |
| thinking 按 agent | 默认 off;评分 / 综合判断类显式 on(详见 5-AGENT §2.1) | 节省 reasoning_tokens 成本 |
| JD 累积型 | jds 表跨时间累积,parsed_payload 上传即落库 | 类比笔记;不做 batch 概念 |
| JD 一键分析 | hierarchical map-reduce,单次上限 200 条;频次 Python 重算 | 避免单次 LLM context 爆 |
| 简历段落不进 hybrid search | resumes.parsed_chunks JSONB,直接全文喂 LLM | 简历短,无需 retrieval |
| forbidden_pattern 拦截 | ResumeAdvisor service 层正则检测"建议改写为 X"等违规句式 | 避免 LLM 替写文案;0 触发是 M3 DoD |

---

# 不在本文档范围

- 表 schema 字段语义 → `docs/3-DATA_MODEL.md`
- API 端点 / SSE 事件 schema → `docs/4-API_SPEC.md`
- Prompt 全文 / 算分公式 → `docs/5-AGENT_DESIGN.md`
- 评测套件 / kappa 阈值 / dataset 标注 → `docs/6-EVAL_PLAN.md`
- 里程碑 DoD → `docs/7-ROADMAP.md`
- 仓库规范 / CI / lint / typecheck 配置 → `docs/8-ENGINEERING.md`
- 工程踩坑沉淀 → `docs/9-LESSONS.md`
