---
title: API SPEC - JobCopilot v2(REST + SSE 端点契约)
owner: lemma42796
last_updated: 2026-05-16
purpose: 锁前后端接口契约;每个端点的 path / method / 请求 / 响应 / SSE 事件序列
---

# 1. 一句话总览

REST 走标准 JSON,慢请求(出题 / 单轮评分纠偏 / 整场评分 / JD 一键分析 / 简历诊断)走 SSE 推进度。**单用户本地部署,无 auth,无版本前缀**(端点直接挂 `/api/`,FastAPI 默认)。

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

性能注:笔记量 < 1000 篇前直接全量返;超过再加分页 / lazy load(M3)。

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

聊天框 query 出题(M2 主题类;M3 加岗位类 + 空 query 自选)。

请求(M2 主题类):

```json
{
  "query": "考考我多线程",
  "question_count": 5,
  "mode": "topic"
}
```

请求(M3 岗位类 — 三源融合):

```json
{
  "query": "模拟一面 Java 后端",
  "mode": "job",
  "jd_ids": [101, 102, 105],
  "question_count": 5
}
```

请求(M3 空 query / 系统自选 — SR 调度):

```json
{
  "query": "",
  "mode": "auto",
  "question_count": 5
}
```

| field | type | 说明 |
|-------|------|----|
| `query` | string | 用户聊天框输入(`mode=auto` 时可空)|
| `mode` | enum `topic` \| `job` \| `auto` | 默认 `topic`(M2 仅支持此值;`job`/`auto` 在 M3 启用)|
| `jd_ids` | int[] | (`mode=job` 必填)用户从 JD 库选定的 JD 子集 id 数组 |
| `question_count` | int | 3 ≤ N ≤ 10 |

**约束**:
- `mode=topic` 时 `query` 不能空(违反 → 422 `query_required`);query 长度 ≤ 200 字符(否则 422 `query_too_long`)
- `mode=auto` 在 M3 才启用,M2 阶段返 422 `mode_not_implemented`
- `mode=job` 在 M3 才启用,M2 阶段同上;启用时 `jd_ids` 至少 1 个

**题型比例由后端按 chunks 内容自动决定**(开放式 vs 八股),具体推荐逻辑见 5-AGENT_DESIGN。

SSE 事件序列(M2 主题类):

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
    "source_chunk_ids": [5001, 5002, 5003]
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
| `query_required` | 422 | `mode=topic` 但 query 为空 |
| `query_too_long` | 422 | query 超长(> 200 字符)|
| `mode_not_implemented` | 422 | M2 阶段传 `mode=job` 或 `mode=auto` |
| `no_chunks_for_query` | SSE error | retrieval pipeline 命中 chunks < 阈值(PRD Q-10);**直接报"笔记里没这主题",不兜底放宽** |
| `query_rewrite_failed` | trace warning(回退原 query,不抛错) | 详见 2-TECH §7 |
| `rerank_failed` | trace warning(回退 hybrid top-K) | 同上 |
| `llm_call_failed` | SSE error | quiz_generator LLM 调用失败(已重试) |

**后端落库顺序(M2 主题类)**:
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
  "questions": [
    {
      "order_index": 0,
      "question": {
        "id": 1001,
        "type": "open_ended",
        "prompt": "解释 synchronized 的锁升级过程",
        "source_chunk_ids": [5001, 5002]
      },
      "user_answer": "锁升级的过程是...",
      "answer_turns": [
        {"round_index": 0, "turn_type": "initial", "text": "锁升级的过程是..."}
      ],
      "answer_submitted_at": "2026-05-08T03:05:00Z",
      "judged": true,
      "latest_scores": {"coverage": 55.0, "fidelity": 90.0, "depth": 40.0, "total": 67.5},
      "next_action": "remediate",
      "remediation_prompt": {
        "text": "你提到了锁升级,但还没解释为什么会从偏向锁升级到轻量级锁。请补充触发条件和代价。",
        "triggered_by": "coverage",
        "evidence_chunk_ids": [5001]
      }
    },
    ...
  ]
}
```

`mode='job'` 时 `jd_ids` 数组非空(M3 岗位类);`mode='auto'` 时 `query` 为后端 SR 调度自选的 heading_path 末段(M3 系统自选)。

`status='submitted'` 时 `scores` 字段填充三层 + 总分,且每题带 `evidence`(coverage_evidence / fidelity_evidence / depth_evidence)。

M2.1 起,in_progress session 可返回当前题最新 `remediation_prompt` 和 `answer_turns`,用于用户刷新页面后从 `wait_user_answer` 继续补答。

**重要**:这个端点**不返回 `reference_answer` / `reference_points`**,除非 `status='submitted'`。MVP 是 active recall 强约束,答题过程中前端拿不到 reference,防作弊。

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

提交当前题的一轮答案 / 补答,并推进 `InterviewCoachAgent`:

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
  "turn_type": "initial",
  "client_turn_id": "local-uuid-optional"
}
```

`turn_type` 取值:`initial` / `remediation`。后端会把本轮文本追加到 `answer_turns`,并重建 `user_answer` 累计答案。

SSE 事件:

```
event: started
data: {"job_id": "01HX...", "resource_id": 789, "order_index": 0, "round_index": 1}

event: progress
data: {"phase": "context_pack_built", "included": ["question", "source_chunks", "reference_points", "cumulative_answer", "unresolved_gaps"], "compacted": true}

event: judge_done
data: {
  "order_index": 0,
  "round_index": 1,
  "scores": {"coverage": 82.0, "fidelity": 92.0, "depth": 70.0, "total": 84.2},
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
  "remediation_prompt": null
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

错误码:

| code | 说明 |
|------|----|
| `session_not_in_progress` | session 已 submitted / abandoned |
| `order_index_not_found` | 题号不存在 |
| `invalid_turn_type` | turn_type 非 initial / remediation |
| `context_pack_failed` | 必需上下文缺失,例如 source chunks / reference points 不完整 |
| `judge_call_failed` | Judge LLM 失败(已重试) |

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

**M3 扩展事件**:
- `mode=job`:在 `query_rewriting_done` 之后并入 `phase=jd_subset_aggregating` + `phase=resume_loading` 两步,然后才到 `hybrid_searching`(三源融合)
- `mode=auto`:在 `started` 之后插 `phase=sr_picking_topic` + `phase=sr_topic_picked{heading_path}` 两步,后续走主题类 pipeline

**评分(`POST /api/quiz/sessions/{id}/submit`)完整事件流**:

```
1. started        立即
2. progress       phase=judging  附 order_index 当前进度
3. question_done × N
4. result         汇总分 + recall_md_path
5. done           ok=true
```

异常:任一题 Judge 失败 → 已 done 的题留库,失败题 `error` + `done(ok=false)`,session.status 留 `in_progress`(用户可重试 submit)。

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

响应:`Content-Type: text/markdown; charset=utf-8`,body 是 markdown 文本(同 `notes/_recall/{id}.md`)。

注:答题完成页的评分 + evidence 直接用 §4.4 SSE 推过来的数据渲染,**不依赖此端点**。recall 文件的用途是用户存一份留档(放进自己的 Obsidian / 语雀库,日后翻看)。

# 5. 弱点 + 复习 API(M3)

## 5.1 `GET /api/dashboard/gaps`

弱点排行。

请求(query):`?sort=error_rate|attempt_count|last_score&limit=50`

响应:

```json
{
  "items": [
    {
      "folder_path": ["Java", "集合"],
      "heading_path": ["HashMap"],
      "attempt_count": 4,
      "error_count":   3,
      "error_rate":    0.75,
      "last_score":    52.0,
      "last_attempt_at": "2026-05-06T...",
      "next_review_at": "2026-05-09"
    }
  ]
}
```

## 5.2 `GET /api/dashboard/today`

"今日复习"队列(按 `next_review_at <= today` 升序)。

响应:

```json
{
  "items": [
    {
      "folder_path": ["Java", "集合"],
      "heading_path": ["HashMap"],
      "last_score": 52.0,
      "next_review_at": "2026-05-08",
      "days_overdue": 0
    }
  ]
}
```

## 5.3 `POST /api/quiz/sessions/from-review`(**SSE**)

从今日复习队列拉一个节点开 session。**M3 端点**;后端把 heading_path 末段当 query,转走 §4.1 主题类 pipeline。

请求:

```json
{
  "folder_path": ["Java", "集合"],
  "heading_path": ["HashMap"],
  "question_count": 5
}
```

后端等价于调用 `POST /api/quiz/sessions` `{query: "HashMap", mode: "topic", question_count: 5}`,但 session 落库时额外标 `trigger='sr_review'` + `gap_folder_path` / `gap_heading_path`(用于 dashboard 关联回该 gap)。

SSE 序列同 §4.1(M2 主题类完整流程)。

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

event: result
data: {
  "analysis_id": 7,
  "requirement_count": 28,
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
  "total_cost_cny": 0.42,
  "cache_hit_rate": 0.31
}
```

# 7. 简历诊断 API(M3)

## 7.1 `POST /api/resumes`

上传简历。

**markdown 方式**:
```json
{
  "source": "markdown_paste",
  "title": "我的 Java 简历 v3",
  "content_md": "# 张三\n\n..."
}
```

**PDF 方式**(multipart):
- `source`: `"pdf_upload"`
- `title`: 字符串
- `file`: PDF 文件(≤ 7MB)

后端走 Qwen 多模态对 PDF 各页 OCR 拼接成 markdown,再 chunker。

响应 201:
```json
{
  "id": 5,
  "title": "我的 Java 简历 v3",
  "source": "markdown_paste",
  "parsed_chunks": [
    {"position": "§1", "type": "header", "content": "..."},
    {"position": "§2", "type": "summary", "content": "..."},
    ...
  ],
  "created_at": "..."
}
```

## 7.2 `GET /api/resumes` / `GET /api/resumes/{id}` / `DELETE /api/resumes/{id}`

CRUD,同笔记 / JD 风格,不展开。

## 7.3 `POST /api/resume-analyses`(**SSE**)

诊断。

请求:
```json
{
  "jd_analysis_id": 7,
  "resume_id": 5
}
```

SSE 事件:

```
event: started
data: {"resource_id": 12, "jd_count": 100, "resume_chunk_count": 8}

event: progress
data: {"phase": "loading_inputs"}

event: progress
data: {"phase": "diagnosing", "current": 5, "total": 28}

event: progress
data: {"phase": "anchor_validation"}

event: result
data: {
  "analysis_id": 12,
  "anchored_count": 18,
  "unanchored_count": 6,
  "coverage": {"strong": 8, "weak": 10, "missing": 6},
  "url": "/api/resume-analyses/12"
}

event: done
data: {"ok": true}
```

`anchor_validation` 阶段:service 层校验每条 suggestion 的 req_id + resume_position;不齐 → 标 unanchored。

## 7.4 `GET /api/resume-analyses/{id}`

诊断详情:

```json
{
  "id": 12,
  "jd_analysis_id": 7,
  "resume_id": 5,
  "status": "done",
  "anchored_count": 18,
  "unanchored_count": 6,
  "coverage_summary": {"strong": 8, "weak": 10, "missing": 6},
  "suggestions": [
    {
      "req_id": "req_1",
      "req_text": "Redis 集群 + 分布式锁",
      "req_frequency": 0.75,
      "resume_position": "§3",
      "coverage": "weak",
      "diagnosis": "...",
      "suggestion_topic": "...",
      "tag": "anchored"
    }
  ]
}
```

# 9. 杂项

## 9.1 `GET /v1/health`

```json
{
  "status": "ok",
  "version": "0.0.1",
  "env": "dev",
  "timestamp": "2026-05-10T12:00:00Z"
}
```

部署 healthcheck 暂留 `/v1/health`;新业务端点统一挂 `/api`。`/v1/docs` 和 `/v1/openapi.json` 也沿用 FastAPI 开发入口。

# 10. 已锁定的关键决策

| 项 | 决策 | 备注 |
|----|------|------|
| 路径前缀 | `/api/`(无版本号) | 单用户本地部署,无多版本兼容需求 |
| 认证 | 无 | localhost only,M4+ SaaS 化再加 |
| 时间戳 | ISO-8601 UTC | 前端用 dayjs 转本地展示 |
| ID 类型 | BIGINT,JSON 数字传 | 跟 DB BIGSERIAL 对齐,JS Number 53 位足够 |
| 分页 | cursor(`?cursor=<id>&limit=N`)| 不用 offset(深度分页性能差) |
| SSE 协议 | 沿用 v1:`started → progress* → result/done` | 永久约束 #21,前端走 `web/lib/sse.ts` |
| 错误格式 | `{code, detail}`(沿用 v1 JobCopilotError) | code 给前端分支,detail 中文给用户看 |
| 笔记批量导入 | 同步(MVP);前端按 50 条/批 POST,量大切异步 + SSE(M2 再说) | 走 File System Access API,免去 zip 打包 |
| embedder | 异步后台 worker | API 端点不等 embedding 完;`embedding IS NULL` 的 chunk hybrid search 自动跳过 |
| reference 防作弊 | session in_progress 时不返 reference_answer / reference_points | active recall 强约束 |
| Judge 调用粒度 | MVP 单次 LLM 调用拿三层分;后续可拆 | 简化 SSE 事件;若 Judge 准确度不达标再拆 |
| M2.1 单题推进 | `POST /answers/{order_index}/turns` 提交一轮答案 / 补答并推进 Agent | 返回 `next_action=remediate/ask_next/summarize/finish`;补答后重评累计答案 |
| M2.1 长上下文 | turn SSE 暴露 `context_pack_built` 事件 | 前端可见是否压缩旧轮次;后端保证 source chunks / reference points / unresolved gaps 不丢 |
| 题型比例 | 后端按 chunks 内容自动决定(B);前端不传 type_mix | 推荐逻辑见 5-AGENT_DESIGN;后端在 `progress.type_mix_decided` 事件回推决策 |
| 答题草稿保存 | 边打边存(typing 防抖 1s 后 PUT) | 开放题答题长,断电一字不丢 > 省 PUT 请求 |
| recall 文件语义 | 存档下载,不是评分展示 | 评分 evidence 走 SSE;recall 给用户存进 Obsidian / 语雀留档 |
| 出题入口 | `POST /api/quiz/sessions` 入参 `{query, mode, question_count}`(M3 加 `jd_ids`)| 不再用 `node_folder_path` / `node_heading_path`;笔记面板不触发出题 |
| query 模式三态 | `topic`(M2 主题类)/ `job`(M3 岗位类三源)/ `auto`(M3 SR 自选)| M2 仅 topic;`job`/`auto` M2 阶段返 `mode_not_implemented` |
| 0 命中守门 | retrieval 命中 chunks < 阈值 → SSE error `no_chunks_for_query` + done(false),不兜底放宽 | 阈值见 PRD Q-10 |
| retrieval pipeline 事件 | 出题 SSE 推 5 段独立 phase(`query_rewriting` / `hybrid_searching` / `reranking` / `parent_doc_expanding` / `generating`)| 前端可显示进度;Langfuse trace 同步可见 |
| 简历存储 | 单条记录(全库一行 resumes),无"简历库"端点 | 一个人就一份简历;PUT 覆盖语义,不做"切换简历"端点 |

---

# 不在本文档范围

- 表 schema → `docs/3-DATA_MODEL.md`
- 模块分层 / service 调用关系 → `docs/2-TECH_DESIGN.md`
- QuizGenerator / AnswerJudge prompt 全文 → `docs/5-AGENT_DESIGN.md`
- 评测套件如何覆盖这些端点 → `docs/6-EVAL_PLAN.md`
- 仓库结构 / FastAPI 路由文件组织 → `docs/8-ENGINEERING.md`
- 里程碑节奏 → `docs/7-ROADMAP.md`
