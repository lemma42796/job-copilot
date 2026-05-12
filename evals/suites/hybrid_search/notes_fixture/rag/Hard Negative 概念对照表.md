# Hard Negative 概念对照表

## 目的

这份专门制造相近概念对照,用于 RAG hard negative 和 Judge 边界评测。很多错误不是完全无关,而是把相似概念混在一起。

## Context Cache vs Session Memory

Context Cache:

- provider 侧缓存。
- 缓存公共 prompt 前缀。
- 目标是降低重复计算/计费。
- 有 TTL。
- 不是业务状态。

Session Memory:

- 应用侧会话记忆。
- 保存已问问题、用户答案、评分、弱点和追问状态。
- 需要落 DB 或可恢复存储。
- 刷新后要能恢复。

常见错答:

```text
开启 Context Cache 后不用保存 session。
```

纠正:

```text
请求仍需携带必要上下文或可引用缓存前缀,业务状态仍由应用维护。
```

## SSE vs WebSocket vs MQ

SSE:

- 服务端到客户端单向推送。
- 基于 HTTP。
- 适合进度事件和 token streaming。

WebSocket:

- 双向通信。
- 适合实时协作、游戏、双向控制。

MQ:

- 服务端内部异步消息。
- 面向 worker/service。
- 支持削峰、重试、解耦。

常见错答:

```text
SSE 可以替代 MQ 做后台任务可靠投递。
```

纠正:

```text
SSE 是通知通道,不是可靠任务队列。
```

## Reranker vs Query Rewrite

Query Rewrite:

- 在召回前改写用户 query。
- 目标是提高召回。
- 可生成多个子 query。

Reranker:

- 在召回后排序候选文档。
- 目标是提高精排相关性。
- 不能救回未召回证据。

常见错答:

```text
reranker 会自动扩展 query。
```

纠正:

```text
扩展 query 是 rewrite/recall 阶段的事。
```

## Prompt Version vs API Version

Prompt Version:

- 控制 LLM 指令和 few-shot。
- 影响输出质量、分数、格式稳定性。
- 需要和评测结果绑定。

API Version:

- 控制接口契约。
- 影响前后端兼容。
- 需要 schema 和客户端适配。

常见错答:

```text
Prompt 改了不用记录,API 没变就行。
```

纠正:

```text
Judge prompt 改动会影响评分,必须记录 prompt_version。
```

## Outbox vs MQ

Outbox:

- 本地事务事件表模式。
- 解决业务写库和待发送事件一致性。
- 需要 relay 发送 MQ。

MQ:

- 消息中间件。
- 负责投递、消费、削峰。

常见错答:

```text
用了 MQ 就不需要 Outbox。
```

纠正:

```text
MQ 不能自动保证本地 DB 事务和消息发送一致。
```

## MVCC vs Cache

MVCC:

- 数据库并发控制。
- 多版本可见性。
- 让快照读不阻塞写。

Cache:

- 性能优化。
- 存储热点结果。
- 有失效和一致性问题。

常见错答:

```text
MVCC 就是数据库内部缓存。
```

纠正:

```text
MVCC 维护行版本和事务可见性,不是查询结果缓存。
```

## Token Bucket vs Leaky Bucket

Token Bucket:

- 按速率生成 token。
- 允许突发。

Leaky Bucket:

- 固定速率流出。
- 更平滑。

常见错答:

```text
令牌桶和漏桶一样。
```

纠正:

```text
令牌桶允许攒 token 后突发,漏桶更强调恒定输出。
```

## 301 vs 302

301:

- 永久重定向。
- 客户端/搜索引擎可能缓存。

302:

- 临时重定向。
- 更适合需要统计和可控过期的短链。

常见错答:

```text
短链用 301 更快且没有副作用。
```

纠正:

```text
301 可能让后续请求绕过短链服务,影响统计和风控。
```

## Agent Scratchpad vs Durable State

Scratchpad:

- 模型/编排过程中的临时推理或中间状态。
- 可能不持久。
- 不适合作为恢复源。

Durable State:

- 数据库或可靠存储中的业务状态。
- 刷新和重试可恢复。

常见错答:

```text
Agent scratchpad 里有历史,所以不用存 DB。
```

纠正:

```text
scratchpad 不是可靠事实源,session 状态必须落库。
```

## Hard Negative 使用方式

评测样本应包含:

- query 指向 A,候选里有 B。
- 用户答案把 A/B 混淆。
- Judge 必须指出混淆并引用正确 evidence。
- RAG final context 不应被 B 挤占。

示例:

```text
query: Context Cache 能否恢复会话?
positive: Context Cache 不是会话记忆
hard negative: Redis cache / LRU cache / MVCC
```

