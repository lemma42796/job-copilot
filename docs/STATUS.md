---
title: JobCopilot 项目最新状态
owner: lemma42796
last_updated: 2026-08-15
purpose: 只记录项目当前已经实现、已经验证的最新事实。
---

# 当前状态

- 当前没有正在实施的任务。
- M2.5 保持现状,不再追加 learning path prompt、map-reduce trace 或其他可选打磨。
- M2.6 已形成任务草案但尚未开工,因此持久化 JD 后台任务、并发 / 背压、幂等和断线恢复都不能表述为现有能力。
- 当前分支为 `main`,与 `origin/main` 同步;working tree 有本轮文档重构的未提交改动。

# 当前已实现能力

- Markdown 笔记导入、树形导航、编辑、heading-aware chunk 和异步 embedding。
- 主题 query → hybrid RAG → QuizGenerator → AnswerJudge 三层评分。
- InterviewCoach 多轮纠偏、补答重评、教练追问、session 恢复和整场总结。
- 文本 JD 入库、批量技术栈聚合、知识库覆盖矩阵、学习路径、quiz topics 和历史报告。
- `/jds` 报告筛选、覆盖证据、“优先补齐”清单和 topic 批量进入 `/quiz`。

# 最近可信证据

- M2.1 tag:`v0.5-m2.1-end`。
- JD dogfood:`analysis#6(done)` 使用 30 条合成 JD 跑通真实外部 LLM,输出 45 个技术要求和 12 个 quiz topics;用户已在浏览器查看。
- JD Coverage:10 条人工标签的 macro F1 67.7%、missing recall 100.0%、false covered rate 0.0%、evidence P/R/MRR@5 为 77.5% / 100.0% / 87.5%。
- Interview Coach:最近保存的离线 flow smoke 为 10/10;它只证明固定 harness 行为。
- Hybrid Search:最近保存的 12-case smoke 旧 pass rule 为 12/12,但 final context precision 只有 41.75%,未达到 70% 目标。

# 当前边界

- JD 分析当前绑定 SSE 请求生命周期;客户端断开会取消未完成任务。
- 当前没有 JD 任务级并发上限、有界待执行队列、接口幂等或独立 Worker。
- Hybrid Search 固定 smoke 不证明任意 query 泛化能力。
- JD Coverage 只有 analysis#6 的 10 条标签,不证明任意 JD / 知识库质量。
- 单用户本地 dogfood 结果不能外推为多用户、线上高可用或大规模并发。
- 本轮只重构文档,未运行 pytest / mypy / ruff / typecheck / build / Playwright。

# 相关入口

- 当前与未完成任务 → `TASKS.md`
- 技术架构 → `TECH_DESIGN.md`
- 评测规范 → `../evals/EVAL_GUIDE.md`
