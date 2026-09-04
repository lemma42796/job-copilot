---
title: JobCopilot 当前与未完成任务
owner: lemma42796
last_updated: 2026-09-05
purpose: 只记录当前任务和尚未完成、仍计划执行的任务。
---

# 维护规则

- `STATUS.md` 记录已经发生的当前事实;本文只记录还要做的事。
- 每项任务写目标、依赖和状态;改动点与验收标准写在 `CONCURRENCY_PLAN.md` 对应阶段,本文不复制。
- 完成后从本文移除;完成历史由 Git commit / tag 承担。
- 阶段间存在强依赖,不可跳序。

# 当前任务

## P0:更新 TECH_DESIGN 系统范围

状态:**未开始**

目标:把系统范围从“单用户、本地优先、不做多用户 SaaS”改写为并发改造后的定位,否则与 `CONCURRENCY_PLAN.md` 互相矛盾。

依赖:无。P1 的前置条件。

## P1:长任务从在线请求中剥离

状态:**未开始**

目标:在线接口落库建 job 后立即返回,不触碰 LLM;SSE 只订阅进度,断线可重连续看。新增 job / job event 模型与 migration。

依赖:P0。

## P2:拆出独立 worker 与真队列

状态:**未开始**

目标:embed worker 移出 API 进程,引入 Redis Streams 与独立 worker 镜像,任务处理能力可按副本数扩展;以 job_id 做幂等去重。

依赖:P1。会引入 Redis,需同步更新 README 与 `AGENTS.md` 中的服务数记录。

# 未完成任务

以下阶段在 P1–P3 落地后再评估,当前不启动。

## P3:全局并发闸门改为 Redis 实现

目标:多进程多副本下全局控制上游并发,按 QPS 与 TPM 双维度限制。依赖 P2。

## P4:多进程与连接池显式配置

目标:gunicorn 多 worker 吃满多核,显式配置 `pool_size` / `max_overflow` 并校验总连接数。依赖 P2。

## P5:减少上游调用

目标:语义缓存近似命中、跳过不必要的 rewrite / rerank、embed 批处理并发化,且不丢失成本归因。无强依赖,可与 P4 并行。

## P6:过载保护

目标:两层限流、队列水位 503、上游 429 熔断、任务 deadline。依赖 P2;限流必须与 P1–P3 同期上线,不可后置。

## P7:压测与基线

目标:引入 k6 或 locust,产出可复现压测报告,用实测值替换 `CONCURRENCY_PLAN.md` 中全部推算数字。依赖 P1–P6。

# 不在本文档范围

- 项目最新状态 → `STATUS.md`
- 稳定技术架构与约束 → `TECH_DESIGN.md`
- 并发改造的改动点与验收标准 → `CONCURRENCY_PLAN.md`
- 评测方法与证据 → `../evals/EVAL_GUIDE.md`
