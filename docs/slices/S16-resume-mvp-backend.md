---
title: S16 简历定制 MVP 后端骨架 — 切片归档
status: ✅ 完成,待 commit & push(用户决定不跑闸门 / 不冒烟)
date: 2026-05-04
purpose: M2 主线刀 — 基于 S13-S15 match 端到端,起 retrieve → draft → review 三函数串调生成定制 markdown 简历;SSE 路由 + resumes / resume_versions 表 + drafter/reviewer agent + prompt v1.0.0
---

# 切片范围

S16 = 简历定制 MVP **后端骨架**(数据层 + 服务层 + agent 层 + SSE 路由)。前端 = S17(后置切片)。

**MVP 边界(STATUS.md "S16-S17 简历定制 MVP 规划" 区拍板,用户选 D8 = A)**:
- D1 markdown only(LaTeX/PDF 留 M3)
- D2 + D8 **不上 LangGraph**,3 函数串调(retrieve / draft / review)
- D3 review 保留,revise 不做(任意 high severity → status='review_failed' + 仍存 markdown 让前端展示警告条)
- D4 复用 S13 `retrieve_for_match`,K=20
- D5 markdown 只读展示(M3 加 monaco / live preview / version diff)
- D6 match_id 可选,提供时验 (jd_id, profile_id) 一致 + status='scored'
- D7 偏离:**drafter / reviewer 都 CHEAP 不开 thinking**(原文档说 drafter STANDARD/thinking),基于 S14 dogfood 教训
- D9 触发入口收窄到 match 详情页(S17 实现)
- D10 4 endpoints:`POST /v1/resumes/generate` (SSE) / `GET /v1/resumes` / `GET /v1/resumes/{id}` / `DELETE /v1/resumes/{id}`

# 产出

```
apps/api/
├── alembic/versions/0012_resumes.py                 # resume_status enum + resumes + resume_versions 表
├── src/jobcopilot_api/
│   ├── models/
│   │   ├── resume.py                                 # Resume ORM(套 Match 模板)
│   │   ├── resume_version.py                         # ResumeVersion ORM(只 created_at,不套 TimestampMixin)
│   │   └── __init__.py                               # 注册 Resume + ResumeVersion
│   ├── schemas/resumes.py                            # ResumeStatus / ReviewFinding / ResumeReview / ResumeCreateInput / ResumeListItem / ResumeListResponse / ResumeDetail / ResumeTokens
│   ├── agents/
│   │   ├── resume_drafter/{__init__,agent}.py       # draft_resume(jd, chunks, hint, prompt, llm) → LLMResult(plain text markdown)
│   │   └── resume_reviewer/{__init__,agent}.py      # review_resume(draft_markdown, chunks, prompt, llm) → LLMResult(parsed=ResumeReview)
│   ├── prompts/
│   │   ├── resume_drafter/v1.0.0.j2                 # SYSTEM 写作铁律 + 7 章节硬编码 + 反注入 / USER chunks + JD + optional hint
│   │   └── resume_reviewer/v1.0.0.j2                 # SYSTEM 核查方法 + 严重度判定 + 通过标准 + 反注入 / USER chunks + draft_markdown
│   ├── services/resume_service.py                    # create_pending_resume + run_generate(retrieve→draft→review→ UPDATE+INSERT v1)+ list/get/soft_delete
│   ├── routers/resumes.py                            # POST /v1/resumes/generate SSE + GET list / detail + DELETE
│   └── main.py                                       # include_router(resumes.router)
```

未改动:测试(用户决定跳过 S16 写新测试,留 S18 dogfood 后再加)/ 前端(S17)/ docs(本归档卡 + STATUS.md 即所有文档改动)。

# 设计决策(实现细节)

## 状态机 / 数据建模

- **resume_status enum 取 §3.10 完整 4 值**:`('generating', 'review_failed', 'ready', 'failed')`。`generating` 充当永久约束 #4 phase-1 INSERT 的中间态(替代 match 的 `pending`);`review_failed` 是简历特有的"业务级失败但仍展示"态(任意 high severity finding,markdown 仍存,前端展示警告条让用户自决)。

- **resume_versions 表完整建好但 MVP 只插 v1**:STATUS.md S16 规划"MVP 不用的列建好备用"。`run_generate` 成功后插一行 `version_number=1, edit_type='generated'`,后续 regenerate / edit / patch / monaco 等 M3 再接。`resume_versions` 表只有 `created_at`(§3.11),所以 ORM 不套 TimestampMixin,自己声明 `created_at`。

- **resume_versions 不挂 `set_updated_at` trigger**:与 §3.11"无 updated_at"对齐。0012 migration 只给 resumes 挂。

- **§3.10 schema 不偏离**:不像 0011 matches 那样改 `score nullable + 加 status enum`。理由:§3.10 原本就有 `status enum` 含 `generating`(命中永久约束 #4),`markdown`/`review_passed`/`review_findings` 也都允许 null/默认值,与 SSE phase-1 INSERT 兼容。0012 docstring 标"无偏离"。

## Agent / Prompt

- **不上 LangGraph(D8 = A)**:三函数串调跟 S14 `run_analyze` 同模式。锁定决策表说 LangGraph 仅用于简历定制 + 面试模拟 — 这意味着**最终**要上,M3 GA 阶段把 plan 节点 + revise 循环 + checkpointer 加回来时再升 LangGraph,届时三大价值(条件循环 / 断线续跑 / 跨节点 cache 复用)才真正发挥。MVP 退化为线性 DAG,LangGraph 等同函数串调,引入只增成本不增价值。**known debt**:M3 启动前必须升级,否则违反锁定决策表。

- **chunks 在 user 段而非 system 段(偏离 AGENT_DESIGN §7.3.3)**:套 match_analyst 模板。§7.3.3 让 chunks 进 system 是为了 cross-call cache 命中(同 profile 多次生成),但 retrieval 在每次 generate 拉的 K=20 chunks 不固定(查询依赖 JD,同 profile 不同 JD 检索结果不同),命中率低;放 user 段更直观,system 段保留稳定的"角色 + 风格 + 章节顺序",`cache_system=True` 命中目标更纯粹。

- **drafter 不走 `response_schema`,plain text markdown**:简历正文是 ~1000 字 markdown,JSON 包装会让 LLM 把整段转义到一个字符串字段(`\n` → `\\n`,代码块 / 引号 / 中文标点都易出错)。直接 plain text 把 LLM `result.content` 当 markdown 收。reviewer 仍走 schema(`ResumeReview` { passed, findings }),结构化拿来落库。这一条是新永久约束(下方"跨切片永久约束"区)。

- **drafter / reviewer 都 CHEAP 不开 thinking(偏离 STATUS.md D7)**:D7 推荐"drafter STANDARD/thinking + reviewer CHEAP/flash"。S14 dogfood 实测 STANDARD(thinking)+ JD+10 chunks 在默认 30s timeout 下 3 次重试都过不去(`llm_calls.error_code='timeout' latency_ms=276963`)。drafter 输入更大(JD + 20 chunks ≈ 8k token),开 thinking 大概率超时。MVP 保守:都 CHEAP 不开 thinking,timeout 给 drafter 90s / reviewer 60s。M3 评测扎根 / dogfood 反馈再决定升 STANDARD/PREMIUM 换质量,届时同步放宽 timeout。

- **章节顺序硬编码到 prompt(D2 不做 plan 节点)**:`基本信息 → 求职意向 → 专业概要 → 工作经历 → 项目经历 → 技能 → 教育背景`(§7.3.2 推荐顺序)。没 ResumePlannerAgent,drafter 直接按硬编码 7 章节生成。M3 加回 planner 时 prompt 接 `plan.sections` 字段。

- **drafter 总字数 800-1200 字 / 每章 ≤ 6 bullet / 每 bullet ≤ 30 字**:写到 prompt"章节顺序"末端。MVP 凭 LLM 自律,不做硬截断。dogfood 时观察是否超长,超长再加 prompt 强约束。

- **反幻觉两道防线**:① drafter prompt 第 1 条"绝对不允许编造";② reviewer 强制做事实核查(逐 bullet vs chunks)。任意 high severity finding 阻断 status='ready',但仍保 markdown(D3:不 revise,让用户看 finding 自决)。

- **hint 拼接 = `match.gap_summary` + `missing_skills[*].name` 拼接成一段**:`_compose_hint(match)` 在 service 层组合,drafter prompt 用 `{% if hint %}` 渲染。从 STATUS.md "还需要锁的事"这一条对齐("把 match.gap_summary + missing_skills 作为 drafter prompt 段")。MVP 入口收窄到 match 详情页,所以提供 match_id 时一定有 hint,从 JD 详情触发的链路目前不暴露(S17 不接)。

## Service 层

- **两阶段 pipeline 完全套 match_service 模板**:`create_pending_resume`(phase 1: validate + INSERT 'generating')+ `run_generate`(phase 2: retrieve → draft → review → 单事务 UPDATE + INSERT version)。Failure path 也对齐 — `_mark_failed` 旁路 commit 套 ADR-0004。

- **match_id 一致性 + 状态校验**:phase-1 校验 match 必须存在 + 属于 user + (jd_id, profile_id) 与 body 一致 + `status='scored'`。phase-2 重新读 match 时若已被软删 / 状态变非 scored 则**降级为无 hint** 而非失败 — 用户中途软删 match 不应阻断已 INSERT 的 resume 生成。

- **review_failed 不 `_mark_failed`**:reviewer.passed=False 直接 UPDATE 为 status='review_failed' + 写 markdown + review_findings + INSERT v1 version。这与 'failed' 区分 — 'failed' 是 IO/schema 层错(无 markdown 可看),'review_failed' 是业务层"做完了但有问题"(markdown 可看让用户自决)。

- **tokens / cost / latency 落库 = drafter + reviewer 之和**:`_apply_generate_result` 把两个 LLMResult 的 `tokens_in / tokens_out / cached_tokens / cost_cny / latency_ms` 直接相加;`generation_model` / `review_model` 拆两列存模型 ID。LLMResult 字段都是非 Optional 的 int / Decimal,不需要 `or 0` 防御。

- **`title` 模板 = `{jd.title} - {jd.company}`**(任一缺失 fallback 另一个,两者皆缺时"未命名简历"),200 字符截断适配 §3.10 column。

- **list / get / soft_delete 走 caller-managed session**:同 jd_service / match_service 模式。`run_generate` / `create_pending_resume` / `_mark_failed` 走 sessionmaker(永久约束 #7 IO 在事务外的标配)。

## Router / SSE

- **SSE shape = matches 完全同款**:`started → result → done`(失败:`started? → error → done(ok=false)`)。**不发 node-level 进度事件**(retrieve / draft / review 各阶段无 emit)。理由:① S17 计划 STATUS.md 写"MatchTrigger 同模式 SSE 流",前端复用 lib/sse.ts 不改;② run_generate 在 service 层一站式串调,拆 retrieve_phase / draft_phase / review_phase 给 router 编排会让失败语义复杂化;③ 若 dogfood 发现 30-90s 等待 UX 太差,v1.1 再加 node 事件(同 STATUS.md "匹配 v1.1 提质" 区思路)。

- **`result` 事件载荷**:`{ resource_id, url, status, review_passed }`。`status` 让前端区分 ready vs review_failed(后者要展示警告条);`review_passed` 三态(true / false / null,null = 走到 done 前 status 仍 generating,理论不会发生但保字段)。`done(ok=true)` 涵盖 ready 和 review_failed —— review_failed 不算"流程失败",markdown 仍可看。

- **prompt cache 复用现有 lifespan**:`infra/prompts.py:load_prompt_versions` 自动扫描 `prompts/<agent>/v*.j2`,所以新增 `resume_drafter/v1.0.0.j2` + `resume_reviewer/v1.0.0.j2` 在启动时自动 upsert + cache,**main.py 仅加 include_router 不需手工注册 prompt**。

# 期间踩到的坑

1. **TimestampMixin 给 resume_versions 不合适**:§3.11 表只有 `created_at`,没有 `updated_at` / `deleted_at`。第一版套 TimestampMixin 多三列。改:ResumeVersion 自己声明 `created_at` 单列,不套 mixin。归档下次新表前先看 §X.Y 列清单决定是否套 TimestampMixin。

2. **LLMResult 字段非 Optional**:第一版 `_apply_generate_result` 写了 `(drafter.tokens_in or 0) + ...` 防御。看 `LLMResult` 数据类定义后清掉 `or 0`,字段都是 `int` / `Decimal`(非 Optional)。

3. **drafter `response_schema=None` vs 默认值**:LLMClient.complete 的 `response_schema: type[BaseModel] | None = None`,显式传 None 跟省略一样,但显式传更清楚。route 层 `cache_system=True` 也是默认值,显式传防漏。

4. **prompt 文件 `## SYSTEM` / `## USER` 分隔符严格**:首版 drafter prompt SYSTEM 块包含 `## 写作铁律`、`## 章节顺序` 子标题。loader regex `_SYSTEM_HEADER` 是行起 `^##\s*SYSTEM\s*$`,所以 `## 写作铁律` 不会误命中(必须严格匹配 SYSTEM/USER)。验证 OK,无须改。但下次写 prompt 时如果 SYSTEM 子标题恰好是 "## SYSTEM-EXTRA" 这种会误命中,要小心。

5. **`Decimal` 导入孤儿**:第一版 `from decimal import Decimal` 但清掉 `or Decimal("0")` 后 import 没用,ruff 会挂 F401。手动清掉。

# 闸门

| 项 | 状态 |
|---|---|
| 后端 `pytest -q` | **未跑**(用户跳) |
| 后端 ruff / mypy | **未跑**(用户跳) |
| 后端 alembic | **未跑**(用户跳;期望 0011 → 0012) |
| 前端 typecheck / biome / next build | **未跑**(用户跳;S16 没改前端,只新增 schema 类型留 S17 重跑 `pnpm gen` 同步) |
| OpenAPI dump + `pnpm gen` | **未跑**(用户跳;留 S17 起手) |
| 端到端 dogfood | **未跑**(留 S18) |

期望若跑应当过的:
- alembic upgrade head → **0012**
- pytest 数字不动(没新测试,test_migrations 只读 EXPECTED_TABLES,S16 没改它就不进表;若期望 EXPECTED_TABLES 收 resumes/resume_versions 则需在 S17/S18 同步加)
- ruff / mypy 全过(本切片仅新增文件,无既有文件大改动)

S17 起手前 / commit 前用户视情况补跑。

# 给后续切片的输入

- **S17 简历定制前端**:
  - match 详情页加"基于此次匹配生成简历"按钮(套 MatchTrigger 模式 SSE 流;result.url → router.push)
  - `/resumes` 列表(套 S12 永久约束 #1 模板)
  - `/resumes/[id]` 详情:markdown prose 渲染 + review_findings 警告条(只在 status='review_failed' 时显)+ status badge + "下载 .md" 按钮(浏览器 Blob 下载,后端不出 endpoint)
  - sidebar 加"简历定制"组
  - SSE result 事件载荷新增 `status` / `review_passed`,前端拿来区分跳转后展示什么

- **S18 dogfood + 调整**(后置切片):跑 5+ 真实 (JD, profile),看:
  - 简历质量(章节齐全度 / chunks 引用准确度 / 是否有幻觉漏网)
  - reviewer 通过率(过低则 prompt v1.0.1)
  - 单次成本(MVP 估 ¥0.04-0.06,不破 ¥0.15 budget)
  - P95(MVP 估 30-60s,不破 ≤ 90s SSE 上限)
  - 是否需要给 LLMResult 暴露 cache hit 数(MVP `cached_tokens` 落库但靠 LLMResult 字段已经 sum)
  - drafter 总字数 / 每章 bullet 上限是否需要 prompt 硬约束

- **M3 升级 LangGraph(known debt)**:加 plan 节点 + revise 循环(最多 2 次)+ Postgres checkpointer。彼时 drafter cache 复用价值才发挥;同时考虑 chunks 是否回 system 段(若 plan 节点用同份 chunks 多次,放 system 段才有 cross-node cache 命中)。

# 什么没改(本切片范围外)

- 前端任何文件(S17)
- 测试(留 S18 dogfood 后补)
- `applications` 表(M3 投递追踪)
- LangGraph 依赖 / `langgraph` package
- Postgres checkpointer
- LaTeX `awesome-cv-zh` 模板 + PDF 渲染(M3)
- monaco editor / live preview / version diff(M3)
- regenerate / export / patch / new-version endpoints(D10 留 M3 三个 endpoint)
- match 详情页 footer 调试 metadata 收折叠(STATUS.md 沉淀的 M2 后置债)
- chunk content evidence hover 联动(M2 v1.1 提质)
