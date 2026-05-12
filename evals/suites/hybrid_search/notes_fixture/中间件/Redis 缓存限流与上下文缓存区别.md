# Redis 缓存限流与上下文缓存区别

## Redis 在 LLM 应用中的位置

Redis 常用于缓存、分布式锁、限流、队列和会话状态。LLM 应用里 Redis 也很常见:

- API response cache。
- embedding cache。
- rate limit counter。
- SSE 进度短暂缓存。
- background job queue。
- distributed lock。

但 JobCopilot 当前没有把 Redis 作为主线依赖。当前本地开发优先 Docker Postgres + 本机 API,减少组件数量。Redis 可以作为后续扩展,但不应为了"架构完整"提前引入。

## Redis 数据结构

常见结构:

| 结构 | 用途 |
|---|---|
| string | 缓存值、计数器 |
| hash | 对象字段 |
| list | 简单队列 |
| set | 去重集合 |
| sorted set | 排行榜、延迟队列 |
| stream | 消息流 |
| bitmap | 大规模布尔统计 |

LLM 应用中最常用的是 string、hash、sorted set 和 stream。

## 缓存模式

### Cache Aside

应用先查缓存,miss 后查数据库,再写缓存:

```text
read cache
if miss:
    read db
    write cache
```

优点是简单。缺点是要处理缓存击穿、穿透、雪崩和一致性。

### Write Through

写请求同时写缓存和数据库,由缓存层保证写入。

### Write Behind

先写缓存,异步写数据库。性能好但一致性风险高,不适合 quiz session 这种关键状态。

JobCopilot 的 session 状态不应只写 Redis 后异步落库。评分和恢复需要强持久化,应以数据库为准。

## 缓存穿透、击穿、雪崩

| 问题 | 含义 | 处理 |
|---|---|---|
| 穿透 | 查不存在 key,每次打 DB | 缓存空值、布隆过滤器 |
| 击穿 | 热 key 过期瞬间大量请求打 DB | mutex、singleflight |
| 雪崩 | 大量 key 同时过期 | 随机 TTL、分批预热 |

LLM 应用也会遇到类似问题。例如热门 prompt 或热门 embedding 同时过期,会突然打爆 provider。

## 限流

Redis 常用于分布式限流:

- fixed window。
- sliding window。
- token bucket。
- leaky bucket。

LLM provider 有 RPM/TPM 限制。应用自身也应做限流,避免把用户请求全部打到 provider 后才收到 429。

简单 token bucket:

```text
bucket capacity = 1000 tokens
refill rate = 100 tokens/sec
request cost = estimated tokens
```

LLM 限流最好按 token 估算,不只是请求数。一个 500 token 请求和一个 50000 token 请求成本不同。

## 分布式锁

Redis 分布式锁常见写法:

```text
SET key value NX PX 30000
```

释放时要检查 value,避免删掉别人的锁。更严谨需要 Lua 脚本。

但分布式锁不是万能药。对于数据库状态转换,行锁或乐观锁往往更直接可靠。JobCopilot 的 session submit/abandon 冲突更适合数据库约束,不一定需要 Redis 锁。

## Redis Stream

Redis Stream 可做轻量消息流:

- 生产者 XADD。
- 消费者组 XREADGROUP。
- ACK。
- pending list。

适合任务队列和事件流。但如果系统已经有 Celery/RQ/Kafka 或数据库任务表,不一定要加 Redis Stream。

JobCopilot 评测任务如果未来变长,可以考虑任务队列。当前补测阶段由用户手动触发脚本,不需要先引入 Redis。

## Context Cache 不是 Redis Cache

JobCopilot 中有一个容易混淆的概念:百炼 Context Cache。它和 Redis 缓存完全不同。

| 项 | Redis cache | Provider Context Cache |
|---|---|---|
| 位置 | 应用侧/基础设施 | 模型 provider 侧 |
| 缓存对象 | 任意 key-value | prompt prefix/context |
| 控制方式 | 应用读写 | provider API 参数 |
| 作用 | 降低 DB/provider 调用 | 降低重复 prompt 计算/计费 |
| 是否是记忆 | 不是 | 也不是 |

Context Cache 不是 session memory。请求仍需带必要上下文。

## JobCopilot 为什么当前默认关闭显式 Context Cache

项目事实:

- 百炼 Context Cache provider-side 命中已验证。
- 显式缓存 TTL 只有 5 分钟。
- M2 当前是一次性答题流,重复公共前缀收益有限。
- M2.1 多轮面试讨论更适合打开。

这不是 Redis 能不能用的问题。Redis 可以缓存应用数据,但不能替代 provider context cache 的前缀计算优化。

## 缓存一致性

缓存一致性常见策略:

- 先写 DB 再删缓存。
- 延迟双删。
- 设置短 TTL。
- 订阅 binlog。
- 只缓存可容忍短暂不一致的数据。

JobCopilot 中不适合缓存的:

- quiz session 当前状态。
- answer_judge 最终结果写入前的中间状态。
- 用户草稿唯一事实。

适合缓存的:

- embedding 结果。
- prompt template。
- 不敏感的静态配置。
- 短期 retrieval result,但要带 note corpus version。

## 缓存 key 设计

缓存 key 要包含影响结果的变量。

embedding cache key:

```text
embedding:{model_id}:{content_hash}
```

retrieval cache key:

```text
retrieval:{corpus_version}:{query_hash}:{pipeline_version}
```

Judge cache key:

```text
judge:{prompt_version}:{question_hash}:{answer_hash}:{chunk_hash}
```

如果 key 漏掉 prompt_version 或 model_id,缓存会返回旧逻辑结果。

## TTL 设计

TTL 取决于数据变化频率和一致性要求:

- prompt template:可长。
- embedding:可长,但 model_id 变化要隔离。
- retrieval result:中短,受笔记更新影响。
- session state:不建议只靠 cache TTL 管。
- rate limit counter:按窗口。

随机 TTL 可以防止雪崩。

## 限流与用户体验

遇到限流时,不要只返回 500。更好的用户体验:

- 告诉用户稍后重试。
- 保存草稿。
- 保留 session。
- 如果已经生成题目,不要丢失题目。
- SSE 发 `rate_limited` 事件。

限流不是异常小概率,而是 LLM 应用的常态工程约束。

## Redis 与 Langfuse

Redis 可用于成本计数或短期聚合,但 Langfuse trace 是可观测主线。不要把 Redis counter 当唯一审计来源。

如果一次 Judge 重试了 2 次,Langfuse 应能看到每次 generation。Redis 只适合做当前窗口的限流计数。

## 面试追问

### Q: Redis cache 和 Context Cache 有什么区别?

Redis 是应用侧 key-value 缓存;Context Cache 是 provider 侧 prompt prefix 缓存。二者都不是会话记忆。Context Cache 不能让请求省略必要上下文。

### Q: JobCopilot session 状态适合放 Redis 吗?

不适合作为唯一事实来源。session 需要持久化和恢复,应以数据库为准。Redis 可以做短期辅助,但不能替代 DB。

### Q: LLM 限流为什么不能只按请求数?

因为不同请求 token 量差异很大。长上下文请求消耗的 TPM 和成本远高于短请求。限流应考虑 token 估算。

### Q: 缓存 key 为什么要带 model_id 和 prompt_version?

因为模型或 prompt 变化会改变输出。key 不带这些版本会命中旧结果,导致评测和线上行为不一致。

## 常见错误回答

错误:

```text
有 Redis 就可以不落库 session。
```

问题:Redis 不是强持久业务事实来源,session 恢复和审计应以数据库为准。

错误:

```text
Context Cache 就是 Redis 缓存 prompt。
```

问题:Context Cache 在 provider 侧,缓存的是模型上下文前缀计算。

错误:

```text
限流收到 429 后立刻重试就行。
```

问题:无脑重试会放大拥塞和成本。

## 可作为 evidence anchor 的短句

- Context Cache 不是 Redis Cache,二者位置、对象和控制方式都不同。
- JobCopilot 的 session 状态不应只写 Redis 后异步落库。
- LLM 限流最好按 token 估算,不只是请求数。
- 缓存 key 要包含 model_id、prompt_version、corpus_version 等影响结果的变量。
- Redis 可以作为后续扩展,但当前不应为了架构完整提前引入。
