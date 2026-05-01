---
adr: 0002
title: 使用 Postgres + pgvector 作为唯一数据存储
owner: lemma42796
status: Accepted
date: 2026-05-01
---

# ADR-0002:Postgres 一把梭(业务 + 向量 + 全文 + 队列 + 对象)

## 上下文

JobCopilot 需要存储:

| 数据类型 | 量级(单用户) | 访问模式 |
|---------|-------------|---------|
| 业务数据(JD、Profile、Resume、Application) | 数百-数千行 | 频繁读写 |
| 向量索引(个人档案多粒度) | 数十-数百向量 | 高频检索 |
| 全文检索(BM25 风格) | 同上 | 高频检索 |
| 任务队列(JD 解析、简历生成等异步任务) | 持续流动 | 写入+轮询 |
| 原始文件(PDF、图片) | 几十 MB | 偶尔读写 |
| 缓存(LLM 调用结果、计算中间产物) | 中等 | 高频读 |

候选方案:

- **Postgres 16 + pgvector + tsvector + pgmq + bytea**(单库一把梭)
- Postgres 业务库 + Milvus 向量库 + Elasticsearch 全文 + Redis 队列 + MinIO 对象
- SQLite + sqlite-vec(更轻量,但生产功能弱)
- MongoDB + Atlas Vector Search(单库,但生态不熟悉)

## 决策

**全部数据存于一个 Postgres 16 实例**,启用以下扩展:

| 扩展 | 用途 |
|------|------|
| pgvector ≥ 0.7 | 向量存储与 HNSW 索引 |
| pg_jieba(可选,中文分词) | 全文检索的中文支持 |
| pgmq | 任务队列(基于 SKIP LOCKED) |
| pg_cron(可选) | 定时任务(M5 可用) |
| pg_stat_statements | 性能监控 |

二进制大对象(PDF / 图片)存于业务表的 `bytea` 列,小对象 ≤ 10 MB 直接 inline。

## 替代方案与拒绝理由

### A. 多服务架构:Postgres + Milvus + Elasticsearch + Redis + MinIO

**优点**:每个组件都是行业标杆,各司其职。

**拒绝理由**:

1. **本地优先架构原则被破坏**:用户笔记本上要跑 5 个 service,启动慢、占内存大
2. **运维成本爆炸**:5 个 service 各自的版本、配置、备份、监控
3. **数据一致性问题**:跨服务事务无法保证(JD 写入业务库成功但向量同步失败,需要补偿机制)
4. **量级对不上**:单用户向量数 < 1000,Milvus 是十亿级方案,完全是过度设计
5. **简历劣势**:面试官会反问「为什么单用户场景需要 Milvus」,无法清晰回答

### B. SQLite + sqlite-vec

**优点**:零运维,文件即数据库,本地优先到极致。

**拒绝理由**:

1. **并发写入弱**:Worker 与 API 同时写时容易锁等
2. **JSON 字段、复杂查询、partial index 等高级特性弱于 Postgres**
3. **生产化迁移困难**:用户量增长时,SQLite → Postgres 的迁移工作量大于"一开始就用 Postgres"
4. **简历讲述层面**:Postgres 才是行业默认,SQLite 显得"玩具"

### C. MongoDB + Atlas Vector Search

**拒绝理由**:

1. 作者对 Postgres 生态更熟悉
2. 关系型数据(用户-档案-投递这种)用文档库表达不自然
3. 自托管运维比 Postgres 复杂

### D. Postgres + 独立向量库(只剥离向量)

**拒绝理由**:

1. 单用户向量数低于 1000,**pgvector + HNSW 在该量级 P95 检索 < 5ms**,完全足够
2. 独立向量库带来跨库 join 问题(向量结果回查业务表)

## pgvector 性能验证依据

参考 pgvector 官方 benchmark 与社区报告:

- HNSW 索引,1M 向量 + 1024 维:P95 检索 < 5ms,召回率 > 95%
- JobCopilot 单用户 ~500 向量 × 1024 维:**远低于压力点**
- 多用户场景(多租户)用 `WHERE user_id = ?` partial index,性能不受影响

## 后果

### 正面

- **本地部署体验**:`docker compose up` 拉两个镜像(app + postgres),5 分钟内启动完成
- **数据一致性**:所有写入都在同一事务内,不需要补偿
- **运维简单**:一个 dump 备份所有数据
- **可观测性**:`pg_stat_statements` 一站式查看慢查询(向量、全文、业务)
- **简历亮点**:能清晰讲「为什么不用 Milvus / ES / Redis」,体现架构成熟度
- **跨表 join**:RAG 检索结果可直接 join 业务表,逻辑清晰

### 负面

- 当 JobCopilot 增长到**百万级向量**或**多租户高并发**时,可能需要剥离专业向量库。但这超出 v1 范畴,不在 16 周交付内。
- pgmq 不如 Celery 生态丰富(但单用户场景 SKIP LOCKED 足够)
- bytea 存大文件 > 100 MB 时性能下降(规避方法:文件大小限制 100 MB,大文件走文件系统 + 路径存数据库)

## 实施细节

详见 `3-DATA_MODEL.md`。要点:

- 所有用户数据按 `user_id` 分区(单机但保留多租户能力)
- HNSW 索引参数:`m=16, ef_construction=64`
- 中文全文检索:`to_tsvector('chinese_jieba', ...)`(可选启用 pg_jieba)
- 队列表使用 `unlogged table` + `pgmq` 扩展
- bytea 列上 lz4 压缩(简历/JD 文本压缩比 ~3x)

## 复审条件

满足以下任一需重新评审:

1. 向量规模超过 100 万,pgvector 检索 P95 > 50ms
2. 多用户并发写超过 100 QPS,pgmq 队列出现明显延迟
3. 用户反馈本地部署 Postgres 16 资源占用过高(目前监控:idle 占用 ~150 MB,可接受)

## 相关

- ADR-0001:仅使用 DeepSeek V4
- 2-TECH_DESIGN.md §3 技术栈选型
- 3-DATA_MODEL.md
