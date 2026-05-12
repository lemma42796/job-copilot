# Outbox Saga 与事务消息

## 目的

分布式可靠性面试经常问:

- 本地事务和消息发送如何保持一致。
- 跨服务事务怎么做。
- 失败补偿如何设计。
- exactly once 是否现实。

Outbox、Saga、事务消息是重要概念。它们也能帮助理解 JobCopilot 后续如果把 Judge、embedding、eval 拆成后台任务时,如何避免状态和事件不一致。

## 本地事务 + 发消息的问题

常见错误:

```text
写 DB 成功 -> 发 MQ 失败
发 MQ 成功 -> 写 DB 失败
```

两步之间任何失败都会不一致。

例子:

```text
订单状态已支付,但未发通知消息。
通知消息发出,但订单未支付成功。
```

JobCopilot 类比:

```text
judge_result 写库成功,但 session_summary 更新事件丢失。
```

## Outbox Pattern

Outbox 把业务变更和待发送消息放在同一个本地事务里:

```text
begin
  update order status = paid
  insert into outbox(event_type, payload, status=pending)
commit
```

后台 relay 扫 outbox,发送 MQ,成功后标记 sent。

优点:

- 本地事务保证业务数据和 outbox 事件一致。
- relay 可重试。

缺点:

- 消费端仍可能重复收到。
- outbox 表需要清理。
- 发送延迟。

## CDC Outbox

另一种方式是通过 CDC 读取数据库变更日志:

- Debezium。
- binlog。
- WAL。

优点:

- 减少业务代码扫表。
- 事件接近实时。

缺点:

- 基础设施复杂。
- schema 变更影响。
- 运维成本。

早期项目通常先用 outbox 表 + worker 更简单。

## Inbox Pattern

消费端用 inbox 去重:

```text
processed_messages(message_id primary key)
```

消费时:

```text
begin
  insert message_id into processed_messages
  apply business effect
commit
```

如果 message_id 已存在,说明处理过,直接 ack。

这比相信 MQ 不重复更安全。

## 事务消息

一些 MQ 提供事务消息或半消息:

1. 发送半消息。
2. 执行业务事务。
3. 提交或回滚消息。
4. MQ 可回查事务状态。

优点:

- 框架封装一致性流程。

缺点:

- 绑定特定 MQ。
- 回查逻辑复杂。
- 仍需消费端幂等。

不要把事务消息理解成全链路 exactly once。

## Saga

Saga 用一系列本地事务和补偿动作实现长事务:

```text
预订机票 -> 预订酒店 -> 扣款
如果扣款失败 -> 取消酒店 -> 取消机票
```

两种编排:

- choreography:事件驱动,服务互相响应事件。
- orchestration:中心协调器决定下一步。

Saga 是最终一致,不是强一致。

## 补偿动作

补偿不是简单反向 SQL。

例子:

- 已发短信无法撤回,只能再发更正通知。
- 已退款不能随便扣回。
- 已调用 LLM 产生费用不能取消账单。

设计 Saga 时要确认每一步是否可补偿,补偿失败怎么办。

## TCC

TCC:

- Try:预留资源。
- Confirm:确认提交。
- Cancel:取消预留。

适合资源预留明确的场景,如库存、额度。

缺点:

- 业务侵入强。
- 每个参与方都要实现三套接口。
- 空回滚、悬挂、幂等等问题复杂。

大多数普通业务不必上 TCC。

## Exactly once

分布式系统里端到端 exactly once 很难。更现实的是:

- 消息 at-least-once。
- 消费端幂等。
- 业务唯一约束。
- 去重表。
- 可补偿。

面试中如果直接说"MQ 保证 exactly once"通常是不严谨的。

## JobCopilot 映射

当前 M2 可以同步完成 Judge 并写库。但未来如果:

- Judge 变后台任务。
- embedding 批处理。
- eval run 异步。
- 通知/提醒。

就需要任务表或 outbox 思路。

例:

```text
submit answer:
  事务内写 answer + judge_run(pending)
worker:
  调 LLM judge
  条件更新 judge_run + session summary
```

如果还要发事件给前端或通知系统,可用 outbox。

## 面试追问

问: Outbox 解决了什么问题?

答: 解决本地业务数据变更和消息发送之间的一致性问题。业务表更新和 outbox 事件插入在同一事务提交,relay 后台可靠发送。它不解决消费端重复问题,消费端仍要幂等。

问: Saga 和 2PC 有什么区别?

答: 2PC 追求分布式强一致,需要协调者和参与者锁资源,可用性和性能差。Saga 是多个本地事务加补偿,追求最终一致,适合长业务流程,但需要设计补偿和幂等。

问: 为什么 exactly once 很难?

答: 网络超时让发送方不知道接收方是否已处理,重试可能重复,不重试可能丢。现实做法是 at-least-once 投递 + 消费端幂等 + 业务唯一约束,让重复处理无副作用。

## Hard negatives

- Outbox 不是消息队列本身,是本地事务事件表模式。
- Saga 不是强一致事务。
- 补偿不是简单 undo。
- 事务消息不等于端到端 exactly once。
- SSE 不是 MQ,不能替代 outbox/relay。

