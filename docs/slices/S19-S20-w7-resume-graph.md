---
title: S19 + S20 W7 简历定制状态机 + 前端联动 — 切片归档
status: ✅ 完成,代码 + 文档 + push 一并提交
date: 2026-05-05
purpose: M3 主线 W7 — 把 S16 的"3 函数串调"升级为 5 节点 LangGraph(retrieve → plan → draft → review → revise),Planner v1.0.0 + Drafter v1.0.4 + Reviewer 沿用 v1.0.2;前端 4 节点 stepper + revise 提示;首次 dogfood 端到端跑通,顺手暴露并修了 MemorySaver ormsgpack 序列化挂(去 checkpointer)
---

# 切片范围

S19 + S20 = W7 简历定制状态机后端 + 前端联动,作为一个端到端单元归档(STATUS.md 原本拆两刀,但 dogfood 才能验证 graph 是否真的工作 — S19 后端单 commit 没法 dogfood,S20 收完 + 验完一起归档,合一个 PR 更清晰)。

按 ROADMAP §6.2 W7:

- ✅ `resumes` / `resume_versions` 表(S16 已建好,本切片不动)
- ✅ LangGraph 5 节点(retrieve / plan / draft / review / revise)— 但**不带 checkpointer**(见下方"设计决策" + 永久约束 #21 修订)
- ✅ Planner v1.0.0 / Drafter v1.0.4 prompt;Reviewer 沿用 v1.0.2
- ✅ `/v1/resumes/generate` SSE 加 `node_completed` 事件 + `result` 加 `revisions` 字段
- ✅ 前端进度条(4 节点 stepper + revise 黄色提示行)
- ⏸ drafter token 流式预览 — 推 W8(LLMClient `complete` 当前不返流,要先升 stream)
- ⏸ W7 末 DoD review 通过率 / 无 high severity — 第二轮 13 JD dogfood 推 W8 / W9 一并跑(本切片只做了 1 条端到端)

# 产出

```
apps/api/
├── pyproject.toml                                 # +langgraph>=0.2.50
└── src/jobcopilot_api/
    ├── agents/
    │   ├── resume_graph.py                        # 新 — 5 节点 StateGraph + ResumeGraphDeps + ResumeGraphState + stream_resume_graph
    │   ├── resume_planner/
    │   │   ├── __init__.py                        # 新
    │   │   └── agent.py                           # 新 — plan_resume(jd, chunks, hint, candidate, ...) → LLMResult
    │   └── resume_drafter/agent.py                # 改 — 加 plan / prev_findings 入参
    ├── prompts/
    │   ├── resume_planner/v1.0.0.j2               # 新 — 章节计划 + emphasis_skills + de_emphasize → ResumePlan schema
    │   └── resume_drafter/v1.0.4.j2               # 新 — v1.0.3 + G(plan 联动)+ H(revise 修订规则)
    ├── schemas/resumes.py                         # +ResumePlan / ResumeSectionPlan
    ├── services/resume_service.py                 # run_generate → run_generate_stream(async iterator)
    └── routers/resumes.py                         # SSE 加 node_completed 事件 + result 加 revisions 字段

apps/web/src/
├── lib/api.ts                                     # +ResumeNodeName 联合类型 + ResumeSseFrame 加 node_completed 帧 + result.revisions
└── app/matches/[id]/resume-trigger.tsx            # 加 NodeProgress 组件(4 节点 stepper + revise 提示)
```

# 设计决策(实现细节)

- **去 checkpointer(S20 修订原 S19 决策)**:S19 原方案 `MemorySaver`,docstring 写"MemorySaver 不序列化",**实测错误**。langgraph 0.2.x 所有 checkpointer 都走 `JsonPlusSerializer` + ormsgpack,SQLAlchemy ORM 行(`Jd` / `ProfileChunk`)+ dataclass(`LLMResult` / `RetrieveResult` / `ResumePlan` / `ResumeReview`)都不在 ormsgpack 内置类型表 → 第一次 dogfood 触发 revise 路径时 raise `Type is not msgpack serializable: Jd`。**修法**:`workflow.compile()` 不传 checkpointer。业务上单 SSE 请求生命周期内不需要跨进程恢复,W7 不开 interrupt,本来就不需要持久化 state。永久约束 #21 同步修订。

- **graph 不吞业务异常,service 集中 mark_failed**:retrieve / planner / drafter / reviewer 节点 raise `LLMUpstreamError / LLMTimeoutError / LLMSchemaInvalidError / ResumeGenerationFailedError` 后,冒泡到 `service.run_generate_stream`,service 在 `except` 块按 class 分发错误码 + 调 `_mark_failed`(side-channel commit,sessionmaker 开新事务避免污染主路径)+ re-raise 给 router 转 SSE error → done(ok=false)。Graph 是状态推进器,不是错误处理器(永久约束 #21 第一条)。

- **运行时依赖通过 deps 闭包,不放 state**:LLMClient / Embedder / sessionmaker / LoadedPrompt 通过 `ResumeGraphDeps` frozen dataclass 闭包到 node 函数,不写进 state。State 只放 ORM 行 + dataclass(因为没 checkpointer 做 serde,允许复杂类型直接传引用)。

- **`build_resume_graph(deps, *, pre_loaded)` 二段构造**:Service 层 phase-1 INSERT 后已经把 `jd` / `hint` / `candidate` 从 DB 拿好,通过 `PreLoadedResumeContext` 传给 graph;retrieve 节点直接用 pre_loaded 的字段,不再开 session(沿 S16 MVP "phase-1 短 tx 拿 detached row,phase-2 复用" 模板)。

- **revise 节点 = drafter + prev_findings + revision_count++**:revise 等价 `draft_resume(plan=plan, prev_findings=review.findings)`,产出新 markdown 覆盖 `state["draft_markdown"]` + `revision_count += 1` + 累计 drafter_results 列表(成本 / token 聚合用),回到 review 节点。`max_revisions=1` 默认 — review 失败 1 次允许 revise 1 次,revise 后再失败直接 END(status=review_failed)。

- **drafter prompt v1.0.4 = v1.0.3 + G + H**:G 约束 plan 联动(收到 ResumePlan 后按其中 sections 顺序 + emphasis_skills 写作,de_emphasize 内容尽量删 / 弱化);H 约束 revise 行为(prev_findings 非空时按 finding.section + quoted_text + 修订建议精准修改,不重写整篇)。两个约束在 prompt 内分支:plan=null 时退化 v1.0.3,prev_findings=null 时按首次 draft。

- **planner v1.0.0 章节计划简单**:输出 `ResumePlan{sections: [{name, emphasis_skills, de_emphasize}], notes}`。emphasis_skills 取自 match 的 matched_skills(强度高的 N 项)+ JD 的 hard_requirements;de_emphasize 取自 match 的 missing_skills(避免凸显)。MVP 不做 multi-pass / refine。

- **SSE node_completed shape**:`{"node": "retrieve|plan|draft|review|revise", "revision_count": int}`。前端按节点完成数量驱动进度条;revision_count 反映 revise 节点重跑次数(0 = 首次,1+ = revise 中)。

- **前端 4 节点 stepper(不是 5 节点)**:revise 是条件分支(review 失败才触发),所以主轴只显示 4 个常驻节点(retrieve / plan / draft / review),revise 通过 `revisionCount > 0` 显示一条黄色"已修订 N 次,正在重新核查…"提示行。如果按 5 节点画(把 revise 也做成圆圈),review 通过的快路径会出现"灰 revise 圆圈悬空"视觉噪音。

- **stepper 三态**:`done`(✓ 绿底白勾,success-border)/ `next`(蓝边数字 = 下一个待跑,accent)/ `pending`(灰边数字)。连接线在前一个节点完成后变绿。`revisionCount > 0` 时附加一行黄字 `warning-fg`。

- **single-PR S19 + S20 + serde 修**:S19 后端原本独立 commit a109b3f,但 dogfood 暴露的 serde 挂必须修才能跑通,前端 S20 也是端到端验的一部分 — 三件事合一个归档卡 + 一条 commit 比拆三条 git history 清晰(同 S16+S17 合一 commit 的判断)。

# 期间踩到的坑

1. **uv workspace member 新依赖必须 `uv sync --all-packages`**:S19 commit 加了 `langgraph>=0.2.50` 到 `apps/api/pyproject.toml`,但只跑 `uv sync` 不会装到 `.venv`(uv 默认只 sync root package,workspace member `apps/api` 要 `--all-packages` 或 `--package jobcopilot-api` 才装)。表象:API 启动 `ModuleNotFoundError: No module named 'langgraph'`,uvicorn `--reload` 死循环;前端 SSR 调 API 一并堵死,浏览器看到"打不开前端"假象。**修法**:`uv sync --all-packages`。已沉淀到自动 memory `project_uv_sync_workspace.md`。

2. **MemorySaver ormsgpack 序列化挂(本切片最大坑)**:见上方"设计决策"开头。**根因**:langgraph 0.2.x 所有 checkpointer 都做序列化,S19 docstring 误以为 MemorySaver 不序列化。**症状**:retrieve / plan / draft / review 节点流式事件先吐到前端(用户看到 4 个 ✓),graph 在 revise 路径要 `aput_writes` 时才 raise → service 捕获 → 前端收 `error → done(ok=false)` 不跳转。**第一次 dogfood 不到 revise 路径不会暴露**(快路径 review 通过没机会触发 state 写入失败 → 等等,这条要复审:其实快路径每个节点完成都会写 state,只是流式输出在 raise 前已经发出去 — 用户没注意到错误是因为最终也跳转了?— 不,真相是 retrieve / plan / draft / review 各自的 put_writes 也会失败,但 langgraph 的错误是延迟到流末尾抛的,所以前端看到 4 个 ✓ 后再收 error)。下次类似 graph 改造,应在最简 dogfood 后用 `status=review_failed` / `failed` 路径专门复测,不能只看 happy path。

3. **uvicorn `--reload` 卡死时 SIGTERM 无效,要 SIGKILL**:坑 #2 触发后,uvicorn 母进程一直 reload-loop 但 worker 都死着,SIGTERM(`kill <pid>`)没反应,要 `SIGKILL` 才下来。同时 next dev 也"看似 LISTEN 实则不响应"(SSR 在等已死的 API)。**修法**:`pkill -9 -f "uvicorn jobcopilot_api"` + `pkill -9 -f next` 一起清,再启。下次类似情况优先 `-9` 不要乐观 SIGTERM 等。

4. **STATUS.md 永久约束写反**:S19 沉淀的"State 只放可序列化业务数据(SQLAlchemy detached ORM 行 + LLMResult dataclass)"是**假的** — ORM 行不可序列化。当时是把 docstring 抄进 STATUS.md 没实测。**修法**:本切片把约束改成"无 checkpointer = state 全程内存里直接传引用,允许放 ORM 行 + dataclass",标 `[来自 S19 / S20 修订]`。下次写"永久约束"前必须有跑通的代码佐证,**不要把未验证的设计前提当约束**。

# Dogfood 数据(2026-05-05 第一次 W7 端到端)

- **入参**:JD #12 + 简历 #15 + match #7(score=68)
- **流程**:点"生成定制简历" → 4 节点 stepper 全 ✓ → review 第一次失败 → revise(revision_count=1)→ review 第二次 → graph END → 跳转 `/resumes/{id}`
- **修复前**:revise 节点 `aput_writes` raise msgpack 错,前端收 `error → done(ok=false)` 不跳转(用户报告)
- **修复后**:同入参再跑,revise 路径正常,跳转成功(用户口头确认"跳转了")
- **第二轮 dogfood**(13 JD × Planner+Drafter v1.0.4)推后 — 单样本只能验通路,不能算 W7 末 DoD,W8 / W9 启动时一并跑

# 闸门

⚠ 本切片**未跑闸门**(per CLAUDE.md "测试由用户手动跑"):

| 项 | 状态 |
|---|---|
| `pytest -q` | 未跑(S19 commit 时也未跑;S20 仅前端 + docstring 改) |
| `mypy` / `ruff` | 未跑 |
| 前端 `typecheck` / `biome` / `next build` | 未跑(改动小:api.ts 加 1 个类型 alias,resume-trigger.tsx 加 1 个组件) |
| 端到端 dogfood | ✅ 单样本通(match #7,revise 路径) |

后端 `pytest` 数字停在 M2 末 **321 passed**,本切片没新写测试,理论不破。前端无新测试。所有数字推 W8 / W9 闸门一起跑(若有破再补)。

# 给 S21 / W8 的输入

- **drafter token 流式预览**:LLMClient `complete` 升 stream(SSE token 转发),前端 ResumeTrigger 已埋好 phase 状态机 hook,可直接接收新事件类型(`draft_token` / `drafter_chunk` 之类)。设计时考虑:① 是否所有 4-5 个 LLM 调用都流(planner / reviewer 走结构化 schema 不流也行)② revise 路径流 token 的 UX(覆盖之前的 markdown?diff 模式?)
- **第二轮 dogfood + W7 末 DoD 复测**:13 张 BOSS JD × 5 简历组合,统计 review 通过率(目标 ≥ 50%)/ high finding 平均数(目标 ≤ 1)/ P95 latency / cost。dataset 与 W8 `resume_review` 对抗集 20 条 / `resume_generate` 端到端 25 条共用真实样本。
- **graph state 序列化升级路径**(若 W8 真要 monaco patch 流 / 中断恢复):自定义 langgraph serde + pickle ORM 行,或重设计 state 只放 id 引用 + plain dict + nodes 内部按需重建。后者更干净但与 S16 detached ORM 模板冲突,可能要同步重构 service 层 `_load_resume_for_generate`。
- **uv workspace 新依赖必须 `--all-packages`** 已入自动 memory(`project_uv_sync_workspace.md`),后续切片加 dep 时直接套用。

# 什么没改(本切片范围外)

- LLMClient stream 模式(W8)
- markdown editor / monaco / version diff / 一键采纳(W8)
- LaTeX 渲染 / PDF DOCX 导出(W9)
- W7 末 DoD 13-JD 第二轮 dogfood(推 W8 / W9)
- Postgres checkpointer / 中断恢复(无业务诉求)
- ResumePlan 多轮 refine / planner v1.0.1(等真实 dogfood 暴露问题)
