---
title: JobCopilot API 规范
owner: lemma42796
status: Draft
version: 0.1.0
created: 2026-05-01
last_updated: 2026-05-01
related:
  - 1-PRD.md
  - 2-TECH_DESIGN.md
  - 3-DATA_MODEL.md
  - 5-AGENT_DESIGN.md
  - adr/0003-switch-to-qwen.md
---

## 修订记录

| 版本 | 日期 | 作者 | 变更说明 |
|------|------|------|---------|
| 0.1.0 | 2026-05-01 | lemma42796 | 初版 |

---

## 1. 概览

### 1.1 目的

定义 JobCopilot 后端 (`apps/api`,FastAPI) 对外暴露的全部 HTTP / SSE 接口契约。本文是 `apps/web`(Next.js)以及未来 MCP Server 的唯一可信源。

### 1.2 设计原则

- **REST + SSE 二元协议**:同步请求走 REST/JSON;长任务(LLM 生成)统一走 Server-Sent Events
- **Schema-first**:全部请求/响应模型由 Pydantic 定义,导出为 OpenAPI 3.1,前端用 `datamodel-code-generator` 生成 TS 类型
- **资源化路由**:`/v1/<resource>[/<id>][/<sub-resource>]`,严格遵循 HTTP 语义
- **幂等优先**:所有 POST 创建型接口接受可选 `Idempotency-Key` 头
- **错误格式统一**:RFC 7807 Problem+JSON
- **不要 GraphQL、不要 gRPC、不要 WebSocket**:见 §10
- **本地优先**:默认绑定 `127.0.0.1:8000`,云端 Demo 才暴露公网

### 1.3 版本策略

- URL 前缀:`/v1`
- Breaking change 必须升 `/v2`,不动 `/v1`
- 字段新增、可选化不算 breaking
- v1 阶段不提供 `/v2`

---

## 2. 通用约定

### 2.1 Base URL

| 部署 | URL |
|------|-----|
| 本地 | `http://localhost:8000` |
| Docker Compose | `http://api:8000`(容器内) / `http://localhost:8000`(宿主机) |
| 云端 Demo | `https://demo.jobcopilot.local/api`(Caddy 反代) |

### 2.2 内容协商

| 头 | 取值 |
|---|------|
| `Content-Type` | `application/json; charset=utf-8`(默认)/ `multipart/form-data`(上传) |
| `Accept` | `application/json`(REST)/ `text/event-stream`(SSE) |
| `Accept-Language` | `zh-CN`(v1 唯一支持) |

### 2.3 认证

v1 单用户本地部署默认**不启用**鉴权。云端 Demo 启用如下:

- `Authorization: Bearer <token>`(JWT,HS256,过期 24h)
- 登录接口:`POST /v1/auth/login` → 返回 token
- 服务端通过 `pydantic-settings` 的 `JWT_SECRET` 配置签名密钥

BYOK 模式下,用户的百炼 API Key 通过 `X-DashScope-Key` 头随每次请求传递,服务端**只在内存中使用,不持久化**。

### 2.4 Request ID

每个请求服务端生成 `X-Request-Id`(UUIDv7),写入响应头与日志。客户端可主动指定,服务端原样回显。

### 2.5 幂等键

所有 POST 创建型接口(`/jds`、`/profiles`、`/matches`、`/resumes/generate`、`/interviews`)接受 `Idempotency-Key: <uuid>` 头。

- 服务端用 `(user_id, endpoint, idempotency_key)` 作为唯一键缓存响应 24h
- 重放命中:返回原响应 + `X-Idempotent-Replay: true`
- 缓存表:`idempotency_records`(详见 3-DATA_MODEL §3.20,待补)

### 2.6 分页

列表接口统一约定:

```
GET /v1/jds?cursor=<base64>&limit=20&order=created_at:desc
```

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `cursor` | string | null | 上次响应中的 `next_cursor`,首次不传 |
| `limit` | int | 20 | 1-100 |
| `order` | string | `created_at:desc` | 字段:asc\|desc,白名单见各资源 |

响应:

```json
{
  "data": [...],
  "next_cursor": "eyJpZCI6MTIzfQ==",
  "has_more": true
}
```

不使用 `offset/page` 分页,避免深翻页性能塌方。

### 2.7 时间格式

全部用 RFC 3339 字符串(UTC),例 `2026-05-01T08:30:00Z`。客户端负责本地化展示。

---

## 3. 错误响应

### 3.1 RFC 7807 Problem+JSON

```json
{
  "type": "https://jobcopilot.local/errors/jd-parse-failed",
  "title": "JD 解析失败",
  "status": 422,
  "detail": "未能从输入中识别有效字段,请检查文本完整性",
  "instance": "/v1/jds/parse",
  "request_id": "01HXXXXXXXXXXXX",
  "code": "JD_PARSE_FAILED",
  "errors": [
    { "field": "text", "msg": "字段长度 < 50,可能是噪声" }
  ]
}
```

`Content-Type: application/problem+json`。

### 3.2 错误码字典

| HTTP | code | 触发场景 | 用户提示 |
|------|------|---------|---------|
| 400 | `VALIDATION_ERROR` | Pydantic 校验失败 | "请求参数有误" |
| 401 | `UNAUTHORIZED` | token 缺失/过期 | "请重新登录" |
| 403 | `FORBIDDEN` | 资源属于其他用户 | "无权限访问" |
| 404 | `NOT_FOUND` | 资源不存在 | "未找到该资源" |
| 409 | `CONFLICT` | 唯一约束冲突 / 状态冲突 | "资源状态冲突" |
| 413 | `PAYLOAD_TOO_LARGE` | 文件 > 20MB | "文件过大" |
| 415 | `UNSUPPORTED_MEDIA` | 非白名单文件类型 | "不支持的文件格式" |
| 422 | `JD_PARSE_FAILED` | JDParserAgent 失败 | "JD 解析失败,请补充文本" |
| 422 | `PROFILE_PARSE_FAILED` | ProfileParserAgent 失败 | "简历解析失败" |
| 422 | `RESUME_REVIEW_FAILED` | Reviewer 多次未通过 | "简历有事实问题,需修订" |
| 429 | `RATE_LIMITED` | 触发限流 | "请求过快,请稍后再试" |
| 502 | `LLM_UPSTREAM_ERROR` | 百炼 API 5xx | "百炼 API 异常" |
| 503 | `LLM_DEGRADED` | 全 Tier 降级失败 | "服务暂时降级,请稍后" |
| 504 | `LLM_TIMEOUT` | LLM 调用超时 | "LLM 响应超时" |
| 500 | `INTERNAL_ERROR` | 未分类 | "系统错误" |

### 3.3 校验错误格式

`VALIDATION_ERROR` 的 `errors` 数组对应 Pydantic 的 `loc + msg + type`:

```json
{
  "code": "VALIDATION_ERROR",
  "errors": [
    { "field": "salary_min", "msg": "ensure this value is >= 0", "type": "value_error.number.not_ge" }
  ]
}
```

---

## 4. 限流

### 4.1 维度

| 维度 | 限额 | 备注 |
|------|------|------|
| `user_id` 全局 | 60 req/min | 滑动窗口 |
| `user_id` × LLM 重操作 | 10 req/min | 路径前缀 `/jds/parse`、`/profiles/parse`、`/matches`、`/resumes/generate`、`/interviews/*/turns` |
| 文件上传 | 5 req/min | `/files/upload` |
| IP 全局(云端 Demo) | 120 req/min | Caddy 层做 |

### 4.2 算法

- 实现:`fastapi-limiter` + Postgres `pg_advisory_lock`(避免引入 Redis,见 ADR-0002)
- 窗口:60s 滑动
- 触发响应:`429`,响应头 `Retry-After: <seconds>` + `X-RateLimit-Remaining: 0`

### 4.3 例外

- `/v1/health`、`/v1/version`、`/v1/files/<id>` GET:不限流
- 评测 / 内部接口(`/v1/admin/*`):不限流但要求 `X-Admin-Token`

---

## 5. SSE 流式协议

### 5.1 何时使用

LLM 生成的长任务(> 3s 预期延迟)统一走 SSE。涉及:

- `POST /v1/jds/parse?stream=1`(可选流)
- `POST /v1/matches`(强制流)
- `POST /v1/resumes/generate`(强制流)
- `POST /v1/interviews/<id>/turns`(强制流,双向需 `text/event-stream`)

### 5.2 事件协议

每条事件遵循 SSE 标准:

```
event: <event_name>
id: <monotonic_int_per_stream>
data: <json_payload>
\n
```

### 5.3 事件命名规范

| event | 何时发出 | data 形态 |
|-------|---------|---------|
| `started` | 任务接受,资源已写入 DB | `{ "job_id": "...", "resource_id": 123 }` |
| `node_started` | 状态机进入新节点 | `{ "node": "retrieve", "step": 1, "total": 5 }` |
| `node_progress` | 节点内进度(可选) | `{ "node": "draft", "progress": 0.4 }` |
| `token` | LLM 增量 token(仅 chat 类) | `{ "delta": "...", "node": "interviewer" }` |
| `node_completed` | 节点完成 | `{ "node": "draft", "duration_ms": 4200 }` |
| `result` | 状态机完成,带最终结果引用 | `{ "resource_id": 123, "url": "/v1/resumes/123" }` |
| `error` | 任意阶段失败 | `{ "code": "LLM_UPSTREAM_ERROR", "detail": "..." }` |
| `done` | 流结束(总在最后一条) | `{ "ok": true }` |

### 5.4 心跳

每 15s 服务端发 `:heartbeat\n\n` 注释行,客户端用于检测断线。

### 5.5 断线重连

- 客户端断线后 `Last-Event-ID: <id>` 重连
- 服务端按 job_id 找到状态机当前快照,**只重放从该 id 之后的事件**(已完成的状态机直接返回 `result` + `done`)
- 状态机运行结果落 DB,不依赖客户端持续连接

### 5.6 取消

客户端关闭 EventSource → 服务端通过 `asyncio.CancelledError` 触发取消,LangGraph 在节点边界检查中断。已开始的 LLM 调用允许跑完(避免余量浪费),但不再触发后续节点。

---

## 6. REST 端点

下文按资源分组。每个端点列出:Method + Path、说明、请求、响应、错误、示例。

### 6.1 Health & Meta

#### `GET /v1/health`

返回服务健康状态。

**响应 200**:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "deps": {
    "postgres": "ok",
    "dashscope": "ok",
    "embedding": "ok"
  }
}
```

#### `GET /v1/version`

返回构建元信息。

```json
{ "version": "0.1.0", "git_sha": "abc1234", "built_at": "2026-05-01T08:00:00Z" }
```

---

### 6.2 Auth(仅云端 Demo 启用)

#### `POST /v1/auth/login`

请求:`{ "email": "...", "password": "..." }`
响应 200:`{ "token": "...", "expires_at": "...", "user": { "id": 1, "email": "..." } }`
错误:401 `INVALID_CREDENTIALS`

#### `POST /v1/auth/logout`

请求:无 body(token 自携)
响应 204

---

### 6.3 JDs(职位描述)

资源对应 `jds` 表(3-DATA_MODEL §3.2)。

#### `POST /v1/jds`

直接创建 JD(不解析,粘贴原始文本占位)。

请求:

```json
{
  "raw_text": "Senior Backend Engineer @ XYZ ...",
  "source": "text_paste"
}
```

响应 201:`JD` 完整对象(含 `id`、`status: draft`)。

#### `POST /v1/jds/parse`

调用 `JDParserAgent`,返回结构化 JD。**支持 SSE 流(可选)**。

请求(任选其一):

```json
{ "text": "...", "source": "text_paste" }
{ "image_b64": "...", "source": "image_upload" }
{ "file_id": 123, "source": "pdf_upload" }
```

响应(同步,默认):

```json
{
  "id": 42,
  "status": "ready",
  "structured": { /* JDStructured */ },
  "confidence": 0.91,
  "tokens": { "input": 1200, "output": 320 },
  "cost_cny": 0.04
}
```

响应(SSE,`?stream=1`):
```
event: started      data: {"job_id":"...","resource_id":42}
event: node_started data: {"node":"parse"}
event: token        data: {"delta":"{"}
...
event: result       data: {"resource_id":42,"url":"/v1/jds/42"}
event: done         data: {"ok":true}
```

错误:422 `JD_PARSE_FAILED`、502 `LLM_UPSTREAM_ERROR`

#### `GET /v1/jds`

列表,支持过滤:

| query | 说明 |
|-------|------|
| `q` | 全文搜索(title + company + description) |
| `status` | `draft` / `ready` / `archived` |
| `created_after` | RFC3339 |

响应:`{ "data": [JD, ...], "next_cursor": "...", "has_more": false }`

#### `GET /v1/jds/{id}`

返回单个 JD 完整对象(含 `JDStructured`)。

#### `PATCH /v1/jds/{id}`

部分更新。允许字段:`structured`(用户手动修正)、`status`、`notes`。

#### `DELETE /v1/jds/{id}`

软删除(`deleted_at = now()`)。返回 204。

---

### 6.4 Profiles(个人档案)

对应 `profiles` 表 + 子表(experiences / projects / skills / educations / chunks)。

单用户场景下默认只有一个 profile,但 schema 支持多个(便于"多版本简历"试探)。

#### `POST /v1/profiles`

创建空档案。请求 `{ "name": "我的主档案" }`,响应 201 `Profile`。

#### `POST /v1/profiles/parse`

调用 `ProfileParserAgent` 从文件解析。**强制 SSE**(简历可能很长)。

请求:`{ "file_id": 123 }`(必须先上传)
响应 SSE:
```
event: started        data: {"job_id":"...","resource_id":7}
event: node_started   data: {"node":"chunk_extract","step":1,"total":4}
event: node_completed data: {"node":"chunk_extract"}
event: node_started   data: {"node":"detail_parse","step":2,"total":4}
...
event: node_started   data: {"node":"chunking_embedding","step":4,"total":4}
event: result         data: {"resource_id":7,"chunks":42}
event: done
```

#### `GET /v1/profiles`

列表。

#### `GET /v1/profiles/{id}`

完整档案(嵌入子资源):

```json
{
  "id": 7,
  "name": "我的主档案",
  "structured": {
    "basics": {...},
    "experiences": [...],
    "projects": [...],
    "skills": [...],
    "educations": [...]
  },
  "stats": { "chunks": 42, "tokens_total": 18000 }
}
```

#### `PATCH /v1/profiles/{id}`

允许编辑 `structured` 任意子字段。**不**自动重新 chunking,需显式调用下面的接口。

#### `POST /v1/profiles/{id}/rechunk`

重建该 profile 的 `profile_chunks` + embedding。返回 SSE 进度。

#### `GET /v1/profiles/{id}/chunks`

返回当前 chunks(只读,调试用)。

| query | 说明 |
|-------|------|
| `granularity` | `paragraph` / `bullet` / `section` |
| `limit` | 默认 50 |

#### `DELETE /v1/profiles/{id}`

软删除。

---

### 6.5 Matches(匹配分析)

对应 `matches` 表 + `MatchAnalystAgent`。

#### `POST /v1/matches`

请求:`{ "jd_id": 42, "profile_id": 7, "depth": "quick" | "deep" }`

- `quick`:STANDARD tier,只返回评分 + 命中/缺失技能列表
- `deep`:PREMIUM tier,加 gap analysis 与建议

**强制 SSE**:

```
event: started        data: {"job_id":"...","resource_id":17}
event: node_started   data: {"node":"retrieve"}
event: node_completed data: {"node":"retrieve"}
event: node_started   data: {"node":"analyze"}
event: token          data: {"delta":"匹配度评分:"}
...
event: result         data: {"resource_id":17,"url":"/v1/matches/17"}
event: done
```

#### `GET /v1/matches/{id}`

返回 `MatchResult`(见 5-AGENT_DESIGN §6.3)。

#### `GET /v1/matches`

列表,支持 `jd_id`、`profile_id` 过滤。

#### `DELETE /v1/matches/{id}`

软删除。

---

### 6.6 Resumes(定制简历)

对应 `resumes` + `resume_versions` 表 + 简历定制状态机(5-AGENT_DESIGN §7)。

#### `POST /v1/resumes/generate`

启动简历定制状态机。**强制 SSE,有最长 90s 限制**。

请求:

```json
{
  "jd_id": 42,
  "profile_id": 7,
  "options": {
    "tone": "professional",
    "max_words": 1000,
    "force_keywords": ["python", "kubernetes"]
  }
}
```

响应 SSE:

```
event: started        data: {"job_id":"...","resource_id":31}
event: node_started   data: {"node":"retrieve","step":1,"total":5}
event: node_completed data: {"node":"retrieve","duration_ms":1200}
event: node_started   data: {"node":"plan","step":2,"total":5}
event: node_completed data: {"node":"plan"}
event: node_started   data: {"node":"draft","step":3,"total":5}
event: token          data: {"delta":"## 工作经历\n"}
...
event: node_completed data: {"node":"draft"}
event: node_started   data: {"node":"review","step":4,"total":5}
event: node_completed data: {"node":"review","data":{"passed":true}}
event: result         data: {"resource_id":31,"version_id":1,"url":"/v1/resumes/31"}
event: done
```

若 review 失败 + 已重试 2 次:发 `event: error data: {"code":"RESUME_REVIEW_FAILED",...}` 后 `done`。`resumes.status = review_failed`,前端引导用户手动处理。

#### `GET /v1/resumes/{id}`

返回简历元信息 + 当前 active 版本的 markdown。

```json
{
  "id": 31,
  "jd_id": 42,
  "profile_id": 7,
  "status": "ready",
  "active_version_id": 1,
  "markdown": "## 基本信息\n...",
  "review_findings": [],
  "cost_cny": 0.42,
  "created_at": "2026-05-01T08:00:00Z"
}
```

#### `GET /v1/resumes/{id}/versions`

返回该 resume 的全部版本快照。

#### `POST /v1/resumes/{id}/regenerate`

基于现有简历再跑一遍,新增一个 version。可选 `?from_version=<id>` 指定基线。请求体可覆盖 `options`。

#### `POST /v1/resumes/{id}/export`

请求 `{ "format": "pdf" | "docx" | "md" }`
响应 200,`Content-Type` 对应文件类型,`Content-Disposition: attachment; filename="..."`。
PDF 用 LaTeX `awesome-cv` 中文化模板渲染(见 PRD Q-01)。

#### `PATCH /v1/resumes/{id}`

仅允许 `notes`、`status: archived`。`markdown` 不允许直接 patch(用版本机制保证可审计):走下面接口。

#### `POST /v1/resumes/{id}/versions`

请求 `{ "markdown": "...", "note": "手动微调标题" }`
响应 201 → 新 version。`active_version_id` 指向新版本。

#### `DELETE /v1/resumes/{id}`

软删除。

---

### 6.7 Applications(投递追踪)

对应 `applications` 表(简单 CRUD,无 LLM)。

#### `POST /v1/applications`

```json
{
  "jd_id": 42,
  "resume_id": 31,
  "channel": "boss" | "lagou" | "linkedin" | "email" | "other",
  "applied_at": "2026-05-01",
  "note": "..."
}
```

#### `GET /v1/applications`

列表,过滤 `status` / `jd_id` / `applied_after`。

#### `PATCH /v1/applications/{id}`

更新 `status`(`applied`/`screening`/`interview`/`offer`/`rejected`/`ghosted`)、`response_at`、`interview_invited`(NSM 关键字段)、`note`。

#### `DELETE /v1/applications/{id}`

软删除。

#### `GET /v1/applications/stats`

返回当前用户的投递漏斗与 NSM 输入:

```json
{
  "total": 45,
  "by_status": { "applied": 12, "screening": 5, "interview": 8, "offer": 1, "rejected": 15, "ghosted": 4 },
  "interview_invited_rate": 0.18,
  "baseline_invited_rate": 0.10,
  "uplift_pct": 80.0
}
```

---

### 6.8 Interviews(面试模拟)

对应 `interview_sessions` + `interview_turns` 表 + 面试模拟状态机(5-AGENT_DESIGN §8)。

#### `POST /v1/interviews`

启动一场面试模拟。

请求:

```json
{
  "jd_id": 42,
  "profile_id": 7,
  "persona": "principal_engineer" | "tech_lead" | "manager",
  "plan": {
    "basic": 3,
    "advanced": 2,
    "system_design": 1,
    "behavioral": 1
  }
}
```

响应 201:`InterviewSession`(`status: pending`)。

#### `GET /v1/interviews/{id}`

返回会话元信息 + 全部 turns(摘要)。

#### `POST /v1/interviews/{id}/turns`

提交一轮回答,触发评分 + 下一题生成。**强制 SSE,双向式**。

请求(初始题:不传 user_answer,系统生成第一题):

```
POST /v1/interviews/{id}/turns
{ }
```

请求(后续轮):

```
{
  "user_answer": "我会先用 Bloom Filter ...",
  "answered_in_ms": 65000
}
```

响应 SSE:

```
event: started         data: {"job_id":"...","turn_id":12}
event: node_started    data: {"node":"evaluate"}            # 仅非首题有
event: node_completed  data: {"node":"evaluate","data":{"score":78}}
event: node_started    data: {"node":"plan_next"}
event: node_started    data: {"node":"ask"}
event: token           data: {"delta":"假设你的服务"}
...
event: result          data: {"turn_id":13,"question":"..."}
event: done
```

若已问完计划题数:`result` 包含 `done: true` + `final_summary_url`。

#### `GET /v1/interviews/{id}/turns/{turn_id}`

返回单轮完整记录(题目、用户答案、评分、reference answer)。

#### `POST /v1/interviews/{id}/end`

主动结束,触发 `final_summary` 节点(异步)。返回 202 + `summary_job_id`,前端订阅 `/v1/interviews/{id}/summary/stream`。

#### `GET /v1/interviews/{id}/summary`

返回最终汇总(各类题平均分、强弱项、改进建议)。

#### `DELETE /v1/interviews/{id}`

软删除。

---

### 6.9 Files(文件存储)

对应 `files` 表(bytea 存储,见 ADR-0002 §文件)。

#### `POST /v1/files/upload`

`multipart/form-data`:`file` + 可选 `purpose`(`jd_pdf` / `jd_image` / `profile_pdf` / `other`)。

约束:
- 单文件 ≤ 20MB
- MIME 白名单:`application/pdf`、`image/png`、`image/jpeg`、`text/plain`、`text/markdown`、`application/vnd.openxmlformats-officedocument.wordprocessingml.document`
- 总配额(单用户):200MB,超额返回 413

响应 201:

```json
{
  "id": 123,
  "filename": "jd_xyz.pdf",
  "mime": "application/pdf",
  "size_bytes": 102400,
  "sha256": "...",
  "url": "/v1/files/123"
}
```

#### `GET /v1/files/{id}`

下载原文件。`Content-Type` 与上传一致,`Content-Disposition: attachment`。

#### `DELETE /v1/files/{id}`

硬删除(bytea 删完即释放)。

---

### 6.10 LLM Costs(成本可视化)

对应 `llm_calls` 表的聚合视图。

#### `GET /v1/costs/summary`

| query | 说明 |
|-------|------|
| `from` / `to` | RFC3339,默认最近 7 天 |
| `group_by` | `feature` / `model` / `tier` / `day` |

响应:

```json
{
  "total_cny": 1.23,
  "total_input_tokens": 480000,
  "total_output_tokens": 96000,
  "cache_hit_rate": 0.71,
  "groups": [
    { "key": "resume_generate", "cny": 0.84, "calls": 6 },
    { "key": "jd_parse",        "cny": 0.21, "calls": 18 }
  ]
}
```

#### `GET /v1/costs/calls`

最近 LLM 调用列表(分页,调试用)。包含 `prompt_version_id` 用于追溯。

---

### 6.11 Settings(用户偏好)

对应 `users` 表的 settings JSONB 列。

#### `GET /v1/settings`

返回当前用户偏好:

```json
{
  "llm": {
    "provider": "dashscope",
    "byok_enabled": false,
    "auto_degrade": true
  },
  "ui": {
    "theme": "system",
    "locale": "zh-CN"
  },
  "privacy": {
    "telemetry": false
  }
}
```

#### `PATCH /v1/settings`

部分更新。

#### `POST /v1/settings/api-key/test`

请求 `{ "api_key": "sk-..." }`,服务端发一个最小调用(`qwen3.6-flash` 输入 "ping")验证有效性。响应 `{ "ok": true, "balance_estimate_cny": 12.34 }`(余额查询若百炼提供)。

---

### 6.12 Eval(评测,管理员)

对应 `eval_runs` 表。需要 `X-Admin-Token`。

#### `POST /v1/admin/eval/runs`

请求 `{ "suite": "jd_parse_v1", "model_override": "qwen3.6-plus" }`
响应 SSE:进度事件 + `result` 含 `pass_rate`、`per_metric`、`bad_cases_count`。

#### `GET /v1/admin/eval/runs`

历史列表。

#### `GET /v1/admin/eval/runs/{id}`

完整报告(含 bad cases 详情)。

---

### 6.13 Export & Import(数据自管)

按 PRD §5.3 隐私保证。

#### `POST /v1/data/export`

请求 `{ "format": "json" | "zip" }`
响应 SSE 进度 + `result` 携带 `download_url`(临时 token,30min 内有效)。
zip 包含全部表 + 所有 `files` 的原始字节。

#### `POST /v1/data/import`

请求 multipart `file=<zip>`。先做 schema 校验,再事务化写入。冲突策略:`?on_conflict=skip|overwrite`,默认 `skip`。

#### `POST /v1/data/wipe`

⚠️ 不可逆。请求 `{ "confirm": "YES_DELETE_ALL" }`,响应 204。物理 truncate 全部业务表 + 文件 bytea。**保留** `users` 表当前用户行(只清数据,不注销账户)。

---

## 7. 跨切关注点

### 7.1 字段命名

- 请求/响应 JSON 全部 `snake_case`(与 Postgres 列对齐)
- 前端通过 OpenAPI 自动生成的 TS 类型保持 snake_case,在 UI 层再做必要转换
- 时间字段一律以 `_at` 结尾,数值字段成本以 `_cny` 结尾

### 7.2 空值

- 可选字段一律 `null`(不省略,前端类型才稳定)
- 列表字段不存在时为 `[]`,不为 `null`

### 7.3 枚举

OpenAPI 中以 `enum` 数组列出。前端类型生成自动得到 union string literal。新增枚举值视作非 breaking,但移除是 breaking。

### 7.4 Trace 链路

每个请求在响应头追加 `X-Langfuse-Trace-Id`,用户/开发者点击可在自托管 Langfuse 面板看到完整 LLM 调用链(见 2-TECH_DESIGN §6)。

### 7.5 CORS

云端 Demo 默认 `Access-Control-Allow-Origin` 为白名单单条(`https://demo.jobcopilot.local`),**不**用 `*`。本地部署同源不需要 CORS。

### 7.6 Body 大小

JSON body 上限 1MB(`Content-Length` 校验)。超额:413 + `code: PAYLOAD_TOO_LARGE`。文件走专用上传接口,不混在 JSON body。

---

## 8. OpenAPI 与代码生成

### 8.1 自动导出

```
GET /v1/openapi.json     # FastAPI 自动生成
GET /v1/docs             # Swagger UI(开发模式)
GET /v1/redoc            # ReDoc(开发模式)
```

生产模式默认关闭 `/v1/docs` 与 `/v1/redoc`,通过环境变量 `EXPOSE_API_DOCS=1` 启用。

### 8.2 前端类型生成

```bash
# apps/web/scripts/gen-types.ts
pnpm gen:api    # 调 datamodel-code-generator,从 /v1/openapi.json 生成 packages/schemas/src/api.ts
```

CI 步骤:`gen:api` 后 `git diff --exit-code packages/schemas/`,有差异就 fail(强制 PR 同步类型)。

### 8.3 SDK

仅生成 TypeScript SDK(给 web 用)。不写 Python SDK(后端自己用 Pydantic 模型即可)。MCP Server 走另一套接口(详见 8-ENGINEERING.md / 后续 ADR)。

---

## 9. 实现注意事项

### 9.1 SSE 与 FastAPI

- 使用 `sse-starlette` 的 `EventSourceResponse`
- 长任务在独立 `asyncio.Task` 中跑,通过 `asyncio.Queue` 把事件转发给 SSE 通道
- 状态机 checkpoint 写入 `langgraph` 自带 Postgres saver(见 2-TECH_DESIGN §5)

### 9.2 限流与 SSE

SSE 长连接只在**建立**阶段计数,断开重连按新请求计;`token` 事件不计数。计数维度按"任务启动",一场面试模拟 = 1 次,不是每轮一次。

### 9.3 BYOK 的安全边界

- `X-DashScope-Key` 仅传到 LLMClient,中间日志/Trace **必须脱敏**(头打码、URL 不带)
- Langfuse Trace 中 LLM 调用记录不持久化用户 Key,只持久化 model + tokens + cost

### 9.4 幂等键的边界

- 幂等键只对**完全相同的请求体**有效;不同 body 用同一个 key:返回 409 `IDEMPOTENCY_KEY_REUSED`
- LLM 失败的请求**不写**幂等记录(允许重试)

### 9.5 数据时效

- 所有列表接口都接受可选 `as_of` 参数(RFC3339),回放历史状态(用 `created_at <= as_of`)。v1 不实现写入历史,只读快照
- `applications/stats` 必须实时算,不缓存(NSM 数据)

---

## 10. 不在本文档范围

| 主题 | 文档 |
|------|------|
| LLM 调用细节、Tier 路由、Prompt Cache | 2-TECH_DESIGN §4 |
| Agent 行为、状态机节点 | 5-AGENT_DESIGN |
| 数据库 schema、索引 | 3-DATA_MODEL |
| 评测集设计、CI 回归 | 6-EVAL_PLAN |
| MCP Server 工具粒度 | (后续 ADR / 8-ENGINEERING) |
| GraphQL / gRPC / WebSocket | 显式不做。理由:单一协议族(REST + SSE)足以覆盖所有场景,引入第二套协议增加客户端与文档复杂度 |
| 多租户行级 RLS | 当前单用户本地,不需要;云端 Demo 通过会话隔离 |
| Webhook(对外推送) | v1 不做,本地优先 |

---

## 11. 待决问题

- **Q-API-01**:`/v1/resumes/{id}/export` 的 LaTeX PDF 渲染是同步(阻塞返回)还是异步(SSE 进度)?默认同步(< 5s 可接受);若中文字体加载实测 > 8s 改异步
- **Q-API-02**:面试模拟的 `final_summary` 是否需要可流式订阅?默认放在 `POST /interviews/{id}/end` 同步生成(短),若实测 > 10s 改 SSE
- **Q-API-03**:云端 Demo 的会话隔离粒度(IP / cookie / 临时 user)?默认 cookie + 临时 user,30 分钟 TTL。在 7-ROADMAP M5(云端 Demo 上线)前定稿
