---
title: JobCopilot 工程规范
owner: lemma42796
status: Draft
version: 0.1.0
created: 2026-05-01
last_updated: 2026-05-01
related:
  - 2-TECH_DESIGN.md
  - 4-API_SPEC.md
  - 6-EVAL_PLAN.md
  - 7-ROADMAP.md
---

## 修订记录

| 版本 | 日期 | 作者 | 变更说明 |
|------|------|------|---------|
| 0.1.0 | 2026-05-01 | lemma42796 | 初版 |

---

## 1. 仓库结构

### 1.1 总览

```
jobcopilot/
├── apps/
│   ├── api/                    # FastAPI 后端
│   └── web/                    # Next.js 前端
├── packages/
│   └── schemas/                # 共享类型(从 OpenAPI 自动生成)
├── evals/                      # 评测套件
├── docker/                     # Dockerfile / compose / caddy
├── docs/                       # 设计文档(本目录)
├── extension/                  # Chrome 扩展(M5 起)
├── mcp/                        # MCP server(M5 起)
├── scripts/                    # 一次性脚本(种子 / 迁移 / 数据导入)
├── .github/
│   ├── workflows/              # CI
│   ├── ISSUE_TEMPLATE/
│   └── pull_request_template.md
├── .env.example
├── .gitignore
├── docker-compose.yml          # 默认本地部署
├── docker-compose.dev.yml      # 开发覆盖(挂载源码、热重载)
├── docker-compose.eval.yml     # CI 评测专用(独立 DB)
├── pnpm-workspace.yaml
├── package.json                # workspace 根
├── pyproject.toml              # uv 根工程
├── uv.lock
├── pnpm-lock.yaml
├── README.md
└── LICENSE                     # MIT
```

### 1.2 `apps/api/` 内部结构

```
apps/api/
├── pyproject.toml
├── src/
│   └── jobcopilot_api/
│       ├── __init__.py
│       ├── main.py              # FastAPI 入口
│       ├── settings.py          # pydantic-settings
│       ├── routers/             # 路由,1 资源 = 1 文件
│       │   ├── jds.py
│       │   ├── profiles.py
│       │   ├── matches.py
│       │   ├── resumes.py
│       │   ├── applications.py
│       │   ├── interviews.py
│       │   ├── files.py
│       │   ├── costs.py
│       │   ├── settings.py
│       │   ├── auth.py
│       │   └── admin/
│       ├── services/            # 业务编排
│       │   ├── jd_service.py
│       │   ├── profile_service.py
│       │   ├── match_service.py
│       │   ├── resume_service.py
│       │   └── interview_service.py
│       ├── agents/              # Agent 实现 + Prompt
│       │   ├── jd_parser/
│       │   ├── profile_parser/
│       │   ├── query_rewriter/
│       │   ├── match_analyst/
│       │   ├── resume_planner/
│       │   ├── resume_drafter/
│       │   ├── resume_reviewer/
│       │   ├── interview_planner/
│       │   ├── interviewer/
│       │   ├── interview_evaluator/
│       │   ├── eval_judge/
│       │   ├── prompts/         # *.j2(版本化)
│       │   └── tools/           # function calling 工具
│       ├── llm/                 # LLM 抽象层
│       │   ├── client.py
│       │   ├── tiers.py
│       │   ├── providers/
│       │   │   ├── dashscope.py     # Qwen3.6(主)
│       │   │   └── deepseek.py      # 备选(ADR-0001 回切)
│       │   └── cache.py
│       ├── infra/
│       │   ├── db.py             # SQLAlchemy 2.x async
│       │   ├── pgvector.py
│       │   ├── pgmq.py
│       │   ├── embedding.py
│       │   └── tracing.py        # Langfuse
│       ├── models/               # SQLAlchemy ORM
│       ├── schemas/              # Pydantic(请求/响应)
│       └── streaming/            # SSE 实用
├── alembic/                     # 迁移
│   ├── versions/
│   └── env.py
└── tests/
    ├── unit/
    ├── integration/
    └── conftest.py
```

### 1.3 `apps/web/` 内部结构

```
apps/web/
├── package.json
├── next.config.ts
├── biome.json
├── src/
│   ├── app/                     # App Router
│   │   ├── (dashboard)/
│   │   │   ├── jds/
│   │   │   ├── profiles/
│   │   │   ├── matches/
│   │   │   ├── resumes/
│   │   │   ├── applications/
│   │   │   └── interviews/
│   │   ├── settings/
│   │   ├── api/                 # 仅做 BFF / 透传
│   │   └── layout.tsx
│   ├── components/              # 复用组件
│   ├── lib/
│   │   ├── api-client.ts        # fetch wrapper + SSE 封装
│   │   ├── stream.ts            # ReadableStream / EventSource 助手
│   │   └── format.ts
│   ├── hooks/                   # Tanstack Query 钩子
│   └── types/                   # 从 packages/schemas 重导出
├── e2e/                         # Playwright
└── public/
```

### 1.4 `packages/schemas/`

```
packages/schemas/
├── package.json
├── src/
│   └── api.ts                   # datamodel-code-generator 生成,不手改
└── README.md                    # 生成方法说明
```

### 1.5 `evals/`

详见 6-EVAL_PLAN §2.2,不重复。

---

## 2. 后端 Python 规范

### 2.1 工具链

| 用途 | 工具 | 版本 |
|------|------|------|
| 包管理 + 虚拟环境 | `uv` | ≥ 0.5 |
| Lint + Format | `ruff` | ≥ 0.7,配置 `select = ["ALL"]` 减去白噪音 |
| 类型检查 | `mypy --strict` | ≥ 1.13 |
| 单测 | `pytest` + `pytest-asyncio` + `httpx` | - |
| 覆盖率 | `coverage` | 阈值见 §6 |
| Schema 校验 | `pydantic` | v2 |

`pyproject.toml` 要点:

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E","F","W","I","N","UP","B","SIM","RUF","ASYNC","S","TID","PT"]
ignore = ["E501","S101"]   # line-too-long 给 formatter / pytest assert

[tool.mypy]
strict = true
plugins = ["pydantic.mypy"]
```

### 2.2 命名

- 模块 / 函数 / 变量:`snake_case`
- 类 / Pydantic 模型:`PascalCase`
- 常量:`UPPER_SNAKE_CASE`
- 私有内部:`_leading_underscore`,公共 API 不前置下划线
- 测试函数:`test_<unit>__<scenario>__<expected>`(双下划线分段)

### 2.3 文件组织规则

- 1 资源 = 1 router 文件(`routers/jds.py`),路由函数前缀 `route_`
- 业务编排在 `services/`,**不**在 router 里直接写业务逻辑
- Agent 严格只做"输入 → LLM → 结构化输出",**不**做数据库写入(写在 service)
- LLM 调用统一通过 `llm.client.LLMClient`,Agent 不直接 import provider

### 2.4 异步

- 全部 IO 走 `async`(包括 DB / LLM / HTTP)
- 不允许 `time.sleep`、`requests.get` 等同步 IO
- CPU 重活(BERTScore、文档解析)用 `asyncio.to_thread` 避免阻塞 loop

### 2.5 错误处理

- 业务错误:自定义 `JobCopilotError` 基类 + 子类,FastAPI 统一 ExceptionHandler 转 RFC 7807(见 4-API_SPEC §3)
- 不允许裸 `except Exception`,必须明确异常类型
- LLM 错误必须区分:`LLMTimeoutError` / `LLMUpstreamError` / `LLMSchemaInvalidError`,各自走不同重试/降级策略

### 2.6 日志

- `structlog` JSON 输出
- 必带字段:`request_id` / `user_id` / `trace_id`(Langfuse)
- 禁止日志中出现 `api_key` / 用户邮箱 / 简历正文。`structlog` 处理器中黑名单脱敏

### 2.7 数据库

- ORM:`SQLAlchemy` 2.x async
- 迁移:`alembic`,**任何 schema 改动必须 commit migration**(没有迁移就不算 schema 改动)
- 查询:优先 `select(Model)`,避免 raw SQL;复杂聚合允许 raw SQL,但必须有单测
- 事务边界:service 层用 `async with session.begin()`,router 不开事务
- N+1:绝对不允许,代码 review 必查;用 `selectinload`

### 2.8 测试

- 单测:`tests/unit`,跑无 DB,无 LLM,**禁止 mock 业务逻辑**(只 mock 外部 IO)
- 集成:`tests/integration`,起真实 Postgres(testcontainers),**不 mock 数据库**(避免迁移漂移)
- LLM 在测试里默认走 `dummy provider`,返回固定 fixture;走真实 LLM 的测试单独打 `@pytest.mark.live` 标签,默认不跑
- 覆盖率门槛:**单测 + 集成合计 ≥ 70%**;`apps/api/agents/**` ≥ 80%(prompt 改动多,需要保护)

---

## 3. 前端 TypeScript 规范

### 3.1 工具链

| 用途 | 工具 | 版本 |
|------|------|------|
| 包管理 | `pnpm` workspace | ≥ 9 |
| 框架 | Next.js | 15.x(App Router) |
| Lint + Format | `biome` | 2.x |
| 类型 | `tsc --strict` | TS 5.x |
| 单测 | `vitest` | ≥ 1 |
| e2e | `playwright` | ≥ 1.45 |
| UI | `shadcn/ui` + `tailwindcss` | - |
| 数据 | `@tanstack/react-query` | v5 |

### 3.2 命名

- 文件:`kebab-case.tsx`(组件)/ `kebab-case.ts`(工具)
- 组件 / 类型:`PascalCase`
- hooks:`useCamelCase`
- 常量:`UPPER_SNAKE_CASE`(放 `lib/constants.ts`)

### 3.3 类型与 schema

- 接口请求/响应 **必须** 用 `packages/schemas` 自动生成的类型
- 不允许手写 `interface UserResponse {...}` 重复定义
- `any` 不允许;`unknown` 必须立即收窄
- 任意 API 调用走统一 `apiClient.<resource>.<method>`,不直接 fetch

### 3.4 SSE 客户端

- 标准 `EventSource` 仅支持 GET,不能携 body → 用 `fetch + ReadableStream` 自行解析 `event:/data:/id:`
- 已封装 `lib/stream.ts`,新代码必须用,不要重复造

### 3.5 状态

- 服务端状态:`react-query`(缓存 / 重试 / 失效统一)
- 客户端 UI 状态:`useState` / `useReducer`,不引入 Zustand 等(规模不到)
- URL 状态(过滤 / cursor):`searchParams`(App Router 原生)

### 3.6 测试

- 组件单测:`vitest` + `@testing-library/react`(纯 UI 行为)
- e2e:`playwright`,覆盖 4-API_SPEC 的 P0 闭环(JD → 档案 → 匹配 → 简历 → 投递)
- e2e 必须能在 CI 跑(headless),无需视频录制

---

## 4. Git 工作流

### 4.1 分支策略

- `main`:始终可部署,只接受 PR 合入
- `feat/<short-name>`:功能开发
- `fix/<short-name>`:bug 修复
- `chore/<short-name>`:依赖升级、CI、文档(无功能影响)
- `docs/<short-name>`:文档专属
- `eval/<short-name>`:评测样本 / Prompt 版本

不使用 `develop` 分支(单人项目无需)。

### 4.2 提交信息

Conventional Commits:

```
<type>(<scope>): <subject>

<body 可选>

<footer 可选,如 BREAKING CHANGE / Refs>
```

- `type`:`feat` / `fix` / `chore` / `docs` / `test` / `refactor` / `perf` / `build` / `ci` / `eval`
- `scope`:`api` / `web` / `agents` / `evals` / `docker` / `docs` / `infra` 之一
- `subject`:中文或英文,祈使式,无句号

示例:

```
feat(agents): 实现 ResumeReviewerAgent 与 fabrication recall ≥ 0.95
fix(api): SSE 心跳在 nginx 后被缓冲,改为每 10s + 强 flush
eval(resume_review): 注入 8 条新对抗样本,baseline 重训
```

### 4.3 PR 流程

1. 起分支 → 改动 → 自测 → 推 → 开 PR
2. PR 必须关联 issue 或 ROADMAP 里程碑任务
3. PR 描述用模板(§4.5)
4. CI 全绿 + (单人项目自审)合并方式:**Squash and merge**
5. 合并后立即删除分支

### 4.4 PR 大小

- 单 PR 改动 ≤ 800 行(测试 + 自动生成代码不计)
- 超过的 PR 必须先拆;拆分原则:能不能独立通过 CI / 能不能独立 review
- 例外:M0 仓库初始化、M3 v0.5 内测发布等"打基础"PR 允许 > 1000 行

### 4.5 PR 模板(`.github/pull_request_template.md`)

```markdown
## 变更类型
- [ ] feat
- [ ] fix
- [ ] chore
- [ ] docs / eval / refactor / perf

## 关联
- ROADMAP:M_ / 任务名
- Issue / Discussion:#

## 描述
<!-- 一段话:为什么要改、改了什么、影响什么 -->

## 测试
- [ ] 单测:
- [ ] 集成:
- [ ] e2e:
- [ ] 评测回归:N/A 或 <suite> Δ=<>

## 影响面
- [ ] 数据库迁移(`alembic upgrade head` 已在本地验证)
- [ ] 配置变更(`.env.example` 已同步)
- [ ] 文档变更(`docs/` 已同步,STATUS.md 已更新)
- [ ] Prompt 改动(已新增版本号 + 评测达标)
- [ ] 不向后兼容(已写 BREAKING CHANGE)
```

### 4.6 Code Review(自审 / 协作)

单人项目阶段,作者自审遵循以下顺序:

1. 先看 diff 整体结构,有没有重复或不一致
2. 跑本地测试 + e2e 关键路径
3. 跑 CI,等全绿
4. 24h 后再来 reread(冷读)发现新问题
5. 合并

未来加入协作者后:至少 1 个 approve + 全绿才能合;`.github/CODEOWNERS` 写好。

---

## 5. CI/CD

### 5.1 GitHub Actions 工作流清单

| 工作流 | 文件 | 触发 | 时长目标 |
|-------|------|------|---------|
| Lint & TypeCheck | `lint.yml` | PR / push | ≤ 3min |
| Backend Tests | `test-api.yml` | `apps/api/**` 改动 | ≤ 5min |
| Frontend Tests | `test-web.yml` | `apps/web/**` 改动 | ≤ 4min |
| E2E (Playwright) | `e2e.yml` | PR | ≤ 8min |
| Eval Regression | `eval.yml` | `apps/api/agents/**` / `evals/**` | ≤ 10min |
| Docker Build | `docker.yml` | `main` push + tag | ≤ 6min |
| Schema Drift | `schema-drift.yml` | PR | ≤ 1min |
| Type Sync | `type-sync.yml` | PR | ≤ 1min |

### 5.2 关键卡口

- **Schema Drift**:运行 `alembic check`,本地迁移与 ORM 模型不一致 → fail
- **Type Sync**:跑 `pnpm gen:api` 后 `git diff --exit-code packages/schemas/`,有未提交差异 → fail(强制 PR 同步类型)
- **Coverage Gate**:< 阈值 → fail
- **Eval 不退化**:见 6-EVAL_PLAN §10.4

### 5.3 缓存策略

| 缓存 | key | scope |
|------|-----|------|
| `uv` 包 | `uv-${{ hashFiles('uv.lock') }}` | repo |
| `pnpm` store | `pnpm-${{ hashFiles('pnpm-lock.yaml') }}` | repo |
| Playwright browsers | `pw-${{ runner.os }}-1` | repo |
| Embedding fixtures | `embed-fixtures-v1` | repo,见 6-EVAL_PLAN §11.2 |

### 5.4 Secrets

| 名称 | 用途 |
|------|------|
| `DASHSCOPE_API_KEY_EVAL` | CI 评测专用 Key(与生产隔离)|
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | 仅 nightly 上报 |
| `DOCKER_HUB_TOKEN` | 镜像推送(M6 起)|
| `GITHUB_TOKEN` | 默认提供 |

### 5.5 发布

- 版本号:SemVer。pre-release `0.x.y`,GA 后 `1.0.0+`
- 触发:`git tag v0.5.0 && git push --tags`
- 工作流自动构建镜像 → 推 Docker Hub → 创建 GitHub Release(自动生成 changelog from squash messages)

---

## 6. 质量门槛

### 6.1 强制(违反 = CI fail)

| 项 | 阈值 |
|----|------|
| 后端 lint(ruff) | 0 error |
| 后端类型(mypy strict) | 0 error |
| 前端 lint(biome) | 0 error |
| 前端类型(tsc) | 0 error |
| 后端单测覆盖率 | ≥ 70%(`apps/api/agents/**` ≥ 80%) |
| 前端单测覆盖率 | ≥ 50%(UI 主要靠 e2e) |
| e2e 关键路径 | 100% 通过 |
| Eval 不退化 | 见 6-EVAL_PLAN |
| schema drift | 0 |
| type sync | 0 diff |

### 6.2 软门槛(警告 + 季度复审)

- 后端单文件 ≤ 400 行(超过提示拆分)
- Cyclomatic complexity ≤ 12 / 函数(`ruff` C90)
- TODO 数量 ≤ 30(超过强制 triage)
- 依赖数量增长(`uv tree` / `pnpm why`):月度对比

---

## 7. 性能与成本工程

### 7.1 性能预算

每个 P95 端到端目标见 PRD §5.1。CI 中 `pytest-benchmark` 跑关键路径,基准漂移 > 20% 警告,> 40% 失败。

### 7.2 LLM 成本归因

- 全部 LLM 调用经过 `LLMClient.complete`,自动写入 `llm_calls` 表(see 3-DATA_MODEL §3.16)
- nightly job 把日累计成本 push 到 STATUS 摘要
- 单次 PR 引入的"日成本预估"在 PR 模板里手填(开发者自检)

### 7.3 缓存命中率监控

`/v1/costs/summary` 暴露 `cache_hit_rate`。Premium 档目标 ≥ 70%,低于 50% 时告警(可能 prompt 前缀不稳定)。

---

## 8. 安全与合规

### 8.1 不可入仓的内容

- 任何 `.env` 真实值
- 任何 `*_API_KEY` / `*_SECRET`
- 用户真实简历 / JD(评测样本必须脱敏)
- 大于 5MB 的二进制(用 Git LFS 或外部存储)

`.gitignore` + `pre-commit` `gitleaks` 扫描双保险。

### 8.2 依赖安全

- `dependabot` 周更
- 严重漏洞 24h 内合,中等 1 周
- 不允许直接 `latest` 引入未审计的 LLM SDK

### 8.3 用户数据

- 本地优先架构,默认数据不离开机器
- 云端 Demo 30 分钟 TTL,定时 cron 清表
- BYOK Key 仅内存使用,日志/Trace 不落,LLM 抽象层做最后脱敏(见 4-API_SPEC §9.3)

### 8.4 许可证合规

- 仓库:MIT
- LaTeX 模板:`awesome-cv` 是 LPPL,商用注意条款,README 注明
- 中文字体:用 `Noto Sans CJK`(SIL OFL),不用 Adobe / 思源衍生收费版本
- 第三方 SDK 许可证扫描:`pip-licenses` / `license-checker`,在 `docs/THIRD_PARTY_NOTICES.md` 维护

---

## 9. 文档纪律

### 9.1 文档与代码同步

- 任何 API 改动 → 更新 4-API_SPEC.md 同 PR
- 任何 schema 改动 → 更新 3-DATA_MODEL.md 同 PR
- 任何 Agent / Prompt 改动 → 更新 5-AGENT_DESIGN.md 同 PR
- 任何决策反转 → **新增 ADR**(不改旧 ADR;旧 ADR 标 Superseded)
- STATUS.md:每个 PR 合入后或里程碑结束时更新

### 9.2 新功能上线检查表

```
[ ] 端点已在 4-API_SPEC.md 描述
[ ] schema 已在 3-DATA_MODEL.md 描述
[ ] Agent 已在 5-AGENT_DESIGN.md 描述
[ ] 评测 suite 已在 6-EVAL_PLAN.md 描述
[ ] 工程改动(若有)已在本文档描述
[ ] STATUS.md 当前阶段已更新
[ ] README 快速开始仍然 work
```

### 9.3 ADR 编号规则

- 顺延数字,不复用,不删除
- 当前已用:0001 / 0002 / 0003。下一个 ADR 是 0004
- 文件名:`<编号>-<kebab-case-title>.md`
- frontmatter 必须含 `status`,允许值:`Proposed` / `Accepted` / `Superseded by NNNN` / `Deprecated`

---

## 10. 本地开发指南

### 10.1 第一次 setup

```bash
# 工具
brew install uv pnpm postgresql@16
# 仓库
git clone https://github.com/lemma42796/job-copilot.git && cd job-copilot
# 后端
uv sync                                  # 安装 Python 依赖
# 前端
pnpm install
# 数据库 + 服务
cp .env.example .env                     # 填 DASHSCOPE_API_KEY
docker compose -f docker-compose.dev.yml up -d   # 起 postgres + caddy
# 迁移
cd apps/api && uv run alembic upgrade head
# 跑
uv run uvicorn jobcopilot_api.main:app --reload --port 8000   # 后端
pnpm --filter web dev                                          # 前端
```

### 10.2 常用脚本

```bash
pnpm gen:api          # 拉 OpenAPI → 生成 packages/schemas
pnpm test             # 全部测试
pnpm test:e2e         # Playwright
pnpm eval             # 跑评测(指定 suite:pnpm eval -- --suite jd_extract)
uv run alembic revision --autogenerate -m "msg"  # 新迁移
uv run scripts/seed_dev.py                       # 种子数据
```

### 10.3 调试

- 后端:`uv run python -m debugpy --listen 5678` + VSCode `attach`
- LLM 调试:Langfuse 面板(`docker compose --profile observability up langfuse`)
- 数据库:`pgcli` / TablePlus,DSN 在 `.env`

---

## 11. 不在本文档范围

| 主题 | 文档 |
|------|------|
| API 端点契约 | 4-API_SPEC |
| 数据库表结构 | 3-DATA_MODEL |
| Agent 业务逻辑 | 5-AGENT_DESIGN |
| 评测样本 | 6-EVAL_PLAN |
| 里程碑节奏 | 7-ROADMAP |
| 公开发布运营 | 7-ROADMAP §9 / 后续单独 ops doc |
