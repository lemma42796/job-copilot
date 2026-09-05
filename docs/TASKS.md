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

# 已确定的商业形态

- 多用户线上服务,共用平台 API key,用户不接触 key。
- 用户预充值余额,按官方 API 价格实扣,余额耗尽即禁用。
- 充值链路本阶段只做模拟,不实现真实支付。重点在并发改造。

# 当前任务

## P0:用户体系与数据隔离

状态:**未开始**

目标:新增 user 表与认证,所有业务表补 `user_id` 与索引,所有查询加归属过滤,笔记文件按用户分目录,同步更新 `TECH_DESIGN.md` 系统范围为多用户线上服务。

依赖:无。其余所有阶段的前置条件。**本方案风险最高的一项** — 漏一处查询过滤即数据泄露,没有中间状态。

## P1:余额账本与扣费闸门

状态:**未开始**

目标:新增余额表与流水表,充值做模拟实现;rerank 与 embedding 两条链路接入 `llm_calls` 记账并透传 `user_id`;调用前检查余额、调用后按 `cost_cny` 实扣;余额耗尽禁用新任务。

依赖:P0。**P2 的前置条件,顺序不可颠倒** — 上游 TPM 余量约 83 倍不会帮我们刹车,先放并发后加扣费等于账单裸奔。余额中途耗尽即就地中止、保留半成品,不做任务级预检查。三个设计问题(成本后验、中止终态、三链路覆盖)见 `CONCURRENCY_PLAN.md`。

## P2:并发参数与连接池调整

状态:**未开始**

目标:`JOBCOPILOT_LLM_MAX_CONCURRENCY` 由 4 调整为 32,`infra/db.py` 显式配置 `pool_size=20` / `max_overflow=20`。改动量为一个环境变量加两个参数,预期同时在线用户数从个位数提升到二三十。

依赖:P1。

# 未完成任务

以下阶段在 P0–P2 落地后再评估,当前不启动。

## P3:长任务从在线请求中剥离

目标:四个 SSE 接口(出题、答题回合、结束总结、提交评分)全部改为落库建 job 后立即返回,SSE 只订阅进度,断线可重连续看。新增带 `user_id` 的 job / job event 模型与 migration。依赖 P0。

## P4:拆出独立 worker 与真队列

目标:embed worker 移出 API 进程,引入 Redis Streams 与独立 worker 镜像,以 job_id 做幂等去重防重复扣费,embed 领取查询补 `FOR UPDATE SKIP LOCKED`。依赖 P3。会引入 Redis,需同步更新 README 与 `AGENTS.md` 中的服务数记录。

## P5:多进程

目标:gunicorn 多 worker 吃满多核,重新校验总连接数是否超出 PostgreSQL `max_connections`。依赖 P4。

## P6:减少上游调用

目标:语义缓存近似命中、跳过不必要的 rewrite / rerank、收敛 `MAX_JUDGE_ROUNDS`、embed 批处理并发化。收益是省用户余额而非提吞吐。需判断缓存是否跨用户共享(默认按用户隔离,防笔记内容泄露)。无强依赖,可与 P5 并行。

## P7:过载保护

目标:队列水位 503、上游 429 熔断、任务 deadline。成本维度已由 P1 覆盖,本阶段补容量维度。依赖 P4。

## P8:压测与基线

目标:引入 k6 或 locust,补测单次调用真实 token 分布与单场面试真实成本,用实测值替换 `CONCURRENCY_PLAN.md` 中全部推算数字。依赖 P0–P7。

# 已推迟

## 全局并发闸门改为 Redis 实现

实测上游允许约 333 个并发调用,8 进程 × 32 闸门合计 256 仍在余量内,该阶段暂不产生收益。触发条件与届时改动点见 `CONCURRENCY_PLAN.md` 附录。

# 不在本文档范围

- 项目最新状态 → `STATUS.md`
- 稳定技术架构与约束 → `TECH_DESIGN.md`
- 并发改造的改动点与验收标准 → `CONCURRENCY_PLAN.md`
- 评测方法与证据 → `../evals/EVAL_GUIDE.md`
