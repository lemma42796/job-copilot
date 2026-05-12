# MVCC 隔离级别与可见性追问

## 目的

数据库事务是后端面试高频区。很多候选人会背 ACID 和四种隔离级别,但讲不清:

- MVCC 如何实现可见性。
- 快照读和当前读区别。
- MySQL InnoDB 和 PostgreSQL 的差异。
- 为什么可重复读下仍可能有写冲突。
- RAG/quiz session 这类业务如何选择事务边界。

这篇补数据库深水区,也给 JobCopilot 的 session 状态更新、Judge 结果写入、任务幂等提供语料。

## ACID

ACID:

- Atomicity:事务内操作要么都成功,要么都失败。
- Consistency:事务前后满足约束。
- Isolation:并发事务互不干扰到某种程度。
- Durability:提交后持久化。

面试中不要只背中文解释,要能落到例子:

```text
提交答案时,answer、judge_result、session_summary 应保持一致。
如果 judge_result 写入失败,不能只把 session 标成 completed。
```

## 并发现象

常见并发现象:

| 现象 | 含义 |
|---|---|
| dirty read | 读到未提交数据 |
| non-repeatable read | 同一事务两次读同一行结果不同 |
| phantom read | 同一范围查询两次出现新增/删除行 |
| lost update | 两个事务覆盖彼此更新 |
| write skew | 多行约束下并发写导致业务不变量破坏 |

很多面试只讲前三个,但真实系统里 lost update 和 write skew 更容易踩坑。

## 隔离级别

SQL 标准:

- Read Uncommitted。
- Read Committed。
- Repeatable Read。
- Serializable。

不同数据库实现有差异。不要机械认为名字相同表现完全一致。

PostgreSQL:

- Read Uncommitted 实际表现类似 Read Committed。
- Repeatable Read 基于快照隔离,能避免很多幻读,但不是完整 Serializable。
- Serializable 使用 SSI 检测危险结构。

MySQL InnoDB:

- Repeatable Read 是默认级别。
- 通过 MVCC + next-key lock 在某些当前读场景避免幻读。

## MVCC 是什么

MVCC = Multi-Version Concurrency Control。

核心思想:

- 写入不直接覆盖旧版本。
- 读事务看到符合自己快照的版本。
- 写事务创建新版本。

好处:

- 读不阻塞写。
- 写不阻塞普通快照读。

代价:

- 多版本清理。
- vacuum / purge。
- 可见性判断成本。
- 长事务导致旧版本无法回收。

## 可见性

每个行版本带事务信息。读时判断:

- 创建该版本的事务是否已提交。
- 删除/更新该版本的事务是否已提交。
- 这些事务是否在当前快照可见。

PostgreSQL 用 xmin/xmax 等元数据。InnoDB 用 undo log 和 read view。

面试不必死背字段,但要说明"快照读读的是对当前事务可见的历史版本"。

## 快照读和当前读

快照读:

```sql
select * from sessions where id = 1;
```

在 MVCC 下读快照,通常不加锁。

当前读:

```sql
select * from sessions where id = 1 for update;
update sessions set status = 'completed' where id = 1;
```

当前读要读最新已提交版本并加锁或参与锁冲突。

JobCopilot 状态机更新不应只依赖快照读判断状态,更应该用条件更新:

```sql
update quiz_sessions
set status = 'judging'
where id = :id and status = 'answering';
```

受影响行数为 1 才表示状态推进成功。

## Lost update

例子:

```text
事务 A 读 count=10
事务 B 读 count=10
A 写 count=11
B 写 count=11
```

实际应该是 12,但丢了一次更新。

解决:

- 原子 update:`set count = count + 1`。
- 乐观锁 version。
- `select for update`。
- 唯一约束/条件更新。

提交答案、点赞计数、库存扣减都要避免 lost update。

## Write skew

例子:

```text
约束:至少有一名医生值班
A 看到 B 值班,自己下班
B 看到 A 值班,自己下班
最终没人值班
```

单行锁不一定能保护跨行不变量。

解决:

- Serializable。
- 显式锁住约束相关集合。
- 物化约束行。
- 唯一/排他约束。

JobCopilot 中如果有"同一 session 只能有一个 active judge run",可用唯一约束或状态条件更新,不要靠应用查询后判断。

## 长事务问题

长事务会导致:

- 旧版本无法清理。
- vacuum/purge 压力。
- 锁持有时间长。
- 连接占用。
- 复制延迟。

LLM 调用不能放在数据库事务里等待。因为 provider 可能耗时很长,会拖住连接和锁。

更好的方式:

1. 短事务创建 judge_run/status。
2. 事务外调用 LLM。
3. 短事务写回结果,带状态条件和幂等 key。

## 幻读与业务理解

幻读不是看到鬼,而是范围查询结果集合变化。

例子:

```text
事务 A 查询 status='pending' 的任务数量。
事务 B 插入一个 pending 任务并提交。
A 再查,多了一行。
```

是否有问题取决于业务。如果只是监控统计,可接受。如果是"判断没有任务后关闭队列",可能有问题。

## Serializable 的代价

Serializable 最强,但:

- 冲突更多。
- 可能 abort。
- 吞吐下降。
- 应用要能重试事务。

不要动不动把全系统调 Serializable。多数业务用 Read Committed/Repeatable Read + 明确锁/约束/幂等更可控。

## 面试追问

问: MVCC 为什么能让读不阻塞写?

答: 因为写事务创建新版本,读事务按自己的快照读取可见的旧版本。读不需要等待写事务完成。但如果是当前读或加锁读,仍会参与锁冲突。

问: 提交 answer 时为什么不能先 select status 再 update?

答: 并发请求下两个事务都可能看到可提交状态,然后都推进。应使用条件更新 `where status = expected` 或唯一幂等键,用数据库原子性判断谁成功。

问: LLM 调用为什么不要放事务里?

答: LLM/provider 调用是长时间外部 IO,放在事务里会占用 DB 连接、拖住锁和 MVCC 版本清理。应把事务拆短,调用前后分别落状态和结果。

## Hard negatives

- MVCC 不是缓存。它是数据库多版本并发控制。
- Repeatable Read 不等于所有业务不变量自动安全。
- 快照读不等于加锁读。
- Serializable 不是免费开关,需要处理事务重试。
- Context Cache 和数据库 MVCC 没有直接关系。

