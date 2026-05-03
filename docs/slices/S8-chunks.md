---
title: S8 Chunking + Embedding + /rechunk + /chunks — 切片归档
status: ✅ 完成已 push
date: 2026-05-03
purpose: 简历 5 表 → ChunkInput → 1024 维向量 → profile_chunks;parse SSE 末段自动接 chunk
---

# 产出

```
apps/api/src/jobcopilot_api/
├── agents/
│   └── chunker.py                     # C1: 5 表 ORM → ChunkInput,纯函数,CHUNKER_VERSION="v1"
├── llm/
│   └── embedders.py                   # C2: Embedder Protocol + DummyEmbedder + DashscopeEmbedder
├── infra/
│   └── embedder.py                    # C5: get_embedder() 懒单例(对齐 infra/llm.py)
├── services/
│   └── chunk_service.py               # C4: rebuild_for_profile(embed 在事务外 / DELETE+bulk INSERT 单事务)
└── routers/
    └── profiles.py                    # C5: POST /rechunk(SSE)+ GET /chunks + 解析 SSE 末段 emit chunking_embedding

(C3 ProfileChunk ORM 已在 S7 / migration 0009 建表,本切片不动 schema)

apps/api/tests/
├── unit/
│   ├── test_chunker.py                # C1
│   └── test_embedders.py              # C2
└── integration/
    ├── test_profile_chunks_orm.py     # C3 round-trip + CASCADE + ENUM
    ├── test_chunk_service.py          # C4 happy / 重跑去重 / 空 profile / 跨用户 / embed 失败 / batch 边界
    └── test_profiles_router.py        # C5 新增 7 个:parse 末段 chunking / chunk 失败不阻塞 / rechunk happy / 404 / embed_failed / GET chunks / patch+rechunk

packages/schemas/src/api.ts            # C6: 同步 OpenAPI 生成产物
```

# 设计决策(实现细节)

- **chunker 是纯函数,不写库**:`build_chunks(profile, experiences, projects, skills)` 拿 ORM 实例返回 `list[ChunkInput]`。规则照 5-AGENT_DESIGN §4.7 + 数据脱壳成 `granularity / source_table / source_id / content / metadata`。`metadata.chunker_version="v1"` 是版本标,规则升级时 bump v2。
- **Embedder Protocol + 双实现**:Dummy(sha256 链 → 1024 维 → L2 归一)给测试 / 离线;Dashscope 复用 `DashscopeProvider` 的 OpenAI 兼容 client + base_url + 错误映射 + tenacity 重试(只 retry `LLMTimeoutError` / `LLMUpstreamError`)。
- **rebuild 4 阶段**:① 读(profile + 3 子表,educations 不参与)→ ② 纯函数 build chunks → ③ embed(事务外,batch ≤ 10,对齐百炼 v4 上限)→ ④ 写(单事务 DELETE + bulk INSERT)。embed 失败时 DELETE 还没执行,旧 chunks 完整保留。
- **embed 在事务外**:rebuild 触发链 = parse 末段(几百 ms 的网络 IO),如果在 session.begin() 里调 embedder,会把 PG pool slot 一直占着。拆出后 IO 阶段不持 connection,写阶段一气呵成。
- **embed_version 与 chunker_version 暂同步**:`EMBED_VERSION = CHUNKER_VERSION`(都是 "v1")。chunker 改规则时 bump,embedder 换模型/维度时单独 bump,届时拆开。
- **parse SSE 末段调 chunk 是 best-effort**:LLM 解析成功 → profile 已 `parsed` 落库 → 接着调 `rebuild_for_profile`,失败只 emit `chunking_embedding{ok:false}` + 继续 `result/done(ok=true)`。理由:① parse 已经吃了一次 LLM 钱,不该因 embed 失败让用户重来 ② 用户随时可以 `POST /rechunk` 补。POST /rechunk 是显式独立操作,失败走 `error/done(ok=false)`,语义不同。
- **/chunks 不返 embedding**:1024 维 float[] × 5-20 行 = 几十 KB / 请求,且前端调试用不上。schema `ProfileChunkItem` 刻意 omit;真要用向量直接走 SQL。
- **rechunk SSE 先 ownership check 再 emit started**:404 / 跨用户走 `error → done`,没有误导性的 `started`(对齐 S4 永久约束 4 的语义)。

# 期间踩到的小坑

1. **embedder DI 复用 OpenAI client + base_url 即可**:一开始想另开 httpx session,后来发现 `AsyncOpenAI(api_key=, base_url=DASHSCOPE_BASE_URL)` 直接调 `.embeddings.create(...)` 就走百炼兼容端点,与 chat 同一个 client 风格。pricing 表独立(0.0005 元/千 tokens),共享 `LLM*Error` 体系。
2. **router-level chunk 编排 vs service-level**:S8 规划写的是"`profile_service.run_parse` 末段接 chunk_service",但实际接进 service 会破坏 `run_parse` 返回类型 `(Profile, LLMResult)`(影响 `create_and_parse` 6 个测试 + JD 路径对称),且 service 层失败要不要回滚 parse 也要做新决策。改成 router 层在 `run_parse` 后串调 `rebuild_for_profile`,SSE 中段 emit `chunking_embedding`,失败 best-effort,路径与 STATUS DoD 一致(`/profiles/parse SSE 末段含 chunking_embedding`)。`create_and_parse` 暂不接 chunk(用不到,只在测试里跑)。
3. **`x-` 私有事件名容易污染 OpenAPI 但 SSE event 不进 OpenAPI**:rechunk SSE 用 `chunking_embedding` 而不是 `chunk` / `done`,与 parse 末段事件名复用,前端解析逻辑同一份。
4. **`_BoomEmbedder` test stub 触发 ruff RUF100**:`def embed(self, texts): # noqa: ARG002` 错,ARG002 在项目 ruff 配里没启用;改用 `del texts` 显式消费参数。
5. **profile 测试 fixture 复用**:`make_app` 多加一行 `app.dependency_overrides[_embedder_dep] = DummyEmbedder` 即可让所有现有 router 测试白嫖默认 embedder;需要塞失败 embedder 的测试在 `make_app(...)` 之后再覆写。
