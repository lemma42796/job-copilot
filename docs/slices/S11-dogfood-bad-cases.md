---
title: S11 dogfood + bad case 修复 — 切片归档(M1 末)
status: ✅ 完成
date: 2026-05-04
purpose: 自当志愿者跑端到端流程(粘 JD → 上传简历 → 解析 → 看 chunks),收 high-severity bad case 并修可修的;M1 业务 DoD 收尾
---

# 产出

```
apps/api/src/jobcopilot_api/llm/
├── client.py                    # +max_tokens 透传(LLMRequest / ProviderRequest)
├── tiers.py                     # +TierConfig.default_max_tokens(8192/8192/16384)
└── providers/dashscope.py       # payload +max_tokens

apps/api/src/jobcopilot_api/services/
├── jd_service.py                # create_pending_jd 加 user 校验 + run_parse 透 schema error detail
└── profile_service.py           # 同上对称

apps/api/src/jobcopilot_api/
├── schemas/profiles.py          # ChunkGranularity 加 "education"
├── models/profile.py            # CHUNK_GRANULARITY_ENUM 加 "education"
├── agents/chunker.py            # +build_education_chunk + build_chunks 接 educations 形参
└── services/chunk_service.py    # rebuild_for_profile 传 educations 给 build_chunks

apps/api/alembic/versions/
└── 0010_chunk_granularity_education.py  # 新 — ALTER TYPE ADD VALUE 'education'(downgrade noop)

apps/api/tests/
├── unit/test_chunker.py                 # +build_education_chunk 三个测试 + orchestrator 测试加 educations 形参
├── unit/test_profile_schemas.py         # 5 granularities + invalid 改成 totally_bogus
├── unit/test_llm_client.py              # +max_tokens 透传链路测试
├── integration/test_profile_service.py  # +user 不存在分支测试
├── integration/test_profile_chunks_orm.py  # invalid value 改 totally_bogus
└── integration/test_profiles_router.py  # 3 处 chunks 期望 3→4,grans 加 'education'

apps/web/src/app/profiles/[id]/
└── profile-edit-form.tsx        # SkillsSection(grid 2 列 + chip + dedup)+ StatsRow 用 dedup 数

packages/schemas/src/api.ts      # 重新 gen,ProfileChunkItemGranularity enum 加 education

CLAUDE.md                         # 行为约束 +"所有测试由用户手动跑"
docs/STATUS.md                    # 折叠 M1 / 开 M2 切片表
docs/slices/M1-summary.md         # 新 — M1 收官
docs/slices/S11-dogfood-bad-cases.md   # 本卡
```

# 设计决策(实现细节)

## 1. SSE 起手 user 校验(bad case #1)

**现场**:用户传不存在的 `user_id`,`profiles` INSERT 撞 `fk_profiles_user_id` IntegrityError 在 SSE generator 起手抛,前端拿到 0 字节 close。

**修法**:`create_pending_jd` / `create_pending_profile` 入门先 `SELECT 1 FROM users WHERE id = ?`,不存在 → `NotFoundError` → SSE `error → done` 显式回报。两个 service 对称改。

## 2. LLM schema error 透 Pydantic 路径(bad case #2)

`LLMSchemaInvalidError` 之前只回 "JSON 不符合 schema",dogfood 时无法定位是哪个字段;现在把异常字符串前 500 字符透到 SSE `error.detail`。截断保护 SSE payload 尺寸。

## 3. max_tokens 透传 + Tier 默认 8192/8192/16384(bad case #3,顺手兑 M2 #1)

**实证根因**:profile id=10/12 同一份 727 字节"张三"标准简历,反复 `schema_invalid`;查 `llm_calls` 表,`tokens_out` = 4030 / 3909,贴在 DashScope 默认 4096 边界 → JSON 被截断尾部 → Pydantic 校验失败。

**修法**:`TierConfig` 加 `default_max_tokens` 字段,CHEAP/STANDARD=8192(简历常态)、PREMIUM=16384(预留定制简历 / 长文档);`LLMRequest` / `ProviderRequest` / `BaseLLMClient.complete()` 全链路透传;dashscope provider payload 加 `max_tokens` 字段。

## 4. chunker 漏 education,5 表设计实际只切 4 表(bad case #4)

**现场**:profile 13 简历有 1 条教育(同济大学 软件工程 本科),DB 子表写入正确,但 `profile_chunks` 0 条 education。M2 匹配 JD 教育要求("本科及以上"/"计算机相关")完全召回不到。

**根因**:`agents/chunker.py` 设计期就只写了 summary / experience / project / skill 4 个 builder;`services/chunk_service.py:rebuild_for_profile` 已经预读了 `educations`(变量名 `_edus`),但**没传给** `build_chunks`,接口预留了但漏接通。`test_profiles_router.py` 注释还白纸黑字写"1 edu skipped",成了"已知设计 bug"。

**修法**:加 `build_education_chunk`(school / degree / major / 时间 / GPA / honors),`build_chunks` 形参加 `educations: list[ProfileEducation]`,`chunk_service` 传齐;`schemas/profiles.py` Literal + `models/profile.py` postgresql.ENUM + alembic 0010 三层加值。

## 5. 前端技能区按分类横向 chip + dedup(bad case #5/#6)

**现场**:profile 13 detail 页技能区每个 skill 一个 `<li>` 垂直堆,Java/Go/Python 一个一行;且 "Prometheus + Grafana" / "OpenAI / Anthropic API" **重复展示两次**(top 35,user 数下面只有 33,stats 与 list 数对不上)。

**根因**:LLM 把组合工具拆成 2 个 skill(name=prometheus + name=grafana,但 name_raw 都填整段原文),DB UNIQUE(profile_id, name) 不去重 — 因为 name 不同。前端 `<ListSection>` 通用组件按 name 当 key 渲染所有行,name_raw 重复就重复展示。

**修法**:
- 替换 ListSection 为自定义 `SkillsSection`(CSS Grid 2 列:label 5rem + chips 1fr,内层 `flex flex-wrap`)
- chip = `<span className="rounded border bg-input/40 px-2 py-0.5 whitespace-nowrap">name_raw  level · years</span>`
- dedup helper:`dedupSkillsByDisplay(skills)` 按 `(name_raw.toLowerCase(), category)` Map 去重
- 分类顺序硬编码:language → framework → database → tool → cloud → other
- 中文 label:编程语言 / 框架 / 数据库 / 工具 / 云原生 / 其他
- StatsRow 同口径:技能数走 `dedupSkillsByDisplay(...).length` 而非 `detail.stats.skills`(stats 列保 DB 真实数会与 list 对不上)

**没碰**:LLM prompt 切分一致性问题 ──"`/`、`+` 处理无规则"是 prompt 任务(M2 prompt v1.0.2);DB layer / API layer 不动,前端兜底足够。

## 6. parse_failed UX 锁死路径调研后 **降级 P2 推 M2**

调研发现:`/profiles/new` 页错误处理代码已有"查看 / 删除现有简历"按钮(`new/page.tsx:98-110, 263-273`),user 能 4 步绕过(粘 → 报错 → 跳详情 → 删 → 回 → 重粘)。**不是死锁,只是绕路 + 草稿丢失**。M2 列表页(M2 #12)落地后体感会好;真要做"一键删除并重传"放 M2。

## 7. 占位符简历 full_name 空 — 撤销不算 bad case

profile id=7 用 `[Candidate]` `[Company1]` 等占位符模板,LLM 正确把这些识别为占位符 → full_name 落空。前端 detail 页 `<CardTitle>` 显示 "简历 #{id}" 不依赖 full_name,**input 框为空合理**,user 自己手填即可。LLM 行为正确,不修。

# Baseline(自当志愿者 dogfood)

走过的简历 raw_text(profiles 表实测):

| 简历 | 长度 | 解析结果 | 抽取质量 |
|---|---|---|---|
| 张三(标准模板) | 727B | 修 #3 前 schema_invalid;修后未重测 | — |
| 李航(饱满 4461B,3y → 5y 跳槽) | 4461B | parsed,cost ¥0.0272 | 工作 2 / 项目 3 / 技能 35(去重 33)/ 教育 1,核心字段全对 |

李航简历 LLM 抽取质量观察:
- ✅ name / phone / email / location 全对
- ✅ work + project bullets / tech_stack / 量化成就完整保留
- ✅ summary 自动浓缩(原文没字段,LLM 主动写)
- ✅ partial date 兜底正确(`2024.03` → `2024-03-01` / `2018.09` → `2018-09-01`)
- ✅ honors 识别("ACM 校赛银奖" / "Google Hash Code Top 5%")
- ⚠️ 技能切分一致性(`/`、`+` 拆得不规律)— 推 M2 prompt v1.0.2
- ⚠️ "实时多人协作白板" 项目时间 `2022-01 ~ 2022-01`(原文 "2022")— 推 M2 prompt 处理 partial-year date
- ⚠️ tech_stack 含 "jdk"(空泛非具体技术)— 推 M2 prompt
- ⚠️ 证书段落(AWS Solutions Architect / 阿里云 ACA)被 LLM 直接扔(schema 没 certifications 字段)— 推 M2 schema 扩展

# M1 业务 DoD 兑现检查

| DoD | 状态 |
|---|---|
| 1 志愿者全流程通(粘 JD → 上传简历 → 解析 → 看 chunks) | ✅(自当志愿者) |
| ≥ 5 high-severity bad case 修 | ✅(实修 6 条 — SSE 起手静默 / schema error 不可调试 / max_tokens 截断 / chunker 漏 education / skills 重复展示 / stats 与 list 不一致) |
| 日均成本 < ¥1 | ✅(profile 13 单次 ¥0.0272;dogfood 一天几次完全在预算内) |
| 80 条评测达阈 | ❌ 24/80,差 56 — **明确推 M2**(dataset 扩 + prompt v1.0.2 + 4 新 metric) |

3 / 4 兑现,第 4 条主动放下,**M1 业务 DoD 实质收口**。

# 期间踩到的小坑

1. **dogfood 期间 docker image 是 5-01 build 的旧版**,`POST /v1/profiles/parse` 直接 404(image 在 S7 路由加之前 build)。临时方案:`docker compose stop api web` + `uv run uvicorn ... --reload` + `pnpm web dev` 走 dev mode,代码改动热重载,贴近真实 dogfood 节奏。

2. **PG ENUM 加值的三层同步**:`schemas/profiles.py` Literal + `alembic/versions/0010_*.py` ALTER TYPE + `models/profile.py` `postgresql.ENUM(...)` ORM 包装。漏一处:Pydantic 校验通过但 SQLAlchemy ORM 把 'education' 行 SELECT 回来时撞 LookupError(ORM enum 缓存了旧 4 值)。**这条入永久约束**(M1-summary)。

3. **PG ENUM downgrade**:`ALTER TYPE ... ADD VALUE` 在 PG 16 支持事务内执行,但 PG 不支持 REMOVE VALUE。downgrade 走 noop,依赖 0005(创建 enum 那条 migration)的 downgrade `DROP TYPE` 兜底:round-trip(head → base → head)第二次 upgrade 时 0005 重新创建 4 值 enum + 0010 ADD VALUE 加回 'education',全程正确。**这条入永久约束**(M1-summary)。

4. **测试 fixture 借用"未来非法值"作为反例**:`test_chunk_item_rejects_unknown_granularity` 用 `"education"` 当 invalid value 写测试(写测试时 education 不在枚举),加 'education' 合法后必须换 `totally_bogus` 之类真无效值。`test_chunk_granularity_enum_rejects_unknown_values`(integration 层)也是同模式。两处一起改。

5. **3 个 router 集成测试 hardcode chunks 数 = 3**(`test_parse_sse_success` / `test_rechunk_sse` / `test_get_chunks_returns_rows_without_embedding_payload`),fixture GOLDEN 有 1 个 education,加 build_education_chunk 后变 4。注释明确写"1 edu skipped"是设计期就**故意跳过**的,这次反向重新连通。

6. **嵌套 flex-wrap 不可预期**:第一版 SkillsSection 写了外层 `flex flex-wrap items-baseline gap-x-3 gap-y-2` + 内层 `flex flex-1 flex-wrap gap-2`,实际渲染 chip 各占一行(浏览器复制粘贴看不出 layout,但 user 反馈"还是垂直")。换成 CSS Grid 2 列(`grid grid-cols-[5rem_1fr]`)+ 内层单层 `flex flex-wrap`,稳定横排。**这条入永久约束**(M1-summary)。

7. **chunk_service 已经预读 educations 但没用**(`exps, projs, skills, _edus = await get_children(...)`,`_` 前缀表示"故意忽略")— 这是个**有意识的 dead variable**,不是单纯的漏。设计期可能临时跳过教育切分,留下隐式 todo,后期忘了。**这条入永久约束**(M1-summary):"5 表 chunker 全表覆盖" 强制 build_chunks 形参齐 5 表,signature 强制 caller 传齐。

8. **stats vs list 数字一致性**:首版只 dedup 渲染层,stats.skills 仍是 DB 真实数 35,user 数下面只有 33,立即反馈"技能不是 33 个吗"。修成 stats 同口径用 `dedupSkillsByDisplay(...).length` 后一致。**这条入永久约束**(M1-summary)。

# 不做的(明确推 M2)

- LLM 切分一致性(`/`、`+` 处理)— prompt v1.0.2
- partial-year project end_date 兜底(2022 → 2022-12 而非 2022-01)— prompt v1.0.2
- tech_stack 抽取空泛("jdk")— prompt v1.0.2
- 简历证书章节 schema 扩展(certifications)— schema v2
- parse_failed UX 一键删除并重传 — 列表页 M2 #12 配套
- bad case dataset 扩 30+(从这次 dogfood 真实简历沉淀样本)— M2 #13

# 文件变更清单

```
M  apps/api/src/jobcopilot_api/llm/client.py
M  apps/api/src/jobcopilot_api/llm/tiers.py
M  apps/api/src/jobcopilot_api/llm/providers/dashscope.py
M  apps/api/src/jobcopilot_api/services/jd_service.py
M  apps/api/src/jobcopilot_api/services/profile_service.py
M  apps/api/src/jobcopilot_api/schemas/profiles.py
M  apps/api/src/jobcopilot_api/models/profile.py
M  apps/api/src/jobcopilot_api/agents/chunker.py
M  apps/api/src/jobcopilot_api/services/chunk_service.py
A  apps/api/alembic/versions/0010_chunk_granularity_education.py
M  apps/api/tests/unit/test_chunker.py
M  apps/api/tests/unit/test_profile_schemas.py
M  apps/api/tests/unit/test_llm_client.py
M  apps/api/tests/integration/test_profile_chunks_orm.py
M  apps/api/tests/integration/test_profile_service.py
M  apps/api/tests/integration/test_profiles_router.py
M  apps/web/src/app/profiles/[id]/profile-edit-form.tsx
M  packages/schemas/src/api.ts
M  CLAUDE.md
A  docs/slices/S11-dogfood-bad-cases.md
A  docs/slices/M1-summary.md
M  docs/STATUS.md
```

闸门(收尾跑过):后端 `pytest -q` **321 passed**(+3 vs S10:llm_client max_tokens / profile_service user 不存在 / chunker education 三组测试)/ ruff / mypy / `alembic upgrade head` → 0010(新增 1 revision);前端 typecheck + lint 绿;前端 dev mode 浏览器实测 profile 13 重建 chunks 后 40 → 41(+1 education chunk),技能区分类横排 chip 渲染正确,stats 与 list 同口径 33。

# 不在本文档范围

- M1 整体经验、永久约束累积、跨切片产出 → `slices/M1-summary.md`
- M2 切片规划 → `STATUS.md` "下一刀" 区(M2 待规划)
- prompt v1.0.2 / dataset 扩 / 4 新 metric / 列表页等具体修法 → `STATUS.md` "M2 待办累积"
