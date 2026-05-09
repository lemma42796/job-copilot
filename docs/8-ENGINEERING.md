---
title: ENGINEERING - JobCopilot v2(仓库结构 / 工具链 / CI / 迁移 / 本地开发 / 部署)
owner: lemma42796
last_updated: 2026-05-10
purpose: 锁工程基线 — 一个新协作者按本文从零跑通项目,改完代码不破坏 CI,改 schema 不破坏迁移,改 prompt 不破坏评测
---

# 1. 一句话总览

monorepo:**Python uv workspace**(`apps/api`)+ **pnpm workspace**(`apps/web` / `packages/schemas` / `evals`),Python 一套(ruff + mypy + pytest),JS 一套(biome + tsc + 各包自带 test),Alembic 单 head,docker compose 6 服务本地起。CI 跑 6 条 workflow:lint / test-api / test-web / type-sync / docker-smoke / eval(M0 期间是手动触发,M2 数据集到位后再放开自动)。

# 2. 仓库结构

```
JobCopilot/
├── apps/
│   ├── api/                          # FastAPI 后端(uv workspace member)
│   │   ├── pyproject.toml            # 后端 dependencies(openai / sqlalchemy / langgraph / ...)
│   │   ├── src/jobcopilot_api/       # 详见 2-TECH §4.1
│   │   ├── alembic/                  # 迁移(单 head;详见 §7)
│   │   │   ├── env.py
│   │   │   ├── script.py.mako
│   │   │   └── versions/             # 0001..0015 v1 历史 + 0016 v2 schema(M0 砍 v1 表 + 建 v2 表)
│   │   └── tests/                    # pytest(unit + testcontainers integration)
│   └── web/                          # Next.js 15 前端(pnpm workspace member)
│       ├── package.json
│       └── src/                      # 详见 2-TECH §4.2
├── packages/
│   └── schemas/                      # OpenAPI 拉过来的 TS 类型(`pnpm gen:api` 自动生成,提交进库)
│       ├── package.json
│       └── src/api.ts                # CI type-sync workflow 防漂移(详见 §6.4)
├── evals/                            # 评测套件(数据集 / 报告目录;详见 6-EVAL_PLAN)
│   ├── suites/                       # M0 后重建:hybrid_search / quiz_generator / answer_judge / jd_aggregator / resume_advisor
│   ├── reports/                      # 跑完写这里(.gitignore)
│   └── README.md                     # eval 怎么跑 + DASHSCOPE_API_KEY_EVAL 哪儿来
├── docker/                           # docker compose 配套镜像 / 反代 / 数据库初始化
│   ├── api.Dockerfile                # python:3.12-slim + uv + standalone venv
│   ├── web.Dockerfile                # node:22 多阶段 + Next standalone output
│   ├── postgres/init.sql             # 创 pgvector + tsvector 扩展
│   └── caddy/Caddyfile               # 反代(M0 已落,M3+ 加 HTTPS)
├── docker-compose.yml                # postgres / api / web / caddy(+ langfuse / langfuse-db M0 加)
├── docs/                             # 文档 SSoT(0-9 + STATUS + LESSONS)
├── scripts/                          # 一次性运维脚本(目前空,按需放)
├── pyproject.toml                    # uv workspace root + ruff + mypy + pytest + coverage 配置
├── package.json                      # pnpm workspace root + 顶层脚本(lint / typecheck / build / test)
├── pnpm-workspace.yaml               # apps/* + packages/* + evals
├── biome.json                        # JS/TS lint + format(单一工具,不用 eslint + prettier)
├── tsconfig.base.json                # 各 package extends 这份(strict / noUncheckedIndexedAccess)
├── uv.lock                           # Python 锁文件(必须提交)
├── pnpm-lock.yaml                    # JS 锁文件(必须提交)
├── AGENTS.md                         # Codex 协作指令(行为约束 / 文件导航)
├── CLAUDE.md                         # Claude Code 协作指令(行为约束 / 文件导航)
├── README.md                         # 一段话定位 + 跑起来命令
└── .github/
    ├── workflows/                    # 6 条 CI(详见 §6)
    ├── ISSUE_TEMPLATE/
    └── pull_request_template.md      # 改动类型 + 测试 + 影响面 checklist(详见 §5.5)
```

> **注**:`apps/api/` 用 hatchling 构建(`[build-system]` requires `hatchling`),wheel 只打包 `src/jobcopilot_api/`。`apps/web/` 用 Next.js standalone output(`output: 'standalone'`),docker 镜像不带源码,只带 `.next/standalone` + `static` + `public`,运行时镜像 < 200 MB。

# 3. 工具链与包管理

## 3.1 版本锁

| 工具 | 版本 | 锁在哪儿 |
|------|------|--------|
| Python | 3.12+(`requires-python = ">=3.12"`) | `pyproject.toml` 两份(root + apps/api)|
| uv | 0.5.11(CI / Dockerfile pin) | `.github/workflows/*.yml` `astral-sh/setup-uv@v3` `version: "0.5.11"` |
| Node | 20+(CI 用 22) | `package.json` `"engines": { "node": ">=20" }` |
| pnpm | 9.15.9 | `package.json` `packageManager` + CI `pnpm/action-setup@v4 with version: 9.15.9` |
| TypeScript | 5.6.3 | `package.json` devDependencies |
| Biome | 1.9.4 | `package.json` devDependencies |

## 3.2 关键命令(根目录)

```bash
# Python
uv sync --all-packages                      # 装根 + workspace member 全部(LESSONS §7.2:漏 --all-packages = ImportError)
uv sync --package jobcopilot-api --extra dev    # CI 用(只装 api + dev,省 cache)
uv run pytest -q                            # 跑所有后端测(快,默认收紧输出)
uv run ruff check .                         # lint
uv run ruff format --check .                # format check(CI)/ uv run ruff format . 写回(本地)
uv run mypy apps/api/src apps/api/tests     # 类型检查

# JS
pnpm install --frozen-lockfile              # 装(CI / 本地通用)
pnpm lint                                   # biome check .
pnpm lint:fix                               # biome check --write .
pnpm typecheck                              # 各 package 并行 tsc --noEmit
pnpm build                                  # 各 package 并行 build(目前主要是 web)
pnpm gen:api                                # 拉 /v1/openapi.json → 重生 packages/schemas/src/api.ts(api 必须本地起)
pnpm dev:web                                # 只起前端 dev server(API 走 docker 起)
pnpm eval:jd / eval:profile / eval:view     # 评测 CLI(走 evals workspace)
```

## 3.3 .env 约定

```
# 项目根 .env(gitignored;.env.example 跟着结构走,不带值)
JOBCOPILOT_DASHSCOPE_API_KEY=sk-...         # settings.py 自动读;BYOK 不写死代码(已入 memory)
JOBCOPILOT_LLM_PROVIDER=dashscope           # 当前唯一选项(走 OpenAI 兼容接口,详见 reference memory)
JOBCOPILOT_DATABASE_URL=postgresql+asyncpg://jobcopilot:jobcopilot@localhost:5432/jobcopilot
JOBCOPILOT_CORS_ALLOW_ORIGINS=["http://localhost:3000"]

# Langfuse(M0 加完后落地)
LANGFUSE_HOST=http://localhost:3001
LANGFUSE_PUBLIC_KEY=pk-...
LANGFUSE_SECRET_KEY=sk-...
LANGFUSE_NEXTAUTH_SECRET=<32 字节随机串,生成一次就别动>
LANGFUSE_DB_PASSWORD=<docker 里 langfuse-db 的 password>
LANGFUSE_SALT=<32 字节随机串>

# 评测专用 key(CI secret;跟主 key 分开,防额度互踩)
DASHSCOPE_API_KEY_EVAL=sk-...
```

`.env.example` 跟着结构走、不带值,且要在 PR 里同步(PR 模板 "影响面" 那条)。

# 4. 代码规范

## 4.1 Python(ruff + mypy)

`pyproject.toml` 已锁:

- **line-length 100**(超过 ruff 不报,但拉到 120+ 自己看着改)
- **target-version py312**
- **lint select**:`E F W I N UP B SIM RUF ASYNC S TID PT`(pycodestyle / pyflakes / 命名 / pyupgrade / bugbear / simplify / async / 安全 / tidy-imports / pytest-style)
- **lint ignore**:`E501`(line-too-long,已由 line-length 100 + 项目里允许长字符串)、`S101`(`assert` 在生产代码里也允许 — 内部 invariant 用)
- **per-file-ignores**:
  - `tests/**/*.py` 关掉 `S`(测试里随便用 assert / hardcode secret) + `PT011`(`pytest.raises(Exception)` 不强制窄类型)
  - `apps/api/src/jobcopilot_api/settings.py` 关掉 `S104`(0.0.0.0 bind 在容器里是必要的)
- **mypy strict**:`strict = true` + `pydantic.mypy` 插件;`testcontainers.* / pgvector.*` 忽略缺类型;tests 放宽 `disallow_untyped_decorators`
- **import 风格**:ruff isort,`known-first-party = ["jobcopilot_api"]`,标准库 → 第三方 → 本地三段式;不写 `from foo import *`

## 4.2 TS / TSX(biome + tsc)

`biome.json` 已锁:

- **格式**:`indentStyle: space` / `indentWidth: 2` / `lineWidth: 100` / `quoteStyle: single` / `semicolons: always` / `trailingCommas: all`
- **lint**:`recommended: true` + 关 `style.noNonNullAssertion`(`x!` 在 narrowed 后允许)+ 升 `suspicious.noExplicitAny: error`(any 一律不让过)
- **biome 忽略 apps/api/**:Python 后端不进 biome(防误报);`packages/schemas/src/api.ts` 也忽略(自动生成)
- **tsc strict**:`tsconfig.base.json` 锁 `strict: true` + `noUncheckedIndexedAccess: true`(数组 / Record 索引会回 `T | undefined`,手动 narrow)+ `noImplicitOverride`(继承时 override 必须写关键字)+ `forceConsistentCasingInFileNames`

## 4.3 命名 / 风格

- **错误码**:snake_case + 出处明确(`note_not_found` / `insufficient_chunks` / `forbidden_pattern_persists`),全列表见 2-TECH §7
- **prompt 文件名**:`<agent>_v<X.Y.Z>.j2`(详见 §10.2),改 prompt 必须 bump
- **Pydantic schema**:Input / Output 分别命名(`QuizGeneratorInput` / `QuizGeneratorOutput`),不混着写
- **SSE 事件**:`started` / `progress{phase}` / `question_ready` / `result` / `error` / `done(ok=bool)`(2-TECH §5.2 / 5.4 / 5.7 / 5.8 已固定);所有 phase 全集见 4-API
- **alembic revision id**:`NNNN_短描述`(`0016_v2_schema`),纯数字单调递增不并发分叉,单 head
- **commit 文案**:`feat: ...` / `fix: ...` / `docs: ...` / `refactor: ...` / `chore: ...`,中文描述无所谓但前缀英文
- **路径**:绝对路径文档化引用(`apps/api/src/jobcopilot_api/agents/answer_judge/agent.py`),不写 `./relative/...`

# 5. Git 工作流

## 5.1 分支策略

- **main**:可部署主线,protected(GitHub repo settings 勾 require PR + require status checks)
- **feature 分支**:命名 `feat/<里程碑>-<切片>`(`feat/m1-notes-zip-upload`)/ `fix/<错误码或 issue>` / `docs/<doc-name>`
- **不接受 force-push 到 main**;feature 分支 rebase 自由

## 5.2 commit 风格

- **每个 commit 一件事 + 编译过 + 测过**(M3 期间出过 commit 1 build broken,commit 2 修 build 的丑事 — v2 不再出)
- **小步多 commit**(里程碑展开成切片,切片展开成 commit),不攒大 commit
- **绝不加 Co-Author**
- **不写"Generated with Claude Code" / "Generated with Codex" 注脚**

## 5.3 tag 策略

- **里程碑 tag**:`v0.X-MX-end`,M0 末态 = `v0.1-jobcopilot-v1`(锁 v1 末);M1 完 = `v0.2-m1-end`;依此类推
- **切片不打 tag**(避免 tag 噪音),切片末态走 commit message + STATUS.md 里程碑表

## 5.4 commit 时该不该 commit

- 文档变更 → 单独 commit,跟代码分开
- migration → 跟引入它的 model 改动同 commit(回退时一并退,避免 schema 漂移)
- prompt bump → 跟评测结果同 commit(prompt v1.0.4 + dataset.jsonl 通过 + 报告快照,三件套)

## 5.5 PR 模板(`.github/pull_request_template.md`)

PR 必填:

- **变更类型**:feat / fix / chore / docs / eval / refactor / perf
- **关联**:ROADMAP MX / 切片名 + Issue
- **描述**:**为什么改 / 改了什么 / 影响什么** 一段话(不是 commit message 复述)
- **测试**:单测 / 集成 / e2e / 评测回归 Δ
- **影响面 checklist**:
  - 数据库迁移已本地 `alembic upgrade head` 验证
  - `.env.example` 同步
  - `docs/` 同步 + STATUS.md 更新
  - prompt 改了已 bump 版本号 + 评测达标
  - 不向后兼容 → 写明 `BREAKING CHANGE`

> **说明**:PR 描述里讲 "为什么"。"什么" git diff 自己看,"为什么" diff 看不出来。

# 6. CI 流水线

`.github/workflows/` 6 条,各自独立 cancel-in-progress(同一 PR push 多次只跑最后一次):

## 6.1 lint.yml — 全员体检(每次 push / PR)

3 个 job 并行:

- **python**:`uv sync --frozen --package jobcopilot-api --extra dev` → ruff check + ruff format --check + mypy
- **web**:pnpm install + biome check + tsc(web + schemas 两个 package)
- **model-id-lint**:grep 防 stale 模型 ID 误用(`qwen-vl / qwen-plus / qwen-flash / qwen-max` 等 v1 历史命名禁入新代码 / 新文档;`docs/slices/` 历史归档豁免)

> **为什么单独有 model-id-lint**:S21 W8 真出过 bug — 半年前的注释 / 文档里残留 `qwen-vl-plus`,新人改代码时复制粘贴推到生产。grep CI = 工程化的"永久约束 19"(qwen3.6-flash 唯一)。

## 6.2 test-api.yml — 后端单测 / 集成测

paths 触发:`apps/api/**` / `pyproject.toml` / `uv.lock`

- pytest + coverage,**fail_under: 70**(`pyproject.toml` 已锁)
- 集成测用 `testcontainers[postgres]>=4.8.2` 拉真 PG(LESSONS §7.5 之类:不 mock DB,跟生产同 schema)
- 标记 `integration` 的测才需要 Docker;`@pytest.mark.integration` 默认跑(GitHub runner 自带 Docker)

## 6.3 test-web.yml — 前端 build

paths 触发:`apps/web/**` / `packages/schemas/**`

- `pnpm --filter @jobcopilot/web build`(NEXT_TELEMETRY_DISABLED=1)
- 不跑 e2e — playwright 留到 M3 dashboard 联调时再起 workflow

## 6.4 type-sync.yml — OpenAPI 漂移防线

paths 触发:`apps/api/**` / `packages/schemas/**`

- 起一个 API server → curl `/v1/openapi.json` → `pnpm gen:api` 重生 → `git diff --exit-code packages/schemas/src/api.ts`
- 任何 router 改动忘 regen schema → CI 红
- **说明**:后端 schema 跟前端类型用同一份 source of truth(OpenAPI),CI 防"后端改了字段但前端 API 类型没同步"

## 6.5 docker-smoke.yml — 镜像可起 + 联通

paths 触发:`docker/**` / `docker-compose.yml` / `apps/api/**` / `apps/web/**`

- `docker compose build api web` → `docker compose up -d postgres api web`
- 等 api healthcheck 通过(60 次 × 2s 重试)
- 等 web 渲染含 `API: ok`(homepage SSR 探活)
- 验完 `docker compose down -v`
- **不验 caddy / langfuse**(M0 加完 langfuse 后再扩 smoke 范围)

## 6.6 eval.yml — prompt 防回归

**M0 期间 manual trigger 不 push 自动跑**,M2 数据集到 50 条 + Δ ≤ -2pp 比对脚本就位时再放开 push / pull_request。理由:dataset 不稳定 + 跑一次烧钱 + 评测 kappa 没达标前数字本身不可信。

- 5 个 suite(hybrid_search / quiz_generator / answer_judge / jd_aggregator / resume_advisor)各自 1 个 job
- 用 `DASHSCOPE_API_KEY_EVAL` GH secret(跟主 key 分账,跑炸了不影响 dev)
- 报告 `evals/reports/<suite>/latest.json` 上传 artifact,留存 14 天

## 6.7 CI 触发矩阵

| 改了什么 | 触发哪些 workflow |
|--------|-----------------|
| `apps/api/src/**` Python 代码 | lint / test-api / type-sync(若改了 router)/ docker-smoke |
| `apps/web/src/**` TS / TSX | lint / test-web / docker-smoke |
| `packages/schemas/src/**` | lint / test-web / type-sync |
| `docker/**` / `docker-compose.yml` | docker-smoke |
| `apps/api/.../prompts/**` | (manual)eval — M2 后自动 |
| `docs/**` / `*.md` | 不触发 CI(只跑 model-id-lint via lint.yml 里的 grep) |

# 7. Alembic 迁移

## 7.1 单 head 强制

- 一个时间点 `alembic heads` 永远只回一行;两人同时建 revision(分叉) → 撞 head 时手动 `alembic merge` 合掉
- revision id 命名:`NNNN_<短描述>`(`0016_v2_schema`),纯数字升序,直观看 M0 / M1 / M2 各阶段在哪段

## 7.2 v1 → v2 切换

M0 一次性走 `0016_v2_schema.py`:

```python
def upgrade():
    # 砍 v1 表(整批)
    op.drop_table("matches")
    op.drop_table("resume_versions")
    op.drop_table("resumes")          # v1 resumes(简历草稿用),v2 resumes 表语义不同 → 重建
    op.drop_table("profile_chunks")
    op.drop_table("profiles")
    op.drop_table("jds")              # v1 jds 表语义跟 v2 不同 → 重建
    op.drop_table("files")
    op.drop_table("users")            # v2 单用户暂不要 user 表

    # 建 v2 表(详见 3-DATA_MODEL §5)
    op.create_table("notes", ...)
    op.create_table("note_chunks", ...)
    op.create_table("questions", ...)
    op.create_table("quiz_sessions", ...)
    op.create_table("session_answers", ...)
    op.create_table("knowledge_gaps", ...)
    op.create_table("jds", ...)               # v2 形态(parsed_payload + source 等)
    op.create_table("jd_analyses", ...)
    op.create_table("resumes", ...)           # v2 形态(parsed_chunks JSONB)
    op.create_table("resume_analyses", ...)
    # 沿用 v1 表(0006 / 0014 / 0015)不动:prompt_versions / llm_calls / llm_response_cache + char_ngrams 函数

def downgrade():
    raise NotImplementedError("M0 v1→v2 切换不支持 downgrade(语义不兼容);需要回退请走 v0.1-jobcopilot-v1 tag 重建数据库")
```

## 7.3 写 migration 的纪律

- 每次改 model 必有对应 revision(model 改、migration 没改 = LESSONS §8.7 里"prompt 是产品代码"的同款问题:数据是产品代码)
- 加列 nullable + check constraint 双管(LESSONS §7.4 `matches.score nullable`)
- 删列前先在生产部署一轮"代码不读这列"再删(本地 MVP 不严格,SaaS 化时严格)
- 测**集成测必跑 `alembic upgrade head` → 业务测**,不允许直接 `Base.metadata.create_all`(避免 model 跟 migration 漂移没人发现)

## 7.4 alembic 命令速查

```bash
# 本地
uv run --project apps/api alembic revision -m "v2 schema (drop v1 tables + create v2)"
uv run --project apps/api alembic upgrade head
uv run --project apps/api alembic history              # 看链条
uv run --project apps/api alembic current              # 看当前在哪个 rev
uv run --project apps/api alembic downgrade -1         # 回退一步(downgrade 实现了才行)

# docker compose 起来后
docker exec jobcopilot-api alembic upgrade head
```

# 8. 本地开发环境

## 8.1 第一次 setup

```bash
# 1. clone + 装依赖
git clone <repo> && cd JobCopilot
uv sync --all-packages                      # Python
pnpm install --frozen-lockfile              # JS

# 2. 配 .env(从 .env.example 拷一份填 DASHSCOPE 主 key + LANGFUSE 三件套)
cp .env.example .env
$EDITOR .env

# 3. 起依赖服务(postgres + langfuse + langfuse-db)
docker compose up -d postgres langfuse langfuse-db

# 4. 跑 migration
uv run --project apps/api alembic upgrade head

# 5. 起 api(本地)+ web(本地)
uv run --project apps/api uvicorn jobcopilot_api.main:app --reload --port 8000   # 终端 1
pnpm dev:web                                                                     # 终端 2

# 6. 浏览器打开
#   http://localhost:3000        前端
#   http://localhost:8000/docs   FastAPI Swagger
#   http://localhost:3001        Langfuse UI
```

## 8.2 端口约定

| 服务 | 端口 | 容器外暴露 |
|------|------|-----------|
| postgres(业务)| 5432 | ✅(本地连 client) |
| api | 8000 | ✅ |
| web | 3000 | ✅ |
| caddy | 80 | ✅(M3+ 加 HTTPS 后用)|
| langfuse | 3001 → 容器 3000 | ✅ |
| langfuse-db | 5433(防跟业务 5432 撞) | ❌(只内部用) |

## 8.3 卡死自救手册

参考 LESSONS §7.3:`uvicorn --reload` + 子进程 raise → 母进程 reload-loop 但 worker 都死着,SIGTERM 没用。

```bash
pkill -9 -f "uvicorn jobcopilot_api"
pkill -9 -f next
rm -rf apps/web/.next                       # next dev cache 偶发损坏(LESSONS §6.4)
docker compose down -v                      # 数据库脏了重来(注意 -v 删 volume)
```

# 9. Docker 部署

`docker-compose.yml` 5 服务:**postgres**(pgvector/pg16)、**api**(自建 Dockerfile)、**web**(自建 Dockerfile)、**caddy**(2.8-alpine)、**langfuse + langfuse-db**(M0 加)。

## 9.1 镜像构建

- **api.Dockerfile**:python:3.12-slim-bookworm + uv 0.5.11 pin。两步 install 走 cache:
  1. 先 copy 只 `pyproject.toml + uv.lock` → `uv sync --no-install-project`(装依赖,缓存层)
  2. 再 copy `apps/api/` 全部 → `uv sync --package jobcopilot-api`(装项目本身)
  - 运行时直接 `PATH=/app/.venv/bin:$PATH` + `CMD ["uvicorn", "jobcopilot_api.main:app", "--host", "0.0.0.0", "--port", "8000"]`,不依赖 `uv run`
  - HEALTHCHECK 走 `urllib.request.urlopen('/v1/health')`
- **web.Dockerfile**:多阶段 `deps → builder → runtime`,Next standalone output(`output: 'standalone'` 在 `next.config.js`)只带必要文件,镜像 < 200 MB
  - HEALTHCHECK 走 `fetch('http://127.0.0.1:3000/')`

## 9.2 启动顺序

```yaml
api:
  depends_on:
    postgres:
      condition: service_healthy        # 等 postgres 健康再起 api(不等会撞 connection refused)
web:
  depends_on:
    - api                                # web SSR 需要 api alive
caddy:
  depends_on: [api, web]
langfuse:
  depends_on: [langfuse-db]              # langfuse 自带迁移,启动时撞 db 没起会 panic 退出 → restart 兜
```

## 9.3 数据持久化

- `postgres-data` volume → 业务 PG 数据
- `langfuse_data` volume → langfuse PG 数据(独立,跟业务 PG 隔离)
- `caddy-data / caddy-config` volume → caddy 证书 / 配置(M3 HTTPS 时用)

`docker compose down`(不带 `-v`)保留数据;`down -v` 一并删 volume(慎用,通常只在 LESSONS §7.3 自救时)。

## 9.4 关键决定:Langfuse PG vs 业务 PG 分实例

- **简化 schema 隔离 / 备份策略 / 升级节奏 / 单点失败**(2-TECH §6.5 已锁)
- 多花一个 200MB 内存 + 一份 volume,值
- 不用 `pg_database_create` 做单实例多 db — Langfuse 升级若改 schema / 扩展会污染业务 PG

# 10. 测试体系

## 10.1 后端测试金字塔

| 层 | 工具 | 跑哪些 | CI |
|----|------|------|-----|
| **单测** | pytest(`tests/unit/**`)| pure function / service 不连 DB / agent prompt render 校验 / pydantic schema | test-api 每次 push |
| **集成测** | pytest + testcontainers(`tests/integration/**`,标 `@pytest.mark.integration`)| 真 PG + 真 alembic upgrade + service 完整路径(routers / services / models)| test-api 每次 push |
| **评测** | pnpm eval(`evals/suites/**`)| 真 LLM call + dataset.jsonl + Cohen's kappa | manual / M2 后自动(eval.yml) |
| **e2e** | playwright(M3 起)| 浏览器跑用户路径(笔记上传 → 出题 → 答题 → 评分)| manual M3 后 |

测试覆盖率门槛:`pyproject.toml` `fail_under = 70`,触发 `--cov-fail-under=70`。**不追求 90+** — 单测覆盖率假高有,LLM call 路径(agents/)放 70 比较诚实(走集成测 + 评测兜底)。

## 10.1.5 v2 起 JobCopilot 不再写新测试代码 ⭐

**永久约束(STATUS.md `[来自 M1]`)**:v2 阶段所有后续切片**一律不产出 unit / integration / e2e 测试文件**。STATUS.md 列出的测试 TODO 也不主动开工,需求由用户验完显式追加。

- v1 已写好的测试**保留不删**(`tests/unit` / `tests/integration` / `evals/suites/`),CI 跑(`test-api.yml` 沿用)
- 新切片仅产出业务代码 + 评测 dataset(评测 dataset 不算"测试代码",是 prompt 防回归资产)
- 自动化校验(`pytest` / `mypy` / `ruff` / `pnpm typecheck` / `pnpm lint` / `pnpm build` / `playwright` / `curl localhost:* probe` 等)由用户手动跑;AI 助手改完代码**不主动启动**任何自动化校验,只口头描述期望(URL / 操作步骤 / 期望看到的字段或数字),让用户在浏览器或终端自己验

理由:dogfood 单用户体量,真实场景验比 mock unit 更直观;v1 W8 多次出现"测试都过但 dogfood 撞 bug"的反例(LESSONS §8.5 沉淀永久约束前必须跑过对应路径)。

例外:用户明确说"跑闸门 / 跑测试 / 跑 typecheck"等指令时再跑。

## 10.2 prompt 是产品代码(LESSONS §8.2)

- 改 prompt 必须 bump 版本号:`<agent>_v<X.Y.Z>.j2`(`answer_judge_v1.0.0.j2`)
- 旧版**保留**(便于回退 + ablation 跑老 prompt 对比新 prompt)
- 每类 bug 进 `evals/suites/<agent>/dataset.jsonl` 防回归(JDParser v1 历史 26 类 bug → 26 条 fixture,M0 后扩到 v2 5 个 suite)
- prompt 改完跑全套 evals(M2 后自动,M0-M1 期间手动)+ 报告快照存 `evals/reports/<suite>/latest.json`

## 10.3 LLM-as-Judge 可靠性(6-EVAL 详)

- Judge 用 qwen3.6-plus(thinking on),evaluatee 用 qwen3.6-flash(防"评委即被评者"自评高 5-10pp)
- 每季度 50 条人工复核 → Cohen's kappa(`evals/kappa.py`),`κ ≥ 0.7` 才放心用
- κ < 0.7 触发 Judge prompt 改版 + 历史结果重跑(标"Judge v1.0.x 评的,可信度待验证")

# 11. Langfuse 集成实操

详见 2-TECH §6,落地三件事:

## 11.1 SDK 装与 instrument

```python
# apps/api/pyproject.toml
"langfuse>=2.50.0,<3.0.0",   # 锁 <3.0:server v2 不支持 SDK 3.x 的 OTLP 端点
                              # OpenAI wrapper 走 langfuse.openai

# llm/client.py
from langfuse.openai import OpenAI    # 替代 from openai import OpenAI

client = OpenAI(
    api_key=settings.dashscope_api_key,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
```

`langfuse.openai` 自动 instrument **chat / completions / responses** 11 个方法 — input / output / model / tokens / cost / latency 自动收。

**例外:`embeddings.create` 不在 auto-patch 范围**,要在调用处手动建 generation:

```python
from langfuse import Langfuse

generation = Langfuse().generation(
    name="embedder", model=model, input=texts,
    metadata={"dimensions": dim, "batch_size": len(texts)},
)
try:
    resp = await client.embeddings.create(...)
except Exception as e:
    generation.end(level="ERROR", status_message=str(e))
    raise
generation.end(
    output=f"{len(resp.data)} vectors",
    usage={"input": resp.usage.prompt_tokens, "output": 0, "unit": "TOKENS"},
    metadata={"cost_cny": str(cost)},
)
```

参考实现见 `apps/api/src/jobcopilot_api/llm/embedders.py:DashscopeEmbedder._call`。

**Reranker(M2 起)同样不在 auto-patch 范围**(reranker 协议不在 OpenAI 标准里),`services/reranker.py` 调百炼 `qwen3-rerank` 接口要套同款 `Langfuse().generation()` 包成功 / 失败两路径。详见 5-AGENT §2.7.5 + memory `reference_aliyun_dashscope_rerank.md`。

**总结**:`langfuse.openai` auto-patch 只覆盖 chat / completions / responses 共 11 个方法。任何走非这 11 个端点的调用(embeddings / rerank / future:image gen / TTS 等)都要**手动**包 generation,加新调用类型前先确认 langfuse 是否支持自动 instrument,不支持就走手动路径(参考 embedder / reranker 实现)。

**main.py env mirror 必须早于 routers import**:

```python
from jobcopilot_api.settings import settings   # 先 import settings

if settings.langfuse_public_key:                # 再 mirror env
    os.environ.setdefault("LANGFUSE_HOST", ...)
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", ...)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", ...)

from jobcopilot_api.routers import ...          # 最后才 import routers(noqa: E402)
```

否则 `langfuse.openai` 在 import 时读不到 key 进 noop 模式,trace 不进。

## 11.2 trace 装饰器(业务层)

```python
# services / agents 调用入口加 @observe()
from langfuse.decorators import observe, langfuse_context

@observe()
async def submit_session(session_id: int):
    langfuse_context.update_current_trace(
        user_id="local",                      # MVP 单用户
        session_id=str(session_id),
        tags=["quiz", f"session:{session_id}"]
    )
    ...
```

## 11.3 关键约定

- **trace 异步发送**(SDK 内置队列 + 后台 flush),不阻塞主路径;Langfuse 服务挂了不影响产品
- **dev 不走 trace**(`LANGFUSE_PUBLIC_KEY` 留空时 SDK 进 noop 模式) — debug 时不污染主 project
- **Langfuse Project 分**:`jobcopilot-dev` / `jobcopilot-eval`(评测专用,数据流跟 dev 隔离)/ `jobcopilot-prod`(M4+ 才有)

# 12. 文档 / 沉淀纪律

## 12.1 文档清单(`docs/`)

| 文件 | 谁负责更新 | 何时更新 |
|------|----------|--------|
| `1-PRD.md` | 产品 | PRD 变了改 |
| `2-TECH_DESIGN.md` | 架构 | 架构变了改(模块分层 / 数据流 / 选型) |
| `3-DATA_MODEL.md` | 后端 | schema 变了改(配套 alembic revision) |
| `4-API_SPEC.md` | 后端 | endpoint / SSE 事件变了改 |
| `5-AGENT_DESIGN.md` | 后端 | prompt / thinking / tool use 变了改(配套 prompt bump) |
| `6-EVAL_PLAN.md` | 评测 | suite / dataset / kappa 阈值变了改 |
| `7-ROADMAP.md` | 全员 | 里程碑 DoD 调整 / 切片完工 |
| `8-ENGINEERING.md` | 工程 | 工具链 / CI / 部署变了改(本文件) |
| `9-LESSONS.md` | 全员 | 踩坑就追加(标症状 / 根因 / 修法 / 沉淀)|
| `STATUS.md` | 全员 | **每次切片末态 + 永久约束变化** |

## 12.2 永久约束 = 跨切片不可变结论

- 写在 `STATUS.md` "永久约束累积" 区,每条标 `[来自 SX]` / `[来自 MX]`
- LESSONS §8.5:**沉淀永久约束前必须跑过对应代码路径**(尤其 failure / revise 等非 happy path),不要把未验证的设计前提当事实
- 约束发现写错了,标 `[来自 SX / SY 修订]` 记纠错过程(LESSONS §2.3 反面教材)

## 12.3 ADR(架构决策记录)

- v1 期间用过 `docs/adr/0001..0006`(M0 砍除)
- v2 起**只有真正跨里程碑的架构决策**才另立 ADR,下一个编号 `0007`(协作指令已锁)
- 单切片 / 单里程碑内的设计权衡走 STATUS.md "已锁定的关键决策"表,不开 ADR(避免文档膨胀)

## 12.4 不要重新讨论已锁定的决策

STATUS.md "已锁定的关键决策"表 = 不再返工的清单。如果有理由必须改,走"修订流程":新切片明确写 "撤销 [来自 SX] 的 X 条,理由: ...,新结论: ...",不要在背景讨论里悄悄变了。

# 13. 已锁定的关键决策

| 项 | 决策 | 备注 |
|----|------|------|
| monorepo 工具链 | uv workspace(Python)+ pnpm workspace(JS)| 装 apps/api 新依赖必须 `uv sync --all-packages`(LESSONS §7.2) |
| Python lint / format | ruff 单一工具 | 不用 black + isort + flake8 多工具组合(老古董) |
| Python typecheck | mypy strict + pydantic.mypy | 全量 strict;testcontainers / pgvector 个别忽略 |
| JS lint / format | biome 单一工具 | 不用 eslint + prettier 双工具(2024 年 biome 已经覆盖 99% 用例,单工具维护 1 套配置) |
| TS 严格性 | strict + noUncheckedIndexedAccess + noImplicitOverride | 索引必须 narrow;override 必须显式 |
| 锁文件 | uv.lock + pnpm-lock.yaml 提交进库 | CI / Dockerfile 都用 `--frozen` |
| 覆盖率门槛 | `fail_under = 70` | LLM 路径放宽,走集成测 + 评测兜底 |
| Alembic | 单 head + 升序数字 revision id | 分叉手动 merge;v2 schema 切换走 0016 |
| migration 纪律 | model 改必有 revision;集成测跑真 alembic upgrade(不 create_all)| 防 model / migration 漂移 |
| docker compose 服务数 | 6(postgres / api / web / caddy / langfuse / langfuse-db) | 业务 PG 跟 Langfuse PG 分实例 |
| Langfuse 部署 | 自部署不上 LangSmith(数据不出本地) | 详见 2-TECH §6 |
| CI 触发策略 | 6 条独立 workflow + paths 过滤 + concurrency cancel | 改文档不烧 CI |
| eval workflow | M0-M1 manual 触发,M2 数据集到位再放开 push | 防"dataset 不稳定 + 烧钱 + kappa 没达标数字不可信"三件 |
| model-id-lint | grep CI 防 stale 模型 ID 误用 | 永久约束 19(qwen3.6-flash 唯一)的工程化兜底 |
| 模型版本 | qwen3.6-flash 一把抓(文本 + 图像 + tool use) | 简化模型路由(2-TECH §3) |
| LLM SDK | OpenAI Python SDK(via 百炼兼容)+ langfuse.openai 自动 instrument | LLM 调用零额外埋点(**例外**:embeddings 要手动包 generation,见 §11.1)|
| commit 风格 | feat / fix / docs / refactor / chore 前缀英文,描述中文随意 | 不加 Co-Author / 不写 Generated with Claude Code / Generated with Codex |
| tag 策略 | 里程碑末态打 `v0.X-MX-end`,切片不打 | 避免 tag 噪音 |
| 文档 SSoT | docs/ 9 份核心 + STATUS + LESSONS | 永久约束在 STATUS.md;踩坑细节追加 LESSONS.md |
| ADR 阈值 | 跨里程碑架构决策才开 ADR,下一个编号 0007 | 单切片设计走 STATUS 锁定决策表 |
| 测试纪律(v2)| **不写新测试代码**(unit / integration / e2e 都不写);v1 已写测试保留;CI 沿用 | §10.1.5 详述;dogfood 单用户体量,真实场景验比 mock unit 更直观 |
| 自动化校验 | AI 助手改完代码**不主动启动** pytest / mypy / ruff / typecheck / lint / build / playwright / curl probe;只口头描述期望让用户验 | 用户显式说"跑闸门"等指令例外 |
| Reranker langfuse | 同 embedder,**不在 auto-patch 范围**,要手动包 generation | 5-AGENT §2.7.5 + memory `reference_aliyun_dashscope_rerank.md` |

---

# 不在本文档范围

- 模块边界 / 数据流 / 错误分层 → `docs/2-TECH_DESIGN.md`
- 表 schema 字段语义 → `docs/3-DATA_MODEL.md`
- API endpoint / SSE 事件 schema → `docs/4-API_SPEC.md`
- prompt / thinking 矩阵 / tool use 详细 → `docs/5-AGENT_DESIGN.md`
- 评测 suite / Cohen's kappa / dataset 标注 → `docs/6-EVAL_PLAN.md`
- 里程碑 DoD / 下一刀 → `docs/7-ROADMAP.md`
- 工程踩坑细节(每条症状 / 根因 / 修法)→ `docs/9-LESSONS.md`
- 当前阶段进度 / 永久约束清单 → `docs/STATUS.md`
