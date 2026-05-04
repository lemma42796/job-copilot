---
title: M1 数据入口贯通 — 里程碑收官总结
status: ✅ 完成
date: 2026-05-04
purpose: M1 的整体经验沉淀 + 跨切片永久约束完整归档 + 给 M2 的输入
---

# 里程碑范围

**M1 = S0.5 + S1...S11**(共 12 个切片)

| 切片 | 内容 | 归档卡 |
|---|---|---|
| S0.5 | 卫生债清理:coverage 闸门 / mypy 加测试 / 前端切自动类型 / structlog + request_id + RFC 7807 | [S0.5-hygiene.md](S0.5-hygiene.md) |
| S1   | DB + Alembic + 通用列/触发器/枚举(5 条 migration,9 张表) | [S1-db-alembic.md](S1-db-alembic.md) |
| S2   | LLM Client + DummyProvider + Tier 路由 + `llm_calls` 表 | [S2-llm-client.md](S2-llm-client.md) |
| S3   | User/File ORM + `/v1/files` 上传(sha256 去重 + 软删 + 200MB 配额) | [S3-files.md](S3-files.md) |
| S4   | JDParserAgent + `/v1/jds/parse` SSE + `/v1/jds` 读改删 + prompt_versions 闭环 | [S4-jds.md](S4-jds.md) |
| S5   | 前端:JD 粘贴页 + 结构化结果可视化 + 编辑保存 | [S5-jds-frontend.md](S5-jds-frontend.md) |
| S6   | `evals/suites/jd_extract` MVP(13 条 + 4 指标)+ promptfoo CI workflow_dispatch only | [S6-jd_extract.md](S6-jd_extract.md) |
| S7   | ProfileParserAgent + `/v1/profiles/parse` SSE + 5 表写入 + 409 dup user / 软删可重建 | [S7-profiles.md](S7-profiles.md) |
| S8   | Chunking 纯函数 + Embedding(text-embedding-v4)+ `/rechunk` + `/chunks` + parse SSE 接 chunk | [S8-chunks.md](S8-chunks.md) |
| S9   | 前端:简历上传 + SSE 4 阶段 + detail 表单 + chunks 调试 + delete | [S9-profile-frontend.md](S9-profile-frontend.md) |
| S10  | `evals/suites/profile_extract` 11 条自造 + 4 metric + chunk 召回断言 | [S10-profile_extract.md](S10-profile_extract.md) |
| S11  | dogfood + bad case 修复(M1 末) | [S11-dogfood-bad-cases.md](S11-dogfood-bad-cases.md) |

# M1 业务 DoD 收尾

| DoD | 状态 |
|---|---|
| 1 志愿者全流程通(粘 JD → 上传简历 → 解析 → 看 chunks) | ✅(S11 自当志愿者实测) |
| ≥ 5 high-severity bad case 修 | ✅(实修 6 条,详见 [S11 归档卡](S11-dogfood-bad-cases.md)) |
| 日均成本 < ¥1 | ✅(单次 profile 解析 ¥0.027,JD 解析 ¥0.01-0.02;单 dogfood 一天几次远在预算内) |
| 80 条评测达阈 | ❌ 24/80,差 56 — **明确推 M2**(dataset 扩 50 / prompt v1.0.2 / 4 新 metric) |

3 / 4 兑现,第 4 条主动推 M2,M1 业务 DoD 实质收口。

# M1 工程 DoD 收尾(锁定)

| DoD | 状态 |
|---|---|
| alembic round-trip 在 CI 跑过 | ✅ `test_migration_round_trip` |
| `apps/api` 覆盖率 ≥ 70%(`agents/**` ≥ 80%) | ✅ |
| `packages/schemas` 类型从 OpenAPI 自动生成 | ✅(`type-sync.yml` PR 兜底 + commit 进 repo) |

# M1 数据底座(给 M2 的输入)

```
9 张表(alembic 0001..0010)
├── users / files                   # 上传 + 用户隔离 + sha256 dedupe + 200MB 配额
├── jds                              # JD 结构化(text/pdf/screenshot 三 source + status + parse_confidence)
├── profiles                         # 简历 parent + structured 嵌套字段
├── profile_experiences              # 工作经历(date partial 兼容 + bullets/achievements/tech_stack JSONB)
├── profile_projects                 # 项目经历(同形 + repo_url/demo_url)
├── profile_skills                   # 技能(category/level/years + UNIQUE(profile_id, name) + evidence_*_ids 索引)
├── profile_educations               # 教育(school/degree/major/gpa/honors)
├── profile_chunks                   # 5 类 chunks(summary/experience/project/skill/education)+ 1024 维 pgvector(HNSW)+ tsvector(GIN)
├── llm_calls                        # 全 LLM 调用日志(feature/tier/tokens/cost/latency/error)
└── prompt_versions                  # prompt 版本闭环(template hash + active_at + 自动 lifespan upsert)
```

**M2 起匹配 / 简历定制可用的检索面**:
- 语义召回:`profile_chunks.embedding` ANN(HNSW cosine)+ JD 向量化 → top-K 经验/项目/技能/教育片段
- 全文召回:`profile_chunks.content_tsv` GIN tsvector(中文 simple 分词,M2 可换 zhparser / pgroonga)
- 结构化匹配:子表字段精确比对(years_required ≤ skill.years / hard_skills ⊆ profile.skills 等)

# M1 整体经验(沉淀,跨切片)

## 设计原则全部兑现

1. **纵切优先,不横切分层**:每个切片端到端(API → service → agent → DB → 前端 / eval),无"大爆炸前置"
2. **Schema 单源真相**:DB → ORM → Pydantic → OpenAPI → 前端 `api.ts` 单向流;后端改 schema 必触发 `pnpm gen` 重生 api.ts
3. **LLM 必须可替身**:`LLMClient + DummyProvider + Tier 路由` M1 硬产物;生产 Qwen3.6 / 评测 promptfoo / 单测 Dummy 三套 provider 平行
4. **Agent 是纯函数,不写库**:副作用全在 `services/`;chunker / parser 都纯函数 (`agents/`)
5. **Migration 一条逻辑一条 revision,可 downgrade**:0001..0010 全部 round-trip 测试通过(0010 借 0005 的 DROP TYPE 兜底是允许例外)
6. **SSE 走进程内 AsyncIterator,M2 切 pgmq 时只换消费者**:parse / rechunk 走同一抽象,M2 替换底层不影响 router

## 5 开放问题最终落地

| Q | 默认 | 实际 |
|---|---|---|
| Q1 PDF 抽取 | `pypdfium2` | ✅ 落地,文本+PDF 双 source |
| Q2 Idempotency-Key | M1 跳过 | ✅ 跳过,M2 起做 |
| Q3 `llm_calls` 表 | M1 上(S2) | ✅ S2 落地;但 embedding **未写入此表**,M2 #11 修 |
| Q4 图片 JD | M1 末再做 | ✅ S6 顺手做(Qwen3.6 多模态合并 vl 档进 flash 主模型) |
| Q5 BYOK | 头解析但只读 .env | ✅ |

## LLM Provider 锁定

**仅阿里云百炼 Qwen3.6**(2026-05-01 ADR-0003 切换)。文本 / PDF / 图片全走 `qwen3.6-flash` 主模型;embedding 走 `text-embedding-v4`。**禁止再写**任何其他模型 ID(grep `lint.yml` 兜底)。

# 永久约束累积(M1 沉淀,影响 M2 起所有切片)

> S11 收官前 STATUS.md 累积 20 条,S11 新加 4 条。下面是完整 24 条,M2 起在 STATUS.md 重新累积新约束。

## 后端 / 数据 / Schema

1. **prompt_versions 表只有 `template` 单列** [S4]——无 system/user 拆分,SYSTEM/USER 标记由 `infra/prompts.py` 启动时解析;hash = sha256(整文件)。

2. **`ASGITransport` 不跑 lifespan** [S4]——router 集成测试需手动 seed `app.state.prompt_versions`,`get_llm_client` / `get_sessionmaker` 走 `dependency_overrides`。

3. **ORM 只存 `parse_tokens` 总数** [S4]——同步 POST /parse 响应的 `tokens.input/output` 来自 `LLMResult`;GET 详情只能给总数。

4. **SSE 起手要 resource_id** [S4]——service 拆 `create_pending_*` + `run_parse`;`started` 带 `resource_id`,失败发 `error → done`,pre-insert 失败直接 `error → done`(无 `started`)。

5. **每用户单例资源用 partial unique index** [S7]——`UNIQUE (user_id) WHERE deleted_at IS NULL`(`uq_profiles_user_id` / `uq_files_user_sha256` 模式)。普通 UNIQUE 会让"软删 → 重建"流程被旧软删行卡住;ORM 层不要写 `UniqueConstraint("user_id")`(语义不匹配)。

6. **SSE 副作用编排在 router 而非 service** [S8]——"X 完成顺带做 Y"(parse 后接 chunk 这种)放在 SSE generator 里串调,不要让 service 的 `run_X` 再吞 Y。理由:① 加返回元素破坏既有 caller 的测试 / 对称性;② best-effort 失败语义(parse 不回滚,只 emit warning event)在 router 才能精细表达;③ service 层失败语义统一 raise,router 层才能区分"硬失败 error → done(ok=false)" vs "软失败 chunking_embedding{ok:false} → result → done(ok=true)"。

7. **embed/IO 在事务外,DB 写在单事务** [S8]——`rebuild_for_profile` 模板:① 读(short tx)② 纯函数 build ③ 慢 IO(无事务)④ DELETE+bulk INSERT(单事务)。绝不 hold PG connection 跨 LLM 调用。后续 retrieval / draft 等需要"读 → LLM → 写"的副作用都套这个分层。

8. **LLM 抽出来的日期可能是 partial(`YYYY` / `YYYY-MM`)** [S9]——`PartialDate = Annotated[date | None, BeforeValidator(_pad_partial_date)]` 兜底,缺位补 `01` 落库。前端按精度截断显示(`YYYY-MM`)。后续 JD 解析 / 面试反馈 / 简历定制等任何 LLM 抽出来的 date 字段一律用 `PartialDate`。

9. **PG ENUM 加值的三层同步约束** [S11]——`schemas/*.py` Pydantic Literal + `models/*.py` `postgresql.ENUM(...)` ORM 包装 + `alembic/versions/*.py` PG type 三处必须同步。漏一处:Pydantic 校验通过但 ORM SELECT 回来时撞 LookupError("...not among the defined enum values")。

10. **PG ENUM ADD VALUE 用 `IF NOT EXISTS` + downgrade noop** [S11]——PG 16 支持事务内 ADD VALUE,但**不支持 REMOVE VALUE**。downgrade 走 noop,依赖原始创建 enum 那条 migration 的 downgrade `DROP TYPE` 兜底:round-trip(head → base → head)第二次 upgrade 时原 migration 重建 + 本 migration 重 ADD VALUE,正确。Alembic round-trip 测试 (`test_migration_round_trip`) 不会 fail。

11. **5 表 chunker 全表覆盖** [S11]——`build_chunks` 形参强制接全 5 表(`profile + experiences + projects + skills + educations`),不要让 caller 选择性传(漏一个就漏切一类 chunk)。`chunk_service.rebuild_for_profile` 已经预读了 5 子表,签名强制传齐避免"接口预留但漏接通"陷阱(S11 dogfood 实证,设计期变量 `_edus` 留下一年)。

## 评测 / Prompt

12. **JDStructured 加字段全栈协同 11 处** [S6]——eval schema + Pydantic + Detail + ListItem + ORM + migration + prompt + service + router + 前端 form/detail + 评测 assertions/promptfooconfig。漏一处启动报错或 baseline 数据丢失。

13. **Parser prompt 升级 promote 4 步** [S6]——① 写 `prompts/<agent>/vX.Y.Z.j2` SYSTEM/USER 双段;② router `PROMPT_KEY` 改新版本;③ 测试 fixture 版本号同步;④ 启动 lifespan 自动 upsert,旧版本保留 history。

14. **DashScope 评测 provider 必须显式关 thinking** [S6]——promptfooconfig 加 `config.passthrough.enable_thinking: false`,否则 qwen3.6-flash 默认 reasoning 拼进 content + 截 max_tokens → schema_invalid。生产走 CHEAP tier(thinking_mode=False),评测对齐。

15. **模型 ID 唯一锁定 `qwen3.6-flash`** [S6,2026-05-03 全仓修齐]——文本/PDF/图片全走同一个模型;embedding 走 `text-embedding-v4`。`lint.yml` 的 `model-id-lint` job 用 grep 兜底防回归。

## 前端

16. **`X-User-Id` header** [S5]——M1 单用户,前端从 `NEXT_PUBLIC_USER_ID` 读;M5+ 替成 JWT。前端走统一 `jsonFetch` 注入。

17. **OpenAPI parse endpoint 必须 `responses={201: {"model": ...}}`** [S5]——`response_model=None` 会让 OpenAPI 200 是 unknown,前端拿不到自动类型。

18. **Next 15 typedRoutes 字符串拼接需 `as Route`** [S5]——`<Link href={...}>` / `router.push(\`/jds/${id}\`)` 推断为 `string`,要 `import type { Route } from 'next'`。

19. **openapi-typescript `--enum` 必须用 enum value** [S5]——`source: 'text_paste'` typecheck 报错;改 `import { JDParseInputSource } from '@jobcopilot/schemas'; ... source: JDParseInputSource.text_paste`。

20. **Tailwind v4 主题色用 `@theme` 注册** [S5]——`@theme { --color-* }` 自动生成 `bg-* / text-* / border-*` utility,**没有 `tailwind.config.ts`**。

21. **SSE 前端要走 fetch + ReadableStream,不能用 EventSource** [S9]——`EventSource` 不支持自定义 header(`X-User-Id` 没法塞),且只能 GET。统一走 `lib/sse.ts:streamSse<TFrame>()`。

22. **生产 UI 文案不暴露切片编号 / 内部命名** [S5/S9,2026-05-03 修]——`<CardDescription>`、`<p>`、placeholder、错误文案、按钮 label 等用户可见文本里不出现 `S5` / `M1` / `第一刀` / `本切片` 等内部记号。审查时机:每个前端切片 PR review 时 grep `\bS[0-9]+\b|第一刀|本切片`。

23. **嵌套 flex-wrap 不可预期,优先 CSS Grid** [S11]——"label 列 + 内容流式 wrap"这种 layout,首选 `grid grid-cols-[Wrem_1fr]` + 内层单层 `flex flex-wrap`,而非外 `flex flex-wrap` 嵌内 `flex flex-1 flex-wrap`(子元素排列不可预期,某些场景 chip 会各占一行)。

24. **前端 stats 与 list 数字必须同口径** [S11]——detail 页若 stats 数字与 list 渲染数字不一致(如 dedup 前 vs 后),user 必然困惑;两边都基于同一 helper 算。`detail.stats.*` 暴露 DB 真实数,UI 走 dedup 后数,**不混用**。

## CI / 元

25. **`packages/schemas/src/api.ts` 是 commit 进 repo 的生成产物** [S7 CI 修]——后端加/改路由后流程:① 本地 dump openapi.json ② `OPENAPI_FILE=... pnpm gen` ③ `git add api.ts` 跟代码一起 commit。drift 由 `type-sync.yml` PR 兜底。

(共 25 条,S11 收官时整理。M2 起新约束在 STATUS.md 重新累积。)

# 已锁定的关键决策(贯穿 M1+,不再讨论)

| 项 | 决策 |
|----|------|
| 目标用户 | 1-3 年跳槽开发者(应届生 v2 再说) |
| 北极星 NSM | 投递前后面试邀约率提升;短期 proxy = 端到端完成率(粘 JD → 下载定制简历) |
| MVP 边界 | JD 入库 + 个人档案 + 匹配 + 简历定制 + 本地部署;面试模拟 P1(Phase 5) |
| 部署 / 仓库 | 本地优先 `docker compose up`;monorepo `apps/api` + `apps/web` + `packages/schemas` |
| LLM Provider | 仅阿里云百炼 Qwen3.6(Flash + Plus,ADR-0003) |
| 数据存储 | Postgres 16 一把梭(pgvector + tsvector + pgmq + bytea,ADR-0002) |
| Agent 编排 | LangGraph 仅用于简历定制 + 面试模拟,其他场景单 Agent |

# 给 M2 的明确遗留(待办累积,搬到 STATUS M2 视图)

详见 STATUS.md "M2 待办累积"。M1 期间从评测 / dogfood / 设计审视 中暴露的待办(S11 收官前 13 条 + S11 期间新加几条):

1. JDParser prompt v1.0.2 修 baseline 不达阈
2. dataset 扩 50 条
3. 4 新 metric(level_acc / confidence_calibration / latency_p95 / cost_per_call_cny)
4. bad case 表 + promote 脚本 + 月度 triage
5. 跑 3 次取中位数
6. 不退化策略 Δ ≤ -2pp
7. PR comment 脚本
8. salaryMonthsAcc 改自定义聚合
9. eval.yml 启用 push/PR trigger
10. embedding `DataInspectionFailed` 观察
11. embedding 写 `llm_calls` 表统一
12. 前端 JD 列表页 + 全局导航(顺带 parse_failed UX 一键删除并重传)
13. profile_extract dataset 扩到 30+(从 S11 dogfood 真实简历沉淀)
14. profile_parser prompt v1.0.2(技能切分一致性 / partial-year date / tech_stack 非空泛 / 证书章节 schema 扩展)

# 不在本文档范围

- M2 切片规划 / 优先级 → `STATUS.md` "下一刀" 区
- ROADMAP M3+ 蓝图 → `7-ROADMAP.md`
- 各切片实施细节 → `slices/SX-*.md`
