---
title: JobCopilot 项目最新状态
owner: lemma42796
last_updated: 2026-08-15
purpose: 只记录项目当前已经实现、已经验证的最新事实。
---

# 当前状态

- M2.6 已按轻量可靠性范围完成代码实现,当前等待用户手动验证。
- M2.5 保持现状,不再追加 learning path prompt、map-reduce trace 或其他可选打磨。
- 当前实现是单进程轻量后台任务,不等同于独立 Worker、分布式任务队列或多实例高可用。
- 当前分支为 `main`。

# 当前已实现能力

- Markdown 笔记导入、树形导航、编辑、heading-aware chunk 和异步 embedding。
- 主题 query → hybrid RAG → QuizGenerator → AnswerJudge 三层评分。
- InterviewCoach 多轮纠偏、补答重评、教练追问、session 恢复和整场总结。
- 文本 JD 入库、批量技术栈聚合、知识库覆盖矩阵、学习路径、quiz topics 和历史报告。
- `/jds` 报告筛选、覆盖证据、“优先补齐”清单和 topic 批量进入 `/quiz`。
- JD 分析执行与 SSE 观察已解耦:断线不取消任务,可按稳定 `analysis_id` 恢复订阅,API 重启后会重新启动 `in_progress` 分析。
- JD 分析和文本 LLM 调用分别有进程内并发闸门;普通 Agent 与 AnswerJudge 工具调用链共享 LLM 并发额度。
- LLM 与 JD 分析生命周期写结构化日志,包含功能、关联 id、延迟、Token、成本、缓存和成功 / 失败终态。

# 最近可信证据

- M2.1 tag:`v0.5-m2.1-end`。
- JD dogfood:`analysis#6(done)` 使用 30 条合成 JD 跑通真实外部 LLM,输出 45 个技术要求和 12 个 quiz topics;用户已在浏览器查看。
- JD Coverage:10 条人工标签的 macro F1 67.7%、missing recall 100.0%、false covered rate 0.0%、evidence P/R/MRR@5 为 77.5% / 100.0% / 87.5%。
- Interview Coach:最近保存的离线 flow smoke 为 10/10;它只证明固定 harness 行为。
- Hybrid Search:最近保存的 12-case smoke 旧 pass rule 为 12/12,但 final context precision 只有 41.75%,未达到 70% 目标。
- M2.6 本轮只完成源代码静态审阅和 `git diff --check`;尚未运行新增并发测试、typecheck、构建或浏览器断线 / 重启验证。

# 当前边界

- JD 分析只保证单 API 进程内的并发限制和恢复;多 API 实例可能重复领取同一 `in_progress` 记录。
- 进度事件只保存在进程内有界缓冲区,数据库持久化的是任务状态和最终报告,不是逐步 progress。
- 当前没有待执行容量上限、接口幂等、lease / heartbeat 或独立 Worker。
- Hybrid Search 固定 smoke 不证明任意 query 泛化能力。
- JD Coverage 只有 analysis#6 的 10 条标签,不证明任意 JD / 知识库质量。
- 单用户本地 dogfood 结果不能外推为多用户、线上高可用或大规模并发。
- M2.6 代码尚未经过 pytest / mypy / ruff / typecheck / build / Playwright 或真实模型故障注入验证。

# 相关入口

- 当前与未完成任务 → `TASKS.md`
- 技术架构 → `TECH_DESIGN.md`
- 评测规范 → `../evals/EVAL_GUIDE.md`
