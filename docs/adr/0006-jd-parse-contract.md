---
adr: 0006
title: JD 解析契约(JDParserAgent / /v1/jds / SSE / prompt_versions 闭环)
owner: lemma42796
status: Accepted
date: 2026-05-02
---

# ADR-0006:JD 解析契约

## 上下文

S4 进入 M1"数据入口贯通"的第一条 LLM 抽取链路。ADR-0004 锁定了 LLM 抽象层契约,ADR-0005 锁定了 files 上传契约,二者交汇处 + JD 业务语义留白处使 S4 起步前仍有 12 个开放问题:

1. S4 输入入口范围:文本 only / 文本 + PDF(file_id)/ + 图片?
2. `/v1/jds`(CRUD)与 `/v1/jds/parse`(LLM)端点边界
3. **冲突**:API_SPEC §6.3 的 status 取值(`draft/ready/archived`)与 DATA_MODEL §3.2 + migration 0003 ENUM(`parsing/parsed/parse_failed`)不一致
4. SSE 是否做、若做则事件粒度(JDParser 单 Agent 不走 LangGraph,API_SPEC §5.3 的 7 个事件不全适用)
5. JD Pydantic schema 范围(已在 AGENT_DESIGN §3.3 定义,需与 DATA_MODEL §3.2 对齐)
6. prompt_versions 表(S2 已建)在 S4 是否落地、如何上线
7. `raw_text` 抽取后写回 jds 还是仅 in-memory 喂 LLM
8. 解析失败语义(LLM 上游错 / schema 不合法 / 内容为垃圾)如何分流到 422 / 502
9. 同 user 同 raw_text 的 JD 去重(类似 files D5)
10. PDF 抽取归属层(router / service / Agent)
11. 配额 / 限流是否 S4 做
12. S4 commit 拆分粒度

本 ADR 把以上 12 项一次性锁死,作为 S4 实现的规格依据。

## 决策

### D1. S4 输入范围 = 文本 + PDF(file_id),图片推迟

| `source` | 输入 | S4 |
|---|---|---|
| `text_paste` | `{ text: str }` | ✅ |
| `pdf_upload` | `{ file_id: int }`(引用 S3 已上传的 `purpose=jd_pdf`) | ✅ |
| `image_upload` | `{ image_b64 }` 或 `{ file_id }` 走 qwen3.6-vl-flash | ⏭ M1 末(STATUS Q4) |
| `extension_paste` | 浏览器扩展粘贴 | ⏭ M5+ |

理由:STATUS Q1 已决 PDF 工具为 `pypdfium2`;STATUS Q4 已决图片入口推迟;S3 D4 留好了 `purpose=jd_pdf`,S4 不接 file_id 等于浪费上一切片产出。

`JDParseInput` 在 S4 收敛为(对 AGENT_DESIGN §3.2 的子集):

```python
class JDParseInput(BaseModel):
    text: str | None = None
    file_id: int | None = None
    source: Literal["text_paste", "pdf_upload"]
    # text 与 file_id 必须二选一,model_validator 校验
```

### D2. 端点范围 = parse + 读改删,**不做 POST `/v1/jds`**

S4 端点收敛:

| 端点 | S4 |
|---|---|
| **`POST /v1/jds/parse`** | ✅ 创建 + 解析一步走;`?stream=1` 走 SSE |
| `GET /v1/jds` | ✅ 列表(query: `q` / `status` / `created_after` / `cursor` / `limit`) |
| `GET /v1/jds/{id}` | ✅ 详情 |
| `PATCH /v1/jds/{id}` | ✅ 用户手改 structured 字段(`status` / `notes` 可选) |
| `DELETE /v1/jds/{id}` | ✅ 软删(`deleted_at = NOW()`) |
| ~~`POST /v1/jds`~~(raw_text 占位、不解析) | ⏭ M4 投递追踪手动录入再做 |

理由:S4 没有"创建空 JD"的用例。M4 投递追踪需要"已经在做的岗位手动录入"再补 POST `/v1/jds`;S4 范围缩到最小,SSE 与 LLM 闭环优先。

### D3. status 取值 = DATA_MODEL 为准,API_SPEC §6.3 修文

| 来源 | 取值 |
|---|---|
| DATA_MODEL §3.2 + migration 0003(权威) | `parsing` / `parsed` / `parse_failed` |
| API_SPEC §6.3(冲突,需修文) | `draft` / `ready` / `archived` |

本 ADR 锁:**以 DATA_MODEL 为准**,API_SPEC §6.3 与本 ADR 同 PR 修文,模式与 ADR-0005 修 §6.9 一致。

`POST /v1/jds/parse` 的状态机:

1. 入站:同步 INSERT `(user_id, source, status='parsing', raw_text)` 拿 jd_id;若 `source='pdf_upload'` 先 `pypdfium2` 抽 raw_text 再 INSERT(同步内完成,毫秒级)
2. 调 LLM(走 ADR-0004 的 BaseLLMClient,1 次 schema 修复重试已包含)
3. 成功:`UPDATE jds SET status='parsed', company=..., title=..., ..., parse_confidence=..., parse_model='qwen3.6-flash', parse_tokens=..., parse_cost_cny=...`
4. 失败:`UPDATE jds SET status='parse_failed', parse_confidence=0`,raw_text 保留供前端手填

`archived` 取值 M4 投递归档时再 ALTER TYPE,S4 不引入。

### D4. SSE 双实现 + JDParser 只发 4 个事件

- **同步**(默认):`POST /v1/jds/parse` 返回 200 + `{id, status, structured, confidence, tokens, cost_cny}`
- **流式**(`?stream=1`):走 SSE

JDParser 是单 Agent 不走 LangGraph,API_SPEC §5.3 的 7 个事件中只用 4 个:

| event | data |
|---|---|
| `started` | `{ "job_id": "<uuid>", "resource_id": <jd_id> }` |
| `result` | `{ "resource_id": <jd_id>, "url": "/v1/jds/<id>" }` |
| `error` | `{ "code": "JD_PARSE_FAILED" \| "LLM_UPSTREAM_ERROR", "detail": "..." }` |
| `done` | `{ "ok": true \| false }` |

不发 `node_started/node_progress/token`:DashScope OpenAI compat 的 token 流 M1 不依赖,JD 单 Agent 内部无节点。

- **心跳**:每 15s 服务端 `:heartbeat\n\n`(API_SPEC §5.4)
- **断线重连 / Last-Event-ID**:M1 不实现 — JDParser 跑完即写 DB,客户端断了下次直接 `GET /v1/jds/{id}` 拿结果
- **取消**:客户端关 EventSource → `asyncio.CancelledError`;**已开始的 LLM 调用允许跑完**(避免余量浪费),不再触发后续节点(JDParser 只有一步,等价"等 LLM 返回后忽略写库" — 但仍写库,用户重连即得结果)

### D5. JD Pydantic schema = AGENT_DESIGN §3.3 `JDStructured`

已在 AGENT_DESIGN §3.3 定义,本 ADR 不再复述;只锁定与 DATA_MODEL §3.2 的映射:

| Pydantic | DB 列 | 映射 |
|---|---|---|
| `JDStructured.company / title / location` | 同名 VARCHAR | 1:1 |
| `JDStructured.salary_min / salary_max` | 同名 INTEGER | 1:1 |
| `JDStructured.salary_period` | `salary_period` | 1:1(`monthly`/`yearly`) |
| (LLM 不抽) | `salary_currency` | service 层默认 `"CNY"` |
| `JDStructured.job_level / years_required / education` | 同名 | 1:1 |
| `JDStructured.hard_skills / soft_skills / bonus_skills`(`list[JDSkill]`) | JSONB | `model_dump(mode="json")` |
| `JDStructured.responsibilities`(`list[str]`) | JSONB | `model_dump(mode="json")` |
| `JDStructured.description` | TEXT | 1:1 |
| `JDStructured.confidence` | `parse_confidence`(NUMERIC(4,3)) | 1:1 |
| (LLMResult 元数据) | `parse_model / parse_tokens / parse_cost_cny` | service 从 `LLMResult` 取 |

### D6. prompt_versions 在 S4 落地(启动 upsert + hash 校验)

S2 已建 `prompt_versions` 表,S4 是首个真实使用 LLM 的切片,正好走完闭环。

**模板存放**:

```
apps/api/src/jobcopilot_api/prompts/
└── jd_parser/
    └── v1.0.0.j2     # 包含 ## SYSTEM / ## USER 两段,Jinja2 模板
```

模板格式(单文件双段):

```jinja
## SYSTEM
你是一名专业的招聘信息分析师,擅长把任意格式的中文/英文 JD 转换为结构化数据。
... (AGENT_DESIGN §3.5 系统提示原文)

## USER
请解析以下 JD:
<jd>
{{ jd_text }}
</jd>
直接返回符合 schema 的 JSON 对象。
```

**`infra/prompts.py`**:

- `load_prompt_versions(session_factory) -> dict[(name, version), int]`:扫描 `prompts/**/*.j2` → 解析 SYSTEM/USER 两段 → 计算 `content_hash = sha256(system + "\x1e" + user + "\x1e" + model)` → upsert `prompt_versions(name, version, system_template, user_template, model, content_hash)`
- **同 (name, version) 但 hash 不同 → 启动报错**(避免静默改了 prompt 不升 version);要么改文件名 `v1.0.1.j2`,要么显式接受
- 缓存 `(name, version) → id` 映射到 `app.state.prompt_versions`(FastAPI lifespan 启动时填入)

**JDParserAgent 链路**:

- Agent 接收 `prompt_version_id: int` 参数(由 service 从 app.state 取 `("jd_parser", "1.0.0")`)
- Agent 把 id 透到 `LLMResult.prompt_version_id`(沿用 ADR-0004 的 LLMResult 字段;若无则本 ADR 加)
- `DBCallLogger` 写 `llm_calls.prompt_version_id`(0006 migration 已建 FK)

### D7. raw_text 写回 jds(不只是 in-memory)

- **文本输入**:直接写 `jds.raw_text = input.text`
- **PDF 输入**:`infra/pdf.py::extract_pdf_text(content) -> str` 抽出 → 写 `jds.raw_text` + `jds.raw_file_id`

理由:

- PATCH 后用户希望"重 parse" / evals 重跑 / 失败后用户复制粘贴改文本都需要 raw_text 源
- `raw_file_id ON DELETE SET NULL`(0003 migration)已保证文件软删 / 物理删后 jds 仍可工作
- `jds.search_tsv` 是 GENERATED 列基于 `title/company/description`,不索引 raw_text(避免长文本拖慢 GIN)

### D8. 失败语义 4 分支

| 失败类型 | DB status | parse_confidence | HTTP | error.code |
|---|---|---|---|---|
| LLM 上游 5xx / 超时(`LLMUpstreamError` / `LLMTimeoutError`) | `parse_failed` | 0 | 502 | `LLM_UPSTREAM_ERROR` |
| LLM 返回但 schema 不合法,1 次修复重试后仍失败(`LLMSchemaError`) | `parse_failed` | 0 | 422 | `JD_PARSE_FAILED` |
| LLM 返回 schema 合法但 `title` 为空 / 全 null(垃圾输入) | `parse_failed` | 0 | 422 | `JD_PARSE_FAILED` |
| LLM 返回合法且 `confidence < 0.5` | `parsed` | 实际值 | 200 | — |

- Pydantic schema 严格(json_schema 强制输出,ADR-0004 D2);BaseLLMClient 内部已有 1 次修复重试,service 层只接 `LLMError` 子类与 `LLMResult.parsed_data`
- 第 3 行"title 为空"由 service 层 post-validate(LLM 可能产出 `{"title": null, ...}`)
- 第 4 行 `confidence < 0.5` **不算失败**,DB status 是 `parsed`,UI 高亮提示用户复核(AGENT §3.6)

SSE 路径下,422 / 502 转为 `event: error data: {code, detail}` + `event: done data: {ok: false}`;HTTP 状态码仍是 200(SSE 连接已建立),与 API_SPEC §5 一致。

### D9. JD 去重 = M1 不做

不像 files 有 200MB 配额硬约束;jds 用户主动粘贴语义上每次都是新建。

重复 parse 同一段文字浪费 cost,但 `llm_calls` 表 + ADR-0001 余额监控会暴露。evals(S6)若发现是问题再补部分唯一索引 `(user_id, sha256(raw_text)) WHERE deleted_at IS NULL`(对照 files D5)。

### D10. PDF 抽取在 service 层(纯函数 `infra/pdf.py`)

Agent 是纯函数(M1 §设计原则 4):`text → LLMClient → JDStructured`,**不读文件不写库**。

PDF 抽取归属:

- `infra/pdf.py::extract_pdf_text(content: bytes) -> str`(纯函数,pypdfium2,无副作用)
- `services/jd_service.py::create_and_parse(...)`:
  1. 若 `source='pdf_upload'`:`get_file_for_download(file_id)` → `undefer(content)` → `extract_pdf_text` → text
  2. INSERT jds(status=parsing, raw_text=text, raw_file_id=file_id 或 None)
  3. 调 `JDParserAgent.parse(text=text, prompt_version_id=...)` → `LLMResult[JDStructured]`
  4. UPDATE jds(structured + status + cost)

依赖:`apps/api/pyproject.toml` 加 `pypdfium2`(S3 D6 已留口子)。

PDF 抽取失败语义:

- `pypdfium2` 加载失败 / 0 页 / 抽出空字符串 → 422 `JD_PARSE_FAILED`,`detail="PDF 文本抽取失败,可能是扫描件或加密"`(图片型 PDF M1 末上 vl-flash 才能处理)
- 抽出 < 50 字符 → 视为失败(JD 太短不可能产出有意义结构化)

### D11. 配额 / 限流 = S4 不做

- jds 不占 bytea,无 200MB 类约束
- 限流(5 req/min)与 ADR-0005 D8 一致,推迟到 M1 末横切框架(`fastapi-limiter` + `pg_advisory_lock`)与 jds/profiles/matches 一起做

### D12. S4 commit 拆分 = docs + 4 个原子(无 migration)

| Commit | 内容 |
|---|---|
| **docs**(独立) | ADR-0006 + API_SPEC §6.3 修文(status 取值 + 端点收敛 + SSE 4 事件)+ STATUS 同步 |
| **S4-A** | `models/jd.py` ORM(无 ORM FK 给 user,延续 ADR-0005 D1 原则)+ `schemas/jds.py`(`JDStructured` / `JDSkill` / `JDParseInput` / `JDParseResponse` / `JDListResponse`)+ `apps/api/pyproject.toml` 加 `pypdfium2` |
| **S4-B** | `agents/jd_parser/agent.py`(纯函数,LLMClient 注入)+ `prompts/jd_parser/v1.0.0.j2` + `infra/prompts.py`(扫描 + hash + upsert)+ `main.py` lifespan 接线 + 单测(DummyProvider fixture 回放 + prompt_version_id 链路 + hash 不一致启动报错) |
| **S4-C** | `services/jd_service.py`(create_and_parse / list / get / patch / soft_delete) + `infra/pdf.py`(pypdfium2 抽文本 + 失败语义 D10)+ 单测(失败语义 4 分支 + raw_text 写回 + PDF 抽取失败 + PATCH structured 字段) |
| **S4-D** | `routers/jds.py`(POST `/v1/jds/parse` 同步 / SSE `?stream=1` / GET 列表 + 详情 / PATCH / DELETE)+ `main.py` 注册 + 集成测试(testcontainers + httpx + DummyProvider 注入,golden + 失败 4 分支 + SSE 4 事件 + 软删 404 + PATCH) |

**不需要新 migration**:0003 migration 已建 jds 表 + jd_source / jd_status ENUM;0006 migration 已建 prompt_versions / llm_calls 表 + FK。S4 是首个不动 schema 的实现切片。

## LLM 调用?

S4 是首个真实使用 LLM 的切片。LLMClient / DummyProvider / DBCallLogger / Tier 路由 / cost 表全部由 ADR-0004 锁定,本 ADR 仅消费,不再增加约束。

新增字段:`LLMResult.prompt_version_id: int | None`(D6 链路;若 ADR-0004 实现的 LLMResult 已含此字段,本条无影响)。

## 复审条件

满足任一条件需重新评审本 ADR:

1. dogfood 显示用户大量重复 parse 同一段 raw_text(浪费 cost)— D9 加去重
2. evals(S6)显示 `confidence < 0.5` 误报多 / `title` 空判定误杀 — D8 阈值或字段调整
3. 真实 PDF 简历 / JD 中扫描件比例显著(> 20%)— D10 提前上图片入口(qwen3.6-vl-flash),将本 ADR 范围扩到 image_upload
4. DashScope OpenAI compat 后续支持原生 token 流 — D4 SSE 事件可加 `token`(对前端展示有价值)

## 相关

- ADR-0001:单 Provider 工程纪律(Superseded by 0003,但 cost 监控约束仍生效)
- ADR-0003:切到 Qwen3.6(本 ADR D5 的 `parse_model="qwen3.6-flash"` 来源)
- ADR-0004:LLM 抽象层契约(本 ADR 消费 LLMClient / DBCallLogger / Tier / prompt_versions 表)
- ADR-0005:files 上传契约(本 ADR D1 / D7 / D10 消费 `file_id` + `purpose=jd_pdf` + `get_file_for_download`)
- 3-DATA_MODEL §3.2:jds 表(本 ADR D3 / D5 / D7 引用)
- 4-API_SPEC §6.3:JDs 端点(本 ADR 修正:status 取值 + 端点收敛 + SSE 事件)
- 4-API_SPEC §5:SSE 协议(本 ADR D4 引用)
- 5-AGENT_DESIGN §3:JDParserAgent(本 ADR D5 / D6 消费)
- 7-ROADMAP M1 §S4
- STATUS Q1(PDF 工具,本 ADR D1 / D10 消费)/ Q4(图片入口,本 ADR D1 推迟到 M1 末)

## 不在本 ADR 范围

- 图片 JD 入口 / qwen3.6-vl-flash(M1 末,STATUS Q4)
- POST `/v1/jds`(raw_text 占位,M4 投递追踪)
- ProfileParserAgent / `/v1/profiles/parse`(S7,与本 ADR 同结构,届时另立 ADR-0007)
- 投递归档 status `archived` ENUM 扩展(M4)
- 限流 5 req/min 横切框架(M1 末,ADR-0005 D8 已 deferral)
- 浏览器扩展粘贴 `extension_paste`(M5+)
- JD 去重(等 evals 表明是问题,本 ADR D9 复审条件 1)
