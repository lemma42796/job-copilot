---
title: JobCopilot 项目当前进度(单一可信源)
owner: lemma42796
last_updated: 2026-05-02 (M1 进行中:S0.5/S1/S2/S3/S4 已 push;S5 本地完成未 push;S6 脚手架完成 dataset 2/15 种子,等用户补 13 条 Boss 截图 + 配 GitHub secret;下一刀:用户截 3 张 → from-screenshot 跑通)
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
| S4   | JDParserAgent(文本 + PDF)+ `/v1/jds/parse` SSE + `/v1/jds` 读改删 + prompt_versions 闭环 | ✅ |
| S5   | 前端:JD 粘贴页 + 结构化结果可视化 + 编辑保存(同步,SSE / 列表延后) | ✅ |
| S6   | `evals/suites/jd_extract` MVP(15 条 + 3 指标 title/skill_f1/salary)+ promptfoo CI(**Week 2 末 DoD**;50 条全量 / 8 指标 / bad case promote 推 M2) | 进行中 |
| S7   | ProfileParserAgent + `/v1/profiles/parse` SSE | pending |
| S8   | Chunking 纯函数 + Embedding(text-embedding-v3)+ `/rechunk` | pending |
| S9   | 前端:简历上传 + 表单 + chunks 可视化(调试) | pending |
| S10  | `evals/suites/profile_extract` 30 条 + chunk 召回断言 | pending |
| S11  | 1 名志愿者 dogfood + bad case 修复(**Week 3 末 DoD**) | pending |

## 当前 working tree 状态

**本地领先 origin/main**(S5 + S6 脚手架未 push)。检查领先量:`git log origin/main..main --oneline | wc -l`。

## S6 下一刀(MVP 推进步骤)

脚手架已就绪(`evals/` workspace + `.github/workflows/eval.yml`),dataset 当前 2/15(全合成种子)。

剩余:
1. **你截 3 张 Boss JD 图** → `evals/raw/boss/*.png`(.gitignore,不入仓库)
2. `pnpm --filter @jobcopilot/evals run prep:screenshot evals/raw/boss/*.png` → 输出 3 行候选 JSONL 到 stdout
3. **人工核对** 3 行(脱敏公司名 → `[CompanyA]/[CompanyB]/[CompanyC]`,改错的 ground truth 字段),追加到 `evals/suites/jd_extract/dataset.jsonl`
4. 删掉 dataset.jsonl 头 2 条合成种子,本地跑 `pnpm eval:jd` 验证 3 条全过(LOCAL `DASHSCOPE_API_KEY_EVAL` 必填)
5. GitHub Settings → Secrets → New `DASHSCOPE_API_KEY_EVAL`(独立 Key,与生产分开,见 EVAL_PLAN §10.5)
6. push 前再补 12 张图重复 2-3 步 → 凑 15 条
7. push → CI 触发 → 全绿即 S6 完成

下方 6 个 M2 待办**不**进 S6:
- 50 条全量(剩 35 条:OCR 7 / 邮件 8 / 极短 3 / 薪资模糊 2 / 标准中文 15)
- `level_acc` / `confidence_calibration` / `latency_p95` / `cost_per_call_cny` 4 个指标
- bad case 表 + promote 脚本 + 月度 triage(EVAL_PLAN §12)
- 跑 3 次取中位数(EVAL_PLAN §11.3)
- 不退化策略(Δ ≤ -2pp 比对 main baseline)
- PR comment 脚本

## 当前闸门(S5 完成)

后端:
- `ruff check` / `ruff format --check`:全绿
- `mypy --strict apps/api/src apps/api/tests`:74 files,0 issues
- `pytest --cov --cov-fail-under=70`:**200 passed,93.61%**

前端:
- `pnpm --filter @jobcopilot/web typecheck`:0 errors
- `pnpm --filter @jobcopilot/web lint`(biome):0 errors
- `pnpm --filter @jobcopilot/web build`(Next 15 + Tailwind v4 + typedRoutes):✓ 4 路由

## 当前 docker compose 状态

S1 期间手动起了 postgres 单容器开发(`docker compose up -d postgres`),停机时**未 down**。下次开工前可以选择继续用它,或 `docker compose down -v` 后重启重置数据。**Alembic 已经把 0001-0007 应用到该容器,不重置可以省去一次迁移**。集成测试用 testcontainers 起独立容器,与开发容器无关,无需停。

> 2026-05-01 决策变更:LLM Provider 由 DeepSeek V4 切换为阿里云百炼 Qwen3.6,理由是消耗剩余 ¥15 赠款。详见 ADR-0003。**ADR-0001 复审条件 1(余额 < ¥1)触发时自动回切。**

---

# 切片归档(已完成,详细产出 / 设计决策 / 踩坑见各卡)

- [M0 基础设施](slices/M0-foundation.md) — 选错 postgres 镜像 / Next.js SSR localhost 坑
- [S0.5 卫生债](slices/S0.5-hygiene.md) — coverage 闸门 / structlog / request_id / RFC 7807
- [S1 DB + Alembic](slices/S1-db-alembic.md) — 9 张表 / HNSW / 触发器 / testcontainers
- [S2 LLM 抽象层](slices/S2-llm-client.md) — Tier 路由 / DummyProvider / DBCallLogger
- [S3 Files 上传](slices/S3-files.md) — sha256 去重 / 软删 / 200MB 配额(见 ADR-0005)
- [S4 JD 解析](slices/S4-jds.md) — JDParserAgent / SSE / prompt_versions(见 ADR-0006)
- [S5 前端 JD 闭环](slices/S5-jds-frontend.md) — Tailwind v4 + shadcn / `/jds/new` + `/jds/[id]` / X-User-Id header

---

# M1 规划要点(锁定,不再讨论)

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

# 永久约束累积(影响后续切片设计,跨切片才记)

1. **prompt_versions 表只有 `template` 单列** [来自 S4]——无 system/user 拆分,`.j2` 整文件存进 `template`,SYSTEM/USER 标记由 `infra/prompts.py` 启动时解析;hash = sha256(整文件)。后续新增 Agent 沿用此约定,不要拆列。
2. **`ASGITransport` 不跑 lifespan** [来自 S4]——router 集成测试需要在 fixture 里手动 `app.state.prompt_versions = {(...): LoadedPrompt(...)}`;`get_llm_client` / `get_sessionmaker` 走 `dependency_overrides`。S7 ProfileParser router 测试同样遵循。
3. **JD ORM 只存 `parse_tokens` 总数** [来自 S4]——无 input/output 拆分;同步 POST /parse 响应的 `tokens.input/output` 来自 `LLMResult`(由 `create_and_parse` 一并返回 `(Jd, LLMResult)`)。GET 详情只能给总数。
4. **SSE 起手要 resource_id** [来自 S4]——Phase 1 与 Phase 2 在 service 层拆成 `create_pending_*` + `run_parse`,`started` 事件带 `resource_id`,失败发 `error → done`,pre-insert 失败(text 过短 / PDF 烂)直接发 `error → done`(无 `started`)。
5. **`X-User-Id` header** [来自 S5]——M1 单用户,前端从 `NEXT_PUBLIC_USER_ID` env 读默认 '1';后端 `current_user_id` 依赖只读 header。M5+ 替换成 JWT,所有 router 不变。前端所有 API 调用走统一 `jsonFetch` helper 注入。
6. **OpenAPI parse endpoint 必须 `responses={201: {"model": ...}}`** [来自 S5]——`response_model=None`(因为 union 返回 `Pydantic | EventSourceResponse`)会导致 OpenAPI dump 里 200 response 是 `unknown`,前端拿不到自动类型,违反 S0.5 DoD"前端无手写 API 类型"。S7 ProfileParser parse endpoint 同结构同样要写。
7. **typedRoutes 字符串拼接需要 `as Route`** [来自 S5]——Next 15 `experimental.typedRoutes` 启用,`<Link href={...}>` / `router.push(`/jds/${id}`)` 字符串模板被推断为 `string`,需要 `import type { Route } from 'next'; ... as Route`。S9 简历列表跳详情同样。
8. **openapi-typescript `--enum` 必须用 enum value** [来自 S5]——`scripts/generate.mjs` 用了 `--enum`,生成的是 TypeScript enum 类型而非 string literal union。前端 `source: 'text_paste'` typecheck 会报错,必须 `import { JDParseInputSource } from '@jobcopilot/schemas'; ... source: JDParseInputSource.text_paste`。
9. **Tailwind v4 主题色用 `@theme` 注册** [来自 S5]——`@theme { --color-* }` 自动生成 `bg-* / text-* / border-*` utility(`bg-accent`、`text-muted`、`border-border` 等);**没有 `tailwind.config.ts`**(v4 默认全文件扫)。S9 前端表单沿用此 token 命名。

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

# 文档清单与状态

```
docs/
├── STATUS.md                    ← 你正在读
├── slices/                      ← 已完成切片归档
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
│   ├── 0001-only-deepseek.md          ✅ Superseded by 0003
│   ├── 0002-postgres-as-vector-db.md  ✅
│   ├── 0003-switch-to-qwen.md         ✅
│   ├── 0004-llm-client-contract.md    ✅(S2 规划锁)
│   ├── 0005-files-upload-contract.md  ✅(S3 规划锁)
│   └── 0006-jd-parse-contract.md      ✅(S4 规划锁)
└── runbook/                     (空,部署期再写)
```

---

# 上次会话遗留的开放问题(PRD §9)

在对应里程碑启动前再决策,不阻塞当前切片:

- Q-01:简历 PDF 模板用现成开源还是自研?(默认:LaTeX `awesome-cv` 中文化)— M3 启动前决策
- Q-02:投递追踪看板要不要做日历提醒?(默认:不做)— M4 启动前决策
- Q-03:MCP Server 暴露的工具粒度?(默认:5 tool + 1 resource)— M5 启动前决策
- Q-04:Web demo 站要不要支持 BYOK 在线试用?(默认:做)— M6 启动前决策
