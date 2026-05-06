---
title: JobCopilot 项目当前进度(单一可信源)
owner: lemma42796
last_updated: 2026-05-06 — **M3 W8 子任务 1+2+3 完成 + 第二轮 dogfood 暴露反幻觉链路三层洞**:回归 5 条 resume(match #4/6/8/11)发现 (1) reviewer 假阳性 — 教育 / 语言 chunks 因 retrieve Top-K JD-anchored 召回不全被误判编造;(2) drafter 镜像 JD — 把 JD hard_skills 候选人不会的(C++/Java/OpenAI/Claude/LLaMA)抄进技能段;(3) **更深根因**:`_compose_hint` 文案在主动诱导 — 原版 "缺失关键技能(可在简历中补强相关项目/课程)" 等于命令 drafter 补漏。三层联合修:reviewer 走 profile 全量 chunks + candidate(prompt v1.0.3 加 M7 教育核查) + drafter prompt v1.0.5 加 D.3 严禁 JD-only 技能 + v1.0.6 加 hint 段防注入语 + `_compose_hint` 改反向警告语义("**严禁列入简历的技能**…")+ 前端区分 obsolete vs bogus("已处理" vs "标记可能有误")。验证:resume #21 (match #4 / JD #9) 一轮 0 finding 通过,JD-only 技能全消失,M1 跨 chunk 错配也顺带消(根因不在 prompt 而在 hint 引导污染)。子任务 4(对抗集 + Judge)+ 子任务 5 未起。
purpose: 跨会话续作的状态快照。任何新会话从这里开始读。
---

# 当前阶段

**M3 简历定制 GA — W7 完成,W8 进行中**

| 切片 | 内容 | 状态 |
|------|------|------|
| S19+S20 | W7 简历定制状态机 + 前端联动 + checkpointer serde 修 | ✅ [slices/S19-S20-w7-resume-graph.md] |
| S21  | W8 反幻觉 + 可编辑(对抗集 + monaco + version diff + LLM-as-Judge + drafter token 流式)| 🔄 子任务 1+2+3 ✅ / 4+5 ⏳ |
| S22  | W9 渲染与导出(LaTeX awesome-cv + PDF 导出)| ⏳ |
| S23  | W10 内测 v0.5(招募 + 飞书反馈 + 性能收尾 + Release)| ⏳ |

**S21 W8 子任务进度**:
- ✅ **#1 drafter token 流式**:`LLMClient.complete` 加 `on_token` 回调 / DashScope `stream=True` + `include_usage` / DummyProvider 32 字符切片模拟 / `ResumeGraphDeps.on_drafter_token(phase, delta)` 闭包 / `service.run_generate_stream` 用 asyncio.Queue 桥接 graph 内部 token 与外部 SSE / Router 加 `drafter_token` event / 前端 `ResumeTrigger` 实时预览 buffer + phase 切换重置
- ✅ **#2 Monaco 编辑器 + 版本 diff**:加 `GET/POST /v1/resumes/{id}/versions` + `ResumeVersionItem` schema(generated/edited/regenerated)+ `create_resume_version` 自增 version_number + UPDATE resumes.markdown / 前端 `@monaco-editor/react`(`ssr:false` 动态导入)+ MarkdownEditor + MarkdownDiff 包装 / ResumeDetail 加编辑模式 + 版本历史卡 + 历史预览 + DiffPanel(side-by-side)
- ✅ **#3 Reviewer 标记交互**:可点 finding 行 + 滚动到对应章节(H2 `id=section-{slug}` 7 个章节)+ 一键采纳(`stripQuoted` literal substring 删除 + 标点/空行/空 bullet 收尾,失败提示用户去编辑器手改)+ 忽略(UI 局部)+ obsolete 检测 + 黄底 `<mark>` 高亮(匹配做在整段 md 拿全局偏移 / parseBlocks 给每个 block + bullet item 记 textOffset / 渲染时每个 block 取本段交集 / fallback 2 用 normalized substring 反向索引)
- ✅ **#3.1 第二轮 dogfood 反幻觉链路修订(衍生)**:5 match 重生成回归暴露 (a) reviewer 走 retrieve Top-K JD-anchored 漏 education / language → [M4] 假阳性;(b) drafter 镜像 JD hard_skills(C++/Java/OpenAI/Claude/LLaMA);(c) `_compose_hint` 文案诱导补漏。三层联合修:**reviewer 全量** — `retrieval_service.load_all_profile_chunks` + `ResumeGraphState.all_chunks` + `review_resume(candidate=...)` + reviewer prompt **v1.0.3**(profile 完整 + Profile 字段 + 新 M7);**drafter 反镜像** — prompt **v1.0.5** 加 D.3 严禁 JD-only 技能 + 机械自检 + 心智模型,**v1.0.6** 加 hint 段防注入语;**hint 反向文案** — `_compose_hint` 从"补强相关项目/课程"改成"**严禁列入简历的技能**(候选人 chunks 没有,JD 要 — 列了就是编造)";**前端 obsolete 区分** — 用历史 versions.markdown 做"曾经出现过"判定,任何版本都没的标 bogus("标记可能有误"黄)而非 obsolete("已处理"绿)。**验证**:resume #21 (match #4 / JD #9) 一轮 0 finding,JD-only 技能全消失,M1 跨 chunk 错配同步消(根因在 hint 引导污染,不在 prompt 加约束)
- ⏳ **#4 dataset + Judge + 对抗集**:`resume_generate` 25 条 dataset、对抗集 20 条、LLM-as-Judge(目标 fabrication recall ≥ 0.95)。**对抗集种子**(本次 dogfood 收集):#18 "具备高并发架构设计能力"(M4 模糊能力陈述 / 用 chunks 间接证据)、#19 C++/Java/OpenAI/Claude/LLaMA 抄 JD(已被 v1.0.5/v1.0.6 修但应作回归 case)、#20 "12w QPS 保障 AI 服务高可用"(M1 跨 chunk 业务 context 错配,已被 hint 文案修)、#20 reviewer 凭空捏 "AWS"(reviewer 模型 noise,留给 LLM-as-Judge 评测)
- ⏳ **#5 W7 末 DoD 复测**:review 通过率 ≥ 50% / 无 high severity 幻觉

**当前 working tree**:即将 commit 第二轮 dogfood 反幻觉三层修(reviewer 全量 chunks + drafter v1.0.5/v1.0.6 + hint 文案 + bogus UI 区分)后清空。

**当前生效 prompt**(W8 第二轮 dogfood 修订后):
- `match_analyst` = v1.1.2(4 条规则简化版,消费 `or_group_id`)
- `resume_planner` = v1.0.0(W7 新增)— 章节计划 + emphasis_skills + de_emphasize,response_schema = ResumePlan
- `resume_drafter` = **v1.0.6(W8 第二轮 dogfood)**— v1.0.5 D.3 严禁 JD-only 技能 + 机械自检 + 心智模型("简历 = 真实能力 ∩ JD 关心方向"子集);v1.0.6 加 hint 段防注入语,配合 service `_compose_hint` 反向警告文案
- `resume_reviewer` = **v1.0.3(W8 第二轮 dogfood)**— v1.0.2 基础上把"chunks 是召回子集"改为 profile 全量 + candidate Profile 字段(同等可信),加 M7 教育与 Profile 字段比对
- `jd_parser` = v1.0.6(B.1 复合句式新规)
- `profile_parser` = v1.0.1

**当前闸门**(M2 末,W7 + W8 子任务 1+2+3 + 第二轮 dogfood 修订未跑闸):后端 `pytest -q` 321 passed + ruff / mypy 全过 + alembic 0012;前端 typecheck / biome / next build 全过。W7(S19/S20)+ W8(S21 子任务 1+2+3 + 第二轮修订)**未跑测试**(用户手动验);dogfood 通过项:W7 端到端、drafter token 流式、monaco 编辑/版本/diff、reviewer 标记可点+滚动+采纳+黄底高亮、第二轮 dogfood 5 个 match 重生成全 ready(resume #16/#17/#21 一轮 passed,#18/#19 暴露 drafter 真问题但已被 v1.0.5/v1.0.6 修)。所有数字推 W8 子任务 4 + W9 闸门一起跑。

**M1 完成**:[slices/M1-summary.md](slices/M1-summary.md) — 整体经验 + 25 条永久约束 + DoD 检查 + 给 M2 的数据底座。各切片归档:`slices/{S0.5,S1..S11}-*.md`。

**M2 完成**:[slices/M2-summary.md](slices/M2-summary.md) — 整体经验 + 6 条永久约束 + DoD 检查(部分未达阈,接受现状)+ 给 M3 的数据底座 + 未验证已发布清单。各切片归档:`slices/{S12-jd-list-and-nav,S13-S15-match-mvp,S16-resume-mvp-backend,S17-resume-mvp-frontend,S18-prompt-iterations-2026-05}.md`。

> 2026-05-01 LLM Provider 由 DeepSeek V4 切换到阿里云百炼 Qwen3.6,见 ADR-0003。ADR-0001 复审条件 1(余额 < ¥1)触发时回切。

## 下一刀:S21 子任务 4 + 5

子任务 1+2+3 已落地。下一步子任务 4(对抗集 + Judge)+ W7 末 DoD 复测(子任务 5)一起跑,做完整个 S21 收官。

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
- **[来自 S21 子任务 3] LLM 复述类引用(reviewer.quoted_text 等)的高亮匹配做在原始整段 markdown 上,不分块后逐块匹配**:旧实现在每个 `block.text` 上跑 `findQuotedInText`,reviewer 引文经常跨 block(整章节 / 含 `## H2` 标题 / 多个 bullet),逐块匹配各档 fallback 全失败,DOM 里搜不到 `<mark>`。新模式:在整段 md 上一次拿全局 `[start, end)` 偏移,`parseBlocks` 给每个 block / bullet item 记 `textOffset`,渲染时让每个 block 取本段范围内的交集做高亮 — 跨段引用在每个相关 block 里各自高亮自己那一截。fuzzy regex 用 normalized substring(去空白 + 中英标点等价化 + 反向索引映回原始偏移)替代,边界更稳。同时去掉了 `ctx.remaining` 这种渲染期可变状态。后续如果给面试模拟做 reviewer-style 高亮(引用题目片段)应复用此模式。
- **[来自 S21 第二轮 dogfood] Reviewer 是单文档全文事实核查,不走 JD-anchored Top-K 召回**:reviewer 看到的 chunks 必须是 profile **全量**(`load_all_profile_chunks`),不能复用 drafter 用的 `retrieve_for_match` Top-K 结果。原因:Top-K 按 JD 相关性排序,JD 偏 AI Agent → 教育 chunks / "language" 类 skill chunks(Python/Go)被挤出 Top-K → reviewer 视角"chunks 中无证据" → [M4] 假阳性。同时 reviewer **必须**也拿 `candidate` 字段(profile 表上 deterministic 数据,姓名 / 联系方式 / 求职意向 / educations,**永远不在 chunks 里**),否则教育 / 基本信息会被误判编造。Reviewer prompt v1.0.3 起把这两点纳入,加 M7 "教育 / 基本信息核查与 Profile 字段比对"。后续如果给其他事实核查类 agent(面试评分 / 投递评估)设计 chunks 接口,**默认全量 + deterministic 字段并发**,不要复用 drafter 的相关性召回。
- **[来自 S21 第二轮 dogfood] hint 是 LLM 视角的权威指令位,文案必须反向警告而非"补强"诱导**:`_compose_hint(match)` 把 `gap_summary + missing_skills` 拼成 drafter prompt USER 段的 hint 文本。原版"缺失关键技能(可在简历中补强相关项目/课程)"等于明确命令 drafter 把候选人不会的技能写进简历(JD 镜像),导致 #19 列 C++/Java/OpenAI/Claude/LLaMA 全编造,#20 间接 leak "AI 服务高可用"。修订后必须用反向警告语义:gap_summary 加 "**只读差距分析**(不要写入简历;gap 信息归 match 模块负责告知用户)";missing_skills 改 "**严禁列入简历的技能**(候选人 chunks 没有,JD 要 — 列了就是编造)"。drafter prompt v1.0.6 USER 段 hint 块标题也同步从 "## 历史匹配差距提示(参考,辅助强化简历对 JD 的针对性)" 改 "## 差距警示段(**只读 — 严禁成为简历内容来源**)" + 防注入指引段。**心智模型**:简历 = 候选人真实能力 ∩ JD 关心方向;凸显交集,不补缺集。Gap 信息归 match 模块,不归简历。后续如果有别处往 prompt USER 段注入"差距 / 缺失 / 待补"类信息,文案默认走反向警告,而非鼓励补漏。
- **[来自 S21 第二轮 dogfood] Reviewer findings UI 需要区分 obsolete vs bogus 两态**:reviewer 标记的 quoted_text 在当前 markdown 找不到时分两种语义,前端必须区分:(a) **obsolete** = 在某个**历史版本**里出现过,但当前已不在(用户编辑 / 采纳掉了)→ 标"已处理"绿色;(b) **bogus** = 任何版本都没出现过(reviewer 凭空捏造 quoted_text,本会话见过 reviewer 凭空写"AWS")→ 标"标记可能有误"黄色。`obsoleteFindings` 不能只看当前 `resume.markdown`,要把所有 `versions[].markdown` 拼成"曾经出现过"全集。两态显示行为合并(都灰化 + 隐藏"采纳"按钮 + dismiss 改"从列表移除"),但用户提示语必须不同 — bogus 显示"已处理"会误导用户以为自己处理过(用户没动过)。后续如果有别处展示 LLM 引用 + 当前文档的对照 UI,默认要 (a)/(b) 区分,不要简单"是否在当前文本"二分。

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
