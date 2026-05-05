---
title: JobCopilot 项目当前进度(单一可信源)
owner: lemma42796
last_updated: 2026-05-05 — **M3 W8 子任务 1+2 完成 / 子任务 3 部分完成**:drafter token 流式(LLMClient.complete 加 on_token + DashScope stream + 前端实时预览)+ Monaco 编辑器 + 版本 diff(GET/POST `/resumes/{id}/versions` + monaco MarkdownEditor / DiffEditor + 版本历史卡 + 切换/对比)+ Reviewer 标记部分交互(可点 + 滚动定位 + 一键采纳 strip 文 + 忽略 + obsolete 灰化"已处理"标签 + stripQuoted 标点收尾)。**已知 bug**:点击 finding 行后正文里 `<mark>` 黄底高亮组件没出现在 DOM 里(matched 检测疑似有问题,4 档 fallback 都没命中 / 或 ctx 传递异常),DevTools 搜不到 `data-finding-highlight`。下一步:加 console.log trace 哪一档 fallback 走到了,或换实现策略。子任务 4(对抗集 + Judge)+ 子任务 5(W7 末 DoD 复测)未起。
purpose: 跨会话续作的状态快照。任何新会话从这里开始读。
---

# 当前阶段

**M3 简历定制 GA — W7 完成,W8 进行中**

| 切片 | 内容 | 状态 |
|------|------|------|
| S19+S20 | W7 简历定制状态机 + 前端联动 + checkpointer serde 修 | ✅ [slices/S19-S20-w7-resume-graph.md] |
| S21  | W8 反幻觉 + 可编辑(对抗集 + monaco + version diff + LLM-as-Judge + drafter token 流式)| 🔄 子任务 1+2 ✅ / 3 ⚠ 部分 / 4+5 ⏳ |
| S22  | W9 渲染与导出(LaTeX awesome-cv + PDF 导出)| ⏳ |
| S23  | W10 内测 v0.5(招募 + 飞书反馈 + 性能收尾 + Release)| ⏳ |

**S21 W8 子任务进度**:
- ✅ **#1 drafter token 流式**:`LLMClient.complete` 加 `on_token` 回调 / DashScope `stream=True` + `include_usage` / DummyProvider 32 字符切片模拟 / `ResumeGraphDeps.on_drafter_token(phase, delta)` 闭包 / `service.run_generate_stream` 用 asyncio.Queue 桥接 graph 内部 token 与外部 SSE / Router 加 `drafter_token` event / 前端 `ResumeTrigger` 实时预览 buffer + phase 切换重置
- ✅ **#2 Monaco 编辑器 + 版本 diff**:加 `GET/POST /v1/resumes/{id}/versions` + `ResumeVersionItem` schema(generated/edited/regenerated)+ `create_resume_version` 自增 version_number + UPDATE resumes.markdown / 前端 `@monaco-editor/react`(`ssr:false` 动态导入)+ MarkdownEditor + MarkdownDiff 包装 / ResumeDetail 加编辑模式 + 版本历史卡 + 历史预览 + DiffPanel(side-by-side)
- 🔄 **#3 Reviewer 标记交互**:可点 finding 行 ✅ + 滚动到对应章节 ✅(H2 `id=section-{slug}` 7 个章节)+ 一键采纳(`stripQuoted` literal substring 删除 + 标点/空行/空 bullet 收尾,失败提示用户去编辑器手改)✅ + 忽略(UI 局部)✅ + obsolete 检测(quoted_text 已不在 markdown → "已处理"灰化)✅ — **黄底高亮 `<mark>` 渲染失败 ⚠**:4 档 fallback 已写(精确 / fuzzy regex / 头尾双锚 / head-only 16 字符)但 DevTools 搜不到 `data-finding-highlight`,推测 matched 检测或 ctx 传递有问题。下一步加 console.log 定位
- ⏳ **#4 dataset + Judge + 对抗集**:`resume_generate` 25 条 dataset、对抗集 20 条、LLM-as-Judge(目标 fabrication recall ≥ 0.95)
- ⏳ **#5 W7 末 DoD 复测**:review 通过率 ≥ 50% / 无 high severity 幻觉

**当前 working tree**:即将 commit S21 子任务 1+2 + 子任务 3 部分(高亮 bug 待续)后清空。

**当前生效 prompt**(W7 后端骨架后):
- `match_analyst` = v1.1.2(4 条规则简化版,消费 `or_group_id`)
- `resume_planner` = **v1.0.0(W7 新增)**— 章节计划 + emphasis_skills + de_emphasize,response_schema = ResumePlan
- `resume_drafter` = **v1.0.4(W7 新增)**— v1.0.3 基础上加 G(plan 联动)+ H(revise 修订规则);prompt 内分支:plan=null 时退化 v1.0.3;prev_findings=null 时按首次 draft
- `resume_reviewer` = v1.0.2(M2/M4/M5 判定收窄 + granularity 字段说明)
- `jd_parser` = v1.0.6(B.1 复合句式新规)
- `profile_parser` = v1.0.1

**当前闸门**(M2 末,W7 + W8 子任务 1+2+3 改动未跑闸):后端 `pytest -q` 321 passed + ruff / mypy 全过 + alembic 0012;前端 typecheck / biome / next build 全过。W7(S19/S20)+ W8(S21 子任务 1+2+3)**未跑测试**(用户手动验);dogfood 通过项:W7 端到端(revise 路径)、drafter token 流式(浏览器 SSE 帧 + 实时预览)、monaco 编辑保存创建 v2 + 版本切换 + diff;**未通过**:W8 子任务 3 黄底高亮渲染。所有数字推 W8 子任务 4 + W9 闸门一起跑。

**M1 完成**:[slices/M1-summary.md](slices/M1-summary.md) — 整体经验 + 25 条永久约束 + DoD 检查 + 给 M2 的数据底座。各切片归档:`slices/{S0.5,S1..S11}-*.md`。

**M2 完成**:[slices/M2-summary.md](slices/M2-summary.md) — 整体经验 + 6 条永久约束 + DoD 检查(部分未达阈,接受现状)+ 给 M3 的数据底座 + 未验证已发布清单。各切片归档:`slices/{S12-jd-list-and-nav,S13-S15-match-mvp,S16-resume-mvp-backend,S17-resume-mvp-frontend,S18-prompt-iterations-2026-05}.md`。

> 2026-05-01 LLM Provider 由 DeepSeek V4 切换到阿里云百炼 Qwen3.6,见 ADR-0003。ADR-0001 复审条件 1(余额 < ¥1)触发时回切。

## 下一刀:S21 子任务 3 收尾 + 子任务 4

子任务 1+2 已落地。子任务 3 高亮 bug **优先修复**,然后子任务 4 + W7 末 DoD 复测(子任务 5)一起跑。

### 子任务 3 黄底高亮 bug 排查思路(下次开机直接接)

`resume-detail.tsx` 内 `findQuotedInText` 4 档 fallback 都写好了,但 `<mark data-finding-highlight>` 不出现在 DOM。可能原因:
1. `activeFindingIdx` click 后没正确传到 `activeQuoted`(state 链断在某处) — 加 `console.log("activate", idx, quoted)` 在 `activateFinding` 验证
2. `ctx.remaining` 在某次 React strict mode 双渲染中先被吃光 — 检查 `ResumeRender` 内 `const ctx = ...` 是否每次重新创建(应该是)
3. `parseBlocks` 把 reviewer 引用的章节段落和实际 block 对不齐(eg. block.text 有 leading `### ` 而 quoted 头没有) — 加 `console.log("block", block.kind, block.text.slice(0,30))` 看 H3 是否被解析为 paragraph 含 `### ` 前缀
4. Inline 内 `findQuotedInText` 实际命中但 `<mark>` 因 React fragment 嵌套被并掉 / 父元素 z-index / overflow 盖住

最快验证:`Inline` 函数顶上加 `console.log("inline", text.slice(0,20), ctx.quoted?.slice(0,20), ctx.remaining)`,点一次 finding 行,看终端打多少条、ctx.remaining 是否都是 0、哪个 block 被检查。

### 子任务 4 dataset + Judge + 对抗集

`evals/suites/resume_generate/` 新建 25 条样本(JD + profile + 期望特征);`resume_review_adversarial/` 20 条对抗集;LLM-as-Judge prompt + harness;目标 fabrication recall ≥ 0.95。

### 子任务 5 W7 末 DoD 复测

跑 13-JD 第二轮 dogfood:review 通过率 ≥ 50% / 无 high severity 幻觉。

### W9 渲染与导出
LaTeX `awesome-cv` 中文化 + md → LaTeX 转换器 + `/v1/resumes/{id}/export?format=pdf|docx|md` + PDF 预览 + 字体 license 合规。

### W10 内测 v0.5
招募 30-50 内测 + 飞书反馈表单 + bad case 入库 + 性能收尾 + 里程碑长文 + Demo 视频 + GitHub Release v0.5。

### M3 退出标准
5 位内测每人 ≥ 3 份定制简历无阻塞 / Judge 综合分 ≥ 75 / Reviewer 通过率 ≥ 0.85 / P95 ≤ 60s 成本 ≤ ¥0.50 / Star ≥ 50 / prompt 已修订 ≥ 1 次。

### M3 启动前未决
- **Q-01** 简历 PDF 模板(PRD §9):默认 LaTeX `awesome-cv` 中文化,W9 启动前再确认

---

# 永久约束累积(影响后续 M3 切片设计)

> M1 沉淀 25 条已归档到 [slices/M1-summary.md](slices/M1-summary.md)。
> M2 沉淀 6 条已归档到 [slices/M2-summary.md](slices/M2-summary.md)。
> M3 起新约束在此区累积:

- **[来自 S19] LangGraph 节点不吞业务 / LLM 异常,由调度层(service)集中 mark_failed**:graph 节点 raise 后冒泡到 `service.run_generate_stream`(及后续类似调度函数),by class 分发错误码 + 调 `_mark_failed`(side-channel commit)+ raise。Graph 是状态推进器,不是错误处理器。
- **[来自 S19 / S20 修订] LangGraph state 字段不放运行时依赖**:LLMClient / Embedder / sessionmaker / LoadedPrompt 通过 `ResumeGraphDeps` 闭包到 node,不放 state。**state 允许放 SQLAlchemy detached ORM 行 + dataclass(LLMResult / RetrieveResult / ResumePlan / ResumeReview)**,因为 graph 编译**不带 checkpointer**(`workflow.compile()` 默认值)。S19 原方案 `MemorySaver` 在 W7 第一次 dogfood 触发 revise 路径时报 `Type is not msgpack serializable: Jd` —— langgraph 0.2.x 所有 checkpointer(含 MemorySaver)都走 `JsonPlusSerializer` + ormsgpack,ORM 行 / dataclass 不可序列化。日后真要加 checkpointer(中断恢复 / 长时任务),需配套自定义 serde 或把 state 降级为 plain dict / id 引用。
- **[来自 S19] Drafter prompt 接收 plan / prev_findings 两个可选透传段**:`plan=None` 时退化无 planner 形态(等价 v1.0.3),`prev_findings=None` 时是首次 draft(非 revise);任一非空都触发 prompt USER 段额外渲染段。后续 W8 monaco patch 流可复用 prev_findings 协议。
- **[来自 S21 子任务 1] LLMClient streaming 契约**:`Provider.complete(request, *, on_token=None)` + `LLMClient.complete(..., on_token=None)`,`on_token: Callable[[str], Awaitable[None]]`;Provider 实现见 single-pass content 累积仍走原 ProviderResponse 出口(token 累计 / cost / CallLogger 行为不变),retry / schema repair 内 `_call_with_retry` 透传。Drafter / 用户长 markdown 输出场景适用;Planner / Reviewer / JDParser / ProfileParser 等 schema 输出不开 streaming(token 流式无渲染价值,且 schema retry 重渲染会让前端缓冲乱)。
- **[来自 S21 子任务 1] graph 内 LLM 流式事件向上送 = asyncio.Queue + 后台 task 模式**:graph.astream 只在 node 边界 yield 事件,LLM token 是节点内部异步事件,不能挤回 astream;service 层用 `asyncio.Queue` 作 sidechannel,`_runner` 后台 task 跑 graph + 把 node_completed/final 入队,outer 协程消费 queue + yield SSE。失败语义保留:`runner_error: BaseException | None` 捕获后在主协程末尾按原 W7 except 链路 mark_failed + raise(LLMUpstream 502 / SchemaInvalid / ResumeGenerationFailed)。客户端断开时 `runner_task.cancel()` + `contextlib.suppress(BaseException) await runner_task` 保证清理。
- **[来自 S21 子任务 2] Resume 编辑 = 创建新 ResumeVersion + UPDATE resumes.markdown 同步**:用户编辑保存走 `POST /v1/resumes/{id}/versions {markdown, note?}`,service `create_resume_version` 在同事务里 `INSERT resume_versions (next_version, edit_type='edited')` + 把新 markdown 同步回 `resumes.markdown`(让 GET /resumes/{id} 默认拿活动版本,无需引入 `active_version_id` 列)。`resume.review_findings` **不**清空 — 那是 reviewer 跑的快照,跟用户手改无关;前端遇 quoted_text 已不在 markdown 中时给 finding 行打"已处理"灰化标签做 obsolete 提示。Resume status ∈ {ready, review_failed} 时才允许编辑(failed/generating 不允许)。
- **[来自 S21 子任务 2] Frontend wire 类型在 OpenAPI 同步前手写 inline,标注 TODO**:新加的 ResumeVersionItem / ResumeVersionListResponse / ResumeVersionCreateInput 在 `apps/web/src/lib/api.ts` 里手写;用户跑 `pnpm gen:api`(连 running API 拉 openapi.json)后再切到 `components['schemas']['ResumeVersionItem']` 生成版,与 jds/profiles/matches 等保持一致。后续切片新加 endpoint 都先手写 + 注释,等批量 gen:api 时一次切。

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

风格规矩(中文为主 / 不估工时 / 不加 Co-Author / 测试由用户手动跑)见 `CLAUDE.md`。

---

# 文档清单

| 文件 | 用途 |
|------|------|
| `1-PRD.md` / `2-TECH_DESIGN.md` / `3-DATA_MODEL.md` / `4-API_SPEC.md` / `5-AGENT_DESIGN.md` / `6-EVAL_PLAN.md` / `7-ROADMAP.md` / `8-ENGINEERING.md` | 设计文档,**只在写对应代码时按需读相关章节** |
| `slices/M1-summary.md` | M1 收官总结(整体经验 + 25 条永久约束 + DoD 检查) |
| `slices/M2-summary.md` | M2 收官总结(整体经验 + 6 条永久约束 + DoD 检查 + 未验证已发布清单) |
| `slices/{S0.5,S1..S11}-*.md` | M1 各切片归档 |
| `slices/{S12-jd-list-and-nav,S13-S15-match-mvp,S16-resume-mvp-backend,S17-resume-mvp-frontend,S18-prompt-iterations-2026-05}.md` | M2 各切片归档 |
| `slices/S19-S20-w7-resume-graph.md` | M3 W7 切片归档(简历定制状态机 + 前端联动 + checkpointer serde 修) |
| `slices/{jd-parser-bugs-2026-05,jd-parser-prompt-v1.0.5,profile-parser-bugs-2026-05}.md` | M2 期间 prompt 沉淀(JDParser 26 类 bug → v1.0.5 / ProfileParser 6 类 bug → v1.0.1) |
| `adr/0001-only-deepseek` (Superseded by 0003) / `0002-postgres-as-vector-db` / `0003-switch-to-qwen` / `0004-llm-client-contract` / `0005-files-upload-contract` / `0006-jd-parse-contract` | 架构决策;下一个编号 0007 |
| `runbook/` | 部署期再写,目前空 |

---

# 上次会话遗留的开放问题(PRD §9)

- **Q-01** 简历 PDF 模板(默认:LaTeX `awesome-cv` 中文化)— **M3 启动前决策**(M3 涉及简历下载)
- **Q-02** 投递追踪日历提醒(默认:不做)— M4 启动前决策(投递追踪在 M4)
- **Q-03** MCP Server 工具粒度(默认:5 tool + 1 resource)— M5 启动前决策
- **Q-04** Web demo BYOK 在线试用(默认:做)— M6 启动前决策
