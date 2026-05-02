---
title: S2 LLM Client + DummyProvider + Tier 路由 + llm_calls 表 — 切片归档
status: ✅ 完成已 push
date: 2026-05-01
purpose: 按 ADR-0004 落地 LLM 抽象层,3 条原子 commit
---

# 产出

```
apps/api/alembic/versions/
└── 0006_llm_calls_and_prompt_versions.py  # llm_calls + prompt_versions(FK 完整)

apps/api/src/jobcopilot_api/
├── llm/
│   ├── tiers.py            # Tier(StrEnum:CHEAP/STANDARD/PREMIUM) + tier_to_model
│   ├── errors.py           # LLMError 族,继承 JobCopilotError → RFC 7807
│   ├── pricing.py          # price table + cost_for(本地自算)
│   ├── cache.py            # cache_system 语义占位 + 前缀稳定文档
│   ├── client.py           # Provider Protocol / LLMResult / LLMClient Protocol /
│   │                       # BaseLLMClient(tenacity 重试 + JSON 修复 + 日志 1 行)/
│   │                       # NoopCallLogger / MemoryCallLogger
│   ├── db_logger.py        # DBCallLogger(独立 AsyncSession,失败只 warn)
│   └── providers/
│       ├── dashscope.py    # openai AsyncOpenAI 包装,error 映射
│       └── dummy.py        # 显式 scenario 队列 + from_fixture
├── infra/
│   └── llm.py              # get_llm_client() 懒单例,默认接 DBCallLogger
└── models/
    ├── llm_call.py         # ORM(无 ORM 层 FK,migration 是权威)
    └── prompt_version.py
```

# 设计决策(实现细节)

- **ORM FK 原则**:ORM 只声明需要 navigate(`relationship()`)的关系,纯约束放 migration。LlmCall.user_id / prompt_version_id 都是 DB-only FK
- **每次 `complete()` 最多 1 行日志**:tenacity 多少次重试 / schema 修复多少次,都聚成一行。失败路径在 try 走 `logger.log()` 后 raise,成功路径直接 return 前 log
- **失败 cost = 0 / tokens = 0**:timeout / 5xx 拿不到 `response.usage`,LLMResult 用零占位写 llm_calls
- **DBCallLogger 用独立 AsyncSession**:从注入的 sessionmaker 拿一个新 session,与业务事务无关,业务回滚不影响日志(集成测试 `test_business_rollback_does_not_drop_cost_log` 是这条的 load-bearing 断言)
- **DashScope JSON schema 走 `json_object`**:OpenAI compat 不支持 `json_schema` 字段,降级用 `json_object` + Pydantic 二次校验 + 1 次重试(prompt 追加 schema)
- **retry 参数可注入**:BaseLLMClient `retry_wait` 默认是 ADR-0004 D3,测试传 `wait_none()` 让重试不睡

# 期间踩到的小坑

1. **LlmCall.user_id 的 ORM `ForeignKey("users.id")` mapper 失败**:User ORM 还没建,SQLAlchemy mapper config 阶段 resolve 不到 `users` 表。修复:改用"ORM 只表达需要 navigate 的关系,纯约束放 DB"原则,删 LlmCall 的 ORM 层 FK 声明,留 migration 0006 的 DB 层 FK。后续 S3 建 User ORM 时不需要回头补。
2. **集成测试用 module-scope async engine 跨 event loop 失败**:pytest-asyncio 默认每个测试一个新 loop,asyncpg 连接不能跨 loop 复用。修复:engine fixture 改成 function-scope(每测试新建 + dispose);container 仍 module-scope 避免反复重启 Postgres。
3. **structlog `capture_logs()` 在套件中失效**:前置测试调过 `setup_logging()` 后,structlog 全局配置被锁定,`capture_logs()` 看不到事件。修复:db_logger 单测用 monkeypatch 直接替换模块级 `log` 对象。
4. **DashScope OpenAI compat 不支持 `json_schema`**:M1 走 `response_format={"type":"json_object"}` + 在 prompt 里注入 schema + Pydantic 二次校验 + 1 次重试。已在 ADR-0004 D2 + client.py docstring 注明。
