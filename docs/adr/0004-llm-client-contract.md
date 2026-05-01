---
adr: 0004
title: LLM 抽象层契约(LLMClient / Tier / 重试 / 成本日志)
owner: lemma42796
status: Accepted
date: 2026-05-01
---

# ADR-0004:LLM 抽象层契约

## 上下文

ADR-0001 锁定"单 Provider 工程纪律",ADR-0003 把 v1 阶段的 Provider 切到阿里云百炼 Qwen3.6。两份 ADR 都引用了"抽象层契约"但没有把契约本身写死,导致 S2 起步时仍有 9 个开放问题:

1. Tier ↔ 模型映射 + M1 各 feature 归档
2. DashScope system cache 行为
3. 重试参数(退避 / 超时 / 重试边界)
4. `llm_calls` 写入边界(事务隔离 / 同步 vs 异步)
5. `cost_cny` 计算口径
6. `prompt_versions` 表是否在 S2 同时建
7. Idempotency / cache_key hook
8. S2 commit / PR 拆分粒度
9. BYOK(`X-DashScope-Key` 头)在 M1 是否实现

本 ADR 把这 9 项一次性锁死,作为 S2 实现的规格依据。

## 决策

### D1. Tier ↔ 模型映射(继承 ADR-0003)+ M1 feature 归档

| Tier | 模型 | 思考 | 用途 | M1 使用方 |
|------|------|------|------|-----------|
| `CHEAP` | `qwen3.6-flash` | 关 | 抽取 / 校验 / 打分 | `jd_parse`、`profile_parse` |
| `STANDARD` | `qwen3.6-flash` | 开 | 中等推理 | (M1 不用) |
| `PREMIUM` | `qwen3.6-plus` | 开 | 创作 / 深推理 / 面试模拟 | (M1 不用) |

M1 两个抽取类 feature 都走 `CHEAP`,符合 M1 DoD"日均 < ¥1"。如果 evals(S6)证明 `profile_parse` 召回不足,**升档到 STANDARD 是允许的调整**,无需 ADR 修订。

### D2. system cache 行为

DashScope OpenAI 兼容端点的 prompt cache **默认开启**,SDK 不暴露显式开关。本契约保留 `cache_system: bool = True` 参数作为 **provider 无关的语义占位**,具体实现:

- `DashscopeProvider`:不向 SDK 传递缓存参数;靠 **prompt 拼接顺序**(常量化的 system 在前、变量在后)保证前缀稳定让 provider 自动命中
- `LLMResult.cached_tokens` 直接从 `response.usage.prompt_tokens_details.cached_tokens` 读取(OpenAI compat 字段)写入 `llm_calls.cached_tokens`
- 不在应用层维护命中率指标,百炼控制台已提供

### D3. 重试参数

- 库:`tenacity`
- `max_attempts=3`,`wait_exponential(multiplier=0.5, max=4) + wait_random(0, 0.5)` jitter
- `timeout`:`CHEAP`/`STANDARD` = 30s,`PREMIUM` = 60s(签名层接受 `timeout_s` 覆盖)
- 仅重试以下异常:
  - `LLMTimeoutError`(client 侧 asyncio timeout)
  - `LLMUpstreamError` 且 status ∈ {429, 500, 502, 503, 504}
- **不**重试:
  - `LLMSchemaInvalidError`(走专门的"prompt 追加 + 1 次重试"路径,不进 tenacity)
  - 任何 4xx ≠ 429(认证/参数错误,重试无意义)

### D4. `llm_calls` 写入边界

**同步写,独立 AsyncSession,不与业务事务共享 connection。**

理由与"为什么不 fire-and-forget":
- `asyncio.create_task` 在 FastAPI request 结束后可能被 cancel,会丢日志
- 成本日志的可靠性 > 业务延迟

实现:
- `LLMClient` 持有自己的 `async_sessionmaker`(从 `infra/db.py` 注入,但每次 `await self._log()` 用新 session,与业务 session 完全独立)
- 写日志失败只 `log.warning("llm_call_log_failed", ...)`,**不抛**、不阻断业务返回
- 成功 / 失败 / 超时都写一行;`success=False` 时填 `error_code`(`timeout` / `upstream_5xx` / `schema_invalid` / ...)

### D5. `cost_cny` 计算口径

**本地 price table + `response.usage` 自算**,不信 provider 返回。

- DashScope OpenAI compat 模式不返回 `cost_cny`,只返回 token 数
- `llm/pricing.py`:模型 → `(price_in, price_cached_in, price_out)` 元/M tokens
- `cost = (tokens_in - cached_tokens) * price_in / 1e6 + cached_tokens * price_cached_in / 1e6 + tokens_out * price_out / 1e6`
- 当前价(2026-05-01,ADR-0003):
  - `qwen3.6-flash`: in 0.6 / cached 0.12 / out 7.2
  - `qwen3.6-plus`: 待 PREMIUM 实测时回填
- 汇率不需要(百炼直接计价 CNY)

### D6. `prompt_versions` 表在 S2 同时建

**`0006_llm_calls_and_prompt_versions.py` 一条 migration 同时建两张表 + 完整 FK。**

理由:DATA_MODEL §3.17 schema 已定;延后 = schema 债 + 后续要补 FK migration。S2 期间 prompt_versions 是空表无妨,~30 行 ORM。

涉及索引:
- `llm_calls`:`idx_lc_user_feature_created` / `idx_lc_created` / `idx_lc_feature_success`
- `prompt_versions`:`uq_pv_agent_version` / `idx_pv_agent_active`

### D7. Idempotency / cache_key hook

**M1 不预留。**`LLMClient.complete` 签名不开 `cache_key` / `idempotency_key` 参数(STATUS Q2 已决 M1 跳过 Idempotency-Key)。M2/M3 切片若需要再加。

### D8. S2 commit 拆分

S2 拆 3 个独立 commit(在 S0.5 / S1 commit 之后):

| Commit | 内容 |
|--------|------|
| **C** | `0006_llm_calls_and_prompt_versions.py` migration + ORM 模型 + 集成测试(testcontainers 验证表/索引/FK) |
| **D** | `llm/` 模块全套:`client.py`(LLMClient Protocol + 实现)、`tiers.py`、`pricing.py`、`errors.py`、`providers/{dashscope,dummy}.py`、`cache.py`(语义占位)+ 单测(全 dummy provider) |
| **E** | LLMClient ↔ `llm_calls` 写入 hook(独立 session)+ 集成测试(testcontainers 验证日志真的落库) |

### D9. BYOK 头(`X-DashScope-Key`)

**M1 不实现。**`LLMClient` 只读 `settings.dashscope_api_key`(`.env`)。M5(MCP / Web demo)启动时再加请求级 key 注入(中间件 → contextvar → LLMClient 优先级)。

## LLMClient 契约(签名最终版)

```python
# llm/client.py
class LLMClient(Protocol):
    async def complete(
        self,
        *,
        feature: str,                   # 'jd_parse' / 'profile_parse' / ...
        tier: Tier,                     # CHEAP / STANDARD / PREMIUM
        system: str,
        user: str,
        response_schema: type[BaseModel] | None = None,
        cache_system: bool = True,      # 语义占位,见 D2
        timeout_s: float | None = None, # None → 按 tier 默认(D3)
        related_entity: str | None = None,  # 'jd' / 'profile' / ...
        related_id: int | None = None,
        user_id: int | None = None,
        trace_id: str | None = None,
        prompt_version_id: int | None = None,
    ) -> LLMResult: ...

@dataclass
class LLMResult:
    content: str                        # 原始 LLM 输出
    parsed: BaseModel | None            # response_schema 解析后(若给)
    tokens_in: int
    tokens_out: int
    cached_tokens: int
    cost_cny: Decimal
    latency_ms: int
    model: str
    feature: str
    tier: Tier
```

错误体系:

```python
# llm/errors.py
class LLMError(JobCopilotError): ...                    # 基类(继承全局 errors.py)
class LLMTimeoutError(LLMError): ...                    # 可重试
class LLMUpstreamError(LLMError):                       # 可重试(429/5xx)
    status_code: int
class LLMSchemaInvalidError(LLMError): ...              # 不进 tenacity,走专门重试
class LLMAuthError(LLMError): ...                       # 不重试(4xx ≠ 429)
```

## 复审条件

满足任一条件需重新评审本 ADR:

1. ADR-0003 触发回切 DeepSeek(D1 表格需替换、D5 价表需替换)
2. 实测发现 DashScope 兼容模式不回传 `cached_tokens`(D2 失效,需自建命中率统计)
3. M1 evals 显示 `profile_parse` 在 CHEAP 档召回不足且升档到 STANDARD 仍不够(罕见,意味着抽取 prompt 设计需要重做)

## 相关

- ADR-0001:仅使用 DeepSeek V4(已 Superseded by ADR-0003)
- ADR-0003:v1 阶段切换到 Qwen3.6
- 3-DATA_MODEL §3.16 / §3.17:`llm_calls` / `prompt_versions` 表 schema
- 8-ENGINEERING §1.2 §2.5 §2.8:`llm/` 模块结构、错误处理、测试约束
- 7-ROADMAP §S2:LLMClient 抽象 + DashScope provider + Tier 路由 + Cache 控制位 + 重试

## 不在本 ADR 范围

- 具体 prompt 模板(留给各 Agent ADR / S4-S10 切片)
- Embedding / Reranker 调用(M1 不阻塞,本 ADR 只覆盖 chat completion)
- 多模态(Qwen3.6-VL)调用接口(M1 末再做,见 STATUS Q4)
- pgmq 任务队列(M2)
