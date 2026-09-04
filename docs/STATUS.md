---
title: JobCopilot 项目最新状态
owner: lemma42796
last_updated: 2026-09-05
purpose: 只记录项目当前已经实现、已经验证的最新事实。
---

# 当前状态

- 产品功能链路已完成,当前形态为单用户、本地优先、单 API 进程。
- 下一阶段目标是并发改造,方案见 `CONCURRENCY_PLAN.md`;方案尚未开始实施。
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

# 当前边界

- 单 API 进程 MVP:并发闸门、任务恢复和进度缓冲都只在进程内生效,多实例可能重复领取同一 `in_progress` 记录。
- 后台 worker 跑在 API 进程内,尚未拆出;没有真队列、幂等键、lease / heartbeat 或待执行容量上限。
- 无限流、无熔断;过载行为未定义。
- 数据库连接池与 uvicorn 进程数均为默认值,未按并发目标显式配置。
- 单用户本地 dogfood 结果不能外推为多用户、线上高可用或大规模并发。
- `CONCURRENCY_PLAN.md` 中的瓶颈数字为读码推算,未经压测,不得对外引用。

# 相关入口

- 当前与未完成任务 → `TASKS.md`
- 技术架构 → `TECH_DESIGN.md`
- 并发改造方案 → `CONCURRENCY_PLAN.md`
- 评测规范与最新证据 → `../evals/EVAL_GUIDE.md`
