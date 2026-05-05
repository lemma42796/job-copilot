---
title: S19 W7 简历定制状态机后端骨架 — 切片归档
status: 🟡 后端落地,**未跑测试 / 闸门**(由用户手动验);前端进度条 + 流式 markdown + 第二轮 dogfood = S20
date: 2026-05-05
purpose: M3 W7 主线刀 — 把 S16 三函数串调 MVP 升级为 5 节点 LangGraph(retrieve / plan / draft / review / revise);新增 ResumePlannerAgent + Drafter v1.0.4 接 plan / prev_findings;SSE 加 node_completed 事件
---

# 切片范围

S19 = M3 W7 **后端骨架**(graph + agent + prompt + service + router 改造)。前端 + dogfood = S20。

**升级目标(STATUS.md "下一刀:M3 简历定制 GA" W7 进度对照表)**:
- LangGraph + checkpointer:S16 D8=A 故意跳过的 known debt,W7 必须升回 — ✅ 用 langgraph + MemorySaver(进程内)落地;PG checkpointer 留待"中断恢复"业务诉求出现时再升
- 5 节点状态机(retrieve / plan / draft / review / revise):S16 是 retrieve / draft / review 三节点,W7 加 plan(retrieve 后产章节计划交给 drafter)+ revise(reviewer 失败 → revise → 回 review,带 max_revisions cap)
- Planner agent + prompt v1.0.0:S16 没起,W7 新增
- Drafter prompt v1.0.4:在 v1.0.3(6 条强约束 A-F)基础上加 G(plan 联动)+ H(revise 修订规则);plan=null / prev_findings=null 时退化为 v1.0.3 行为
- SSE 升级:加 `node_completed` 事件供前端进度条接;`result` 加 `revisions` 字段

**S19 边界(明确不做,留 S20+)**:
- 前端 `lib/sse.ts` 加 `node_completed` 事件类型 + 简历生成页进度条 UI
- Drafter token 流式预览(LLMClient 当前 `complete` 不流式,要再升一层)
- 第二轮 dogfood(W7 末 DoD:review 通过率 ≥ 50%、无 high severity 幻觉)
- W8 monaco 编辑 / version diff / 对抗集 / LLM-as-Judge

# 产出

```
apps/api/
├── pyproject.toml                                     # 加 langgraph>=0.2.50
├── src/jobcopilot_api/
│   ├── agents/
│   │   ├── resume_graph.py                            # 新 — 5 节点 StateGraph + MemorySaver + stream_resume_graph helper
│   │   ├── resume_planner/
│   │   │   ├── __init__.py                            # 新
│   │   │   └── agent.py                               # 新 — plan_resume(jd, chunks, hint, candidate, prompt, llm) → LLMResult(parsed=ResumePlan)
│   │   └── resume_drafter/agent.py                    # 加 plan / prev_findings 两个 keyword args
│   ├── prompts/
│   │   ├── resume_planner/v1.0.0.j2                   # 新 — 策略顾问 SYSTEM(章节计划 + emphasis_skills + de_emphasize)
│   │   └── resume_drafter/v1.0.4.j2                   # 新 — v1.0.3 + G(plan 联动)+ H(revise 修订规则)
│   ├── schemas/resumes.py                             # 加 ResumePlan / ResumeSectionPlan
│   ├── services/resume_service.py                     # run_generate → run_generate_stream(async iterator,yield node_completed + final 事件)
│   └── routers/resumes.py                             # SSE 加 node_completed 事件;result 多带 revisions 字段
docs/
├── STATUS.md                                          # 切片表 S19-S23,W7 对照表打勾,当前生效 prompt 升 v1.0.4 + planner v1.0.0
└── slices/S19-w7-resume-graph-backend.md              # 本归档卡
```

未改动:测试(用户决定跳过)、前端(S20)、main.py / lifespan(prompts 自动扫描注册)、alembic(0012 已建好备用列)。

# 设计决策(实现细节)

## Graph / Checkpointer

- **MemorySaver(进程内),不引 langgraph-checkpoint-postgres**:ROADMAP §6.2 W7 写 "Postgres checkpointer 接入" 但落地选 MemorySaver。理由:① SSE 单进程内不需要跨进程恢复 ② `langgraph-checkpoint-postgres` 拉 psycopg sync 链,与本项目 asyncpg 路径正交,引入会增加运行时复杂度 ③ 真正需要"中断恢复 / 长时任务"时(可能 W8 monaco 编辑器配合 patch 流?)再升不晚。docstring 标了这个偏离的原因。

- **State 字段不放运行时依赖**(LLMClient / Embedder / sessionmaker / LoadedPrompt):用 `ResumeGraphDeps` dataclass 闭包到 node 函数里。State 只放业务数据(jd / chunks / plan / draft_markdown / review / revision_count / drafter_results / reviewer_results / planner_result),换 PG checkpointer 时 state 字段全部可序列化(SQLAlchemy detached ORM 行 + LLMResult dataclass)。

- **PreLoadedResumeContext**:Service 层在 phase-1 INSERT 后已经把 jd / hint / candidate 从 DB 拿好(套 S16 `_load_resume_for_generate` 短 tx 模板)。Graph 内 retrieve_node 直接用 pre_loaded 字段,不再开 session。这避免了 graph 节点写 DB 的复杂性 — IO 集中在 service 的 begin/end,中间所有节点都是纯计算。

- **stream_mode="updates"**:`graph.astream()` 用 updates 模式,每步产出 `{node_name: state_delta}`。`stream_resume_graph` helper 把 delta 累积到 final_state,逐 node 同步 yield NodeEvent。Service 层在节点完成事件之外,最后再 yield final_state。

- **lazy import 避免循环引用**:`agents/resume_graph.py` 内 node 函数从 `services.resume_service` lazy import `ResumeGenerationFailedError`(只在 raise 时触发)。Module 顶层 service → graph → service 这条链如果都顶层 import 会循环。Lazy import 是 Python 常用解法。

## 5 节点 + 条件边

- **节点 contract**:每个 node 返回 `dict[str, Any]`(state 部分更新,LangGraph 默认 reducer = override)。retrieve / plan / review 各写自己产出字段;draft / revise 都写 `draft_markdown`(revise 覆盖 draft);revise 自己 `revision_count += 1`。
- **review 条件分支**:`review.passed == True` **或** `revision_count >= max_revisions(默认=1)` → END;否则 → revise → 回 review。max_revisions=1 意味着"reviewer 失败最多重写 1 次,再失败接受 review_failed"。可调高,但 dogfood 验证前不要乐观加大(每次 revise 多一次 drafter LLM 调用,~30s + ¥0.1)。
- **revise 节点不抛业务失败**:即使 drafter 返回空 markdown,也是抛 `ResumeGenerationFailedError`(由 service 层 mark_failed),不退化到旧 markdown — 因为旧 markdown 已被 revise 节点覆盖,而是认为这次生成整体失败。

## Planner agent + prompt v1.0.0

- **走 response_schema=ResumePlan**(JSON):与 drafter 不同。Planner 输出短(~500 token),JSON 包装不会乱;且下游 drafter 要遍历 `plan.sections` 字段做模板渲染,必须结构化。
- **Tier=CHEAP 不开 thinking + 60s timeout**:同 reviewer。Planner 是结构化短输出,不需要长思考。
- **Plan 内容**:`overall_strategy`(3-5 句)+ `emphasis_skills`(JD ∩ chunks 实际命中)+ `de_emphasize`(关联弱内容)+ `sections[7]`(每章节 rationale + must_include_chunk_ids + skip)。
- **铁律对齐**:Planner prompt 显式说"不编造"(emphasis_skills 必须 chunks 里能查到)、"chunk_id 真实存在"、"不替 drafter 写文案"(rationale 是 20-40 字策略提示,不是 bullet 文案)、"skip 仅 candidate 全空时 true"。
- **emphasis_skills 与 D.0 全列铁律联动**:Planner prompt 强调"候选人 `granularity=skill` chunks 必须在 emphasis_skills 中体现"。这呼应 drafter v1.0.3 D.0 的强约束(skill chunks 全列),让 planner 不把这个责任推给 drafter。

## Drafter v1.0.4(在 v1.0.3 基础上)

- **保留 v1.0.3 全部铁律(A 单 chunk / B 副词 / C 侧项目 / D 技能段 / E 求职意向 / F candidate 章节)**:S18 第二轮 dogfood + audit 修扩的 5 类真 bug 还在,不动。
- **新增 G. plan 联动**:`plan.sections[i].must_include_chunk_ids` 非空时该章节必须提及;`skip=true` 时整章节跳过(信任 planner 判断,等价 F 规则的 candidate 全空降级);`emphasis_skills` 中所有技能必须在 `## 技能` 章节出现(D.0 铁律的强化)。`plan=null` 时退化 v1.0.3 行为。
- **新增 H. revise 修订规则**:`prev_findings` 非空时,这是修订轮(不是首次 draft):保留上一轮草稿的整体结构和正确内容,只针对 findings 改;按 issue_type 分类处理(fabrication 删 / exaggeration 降级 / unsupported_number 删数 / other 按 explanation);severity=high 必须改、medium 优先、low 可选。**不要因为某处修订改写其他正确章节**。
- **prompt 渲染分支**:USER 段加 `{% if plan %}...{% endif %}` 段(在 chunks 之前)+ `{% if prev_findings %}...{% endif %}` 段(在 hint 之后);两段都为空时不渲染,等价 v1.0.3 输入。

## Service 改造

- **`run_generate` → `run_generate_stream` async iterator**:返回 `AsyncIterator[dict]`,yield 两类事件:`{"event": "node_completed", "node": ..., "revision_count": ...}` + `{"event": "final", "resume_id": ..., "status": ..., "review_passed": ..., "revisions": ...}`。Router 直接 `async for` 转 SSE。
- **失败处理仍然 service 集中**:graph 节点抛 LLMUpstreamError / LLMTimeoutError / LLMSchemaInvalidError / ResumeGenerationFailedError 一路冒泡到 `run_generate_stream` 的 try/except,by class 分发到对应错误码,统一调 `_mark_failed`(旁路 commit)+ raise。Graph 本身不吞业务异常 — 它是状态推进器,错误处理在调度层。
- **`_apply_generate_result` 改签名**:接收 `drafter_results: list[LLMResult]`(可能多次)+ `reviewer_results: list[LLMResult]`(可能多次)+ `planner_result: LLMResult | None` + `revision_count: int`。tokens/cost/latency 是所有 LLM 调用之和(planner + 所有 draft + 所有 review);`generation_model` 取最新一次 drafter,`review_model` 取最新一次 reviewer;`revisions` 列写入 revision_count。

## Router SSE 升级

- **保留 phase-1 / phase-2 分手**:`started` 仍在 phase-1 INSERT 之后 emit(永久约束 #4);phase-2 改为 `async for event in run_generate_stream`,转发 5 个 `node_completed` 事件 + 1 个 `result` 事件。
- **prompt key 升 v1.0.4 + 加 planner v1.0.0**:`DRAFTER_PROMPT_KEY=("resume_drafter", "v1.0.4")` / `PLANNER_PROMPT_KEY=("resume_planner", "v1.0.0")`;reviewer 沿用 v1.0.2。
- **`result` 加 `revisions` 字段**:让前端可显示"经历 N 次修订"。

## Prompt 注册

- **新 prompt 文件由 lifespan 自动扫描注册**(ADR-0006 D6):`load_prompt_versions` walk `prompts/<agent>/v*.j2`,新增的 `resume_planner/v1.0.0.j2` 和 `resume_drafter/v1.0.4.j2` 在 lifespan startup 时:① 检查 `(agent, version)` 是否存在 → 不存在 INSERT 新行 ② 存在则比对 hash → 不一致 raise PromptVersionMismatchError。无需改 main.py。
- **v1.0.3 / v1.0.2 / v1.0.1 / v1.0.0.j2 旧文件保留**:DB 里已有行,不动;只是 router 不再用 v1.0.3。删除旧文件会导致 lifespan 找不到 DB 已有行对应的内容(不报错,但下次 schema 校验会跑空)— 保留更稳。

# 期间踩到的小坑

1. **drafter v1.0.4 SYSTEM 段 escape 错误(立即 fix)**:写 F 段示例时误以为 SYSTEM 也走 jinja,加了 `{{ "{{" }} candidate.full_name {{ "}}" }}` 这种 escape。看了 `infra/prompts.py:138 render_user`,确认 SYSTEM 是字面字符串(只 user_template 走 jinja),立即改回 `{{ candidate.full_name }}` 字面写法(LLM 看作占位符语法理解)。

2. **PreLoadedResumeContext 类定义在 build_resume_graph 之后**:函数签名 annotation 引用 `PreLoadedResumeContext | None`,但类定义在函数后。`from __future__ import annotations` 让 annotations 都是 lazy strings,运行时不解析,所以正向引用 OK。`__all__` 在所有 class 之后定义,也 OK。

3. **`Decimal` 累加 sum 起始值**:`sum(generator, 0)` 默认起始 int 0,Decimal + int 会 TypeError。改用 `sum(..., Decimal("0"))`。需要 `from decimal import Decimal`。

4. **`run_generate_stream` 是 async generator,`async for` 消费时 router 不能再外层 try/except graph 异常**:graph 异常已在 service `run_generate_stream` 内 try/except + mark_failed + raise,raise 出来时 SSE 已经 yield 过若干 `node_completed` 事件;router 外层 except 只 catch 后续 raise,emit `error → done(ok=false)`。已发的 node_completed 事件不撤回。前端要容忍"事件序列以 error 收尾时,前面的 node_completed 是真实进度但最终失败"。

5. **revise 路径下 reviewer_result 也累积**:revise 节点 → 回 review 节点,review 节点会再调 review_resume,append 到 `reviewer_results` 列表。所以 reviewer 总调用数 = `revision_count + 1`(首次 review + 每次 revise 后再 review)。Cost / latency 累加自然反映。

6. **revisions 列 vs revision_count state 字段**:`§3.10 resumes.revisions` 是 SmallInteger 列,Service 层从 `final_state["revision_count"]` 写入。Drafter 调用次数 = `revision_count + 1`(首次 draft + 每次 revise)。

# 跨切片永久约束(影响后续 M3 切片)

- **[来自 S19] LangGraph 节点不吞业务 / LLM 异常,由调度层(service)集中 mark_failed**:graph 节点 raise 后冒泡到 `service.run_generate_stream`(或后续 W8 / W9 类似的调度函数),by class 分发错误码 + 调 `_mark_failed`(side-channel commit)+ raise。Graph 是状态推进器,不是错误处理器。后续给简历定制 graph 加新节点(W8 monaco patch / W9 PDF 渲染节点)时遵循同款。

- **[来自 S19] LangGraph state 字段不放运行时依赖**:LLMClient / Embedder / sessionmaker / LoadedPrompt 等运行时对象通过 `ResumeGraphDeps` 闭包到 node,不放 state。State 只放可序列化业务数据(SQLAlchemy detached ORM 行 + LLMResult dataclass)。这让"换 PG checkpointer"是个非破坏性升级 —— state 已经全部可 pickle。

- **[来自 S19] Drafter prompt 接收 plan / prev_findings 两个可选透传段**:`plan=None` 时退化无 planner 形态(等价 v1.0.3),`prev_findings=None` 时是首次 draft(非 revise);任一非空都触发 prompt USER 段额外渲染段。后续 W8 monaco patch 流(用户编辑后让 drafter "局部重写")可复用 prev_findings 协议(把用户标记的"待改章节"塞进 prev_findings 列表)。

# S20 待做(W7 收尾)

1. 前端 `lib/sse.ts` 加 `node_completed` 事件类型(struct: `{node, revision_count}`)
2. `apps/web/src/app/resumes/...` 简历生成页加进度条 UI(5 节点 + revision_count 标识)
3. **跑闸门**(`pytest -q` + ruff + mypy + frontend typecheck)— 这次 S19 没跑
4. 第二轮 dogfood(13 张 BOSS JD × Planner+Drafter v1.0.4 全链路)
5. dogfood 真 bug 入档 → 推 prompt v1.0.5(若有)
6. W7 收官归档卡 `slices/S19-S20-w7-resume-graph.md`(或 S20 单独归档)

# 不在本切片范围

- 前端进度条 / 流式 markdown(S20)
- W8 反幻觉 + 可编辑(对抗集 / monaco / version diff / LLM-as-Judge)
- W9 渲染与导出(LaTeX awesome-cv / PDF)
- W10 内测 v0.5 发布
- PG checkpointer / 中断恢复(待"长时任务 / 用户编辑后续跑"业务诉求出现)
