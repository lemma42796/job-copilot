---
title: JobCopilot 当前与未完成任务
owner: lemma42796
last_updated: 2026-08-15
purpose: 只记录当前任务和尚未完成、仍计划执行的任务。
---

# 维护规则

- `STATUS.md` 记录已经发生的当前事实;本文只记录还要做的事。
- 每项任务必须写目标、依赖、验收标准和状态,不写工时估算。
- 完成后从当前区移除;完成历史由 Git commit / tag 承担。
- 已取消或明确不做的事项不保留为待办。
- 涉及架构、数据模型或公共接口的任务,先确认设计再实现。

# 当前任务

## M2.6:JD Analysis Reliability 验证

状态:**轻量实现已完成,待用户手动验证**。

目标:确认 JD 分析断线恢复、进程重启恢复、JD 任务并发闸门、共享 LLM 并发背压和结构化日志符合单进程 MVP 边界。

依赖:

- 可用的 PostgreSQL、DashScope 配置和本地前后端运行环境。
- 用户明确执行测试 / typecheck / build 或真实模型验证。

验收标准:

- [ ] SSE 收到稳定 `analysis_id` 后断开,任务继续执行;重新连接 `/api/jd-analyses/{analysis_id}/events` 可拿到终态。
- [ ] API 重启后,数据库中的 `in_progress` 分析会重新启动并进入 `done / failed`。
- [ ] `JOBCOPILOT_JD_ANALYSIS_MAX_CONCURRENCY=1` 时,同时提交的 JD 分析不会并行运行。
- [ ] 普通 Agent 和 AnswerJudge 工具调用共用 `JOBCOPILOT_LLM_MAX_CONCURRENCY` 背压。
- [ ] 日志可按 `analysis_id` 查看开始、完成 / 中断、延迟、Token、成本和错误终态。
- [ ] 用户要求后运行新增并发单元测试、相关后端测试和前端 typecheck。

# 未完成任务

当前没有其他已确认要实施的开发任务。独立 Worker、lease / heartbeat、接口幂等、多实例接管和完整容量体系不在本轮计划内。

# 不在本文档范围

- 项目最新状态 → `STATUS.md`
- 稳定技术架构与约束 → `TECH_DESIGN.md`
- 评测方法与证据 → `../evals/EVAL_GUIDE.md`
