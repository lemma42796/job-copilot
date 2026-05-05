---
title: JobCopilot 项目当前进度(单一可信源)
owner: lemma42796
last_updated: 2026-05-05 — **M3 W7 完成**(S19 + S20 + checkpointer serde 修):5 节点 LangGraph(retrieve / plan / draft / review / revise)无 checkpointer + SSE 节点事件 + Planner v1.0.0 + Drafter v1.0.4 + 前端 4 节点 stepper + revise 提示;首次 dogfood(match #7,revise 路径)端到端通,顺手暴露并修了 MemorySaver ormsgpack 序列化挂。归档 [slices/S19-S20-w7-resume-graph.md]。第二轮 13-JD dogfood + W7 末 DoD 复测推 W8 / W9 一并跑。
purpose: 跨会话续作的状态快照。任何新会话从这里开始读。
---

# 当前阶段

**M3 简历定制 GA — W7 完成,W8 进行中**

| 切片 | 内容 | 状态 |
|------|------|------|
| S19+S20 | W7 简历定制状态机 + 前端联动 + checkpointer serde 修 | ✅ [slices/S19-S20-w7-resume-graph.md] |
| S21  | W8 反幻觉 + 可编辑(对抗集 + monaco + version diff + LLM-as-Judge + drafter token 流式)| ⏳ |
| S22  | W9 渲染与导出(LaTeX awesome-cv + PDF 导出)| ⏳ |
| S23  | W10 内测 v0.5(招募 + 飞书反馈 + 性能收尾 + Release)| ⏳ |

**当前 working tree**:即将 commit & push S19+S20 + serde 修后清空。

**当前生效 prompt**(W7 后端骨架后):
- `match_analyst` = v1.1.2(4 条规则简化版,消费 `or_group_id`)
- `resume_planner` = **v1.0.0(W7 新增)**— 章节计划 + emphasis_skills + de_emphasize,response_schema = ResumePlan
- `resume_drafter` = **v1.0.4(W7 新增)**— v1.0.3 基础上加 G(plan 联动)+ H(revise 修订规则);prompt 内分支:plan=null 时退化 v1.0.3;prev_findings=null 时按首次 draft
- `resume_reviewer` = v1.0.2(M2/M4/M5 判定收窄 + granularity 字段说明)
- `jd_parser` = v1.0.6(B.1 复合句式新规)
- `profile_parser` = v1.0.1

**当前闸门**(M2 末,W7 改动未跑闸):后端 `pytest -q` 321 passed + ruff / mypy 全过 + alembic 0012;前端 typecheck / biome / next build 全过。S19 + S20 + checkpointer serde 修**未跑测试**(用户手动验);单样本端到端 dogfood 通过(match #7,revise 路径)。所有数字推 W8 / W9 闸门一起跑。

**M1 完成**:[slices/M1-summary.md](slices/M1-summary.md) — 整体经验 + 25 条永久约束 + DoD 检查 + 给 M2 的数据底座。各切片归档:`slices/{S0.5,S1..S11}-*.md`。

**M2 完成**:[slices/M2-summary.md](slices/M2-summary.md) — 整体经验 + 6 条永久约束 + DoD 检查(部分未达阈,接受现状)+ 给 M3 的数据底座 + 未验证已发布清单。各切片归档:`slices/{S12-jd-list-and-nav,S13-S15-match-mvp,S16-resume-mvp-backend,S17-resume-mvp-frontend,S18-prompt-iterations-2026-05}.md`。

> 2026-05-01 LLM Provider 由 DeepSeek V4 切换到阿里云百炼 Qwen3.6,见 ADR-0003。ADR-0001 复审条件 1(余额 < ¥1)触发时回切。

## 下一刀:S21 — W8 反幻觉 + 可编辑

W7(S19 + S20)完成 → [slices/S19-S20-w7-resume-graph.md]。W8 主线:

- **drafter token 流式预览**:LLMClient `complete` 升 stream;前端 ResumeTrigger phase 状态机已埋好 hook,接新事件类型(`drafter_token` / 类似)即可
- **`resume_review` 对抗集 20 条 + LLM-as-Judge**(目标 fabrication recall ≥ 0.95)
- **markdown 编辑器**:monaco + live preview + 版本 diff / 切换
- **Reviewer 标记可点高亮 + 一键采纳 / 忽略**
- **`resume_generate` 端到端 25 条 dataset**(与 W7 推后的 13-JD 第二轮 dogfood 共用真实样本)
- **W7 末 DoD 复测**:review 通过率 ≥ 50% / 无 high severity 幻觉(W7 单样本不算数)

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
