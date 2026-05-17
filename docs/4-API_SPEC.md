---
title: API SPEC - JobCopilot v2(REST + SSE 端点契约)
owner: lemma42796
last_updated: 2026-05-17
purpose: 锁前后端接口契约;每个端点的 path / method / 请求 / 响应 / SSE 事件序列
---

# 1. 一句话总览

REST 走标准 JSON,慢请求(出题 / 单轮评分纠偏 / 整场评分 / JD 一键分析)走 SSE 推进度。**单用户本地部署,无 auth,无版本前缀**(端点直接挂 `/api/`,FastAPI 默认)。

# 2. 通用约定

## 2.1 路径前缀 / 编码

- 所有端点挂 `/api/` 前缀(前端 base URL = `http://localhost:8000/api`)
- 请求 / 响应 body 一律 `application/json; charset=utf-8`,JSON 不转义中文(`ensure_ascii=False`)
- 笔记入库走 application/json(`/api/notes/batch-import`),笔记内容由前端走 File System Access API 从本地读出来后整批 POST,后端不接 multipart
- 时间戳一律 ISO-8601 UTC(`2026-05-08T03:14:00Z`)
- ID 一律 BIGINT,JSON 里以数字传

## 2.2 错误格式(沿用 v1 `JobCopilotError`)

```json
{
  "code": "note_not_found",
  "detail": "note id=123 不存在或已删除"
}
```

- `code`:小写 snake_case,前端用来做分支
- `detail`:中文,直接展给用户
- HTTP 状态码:`400`(参数错)/ `404`(资源不存在)/ `409`(状态冲突,如已 submitted 的 session 再答)/ `422`(校验失败,Pydantic)/ `500`(服务端炸)

## 2.3 SSE 协议(沿用 v1)

媒体类型 `text/event-stream`,事件格式:

```
event: <name>
data: <json>

```

(每事件后空一行)

**通用事件序列**(每个 SSE 端点都遵守):

| 事件 | 时机 | data |
|------|------|------|
| `started` | 资源 INSERT 完拿到 id 后立即推 | `{"job_id": "...", "resource_id": <id>, ...}` |
| `progress` | 中间进度(具体 schema 见各端点) | 见各端点 |
| `result` | 终态结果(可选,有的端点用 `done` 直接带结果) | 见各端点 |
| `error` | 任意阶段失败(后接 `done`) | `{"code": "...", "detail": "..."}` |
| `done` | 收尾(必发,**包括失败也要发**) | `{"ok": true}` 或 `{"ok": false}` |

前端走 `web/lib/sse.ts`(沿用 v1 永久约束 #21)。**重要**:不在 SSE response header 写非 ASCII 字符到 `resource_id`(永久约束 #4)。

## 2.4 分页

列表端点统一用 cursor 分页:

请求:`?cursor=<id>&limit=20`(`limit` 上限 100,默认 20;`cursor` 是上一页最后一行的 id,首页省略)

响应:

```json
{
  "items": [...],
  "next_cursor": 12345,
  "has_more": true
}
```

# 3. 笔记 API(M1)

## 3.1 `POST /api/notes/batch-import`

批量入库 → 前端走 File System Access API 在浏览器选目录 / 选单篇 .md,本地读完 content 后按相对路径解析 folder_path 整批 POST。后端逐条 chunker 入库。**同步**(MVP 笔记量 < 200 篇,5s 内完成),前端显示 loading;如未来量大则切到异步 + SSE(M2 再说)。

**为啥不上传 zip?** 笔记本来就在用户本地,先打包再上传纯属多此一步;浏览器端的 File System Access API(Chromium 系)能直接读本地目录,免去打包/解压两端代码。Safari 仅能选单文件,Firefox 暂不支持 — 前端做特性检测,不支持时提示用 Chromium 系浏览器。

请求:`application/json`

```json
{
  "items": [
    {"folder_path": ["Java", "并发"], "title": "synchronized", "content_md": "# ..."},
    {"folder_path": ["Java"],         "title": "JMM",          "content_md": "# ..."}
  ],
  "root_folder": "archive",
  "overwrite": false
}
```

| field | type | 说明 |
|-------|------|----|
| `items` | NoteBatchImportItem[] | 单批 1-100 条;前端遇到大目录自行分批多次 POST(每批默认 50)|
| `items[].folder_path` | string[] | 相对 `root_folder` 的子路径(如选目录时按子目录层级解析,选单文件默认 `[]`)|
| `items[].title` | string | 文件名去 .md(浏览器端处理) |
| `items[].content_md` | string | UTF-8 markdown 文本(浏览器端 `await file.text()` 读出)|
| `root_folder` | string \| null | (可选)入库时挂载到的 folder 前缀,如 `"Java"` |
| `overwrite` | bool | (可选)同 folder + title 已存在时是否覆盖 content_md,默认 `false`(跳过)|

响应 200(同步入库报告):

```json
{
  "imported": 42,
  "skipped": 3,
  "skipped_reasons": [
    {"path": "Java/dup.md", "reason": "duplicate_folder_title"}
  ],
  "note_ids": [101, 102, ...]
}
```

错误:`409 duplicate_folder_title`(并发场景兜底,不再作为单独 HTTP 错误抛出 — 默认走 skipped 报告)/ `422`(items 为空 / >100 / 字段格式错)

**embedder 异步**:笔记入库时只切 chunks + 落 `content` / `content_tsv`,`embedding` 字段先 NULL;后台 worker 拉队列补 embedding(URL 不感知,前端不等)。

## 3.2 `POST /api/notes`

web 编辑器创建单篇笔记。

请求:

```json
{
  "folder_path": ["Java", "并发"],
  "title": "synchronized 锁升级",
  "content_md": "# synchronized 锁升级\n\n## 无锁\n..."
}
```

响应 201:

```json
{
  "id": 123,
  "folder_path": ["Java", "并发"],
  "title": "synchronized 锁升级",
  "chunk_count": 5,
  "created_at": "2026-05-08T03:14:00Z"
}
```

错误:`409 duplicate_folder_title` / `422 invalid_folder_path`(深度 > 20 / 含空段)

## 3.3 `GET /api/notes/tree`

树形导航数据。返回**扁平列表**,前端按 `folder_path` 自己拼树(避免后端递归 SQL)。

请求:无参数

响应 200:

```json
{
  "notes": [
    {
      "id": 1,
      "folder_path": ["Java", "并发"],
      "title": "synchronized",
      "headings": [
        {"path": ["synchronized"],            "level": 1},
        {"path": ["synchronized", "锁升级"], "level": 2},
        {"path": ["synchronized", "实现"],   "level": 2}
      ],
      "chunk_count": 8,
      "updated_at": "2026-05-08T03:14:00Z"
    },
    ...
  ]
}
```

`headings` 由后端从 `note_chunks.heading_path` 去重聚合得来 — 树形导航的最深一层(heading 级)直接用这里。

性能注:笔记量 < 1000 篇前直接全量返;超过再加分页 / lazy load。

## 3.4 `GET /api/notes/{id}`

单篇笔记详情(编辑器载入用)。

响应 200:

```json
{
  "id": 123,
  "folder_path": ["Java", "并发"],
  "title": "synchronized 锁升级",
  "content_md": "# synchronized ...",
  "source": "web_editor",
  "chunk_count": 5,
  "created_at": "...",
  "updated_at": "..."
}
```

## 3.5 `PUT /api/notes/{id}`

编辑笔记(覆盖语义)。后端先 `DELETE FROM note_chunks WHERE note_id=?` 再重切重 INSERT。

请求:

```json
{
  "folder_path": ["Java", "并发"],
  "title": "synchronized 锁升级",
  "content_md": "...(新内容)..."
}
```

响应 200:同 `POST /api/notes` 的响应结构,新 `chunk_count`。

副作用提示:**老 chunk_id 全失效**,引用该 note 的旧题命中率会降 — service 层异步标记失效题(详见 5-AGENT_DESIGN)。

## 3.6 `DELETE /api/notes/{id}`

软删。`notes.deleted_at = now()`,关联 `note_chunks` 物理删(FK CASCADE)。引用该 note 的旧题不动(用户可能还在 review 历史 session)。

响应 204(空 body)。

## 3.7 `POST /api/notes/{id}/move`

只改 `folder_path`(不动内容)。

请求:

```json
{
  "folder_path": ["Java", "并发", "细节"]
}
```

响应 200:

```json
{
  "id": 123,
  "folder_path": ["Java", "并发", "细节"]
}
```

后端同时 `UPDATE note_chunks SET folder_path = ? WHERE note_id = ?`(反规范化字段同步)。

## 3.8 `GET /api/chunks`

按树节点 prefix 匹配查 chunks(出题前置 / 调试用)。

请求(query string):

| 参数 | type | 说明 |
|------|------|----|
| `folder_path` | repeated string | 必填,节点 folder 路径,如 `?folder_path=Java&folder_path=并发` |
| `heading_path` | repeated string | 可选,如带就要求 `note_chunks.heading_path[1:N] = 给定值` |
| `limit` | int | 默认 200,上限 1000 |

响应 200:

```json
{
  "chunks": [
    {
      "id": 5001,
      "note_id": 123,
      "folder_path": ["Java", "并发"],
      "heading_path": ["synchronized", "锁升级"],
      "heading_level": 2,
      "content": "## 锁升级\n\n无锁 → 偏向锁 → ..."
    }
  ],
  "total": 18
}
```

# 4. 出题 + 答题 API(M2)

## 4.1 `POST /api/quiz/sessions`(**SSE**)

聊天框 topic query 出题。岗位类三源出题和空 query 系统自选已砍掉。

请求:

```json
{
  "query": "考考我多线程",
  "question_count": 5,
  "mode": "topic"
}
```

| field | type | 说明 |
|-------|------|----|
| `query` | string | 用户聊天框输入;或从 JD Intelligence 报告选择的 quiz topic 候选 |
| `mode` | enum `topic` | 默认 `topic`;其他值不支持 |
| `question_count` | int | 1 / 3 / 5 |

**约束**:
- `query` 不能空(违反 → 422 `query_required`);query 长度 ≤ 200 字符(否则 422 `query_too_long`)
- `mode` 只能是 `topic`;`job` / `auto` 返回 422 `mode_not_supported`

**题型比例由后端按 chunks 内容自动决定**(开放式 vs 八股),具体推荐逻辑见 5-AGENT_DESIGN。

SSE 事件序列:

```
event: started
data: {"job_id": "01HX...", "resource_id": 789, "query": "考考我多线程", "mode": "topic"}

event: progress
data: {"phase": "query_rewriting"}

event: progress
data: {"phase": "query_rewriting_done", "expanded_queries": ["考考我多线程", "并发", "锁", "synchronized"]}

event: progress
data: {"phase": "hybrid_searching", "candidate_count": 50}

event: progress
data: {"phase": "reranking"}

event: progress
data: {"phase": "parent_doc_expanding", "chunk_count": 12}

event: progress
data: {"phase": "type_mix_decided", "type_mix": {"open_ended": 3, "definition": 2}}

event: question_ready
data: {
  "order_index": 0,
  "question": {
    "id": 1001,
    "type": "open_ended",
    "prompt": "解释 synchronized 的锁升级过程,以及每一阶段的优化目标",
    "evidence_chunk_ids": [5001, 5002, 5003]
  }
}

... 每题一次 question_ready ...

event: done
data: {"ok": true}
```

失败示例(笔记里没这主题):

```
event: error
data: {"code": "no_chunks_for_query", "detail": "笔记里没找到跟"考考我 React"相关的内容,试试别的主题或先写一些笔记"}

event: done
data: {"ok": false}
```

错误码:

| code | HTTP / SSE | 说明 |
|------|----|----|
| `query_required` | 422 | topic query 为空 |
| `query_too_long` | 422 | query 超长(> 200 字符)|
| `mode_not_supported` | 422 | 传 `mode=job` 或 `mode=auto` |
| `no_chunks_for_query` | SSE error | retrieval pipeline 命中 chunks < 阈值(PRD Q-10);**直接报"笔记里没这主题",不兜底放宽** |
| `query_rewrite_failed` | trace warning(回退原 query,不抛错) | 详见 2-TECH §7 |
| `rerank_failed` | trace warning(回退 hybrid top-K) | 同上 |
| `llm_call_failed` | SSE error | quiz_generator LLM 调用失败(已重试) |

**后端落库顺序(主题类)**:
1. 收到请求,validate query / mode / question_count
2. INSERT `quiz_sessions`(status=`in_progress`, query, mode)拿到 session_id,emit `started`
3. retrieval pipeline:query rewrite → hybrid search → rerank + post-rerank governance/blend → parent-doc 扩展(每段 emit `progress`)
4. 0 命中守门:命中 chunks < 阈值 → emit `error{no_chunks_for_query}` + `done(false)` + UPDATE quiz_sessions.status=abandoned
5. quiz_generator LLM 出 N 题(emit `progress{type_mix_decided}`)
6. INSERT `questions` × N 拿到 ids
7. INSERT `session_answers`(session_id, question_id, order_index, user_answer=NULL)× N
8. emit `question_ready` × N(顺序按 order_index)
9. emit `done(true)`

## 4.2 `GET /api/quiz/sessions/{id}`

session 详情(载入历史 / 续答用)。

响应 200:

```json
{
  "id": 789,
  "query": "考考我多线程",
  "mode": "topic",
  "jd_ids": null,
  "status": "in_progress",
  "agent_state": {
    "last_agent_node": "wait_user_answer",
    "current_question_index": 0,
    "next_action": "remediate"
  },
  "started_at": "2026-05-08T03:00:00Z",
  "submitted_at": null,
  "scores": null,
  "summary": null,
  "questions": [
    {
      "order_index": 0,
      "question": {
        "id": 1001,
        "type": "open_ended",
        "prompt": "解释 synchronized 的锁升级过程",
        "evidence_chunk_ids": [5001, 5002]
      },
      "user_answer": "锁升级的过程是...",
      "answer_turns": [
        {"round_index": 0, "turn_type": "initial", "text": "锁升级的过程是..."}
      ],
      "judge_turns": [
        {"round_index": 0, "turn_type": "judge_feedback", "answer_turn_type": "initial", "coach_message": "..."}
      ],
      "coach_turns": [
        {"round_index": 0, "turn_type": "coach_question", "text": "为什么这里要区分偏向锁和轻量级锁?", "coach_message": "..."}
      ],
      "answer_submitted_at": "2026-05-08T03:05:00Z",
      "judged": true,
      "scores": {"coverage": 55.0, "fidelity": 90.0, "depth": 40.0, "total": 67.5},
      "remediation_state": {
        "last_decision": "remediate",
        "exit_reason": null,
        "judge_score_history": [{"coverage": 55.0, "fidelity": 90.0, "depth": 40.0, "total": 67.5}],
        "unresolved_gaps": []
      },
      "next_action": "remediate",
      "remediation_prompt": {
        "text": "你提到了锁升级,但还没解释为什么会从偏向锁升级到轻量级锁。请补充触发条件和代价。",
        "triggered_by": "coverage",
        "evidence_chunk_ids": [5001]
      },
      "coach_message": "..."
    },
    ...
  ]
}
```

`mode` 当前只支持 `topic`;JD Intelligence 报告里的 quiz topic 候选进入本端点时也作为普通 topic query 处理。

`status='submitted'` 时 `scores` 字段填充三层 + 总分,且每题带 `evidence`(coverage_evidence / fidelity_evidence / depth_evidence)。M2.1 的 `finish_session` 完成后还会返回 `summary`,内容来自 `agent_state.final_summary`。

M2.1 起,in_progress session 可返回当前题最新 `answer_turns / judge_turns / coach_turns / remediation_state / remediation_prompt / coach_message`,用于用户刷新页面后从 `wait_user_answer` 继续补答并回放多轮消息。`remediation_state.judge_score_history` 是后端判断连续无明显提升和生成整场总结的结构化依据。

**重要**:这个端点**不返回 `reference_answer` / `scoring_points`**,除非 `status='submitted'`。MVP 是 active recall 强约束,答题过程中前端拿不到参考答案和采分点,防作弊。

## 4.3 `PUT /api/quiz/sessions/{id}/answers/{order_index}`

单题答案落库(草稿 / 中途保存)。**前端策略:边打边存**(typing 防抖 1 秒后 PUT 一次),不等切题。理由:开放题答题时长可能 5+ 分钟,断电 / 关 tab 一字不丢的体验比省几个 PUT 请求更值。

请求:

```json
{"user_answer": "锁升级的过程是..."}
```

响应 200:

```json
{
  "session_id": 789,
  "order_index": 0,
  "user_answer": "锁升级的过程是...",
  "answer_submitted_at": "2026-05-08T03:05:00Z"
}
```

错误:`409 session_not_in_progress`(session 已 submitted / abandoned)/ `404 order_index_not_found`

### 4.3.1 `POST /api/quiz/sessions/{id}/answers/{order_index}/turns`(**SSE,M2.1**)

提交当前题的一轮用户输入,并推进 `InterviewCoachAgent`。前端只有一个发送入口;后端根据 `turn_type=auto` 把文本分流为初答 / 补答 / 追问教练:

```
wait_user_answer
→ build_context_pack
→ judge_answer
→ decide_next_action
→ generate_remediation_prompt? / ask_next / summarize
```

这个端点是 M2.1 多轮纠偏主入口。`PUT /answers/{order_index}` 仍负责草稿保存;本端点负责"用户确认提交这一轮"后的评分与分支。

请求:

```json
{
  "text": "补充:Outbox 会和业务事务一起落库,再由后台任务投递 MQ。",
  "turn_type": "auto",
  "client_turn_id": "local-uuid-optional"
}
```

`turn_type` 取值:`auto` / `initial` / `remediation` / `coach_question`,默认 `auto`。请求传 `auto` 时,`started.turn_type` 返回后端实际分流后的类型,前端据此显示"按答案处理"或"按追问处理"。

- `auto`:后端判定实际类型。当前题还没有累计答案时归为 `initial`;明确补答词或大段技术陈述归为 `remediation`;明确提问词或模糊短句归为 `coach_question`。
- `initial` / `remediation`:后端会把本轮文本追加到 `answer_turns`,并重建 `user_answer` 累计答案,随后重跑 AnswerJudge。
- `coach_question`:用于用户追问教练反馈,只写 `session_events`,不改 `user_answer`,不重评,不推进题目状态。

SSE 事件:

```
event: started
data: {"job_id": "01HX...", "resource_id": 789, "order_index": 0, "round_index": 1, "turn_type": "remediation"}

event: progress
data: {"phase": "context_pack_built", "included": ["question", "judge_context_chunks", "scoring_points", "cumulative_answer", "unresolved_gaps", "prior_turn_summary"], "compacted": true}

event: judge_done
data: {
  "order_index": 0,
  "round_index": 1,
  "scores": {"coverage": 82.0, "fidelity": 92.0, "depth": 70.0, "total": 84.2},
  "coach_message": "...",
  "unresolved_gaps": []
}

event: decision_done
data: {
  "next_action": "ask_next",
  "decision_reason": "coverage 达标且 fabricated 很低",
  "exit_reason": "target_reached"
}

event: result
data: {
  "session_id": 789,
  "order_index": 0,
  "round_index": 1,
  "next_action": "ask_next",
  "cumulative_answer": "锁升级的过程是...\n补充:...",
  "scores": {"coverage": 82.0, "fidelity": 92.0, "depth": 70.0, "total": 84.2},
  "remediation_prompt": null,
  "coach_message": "..."
}

event: done
data: {"ok": true}
```

若需要继续纠偏:

```json
{
  "next_action": "remediate",
  "remediation_prompt": {
    "text": "你已经说到 Outbox 会落库,但还没解释它为什么能和业务事务保持一致。请补充事务边界和失败重试怎么处理。",
    "triggered_by": "coverage",
    "missing_reference_point_ids": ["rp_2"],
    "fabricated_claim_ids": [],
    "missing_depth_dimensions": [],
    "evidence_chunk_ids": [101]
  }
}
```

若本轮被分流为 `coach_question`,事件流变为:

```
event: started
data: {"job_id": "coach-question-789-0-0", "resource_id": 789, "order_index": 0, "round_index": 0, "turn_type": "coach_question"}

event: progress
data: {"phase": "coach_context_built", "included": ["question", "judge_context_chunks", "cumulative_answer", "previous_coach_message", "remediation_prompt"], "compacted": false}

event: coach_done
data: {"order_index": 0, "round_index": 0, "turn_type": "coach_question", "text": "为什么 Outbox 比直接发 MQ 更稳?", "coach_message": "..."}

event: done
data: {"ok": true}
```

长上下文治理:当答案轮次达到压缩阈值时,后端在 `session_events` 追加 `context_compacted`,并在落库的 `context_pack_built` event payload 写入 `prior_turn_summary / compacted / token_budget_exhausted / judge_context_chunk_ids`。SSE `progress` 只暴露 `included / compacted` 等前端展示所需字段。source chunks、scoring points、累计答案和 unresolved gaps 不被丢弃;旧轮次只进入摘要。

退出语义:当前实现的 `decision_done.exit_reason` 为 `target_reached` / `no_meaningful_improvement` / `token_budget` 之一或 `null`。`no_meaningful_improvement` 依赖 `remediation_state.judge_score_history`:第 3 轮起若最近两轮总分提升都低于阈值且缺口不变,后端不再继续 `remediate`,而是收住当前题。

错误码:

| code | 说明 |
|------|----|
| `session_not_in_progress` | session 已 submitted / abandoned |
| `order_index_not_found` | 题号不存在 |
| `invalid_turn_type` | turn_type 非 auto / initial / remediation / coach_question |
| `context_pack_failed` | 必需上下文缺失,例如 source chunks / scoring points 不完整 |
| `judge_call_failed` | Judge LLM 失败(已重试) |
| `coach_call_failed` | 教练解释 LLM 失败(已重试) |

## 4.4 `POST /api/quiz/sessions/{id}/submit`(**SSE**)

触发 Judge 评分。前置:所有题的 `user_answer` 不为 NULL。

请求 body:空(或 `{}`)。

SSE 事件:

```
event: started
data: {"job_id": "01HX...", "resource_id": 789, "total_questions": 5}

event: question_done
data: {
  "order_index": 0,
  "scores": {
    "coverage": 75.0,
    "fidelity": 85.0,
    "depth":   67.0,
    "total":   76.7
  },
  "evidence": {
    "coverage_evidence": {"points": [...], "score_raw": 0.75, "reasoning": "..."},
    "fidelity_evidence": {"claims": [...], "score_raw": 0.85, "reasoning": "..."},
    "depth_evidence":    {"dimensions": {...}, "score_raw": 0.67, "reasoning": "..."}
  }
}

... 每题一次 question_done ...

event: result
data: {
  "session_id": 789,
  "scores": {
    "coverage": 72.4,
    "fidelity": 80.6,
    "depth":   65.0,
    "total":   74.0
  },
  "recall_md_path": "notes/_recall/789.md"
}

event: done
data: {"ok": true}
```

错误码:

| code | 说明 |
|------|----|
| `session_not_in_progress` | session 已 submitted / abandoned |
| `unanswered_questions` | 还有题没答(detail 里给 missing order_indexes) |
| `judge_call_failed` | Judge LLM 失败(已重试) |

后端流程(详见 §4.6):异步 background — 一次 LLM 调用拿三层分(MVP),N 题串行(因 LLM 推理排队;并行后再说)。

### 4.4.1 `POST /api/quiz/sessions/{id}/finish`(**SSE,M2.1**)

结束 M2.1 面试会话并生成整场总结。这个端点**不重新 Judge**;它要求每题已经通过 `POST /answers/{order_index}/turns` 得到最新评分,再基于各题最新分数、`answer_turns`、Judge gaps 与 `remediation_state` 生成 deterministic session summary。

前置条件:

- session 仍为 `in_progress`
- session 下每题都有非空累计答案
- 每题都有 `judged_at` 与 coverage / fidelity / depth / total 最新分数

SSE 事件:

```
event: started
data: {"job_id": "finish-session-789", "resource_id": 789, "session_id": 789, "total_questions": 3}

event: progress
data: {
  "phase": "summarizing",
  "included": ["questions", "answer_turns", "judge_scores", "judge_gaps", "remediation_state"],
  "compacted": true
}

event: result
data: {
  "session_id": 789,
  "scores": {"coverage": 90.0, "fidelity": 100.0, "depth": 100.0, "total": 95.0},
  "summary": {
    "version": "session_summary_v1",
    "headline": "...",
    "strengths": ["..."],
    "recurring_gaps": [],
    "remediation_wins": ["..."],
    "review_suggestions": ["..."],
    "question_summaries": [...],
    "context_pack": {...},
    "markdown": "# 面试练习总结 #789\n..."
  },
  "recall_md_path": "notes/_recall/789.md"
}

event: done
data: {"ok": true}
```

落库副作用:

- `quiz_sessions.status = submitted`,写入 session-level scores、`submitted_at`、`recall_md_path`
- `quiz_sessions.agent_state` 写入 `last_agent_node=finish_session`、`next_action=finish`、`question_summaries`、`summary_context_pack`、`final_summary`
- 写入本地笔记根目录下的 `_recall/{session_id}.md`;`recall_md_path` 仍保存逻辑路径 `notes/_recall/{session_id}.md`
- `session_events` 追加 `session_summarized` 与 `session_finished`

错误码:

| code | 说明 |
|------|----|
| `session_not_in_progress` | session 已 submitted / abandoned |
| `session_not_ready_to_finish` | 仍有题未答或未完成最新 Judge |
| `context_pack_failed` | session / question / chunk 上下文不完整 |

## 4.5 `POST /api/quiz/sessions/{id}/abandon`

中途放弃。

请求 body 空。

响应 200:`{"id": 789, "status": "abandoned", "abandoned_at": "..."}`

注:abandoned 状态的 session 不能再续答(若用户后悔需新开一个 session)。前端在退出按钮上做二次确认 UX。

## 4.6 出题 / 评分 SSE 事件序列(完整版)

把 §4.1 / §4.4 的 SSE 序列汇总给前端实现参考。

**出题(`POST /api/quiz/sessions`)完整事件流(M2 主题类)**:

```
1. started        立即(预占 session 行,status=in_progress,带 query / mode)
2. progress       phase=query_rewriting
3. progress       phase=query_rewriting_done       附 expanded_queries[]
4. progress       phase=hybrid_searching           附 candidate_count
5. progress       phase=reranking
6. progress       phase=parent_doc_expanding       附 chunk_count
7. (0 命中守门)   chunk_count < 阈值 → emit error{no_chunks_for_query} + done(false) + 标 abandoned
8. progress       phase=generating                 附 model="qwen3.6-flash"
9. progress       phase=type_mix_decided           附 type_mix
10. question_ready × N  按 order_index 升序
11. done          ok=true
```

异常:任一阶段炸 → `error` + `done(ok=false)`,session 行标 `abandoned_at`。

**已砍掉的扩展事件**:`mode=job` 岗位类三源出题和 `mode=auto` SR 系统自选不再实现,因此不会出现 `jd_subset_aggregating`、`resume_loading`、`sr_picking_topic` 等事件。

**评分(`POST /api/quiz/sessions/{id}/submit`)完整事件流**:

```
1. started        立即
2. progress       phase=judging  附 order_index 当前进度
3. question_done × N
4. result         汇总分 + recall_md_path
5. done           ok=true
```

异常:任一题 Judge 失败 → 已 done 的题留库,失败题 `error` + `done(ok=false)`,session.status 留 `in_progress`(用户可重试 submit)。

**M2.1 整场总结(`POST /api/quiz/sessions/{id}/finish`)完整事件流**:

```
1. started        确认每题已答且已评分
2. progress       phase=summarizing,附 summary context pack 字段
3. result         汇总分 + summary + recall_md_path
4. done           ok=true
```

异常:有题未评分 → `error{session_not_ready_to_finish}` + `done(ok=false)`,session.status 留 `in_progress`。这个路径不重新调用 AnswerJudge,避免用户点"生成总结"时分数漂移。

## 4.7 `GET /api/quiz/sessions`

历史 session 列表。

请求(query):`?status=submitted&cursor=<id>&limit=20`

响应:

```json
{
  "items": [
    {
      "id": 789,
      "query": "考考我多线程",
      "mode": "topic",
      "status": "submitted",
      "started_at": "...",
      "submitted_at": "...",
      "total_score": 74.0,
      "question_count": 5
    }
  ],
  "next_cursor": 788,
  "has_more": true
}
```

## 4.8 `GET /api/quiz/sessions/{id}/recall`

下载 session 沉淀 markdown(US-11)— **存档语义**,不是评分展示。

响应:`Content-Type: text/markdown; charset=utf-8`,body 是 markdown 文本。M2.1 当前实现优先读取 `recall_md_path=notes/_recall/{id}.md` 对应的本地文件;文件缺失时兼容回退到 `agent_state.final_summary.markdown`。

本地文件路径:逻辑 `notes/` 对应的 filesystem root 由 `JOBCOPILOT_NOTES_FS_ROOT` 配置;留空时 dev 环境优先使用 `test-notes/llm-notes`,否则使用项目下 `notes/`。写入目标只允许固定 `_recall/{session_id}.md`,不接受请求传任意路径。

注:答题完成页的评分 + evidence 直接用 §4.4 SSE 推过来的数据渲染,**不依赖此端点**。recall 文件的用途是用户存一份留档(放进自己的 Obsidian / 语雀库,日后翻看)。

# 5. 已砍掉的弱点 / 复习 API

不再实现 `GET /api/dashboard/gaps`、`GET /api/dashboard/today`、`POST /api/quiz/sessions/from-review`。后续不做长期弱点表、SR 今日复习队列或 dashboard;练习入口只来自用户 topic query 或 JD Intelligence 报告里的 quiz topic 候选。

# 6. JD 分析 API(M2.5)

## 6.1 `POST /api/jds`

单条上传 JD(文本或截图二选一),后端**立即** jd_parser 解析并落库。

请求(multipart/form-data 或 application/json,二选一):

**文本方式**(`Content-Type: application/json`):
```json
{
  "source": "text_paste",
  "raw_text": "岗位:Java 后端工程师\n职责:1. 负责..."
}
```

**截图方式**(`Content-Type: multipart/form-data`):
- field `source`: `"image_upload"`
- field `image`: 图片文件(≤ 7MB,JPEG/PNG/WEBP/HEIC)

响应 201:
```json
{
  "id": 123,
  "source": "text_paste",
  "title": "Java 后端工程师",
  "parsed_payload": {
    "title": "Java 后端工程师",
    "responsibilities": [...],
    "hard_skills": [...],
    "soft_skills": [...],
    "experience_years": "3-5",
    "education": "本科及以上"
  },
  "created_at": "2026-05-08T03:14:00Z"
}
```

错误:`400 invalid_image_format` / `413 image_too_large` / `500 ocr_failed` / `500 jd_parse_failed`(retry 2 次仍失败)

**截图流程**:multipart 上传 → 服务端 base64 编码 → Qwen 多模态调用(prompt:"请提取这张 JD 截图里的完整文本,只返回文本不加解释")→ 拿到 raw_text → 走文本方式同流程入 jd_parser。

## 6.2 `GET /api/jds`

列 JD 库。

请求(query):`?title=&cursor=<id>&limit=20`(`title` 是模糊匹配过滤)

响应:
```json
{
  "items": [
    {
      "id": 123,
      "title": "Java 后端工程师",
      "source": "text_paste",
      "raw_text_preview": "岗位:Java 后端工程师...",
      "hard_skills_count": 12,
      "created_at": "..."
    }
  ],
  "next_cursor": 122,
  "has_more": true
}
```

`raw_text_preview` 截前 200 字符,避免列表 payload 过大。

## 6.3 `GET /api/jds/{id}`

详情。返回完整 raw_text + parsed_payload。

## 6.4 `PATCH /api/jds/{id}`

仅支持改 `title`(LLM 自动抽的 title 用户可改)。

请求:`{"title": "Java 后端 - 北京"}`

## 6.5 `DELETE /api/jds/{id}`

软删(deleted_at)。已被某次 jd_analysis 引用的 JD 不影响历史报告(jd_analyses.jd_ids 数组里 id 仍存,前端按需过滤)。

## 6.6 `POST /api/jd-analyses`(**SSE**)

一键分析。**单次上限 200 条 JD**(超过 422)。

请求:
```json
{
  "filter": {
    "type": "all"   // "all" | "title" | "ids" | "recent"
    // 如 type=title:  "value": "Java 后端"
    // 如 type=ids:    "ids": [101, 102, 103]
    // 如 type=recent: "n": 50
  },
  "filter_description": "全部"   // 用户可自填,不填则系统按 filter 自动生成
}
```

约束:filter 解析后命中的 jd_ids 数量 ≤ 200(后端校验,超过返 `422 jd_count_exceeds_limit`)。

SSE 事件:

```
event: started
data: {"job_id": "...", "resource_id": 7, "jd_count": 100}

event: progress
data: {"phase": "loading_parsed", "jd_count": 100}

event: progress
data: {"phase": "reducing_batch", "batch": 1, "total": 5}

event: progress
data: {"phase": "reducing_batch", "batch": 5, "total": 5}

event: progress
data: {"phase": "merging"}

event: progress
data: {"phase": "frequency_recompute"}

event: progress
data: {"phase": "learning_path_gen"}

event: progress
data: {"phase": "note_matching"}

event: progress
data: {"phase": "quiz_topic_generating"}

event: result
data: {
  "analysis_id": 7,
  "requirement_count": 28,
  "quiz_topic_count": 12,
  "url": "/api/jd-analyses/7"
}

event: done
data: {"ok": true}
```

错误码:

| code | 说明 |
|------|----|
| `jd_count_exceeds_limit` | filter 命中 > 200 条,SSE 起手前 422 |
| `jd_count_zero` | filter 命中 0 条 |
| `aggregator_call_failed` | LLM 聚合调用失败(已重试) |

## 6.7 `GET /api/jd-analyses`

历史报告列表。`?cursor=&limit=`,分页同笔记列表。

## 6.8 `GET /api/jd-analyses/{id}`

某次分析详情:

```json
{
  "id": 7,
  "jd_ids": [101, ..., 200],
  "jd_count": 100,
  "filter_description": "全部",
  "status": "done",
  "started_at": "...",
  "completed_at": "...",
  "aggregated_requirements": [
    {
      "id": "req_1",
      "canonical_text": "Redis 集群 + 分布式锁",
      "category": "硬技能",
      "frequency": 0.75,
      "raw_phrases": [...],
      "supporting_jd_ids": [...]
    }
  ],
  "learning_path_md": "## 你的学习路径...",
  "quiz_topic_candidates": [
    {
      "topic": "JVM 内存模型与 GC 调优",
      "priority": "high",
      "source_req_ids": ["req_2"],
      "frequency": 0.92,
      "note_match_status": "partial"
    }
  ],
  "note_match_summary": [
    {"req_id": "req_2", "status": "partial", "matched_note_ids": [12, 18]}
  ],
  "total_cost_cny": 0.42,
  "cache_hit_rate": 0.31
}
```

# 7. 已砍掉的简历 API

不再实现 `/api/resumes`、`/api/resume-analyses` 或任何简历诊断 / 改写端点。JD Intelligence 报告只输出岗位要求地图、学习路径和 quiz topic 候选,不读取或生成简历内容。

# 8. 杂项

## 8.1 `GET /v1/health`

```json
{
  "status": "ok",
  "version": "0.0.1",
  "env": "dev",
  "timestamp": "2026-05-10T12:00:00Z"
}
```

部署 healthcheck 暂留 `/v1/health`;新业务端点统一挂 `/api`。`/v1/docs` 和 `/v1/openapi.json` 也沿用 FastAPI 开发入口。

# 9. 已锁定的关键决策

| 项 | 决策 | 备注 |
|----|------|------|
| 路径前缀 | `/api/`(无版本号) | 单用户本地部署,无多版本兼容需求 |
| 认证 | 无 | localhost only;SaaS 不进入当前路线 |
| 时间戳 | ISO-8601 UTC | 前端用 dayjs 转本地展示 |
| ID 类型 | BIGINT,JSON 数字传 | 跟 DB BIGSERIAL 对齐,JS Number 53 位足够 |
| 分页 | cursor(`?cursor=<id>&limit=N`)| 不用 offset(深度分页性能差) |
| SSE 协议 | 沿用 v1:`started → progress* → result/done` | 永久约束 #21,前端走 `web/lib/sse.ts` |
| 错误格式 | `{code, detail}`(沿用 v1 JobCopilotError) | code 给前端分支,detail 中文给用户看 |
| 笔记批量导入 | 同步(MVP);前端按 50 条/批 POST,量大切异步 + SSE(M2 再说) | 走 File System Access API,免去 zip 打包 |
| embedder | 异步后台 worker | API 端点不等 embedding 完;`embedding IS NULL` 的 chunk hybrid search 自动跳过 |
| reference 防作弊 | session in_progress 时不返 reference_answer / scoring_points | active recall 强约束 |
| Judge 调用粒度 | MVP 单次 LLM 调用拿三层分;后续可拆 | 简化 SSE 事件;若 Judge 准确度不达标再拆 |
| M2.1 单题推进 | `POST /answers/{order_index}/turns` 提交一轮输入并推进 Agent | `turn_type=auto` 由后端分流为初答 / 补答 / 追问教练;补答后重评累计答案 |
| M2.1 长上下文 | turn SSE 暴露 `context_pack_built` 事件 | 前端可见是否压缩旧轮次;后端保证 source chunks / scoring points / unresolved gaps 不丢 |
| M2.1 退出策略 | `remediation_state.judge_score_history` 驱动无明显提升退出;token budget 退出单独标 `exit_reason=token_budget` | 不恢复"单题最多 1 轮"限制,但必须有 deterministic 退出条件 |
| 题型比例 | 后端按 chunks 内容自动决定(B);前端不传 type_mix | 推荐逻辑见 5-AGENT_DESIGN;后端在 `progress.type_mix_decided` 事件回推决策 |
| 答题草稿保存 | 边打边存(typing 防抖 1s 后 PUT) | 开放题答题长,断电一字不丢 > 省 PUT 请求 |
| recall 文件语义 | 存档下载,不是评分展示 | 评分 evidence 走 SSE;recall 给用户存进 Obsidian / 语雀留档 |
| 出题入口 | `POST /api/quiz/sessions` 入参 `{query, mode, question_count}` | 不再用 `node_folder_path` / `node_heading_path`;笔记面板不触发出题 |
| query 模式 | 只支持 `topic` | `job` 岗位类三源和 `auto` SR 自选已砍掉 |
| 0 命中守门 | retrieval 命中 chunks < 阈值 → SSE error `no_chunks_for_query` + done(false),不兜底放宽 | 阈值见 PRD Q-10 |
| retrieval pipeline 事件 | 出题 SSE 推 5 段独立 phase(`query_rewriting` / `hybrid_searching` / `reranking` / `parent_doc_expanding` / `generating`)| 前端可显示进度;Langfuse trace 同步可见 |
| 简历 API | 全部砍掉 | 不上传、不诊断、不改写、不参与出题 |

---

# 不在本文档范围

- 表 schema → `docs/3-DATA_MODEL.md`
- 模块分层 / service 调用关系 → `docs/2-TECH_DESIGN.md`
- QuizGenerator / AnswerJudge prompt 全文 → `docs/5-AGENT_DESIGN.md`
- 评测套件如何覆盖这些端点 → `docs/6-EVAL_PLAN.md`
- 仓库结构 / FastAPI 路由文件组织 → `docs/8-ENGINEERING.md`
- 里程碑节奏 → `docs/7-ROADMAP.md`
