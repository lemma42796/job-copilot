---
title: JobCopilot 项目当前进度(单一可信源)
owner: lemma42796
last_updated: 2026-05-02 (M1 进行中:S0.5 / S1 / S2 / S3 已完成已 push;S4 全部完成本地未 push,领先 origin 5 commit;下一刀 S5 前端 JD 粘贴页)
purpose: 跨会话续作的状态快照。任何新会话从这里开始读。
---

# 当前阶段

**M1 数据入口贯通 — 进行中**

| 切片 | 内容 | 状态 |
|------|------|------|
| S0.5 | M0 卫生债清理(coverage 闸门 / mypy 加测试 / 前端切自动类型 / structlog+request_id+RFC 7807) | ✅ |
| S1   | DB + Alembic + 通用列/触发器/枚举(5 条 migration,9 张表) | ✅ |
| S2   | LLM Client + DummyProvider + Tier 路由 + `llm_calls` 表 | ✅ |
| S3   | User/File ORM + `/v1/files` 上传(sha256 去重 + 软删 + 200MB 配额),见 ADR-0005 | ✅ |
| S4   | JDParserAgent(文本 + PDF)+ `/v1/jds/parse` SSE + `/v1/jds` 读改删 + prompt_versions 闭环 | ✅(本地未 push,等 S5 一起或单独 push) |
| S5   | 前端:JD 粘贴页 + 结构化结果可视化 + 编辑保存 | pending |
| S6   | `evals/suites/jd_extract` 50 条 + promptfoo CI(**Week 2 末 DoD**) | pending |
| S7   | ProfileParserAgent + `/v1/profiles/parse` SSE | pending |
| S8   | Chunking 纯函数 + Embedding(text-embedding-v3)+ `/rechunk` | pending |
| S9   | 前端:简历上传 + 表单 + chunks 可视化(调试) | pending |
| S10  | `evals/suites/profile_extract` 30 条 + chunk 召回断言 | pending |
| S11  | 1 名志愿者 dogfood + bad case 修复(**Week 3 末 DoD**) | pending |

## 当前 working tree 状态

**本地 main 领先 origin/main 5 commit(S4 docs + A/B/C/D 待 push)**。检查领先量:`git log origin/main..main --oneline | wc -l`。

## 当前闸门(S4-D 完成,本地)

- `ruff check` / `ruff format --check`:全绿
- `mypy --strict apps/api/src apps/api/tests`:74 files,0 issues
- `pytest --cov --cov-fail-under=70`:**200 passed,93.61%**

## 当前 docker compose 状态

S1 期间手动起了 postgres 单容器开发(`docker compose up -d postgres`),停机时**未 down**。下次开工前可以选择继续用它,或 `docker compose down -v` 后重启重置数据。**Alembic 已经把 0001-0007 应用到该容器,不重置可以省去一次迁移**。集成测试用 testcontainers 起独立容器,与开发容器无关,无需停。

> 2026-05-01 决策变更:LLM Provider 由 DeepSeek V4 切换为阿里云百炼 Qwen3.6,理由是消耗剩余 ¥15 赠款。详见 ADR-0003。**ADR-0001 复审条件 1(余额 < ¥1)触发时自动回切。**

---

# M1 规划要点(本会话讨论后锁定)

## 设计原则

1. **纵切优先**:每切片端到端可用,不做"先建一层 ORM 再建一层 service"的横切
2. **Schema 是单源真相**:DB → ORM → Pydantic → OpenAPI → `packages/schemas/api.ts` → 前端,衍生物全自动生成
3. **LLM 必须可替身**:`LLMClient` 抽象 + `DummyProvider`(fixture 回放)是 M1 硬性产物
4. **Agent 是纯函数**:输入 → LLM → 结构化输出,**不写库**;副作用在 `services/`
5. **Migration 一条逻辑一条 revision**,每条必须可 downgrade
6. **SSE 走进程内 AsyncIterator**,M2 切 pgmq 时只换消费者

## 5 个开放问题与默认值(已接受)

| Q | 默认 | 理由 |
|---|------|------|
| Q1 PDF 文本抽取 | `pypdfium2` | 零依赖;MinerU 装包重(>1GB),M3 简历定制再上 |
| Q2 Idempotency-Key | M1 跳过 | DATA_MODEL §3.20 待补;M1 单用户场景误重放概率低 |
| Q3 `llm_calls` 表 | M1 上(S2) | 否则"日均 < ¥1"无表无法验证 |
| Q4 图片 JD 入口 | M1 末再做 | 文本评测先拿基线,vl-flash 风险已在 ROADMAP §4.4 |
| Q5 BYOK 头(`X-DashScope-Key`) | 解析头但只读 .env | 实现成本低,入口预留给 M5 |

## M1 DoD(在 ROADMAP §4.3 上加 3 条工程门槛)

ROADMAP 已有:1 志愿者全流程通 / ≥5 bad case high severity 修 / 日均 < ¥1 / 80 条评测达阈。

新增:
- ✅ `alembic upgrade → downgrade → upgrade` 在 CI 跑过(S1 已实现,通过 testcontainers)
- ✅ `apps/api` 单测 + 集成覆盖率 ≥ 70%(`agents/**` ≥ 80%)(S0.5 已上闸门)
- ✅ `packages/schemas` 类型从 OpenAPI 自动生成,前端无手写 API 类型(S0.5 已切)

---

# S0.5 卫生债产出(2026-05-01)

为什么需要:M0 完成度高但有几处 CI 不严、前端类型漂移风险、错误处理基础架子缺失。在 S2 之前一并补上。

| 项 | 落地 |
|---|---|
| CI coverage 闸门 | `pytest-cov` 入 dev deps;root `pyproject.toml` 加 `[tool.coverage.{run,report}]`(branch=true、omit alembic、`fail_under=70`、`exclude_also` 排除 `if TYPE_CHECKING:` 等);`test-api.yml` 改为 `pytest --cov --cov-fail-under=70` |
| CI 加严 | `lint.yml` mypy 行加 `apps/api/tests`;所有 workflow 的 `uv sync` 加 `--frozen` |
| 前端切自动类型 | `apps/web/src/lib/api.ts` 删手写 `HealthResponse`,改为 `components['schemas']['HealthResponse']` from `@jobcopilot/schemas` |
| structlog + request_id + RFC 7807 | 见下 |

## structlog / request_id / errors 关键文件

```
apps/api/src/jobcopilot_api/
├── infra/
│   ├── logging.py          # JSON renderer + 敏感键黑名单 + idempotent guard
│   └── request_id.py       # X-Request-Id 中间件 + 手写 UUIDv7(无外部依赖)+ contextvar
├── errors.py               # JobCopilotError + NotFoundError / ConflictError / ValidationError
│                           # ProblemResponse(media_type=application/problem+json)
│                           # 三个 ExceptionHandler:JobCopilotError / RequestValidationError / StarletteHTTPException
└── main.py                 # CORS(内)→ RequestID(外)中间件顺序;接 setup_logging() / install_exception_handlers()
```

测试覆盖:`tests/unit/test_logging.py`(redactor + idempotent)、`test_request_id.py`(UUIDv7 形状 + 缺省生成 + 入站回显)、`test_errors.py`(JobCopilotError 转 7807 + 校验错误带 errors[] + 未匹配路由 7807)。

---

# S1 数据 schema 产出(2026-05-01)

按 DATA_MODEL §3.1-3.8 + §3.15 落 9 张表,5 条 migration:

```
apps/api/alembic/versions/
├── 0001_extensions_and_helpers.py  # vector + pg_trgm + set_updated_at() 触发器函数
├── 0002_users_and_files.py         # users + files(lz4 压缩 + sha256 索引 + 100MB CHECK)
├── 0003_jds.py                     # jd_source / jd_status ENUM + jds(GENERATED tsvector + GIN + salary CHECK)
├── 0004_profiles.py                # skill_level ENUM + profiles + 4 张子表(experiences/projects/skills/educations)
└── 0005_profile_chunks.py          # chunk_granularity ENUM + profile_chunks(vector(1024) + HNSW m=16,ef_construction=64 + GIN tsv)
```

ORM/基础设施:
```
apps/api/src/jobcopilot_api/
├── models/
│   ├── base.py                     # DeclarativeBase + IDMixin + TimestampMixin
│   └── __init__.py                 # 导出 Base
└── infra/
    └── db.py                       # 懒加载 async engine + sessionmaker + get_session FastAPI 依赖
```

集成测试:`tests/integration/test_migrations.py` 用 testcontainers 拉 `pgvector/pgvector:pg16`,跑 `upgrade head → downgrade base → upgrade head`,断言扩展 / 9 张表 / HNSW 索引 / 7 个触发器存在。

## S1 设计决策(实现细节)

- **alembic.ini 的 URL 是占位符**,真正的 URL 由 `env.py` 按优先级解析:`-x dburl=...` > 配置中非占位 URL > `settings.database_url`
- **ENUM 显式 `.create()` / `.drop()`**(`create_type=False`),避免与 `op.create_table()` 隐式交互
- **`tsvector` / `Vector(1024)` 用 `sa.Computed(persisted=True)`** 表达 `GENERATED ALWAYS AS ... STORED`
- **`set_updated_at()` 是单一 PL/pgSQL 函数**,7 张需要 `updated_at` 维护的表共用,触发器名 `tg_<table>_set_updated_at`
- **HNSW 参数**:`vector_cosine_ops`,`m=16`,`ef_construction=64`(沿用 DATA_MODEL §3.8,M1 不调)
- **`metadata` 列**:目前在 migration 里直接叫 `metadata` 没问题;**后续做 ORM 模型时要用 `meta_data: Mapped[dict] = mapped_column("metadata", JSONB, ...)`** 避免与 `Base.metadata` 撞名

---

# S2 LLM 抽象层产出(2026-05-01)

按 ADR-0004 落地,3 条原子 commit。

```
apps/api/alembic/versions/
└── 0006_llm_calls_and_prompt_versions.py  # llm_calls + prompt_versions(FK 完整)

apps/api/src/jobcopilot_api/
├── llm/
│   ├── tiers.py            # Tier(StrEnum:CHEAP/STANDARD/PREMIUM) + tier_to_model
│   ├── errors.py           # LLMError 族,继承 JobCopilotError → RFC 7807
│   ├── pricing.py          # price table + cost_for(本地自算)
│   ├── cache.py            # cache_system 语义占位 + 前缀稳定文档
│   ├── client.py           # Provider Protocol / LLMResult / LLMClient Protocol /
│   │                       # BaseLLMClient(tenacity 重试 + JSON 修复 + 日志 1 行)/
│   │                       # NoopCallLogger / MemoryCallLogger
│   ├── db_logger.py        # DBCallLogger(独立 AsyncSession,失败只 warn)
│   └── providers/
│       ├── dashscope.py    # openai AsyncOpenAI 包装,error 映射
│       └── dummy.py        # 显式 scenario 队列 + from_fixture
├── infra/
│   └── llm.py              # get_llm_client() 懒单例,默认接 DBCallLogger
└── models/
    ├── llm_call.py         # ORM(无 ORM 层 FK,migration 是权威)
    └── prompt_version.py
```

## S2 设计决策(实现细节)

- **ORM FK 原则**:ORM 只声明需要 navigate(`relationship()`)的关系,纯约束放 migration。LlmCall.user_id / prompt_version_id 都是 DB-only FK
- **每次 `complete()` 最多 1 行日志**:tenacity 多少次重试 / schema 修复多少次,都聚成一行。失败路径在 try 走 `logger.log()` 后 raise,成功路径直接 return 前 log
- **失败 cost = 0 / tokens = 0**:timeout / 5xx 拿不到 `response.usage`,LLMResult 用零占位写 llm_calls
- **DBCallLogger 用独立 AsyncSession**:从注入的 sessionmaker 拿一个新 session,与业务事务无关,业务回滚不影响日志(集成测试 `test_business_rollback_does_not_drop_cost_log` 是这条的 load-bearing 断言)
- **DashScope JSON schema 走 `json_object`**:OpenAI compat 不支持 `json_schema` 字段,降级用 `json_object` + Pydantic 二次校验 + 1 次重试(prompt 追加 schema)
- **retry 参数可注入**:BaseLLMClient `retry_wait` 默认是 ADR-0004 D3,测试传 `wait_none()` 让重试不睡

## 当前闸门(本地,M0 → S3-C 累计)

- `ruff check`:All checks passed
- `ruff format --check`:58 files already formatted
- `mypy --strict apps/api/src apps/api/tests`:57 files, 0 issues
- `pnpm lint`(biome):16 files, no fixes
- `pnpm typecheck`(tsc):0 errors
- `pytest --cov --cov-fail-under=70`:**113 passed,97.65%**(79 unit + 34 integration)

---

# 已经锁定的关键决策(不要再讨论)

| 项 | 决策 |
|----|------|
| 目标用户 | 1-3 年跳槽开发者(单一画像,应届生 v2 再说) |
| 北极星 NSM | 用户使用前后投递的面试邀约率提升 |
| 短期 Proxy | 端到端完成率(粘 JD → 下载定制简历) |
| MVP 边界(P0) | JD 入库 + 个人档案 + 匹配分析 + 简历定制 + 本地部署 |
| 面试模拟 | P1(Phase 5,Week 11-13) |
| 部署形态 | 本地优先,`docker compose up` 一键启动 |
| 仓库结构 | 单仓 monorepo:`apps/api` + `apps/web` + `packages/schemas` |
| LLM Provider | **仅阿里云百炼 Qwen3.6**(Flash + Plus,见 ADR-0003;ADR-0001 已 Superseded,作为额度耗尽后的回切方案) |
| 数据存储 | **Postgres 16 一把梭**(pgvector + tsvector + pgmq + bytea,见 ADR-0002) |
| Agent 编排 | LangGraph 仅用于简历定制 + 面试模拟两条状态机,其他场景单 Agent |
| 文档 owner | lemma42796(GitHub 用户名) |
| 不估工时 | 用户已明示:规划/方案不写小时数、天数、Week 汇总等估算,只讲依赖顺序与最佳实践 |
| 不加 Co-Author | git commit / PR body 一律省略 Co-Authored-By: Claude 与"Generated with Claude Code"注脚 |

---

# M0 期间踩到的两个坑(已记录,留作经验)

1. 选错 postgres 镜像 `ghcr.io/tembo-io/tembo-pg-cnpg`(私有镜像,registry 拒绝匿名拉取)。改用公开 `pgvector/pgvector:pg16`。**pgmq 不在该镜像中,推迟到 M2 用自定义 Dockerfile 装(届时基于 pgvector 镜像 + tembo-io/pgmq 的 .deb 安装)。** docker/postgres/init.sql 与 docker-compose.yml 已注释。
2. Next.js 服务端组件在容器内 SSR 时通过 `localhost:8000` 调 API 失败 —— 容器内 `localhost` 指向 web 自己。修复:在 `apps/web/src/lib/api.ts` 区分 `INTERNAL_API_BASE_URL=http://api:8000`(SSR)与 `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`(浏览器)。

# S1 期间踩到的小坑(已记录)

1. `alembic.ini` 的 `script_location = alembic` 是相对路径;集成测试在仓库根目录跑时找不到。修复:测试里 `cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))`。
2. alembic 1.18 提示 `path_separator` 需显式;已在 `alembic.ini` 加 `path_separator = os`。
3. ruff `N818` 要求异常类名 `*Error` 后缀:`ValidationFailed` → `ValidationError`。
4. structlog processor 的入参类型是 `MutableMapping[str, Any]`,不是 `dict`;mypy strict 会报。修复:`_redact` 签名换成 `MutableMapping`。

# S2 期间踩到的小坑(已记录)

1. **LlmCall.user_id 的 ORM `ForeignKey("users.id")` mapper 失败**:User ORM 还没建,SQLAlchemy mapper config 阶段 resolve 不到 `users` 表。修复:改用"ORM 只表达需要 navigate 的关系,纯约束放 DB"原则,删 LlmCall 的 ORM 层 FK 声明,留 migration 0006 的 DB 层 FK。后续 S3 建 User ORM 时不需要回头补。
2. **集成测试用 module-scope async engine 跨 event loop 失败**:pytest-asyncio 默认每个测试一个新 loop,asyncpg 连接不能跨 loop 复用。修复:engine fixture 改成 function-scope(每测试新建 + dispose);container 仍 module-scope 避免反复重启 Postgres。
3. **structlog `capture_logs()` 在套件中失效**:前置测试调过 `setup_logging()` 后,structlog 全局配置被锁定,`capture_logs()` 看不到事件。修复:db_logger 单测用 monkeypatch 直接替换模块级 `log` 对象。
4. **DashScope OpenAI compat 不支持 `json_schema`**:M1 走 `response_format={"type":"json_object"}` + 在 prompt 里注入 schema + Pydantic 二次校验 + 1 次重试。已在 ADR-0004 D2 + client.py docstring 注明。

# S3 期间踩到的小坑(已记录)

1. **`pytest.raises` 不能与 `async with` 同行组合**:`async with sessionmaker_() as session, pytest.raises(NotFoundError):` mypy 报 `RaisesExc` 没有 `__aenter__`。修复:拆成 `async with sessionmaker_() as session:` + 内嵌 `with pytest.raises(...):`。
2. **`Result.rowcount` 在 mypy strict 下不可见**:`session.execute(sa.update(...))` 返回 `Result[Any]`,`rowcount` 属性是 `CursorResult` 的。修复:改用 `.returning(File.id)` + `scalar_one_or_none()` 检测命中,无需 cast。
3. **`session.begin_nested()` SAVEPOINT 接 IntegrityError**:dedup INSERT 失败要在外层事务里 SELECT 已有行,直接 `try/except IntegrityError` 会让外层 txn 进入 aborted 状态。修复:`async with session.begin_nested(): session.add(...); flush()` — IntegrityError 时 SAVEPOINT 自动 rollback,外层 txn 仍可用。
4. **CHECK 约束撞配额测试**:`ck_files_size <= 100MB` 与 200MB 配额测试不能用单行;改成两行各 `(USER_QUOTA-100)//2` 各算各的避开 CHECK。
5. **配额测试的 PDF 太短**:`b"%PDF-1.7\n%..."` 只 35 bytes,小于 quota headroom(100 bytes)所以不触发。修复:PDF 常量加长到 ~1KB。
6. **FastAPI Form/UploadFile 需要 `python-multipart`**:M0/M1 没装,S3-C router 跑测试时 `RuntimeError: Form data requires "python-multipart"`。修复:加进 `apps/api/pyproject.toml` 的 dependencies。
7. **uv workspace 的 dev extras**:`uv sync` 默认不带 optional-dependencies。要 `uv sync --package jobcopilot-api --all-extras` 才能装回 pytest/mypy/ruff。否则 `.venv/bin/pytest` 缺失。
8. **ruff `SIM300 Yoda condition` 误判**:`ALLOWED_MIME == frozenset({...})` 被认为是 Yoda 条件(把 frozenset 字面量当作 literal),自动修成 `frozenset({...}) == ALLOWED_MIME`。无害,接受 autofix 即可。

---

# 文档清单与状态

```
docs/
├── STATUS.md                    ← 你正在读
├── 1-PRD.md                     ✅ 完成(~350 行)
├── 2-TECH_DESIGN.md             ✅ 完成(~560 行)
├── 3-DATA_MODEL.md              ✅ 完成(964 行)
├── 4-API_SPEC.md                ✅ 完成(959 行)
├── 5-AGENT_DESIGN.md            ✅ 完成(962 行)
├── 6-EVAL_PLAN.md               ✅ 完成(580 行)
├── 7-ROADMAP.md                 ✅ 完成(475 行)
├── 8-ENGINEERING.md             ✅ 完成(599 行)
README.md (项目根)               ✅ 完成(214 行)
├── adr/
│   ├── 0001-only-deepseek.md    ✅ 完成(已 Superseded by 0003)
│   ├── 0002-postgres-as-vector-db.md  ✅ 完成
│   ├── 0003-switch-to-qwen.md   ✅ 完成
│   ├── 0004-llm-client-contract.md  ✅ 完成(S2 规划锁)
│   ├── 0005-files-upload-contract.md  ✅ 完成(S3 规划锁)
│   └── 0006-jd-parse-contract.md   ✅ 完成(S4 规划锁)
└── runbook/                     (空,部署期再写)
```

---

# 续作指引(给下一会话的 Claude)

## 当用户问"开发进度到了?"时

1. 读本文件(STATUS.md)
2. 简短汇报:M0 / S0.5 / S1 / S2 / S3 已完成已 push;S4 全部完成本地未 push(领先 origin 5 commit);下一刀 S5 前端 JD 粘贴页。检查领先量用 `git log origin/main..main --oneline | wc -l`
3. **等待用户指示后再行动**,不要自动开工

## S4 已完成产出(全部本地未 push,见 ADR-0006)

```
apps/api/src/jobcopilot_api/
├── models/jd.py               # JD ORM(无 ORM FK,延续 ADR-0005 D1)
├── schemas/jds.py             # JdSource / JdStatus / JDStructured / JDSkill /
│                              # JDParseInput(text/file_id 二选一)/ JDParseResponse /
│                              # JDListItem / JDListResponse / JDDetail / JDPatchInput
├── prompts/jd_parser/v1.0.0.j2  # SYSTEM/USER 双段 Jinja2 模板
├── agents/jd_parser/agent.py    # parse_jd 纯函数,prompt_version_id 透到 LLMResult
├── infra/
│   ├── prompts.py             # 扫描 + sha256 hash + upsert + lifespan 缓存 +
│   │                          # PromptVersionMismatchError 启动报错
│   └── pdf.py                 # pypdfium2 抽取,PdfExtractionError(422)
├── services/jd_service.py     # create_pending_jd + run_parse + create_and_parse 包装 /
│                              # list / get / patch / soft_delete / 失败 4 分支
└── routers/jds.py             # POST /parse(sync 201 + ?stream=1 SSE)/
                               # GET 列表(cursor)+ 详情 / PATCH / DELETE 204
```

依赖新增:`pypdfium2>=4.30.0`(S4-A)、`jinja2>=3.1.4`(S4-B)。`main.py` lifespan
接线 `app.state.prompt_versions = await load_prompt_versions(get_sessionmaker())`。
**S4 不需要新 migration**(0003 / 0006 已建 jds 表 + ENUM + prompt_versions / llm_calls)。

## S4 永久约束(只记会影响后续切片决策的)

1. **prompt_versions 表只有 `template` 单列**(无 system/user 拆分),`.j2` 整
   文件存进 `template`,SYSTEM/USER 标记由 `infra/prompts.py` 启动时解析;
   hash = sha256(整文件)。后续新增 Agent 沿用此约定,不要拆列。
2. **`ASGITransport` 不跑 lifespan**——router 集成测试需要在 fixture 里手动
   `app.state.prompt_versions = {(...): LoadedPrompt(...)}`;`get_llm_client` /
   `get_sessionmaker` 走 `dependency_overrides`。S7 ProfileParser 的 router
   测试同样要遵循这个模式。
3. **JD ORM 只存 `parse_tokens` 总数**,无 input/output 拆分;同步 POST /parse
   响应的 `tokens.input/output` 来自 `LLMResult`(由 `create_and_parse` 一并
   返回 `(Jd, LLMResult)`)。GET 详情只能给总数。
4. **SSE 起手要 jd_id**——Phase 1 与 Phase 2 在 service 层拆成
   `create_pending_jd` + `run_parse`,`started` 事件带 `resource_id`,
   失败发 `error → done`,pre-insert 失败(text 过短 / PDF 烂)直接发
   `error → done`(无 `started`)。

## 重要风格约定

- 文档元数据头格式见已完成文档,严格遵循
- 中文为主,代码示例与 schema 标识符为英文
- 每份文档末尾写"不在本文档范围"指向相关文档
- 切片规划写在 STATUS.md 的"下一刀"区(不再每切片一份 ADR);只有真正跨切片
  的架构决策才另立 ADR(下一个编号 0007)
- 不要重新讨论已锁定的决策
- **不估工时**

---

# 上次会话遗留的开放问题(PRD §9)

在对应里程碑启动前再决策,不阻塞当前切片:

- Q-01:简历 PDF 模板用现成开源还是自研?(默认:LaTeX `awesome-cv` 中文化)— M3 启动前决策
- Q-02:投递追踪看板要不要做日历提醒?(默认:不做)— M4 启动前决策
- Q-03:MCP Server 暴露的工具粒度?(默认:5 tool + 1 resource)— M5 启动前决策
- Q-04:Web demo 站要不要支持 BYOK 在线试用?(默认:做)— M6 启动前决策
