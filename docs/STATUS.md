---
title: JobCopilot 项目当前进度(单一可信源)
owner: lemma42796
last_updated: 2026-05-03 — M1 进行中,S0.5/S1-S10 已落地;S11 待开工(dogfood + bad case)
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
| S6   | `evals/suites/jd_extract` MVP(13 条 + 4 指标)+ promptfoo CI workflow_dispatch only | ✅ |
| S7   | ProfileParserAgent + `/v1/profiles/parse` SSE + 5 表写入 + 409 dup user / 软删可重建 | ✅ |
| S8   | Chunking 纯函数 + Embedding(text-embedding-v4)+ `/rechunk` + `/chunks` + parse SSE 接 chunk | ✅ |
| S9   | 前端:简历上传(文本+PDF)+ SSE 4 阶段 + detail 表单 + chunks 调试折叠 + delete | ✅ |
| S10  | `evals/suites/profile_extract` 11 条自造 + 4 metric + chunk 召回断言 | ✅ |
| S11  | 1 名志愿者 dogfood + bad case 修复(**Week 3 末 DoD**) | pending |

**当前 working tree**:S10 改动 untracked,待 commit & push。续作前检查:`git status --short && git log origin/main..main --oneline | wc -l`。

**当前闸门**(S10 完成):本地必须跑 CI 等价命令——后端 `uv run --project apps/api {ruff check . / ruff format --check . / mypy apps/api/src apps/api/tests}` + `pytest -q` **315 passed** + `alembic upgrade head` → 0009(未新增 revision);前端 `pnpm install --frozen-lockfile && pnpm lint && pnpm --filter @jobcopilot/web typecheck && pnpm --filter @jobcopilot/schemas typecheck && pnpm --filter @jobcopilot/web build`;evals `pnpm eval:jd` 13 条 case_pass=2/13;**`pnpm eval:profile` 11 条 case_pass=11/11**(schemaValid=1.0 / experienceRecall=1.0 / skillF1=0.988 / chunkRecall=1.0 / 50k tokens / 24s)。

> 2026-05-01 LLM Provider 由 DeepSeek V4 切换到阿里云百炼 Qwen3.6,见 ADR-0003。ADR-0001 复审条件 1(余额 < ¥1)触发时回切。

## 下一刀:S11 dogfood + bad case 修复(M1 末)

**边界**:① 找 1 名志愿者(或自己当志愿者)走端到端流程:粘 JD → 上传简历 → 解析 → 看结构化结果 → 看 chunks;② 收集 ≥ 5 条 high-severity bad case(分类:JD 解析错 / profile 解析错 / chunk 漏召回 / UI 卡点);③ 修可修的(prompt 微调 / bug fix);④ M1 业务 DoD 最后兑现:1 志愿者全流程通 + 日均成本 < ¥1 + 80 条评测达阈(jd 13 + profile 11 当前共 24,差 56 — 推迟 M2)。

**复用**:S5 / S9 全部前端入口已就位;S6 / S10 评测 baseline 已就位。

**不做**:dataset 扩 50 条 / prompt v1.0.2 升级 / 4 新 metric — 全推 M2(STATUS M2 待办累积 14 条,见下文)。

---

## M2 待办(从 S6 暴露 / 推迟)

1. **生产 LLMClient 没设 `max_tokens`** → DashScope 默认值偏低,长 JD 输出截断 JSON。评测侧已用 `max_tokens=2048` 绕过。修 `LLMClient.complete()` + 各 Tier 默认值。
2. **JDParser prompt v1.0.2** 修 baseline 不达阈:① "hard_skills 不抽厂商名/概念名"(修 hardSkillF1=0.67);② "title 抽到第一行末"(修 titleExact=0.769)。
3. **dataset 扩 50 条**(剩 37:OCR 7 / 邮件 8 / 极短 3 / 薪资模糊 2 / 标准中文 17)。
4. **4 新 metric**:`level_acc` / `confidence_calibration` / `latency_p95` / `cost_per_call_cny`。
5. **bad case 表 + promote 脚本 + 月度 triage**(EVAL_PLAN §12)。
6. **跑 3 次取中位数**(EVAL_PLAN §11.3)。
7. **不退化策略**:Δ ≤ -2pp 比对 main baseline。
8. **PR comment 脚本**。
9. **`salaryMonthsAcc` 改自定义聚合**(去掉 want=null 拉高分母的水分)。
10. **`.github/workflows/eval.yml` 启用 push/PR trigger**(取消注释 + 配 GitHub Secret `DASHSCOPE_API_KEY_EVAL`,见 EVAL_PLAN §10.5)。
11. **embedding `DataInspectionFailed` 观察**(S8 规划期暴露)— 跑 30+ profile dataset 时若有命中,需对 chunker 加内容脱敏或 retry-skip 策略。
12. **embedding 写 `llm_calls` 表统一**(S8 规划期暴露)— S8 阶段只 structlog 打印 embedding token + cost;M2 把 schema 通用化(加 `kind` 枚举或拆表)。
13. **前端 JD 列表页 + 全局导航**(S5 起"列表延后"的兑现)— 调现成 `GET /jds`(cursor 分页已就位),提供卡片列表 / 进入详情 / 删除;同时补简历列表入口与首页导航。M2 起匹配场景需"选哪份 JD",列表才真正有产品意义。
14. **profile_extract dataset 扩到 30+**(S10 baseline 10-13 条只覆盖核心场景;M2 补 PDF 简历真实样本 / 多轮跳槽 10+ 年 / 冷门行业 / OCR 噪声等长尾)。

---

# 切片归档(细节见各卡)

- [M0 基础设施](slices/M0-foundation.md) — 选错 postgres 镜像 / Next.js SSR localhost 坑
- [S0.5 卫生债](slices/S0.5-hygiene.md) — coverage 闸门 / structlog / request_id / RFC 7807
- [S1 DB + Alembic](slices/S1-db-alembic.md) — 9 张表 / HNSW / 触发器 / testcontainers
- [S2 LLM 抽象层](slices/S2-llm-client.md) — Tier 路由 / DummyProvider / DBCallLogger
- [S3 Files 上传](slices/S3-files.md) — sha256 去重 / 软删 / 200MB 配额(ADR-0005)
- [S4 JD 解析](slices/S4-jds.md) — JDParserAgent / SSE / prompt_versions(ADR-0006)
- [S5 前端 JD 闭环](slices/S5-jds-frontend.md) — Tailwind v4 + shadcn / `/jds/new` + `/jds/[id]` / X-User-Id
- [S6 评测 baseline + salary_months 全栈](slices/S6-jd_extract.md) — promptfoo + 13 boss / Qwen3.6 多模态 / prompt v1.0.1 / 11 处协同
- [S7 ProfileParser + 5 表写入](slices/S7-profiles.md) — 子表 DELETE+INSERT / skill 去重 / 409 dup / 软删 partial unique 修复
- [S8 Chunking + Embedding + /rechunk](slices/S8-chunks.md) — 5 表 → ChunkInput → 1024 维 / embed 在事务外 / parse SSE 末段 best-effort 接 chunk
- [S9 前端简历闭环 + PartialDate](slices/S9-profile-frontend.md) — `lib/sse.ts` 通用 SSE / `/profiles/new` 双 tab 4 阶段 / `/profiles/[id]` 折叠卡 + 调试 / `_pad_partial_date` 兼容简历缺位日期 / 修 `stats.chunks` 漏算
- [S10 profile 评测 baseline](slices/S10-profile_extract.md) — 11 条自造 case / 4 metric(schemaValid / experienceRecall / skillF1 / chunkRecall)/ JS 端 chunker 复刻 / 子串包含降级版 chunk_recall(M2 升级真 embedding)

---

# M1 规划要点(锁定,不再讨论)

**设计原则**:① 纵切优先,不横切分层 ② Schema 单源真相(DB → ORM → Pydantic → OpenAPI → 前端) ③ LLM 必须可替身(`LLMClient` + `DummyProvider` 是 M1 硬产物) ④ Agent 是纯函数,**不写库**;副作用在 `services/` ⑤ Migration 一条逻辑一条 revision,可 downgrade ⑥ SSE 走进程内 AsyncIterator,M2 切 pgmq 时只换消费者。

**5 开放问题已接受默认**:Q1 PDF 抽取 = `pypdfium2` / Q2 Idempotency-Key M1 跳过 / Q3 `llm_calls` 表 M1 上(S2)/ Q4 图片 JD M1 末再做 / Q5 BYOK 头解析但只读 .env。

**M1 DoD 工程门槛**(3 条 ✅):alembic round-trip 在 CI 跑过、`apps/api` 覆盖率 ≥ 70%(`agents/**` ≥ 80%)、`packages/schemas` 类型从 OpenAPI 自动生成。**业务 DoD**(待):1 志愿者全流程通 / ≥5 high-severity bad case 修 / 日均 < ¥1 / 80 条评测达阈。

---

# 永久约束累积(影响后续切片设计)

1. **prompt_versions 表只有 `template` 单列** [S4]——无 system/user 拆分,SYSTEM/USER 标记由 `infra/prompts.py` 启动时解析;hash = sha256(整文件)。
2. **`ASGITransport` 不跑 lifespan** [S4]——router 集成测试需手动 seed `app.state.prompt_versions`,`get_llm_client` / `get_sessionmaker` 走 `dependency_overrides`。
3. **ORM 只存 `parse_tokens` 总数** [S4]——同步 POST /parse 响应的 `tokens.input/output` 来自 `LLMResult`(由 `create_and_parse` 一并返回)。GET 详情只能给总数。
4. **SSE 起手要 resource_id** [S4]——service 拆 `create_pending_*` + `run_parse`;`started` 带 `resource_id`,失败发 `error → done`,pre-insert 失败直接 `error → done`(无 `started`)。
5. **`X-User-Id` header** [S5]——M1 单用户,前端从 `NEXT_PUBLIC_USER_ID` 读;M5+ 替成 JWT,`current_user_id` 依赖签名不变。前端走统一 `jsonFetch` 注入。
6. **OpenAPI parse endpoint 必须 `responses={201: {"model": ...}}`** [S5]——`response_model=None`(union 返回 Pydantic | EventSourceResponse)会让 OpenAPI 200 是 unknown,前端拿不到自动类型。
7. **Next 15 typedRoutes 字符串拼接需 `as Route`** [S5]——`<Link href={...}>` / `router.push(\`/jds/${id}\`)` 推断为 `string`,要 `import type { Route } from 'next'`。
8. **openapi-typescript `--enum` 必须用 enum value** [S5]——`source: 'text_paste'` typecheck 报错;改 `import { JDParseInputSource } from '@jobcopilot/schemas'; ... source: JDParseInputSource.text_paste`。
9. **Tailwind v4 主题色用 `@theme` 注册** [S5]——`@theme { --color-* }` 自动生成 `bg-* / text-* / border-*` utility,**没有 `tailwind.config.ts`**。
10. **JDStructured 加字段全栈协同 11 处** [S6]——eval schema + Pydantic + Detail + ListItem + ORM + migration + prompt + service + router + 前端 form/detail + 评测 assertions/promptfooconfig。漏一处启动报错或 baseline 数据丢失。详见 `slices/S6-jd_extract.md`。
11. **Parser prompt 升级 promote 4 步** [S6]——① 写 `prompts/<agent>/vX.Y.Z.j2` SYSTEM/USER 双段;② router `PROMPT_KEY` 改新版本;③ 测试 fixture 版本号同步;④ 启动 lifespan 自动 upsert,旧版本保留 history。
12. **DashScope 评测 provider 必须显式关 thinking** [S6]——promptfooconfig 加 `config.passthrough.enable_thinking: false`,否则 qwen3.6-flash 默认 reasoning 拼进 content + 截 max_tokens → schema_invalid。生产走 CHEAP tier(thinking_mode=False),评测对齐。
13. **每用户单例资源用 partial unique index** [S7]——`UNIQUE (user_id) WHERE deleted_at IS NULL`(`uq_profiles_user_id` / `uq_files_user_sha256` 模式)。普通 UNIQUE 会让"软删 → 重建"流程被旧软删行卡住;ORM 层不要写 `UniqueConstraint("user_id")`(语义不匹配)。
14. **`packages/schemas/src/api.ts` 是 commit 进 repo 的生成产物** [S7 CI 修]——不要重新放进 `.gitignore`(`index.ts` 直接 re-export `./api`,gitignored 会让 lint CI 拿不到 typecheck;`type-sync.yml` 只 PR 跑,push CI 不会重生)。后端加/改路由后流程:① 本地 `uv run --project apps/api python -c "import json; from jobcopilot_api.main import app; print(json.dumps(app.openapi(), ensure_ascii=False))" > /tmp/openapi.json` ② `OPENAPI_FILE=/tmp/openapi.json pnpm --filter @jobcopilot/schemas gen` ③ `git add packages/schemas/src/api.ts` 跟代码一起 commit。drift 由 PR 上 `type-sync.yml` 兜底。
15. **SSE 副作用编排在 router 而非 service** [S8]——"X 完成顺带做 Y"(parse 后接 chunk 这种)放在 SSE generator 里串调,不要让 service 的 `run_X` 再吞 Y。理由:① 加返回元素破坏既有 caller(`create_and_parse` 的测试 / JD 路径对称);② best-effort 失败语义(parse 不回滚,只 emit warning event)在 router 才能精细表达;③ service 层失败语义统一 raise,router 层才能区分"硬失败 error → done(ok=false)"vs"软失败 chunking_embedding{ok:false} → result → done(ok=true)"。
16. **embed/IO 在事务外,DB 写在单事务** [S8]——`rebuild_for_profile` 模板:① 读(short tx)② 纯函数 build ③ 慢 IO(无事务)④ DELETE+bulk INSERT(单事务)。绝不 hold PG connection 跨 LLM 调用。后续 retrieval / draft 等需要"读 → LLM → 写"的副作用都套这个分层。
17. **LLM 抽出来的日期可能是 partial(`YYYY` / `YYYY-MM`)** [S9 LLM 实测暴露]——简历原文常写「2020-01 至今」「2016-2020」,Pydantic `date` 不收;统一用 `PartialDate = Annotated[date \| None, BeforeValidator(_pad_partial_date)]`(`schemas/profiles.py`)兜底,缺位补 `01` 落库。**前端按精度截断显示**:简历日期 → `YYYY-MM`(`profile-edit-form.tsx:fmtMonth`)。后续 JD 解析 / 面试反馈 / 简历定制等任何 LLM 抽出来的 date 字段一律用 `PartialDate`,不要直接 `date | None`。
18. **SSE 前端要走 fetch + ReadableStream,不能用 EventSource** [S9]——`EventSource` 不支持自定义 header(`X-User-Id` 没法塞),且只能 GET。统一走 `lib/sse.ts:streamSse<TFrame>()` = fetch + `body.getReader()` + TextDecoder + 手解 SSE 帧(`\n\n` 边界)。M5+ 换成 JWT cookie 后可以重新评估,但 POST + SSE 永远过不了 EventSource。
19. **模型 ID 唯一锁定 `qwen3.6-flash`** [S6 教训,2026-05-03 全仓修齐]——文本/PDF/图片**全走同一个模型**(Qwen3.6 主模型已合并 VL,无需独立 vl 档);embedding 走 `text-embedding-v4`。**禁止再写**:`qwen3.6-vl-flash`(规划名,百炼实际无)/ `qwen3-vl-flash`(Qwen3 时代旧名)/ `qwen-flash` / `qwen-plus` / `qwen-vl-max` 等通用名(不在项目锁定清单)。`lint.yml` 的 `model-id-lint` job 用 grep 兜底防回归;`docs/slices/` 下的历史归档卡作为踩坑记录保留旧名,grep 已 `--exclude-dir=slices`。
20. **生产 UI 文案不暴露切片编号 / 内部命名** [S5/S9 老备注泄露,2026-05-03 修]——`<CardDescription>`、`<p>`、placeholder、错误文案、按钮 label 等**用户可见文本**里不要出现 `S5` / `S9` / `M1` / `第一刀` / `本切片` 等内部记号。开发期的"TODO / 只读 / 暂未支持"等提示,统一用无切片号的措辞(如"当前只读,编辑能力后续切片再开")。真正的 dev hint 走 `console.log` / `data-*` / 注释,绝不进 DOM。**审查时机**:每个前端切片 PR review 时 grep 一次 `\bS[0-9]+\b|第一刀|本切片`;后续可在 `lint.yml` 加 grep job 兜底(类比 19 条的 `model-id-lint`)。

---

# 已锁定的关键决策(不要再讨论)

| 项 | 决策 |
|----|------|
| 目标用户 | 1-3 年跳槽开发者(应届生 v2 再说) |
| 北极星 NSM | 投递前后面试邀约率提升;短期 proxy = 端到端完成率(粘 JD → 下载定制简历) |
| MVP 边界 | JD 入库 + 个人档案 + 匹配 + 简历定制 + 本地部署;面试模拟 P1(Phase 5) |
| 部署 / 仓库 | 本地优先 `docker compose up`;monorepo `apps/api` + `apps/web` + `packages/schemas` |
| LLM Provider | 仅阿里云百炼 Qwen3.6(Flash + Plus,ADR-0003;ADR-0001 已 Superseded) |
| 数据存储 | Postgres 16 一把梭(pgvector + tsvector + pgmq + bytea,ADR-0002) |
| Agent 编排 | LangGraph 仅用于简历定制 + 面试模拟,其他场景单 Agent |

风格规矩(中文为主 / 不估工时 / 不加 Co-Author)见 `CLAUDE.md`。

---

# 文档清单

| 文件 | 用途 |
|------|------|
| `1-PRD.md` / `2-TECH_DESIGN.md` / `3-DATA_MODEL.md` / `4-API_SPEC.md` / `5-AGENT_DESIGN.md` / `6-EVAL_PLAN.md` / `7-ROADMAP.md` / `8-ENGINEERING.md` | 设计文档,**只在写对应代码时按需读相关章节** |
| `slices/` | 已完成切片归档(产出 / 设计决策 / 踩坑) |
| `adr/0001-only-deepseek` (Superseded by 0003) / `0002-postgres-as-vector-db` / `0003-switch-to-qwen` / `0004-llm-client-contract` / `0005-files-upload-contract` / `0006-jd-parse-contract` | 架构决策;下一个编号 0007 |
| `runbook/` | 部署期再写,目前空 |

---

# 上次会话遗留的开放问题(PRD §9)

- **Q-01** 简历 PDF 模板(默认:LaTeX `awesome-cv` 中文化)— M3 启动前决策
- **Q-02** 投递追踪日历提醒(默认:不做)— M4 启动前决策
- **Q-03** MCP Server 工具粒度(默认:5 tool + 1 resource)— M5 启动前决策
- **Q-04** Web demo BYOK 在线试用(默认:做)— M6 启动前决策
