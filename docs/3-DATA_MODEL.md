---
title: DATA MODEL - JobCopilot v2(笔记 / 题 / 答 / 弱点 schema)
owner: lemma42796
last_updated: 2026-05-08
purpose: 锁所有表 schema、字段语义、JSONB 子结构、索引、迁移路径
---

# 1. 一句话总览

笔记切 chunks 入库 → 出题(带 source_chunk_ids 反幻觉)→ 答(纯文本)→ 三层 LLM Judge 评分(每层带 evidence)→ 按 `(folder_path, heading_path)` 维度跟踪弱点 + SR 排期。

# 2. 实体关系总览

```
┌──────────┐ 1   N ┌──────────────┐ 1   N (FK source_chunk_ids[])  ┌────────────┐
│  notes   │───────│  note_chunks │ ─────────────────────────────► │ questions  │
└──────────┘       └──────────────┘                                 └────────────┘
                                                                           │
                                                                           │ N
                                                                           ▼
┌──────────────┐ 1   N ┌──────────────────┐                  ┌────────────┐
│ quiz_sessions│───────│ session_answers  │ ────────────────►│ questions  │
└──────────────┘       └──────────────────┘  (FK question_id) └────────────┘
       │                       │
       │ ↓ 评分后 upsert        │ ↓ 评分后 upsert
       ▼                       ▼
                        ┌────────────────┐
                        │ knowledge_gaps │  按 (folder_path, heading_path) 唯一
                        └────────────────┘
```

辅助表(沿用 v1,跟核心闭环正交):

- `llm_calls` / `prompt_versions`(LLM 成本 + Prompt 版本号,沿用 v1 alembic 0006)
- `llm_response_cache`(LLM 响应缓存,沿用 v1 alembic 0015)

砍掉的 v1 表见 §10。

# 3. 全局约定

## 3.1 单用户(MVP)

M1-M3 是单用户本地 dogfood,**所有业务表均不带 `user_id`**。M4+ SaaS 化时统一 `ALTER TABLE ... ADD COLUMN user_id BIGINT NOT NULL DEFAULT 1` + 加唯一约束。

(沿用 v1 的 `users` 表也砍。M0 末态 `users` / `files` / `profiles` / `jds` / `resumes` / `matches` 全部 DROP。)

## 3.2 路径字段统一用 `text[]`

`folder_path` 和 `heading_path` 都用 Postgres 一维 `text[]`,**不**用 ltree(label 不允许中文 / 空格,笔记目录名约束太强)、**不**用 `text` 加 `/` 分隔(prefix LIKE 慢且字符冲突难处理)。

- 等值匹配:`folder_path = ARRAY['Java','并发']`
- 前缀匹配(树形节点查所有子孙 chunk):`folder_path[1:cardinality(query)] = query`
- GIN 索引:`USING gin (folder_path)`(支持包含查询)
- 不设硬上限(`text[]` 本身无限制),chunker 入库时加 sanity check(深度 > 20 层报错提示),具体阈值跑起来看真实分布再调

## 3.3 时间戳 / 软删

- 所有"主体表"(notes / questions / quiz_sessions / knowledge_gaps)有 `created_at` / `updated_at` / `deleted_at`(全 `TIMESTAMPTZ`)
- 软删 = `deleted_at IS NOT NULL`;唯一约束改用 partial index(`WHERE deleted_at IS NULL`)
- "子表"(note_chunks / session_answers)走 `ON DELETE CASCADE` 硬删,跟父表生命周期绑死

## 3.4 主键 / 外键

- 全 `BIGSERIAL` 主键(沿用 v1 IDMixin)
- FK 用 `ON DELETE CASCADE`,父表软删时由应用层级联软删 chunks(在 service 层而非 DDL,因为 chunks 不需要软删,直接物理删除)
- ADR-0005 D1 沿用:**ORM 不写 `relationship()`**,Python 侧手写 `select(...).where(parent_id == ...)` 走子查询

# 4. ENUM 类型

| ENUM 名 | 值 | 用途 |
|---------|----|----|
| `note_source` | `local_md` / `web_editor` / `yuque` | notes.source;`local_md` = File System Access API 选目录 / 选单篇;`yuque` 仅 M3 启用 |
| `question_type` | `open_ended` / `definition` | questions.type;PRD §5.2 US-6 |
| `quiz_session_status` | `in_progress` / `submitted` / `abandoned` | quiz_sessions.status |

JSONB 内的标签字段(`coverage_label` / `fidelity_label`)不用 ENUM,在应用层 Pydantic 校验。

# 5. 表结构

## 5.1 `notes`(笔记)

一行 = 一篇 markdown 笔记(文件级或 web 编辑器单篇)。

```sql
CREATE TABLE notes (
  id           BIGSERIAL PRIMARY KEY,

  folder_path  TEXT[]      NOT NULL,                  -- ['Java','并发']
  title        VARCHAR(255) NOT NULL,                  -- 文件名(去 .md)或编辑器标题
  content_md   TEXT         NOT NULL,                  -- 原始 markdown

  source       note_source  NOT NULL,
  external_id  VARCHAR(100),                           -- yuque doc slug / id (M3)
  external_updated_at TIMESTAMPTZ,                     -- yuque 增量同步用

  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at   TIMESTAMPTZ
);

-- 同 folder + title 不能重复(软删后可复用)
CREATE UNIQUE INDEX uq_notes_folder_title
  ON notes (folder_path, title)
  WHERE deleted_at IS NULL;

CREATE INDEX ix_notes_folder
  ON notes USING GIN (folder_path);

-- M3 yuque 增量同步:按 external_id 查
CREATE INDEX ix_notes_external_id
  ON notes (external_id)
  WHERE external_id IS NOT NULL;
```

字段语义:

- `folder_path`:笔记在树形导航中的位置。本地目录直读时从相对路径解析(`Java/并发/synchronized.md` → `['Java','并发']`),编辑器场景用户在保存时点选目标 folder
- `content_md`:整篇 markdown,用户编辑保存覆盖即可。**chunker 从这里取**,不依赖磁盘文件
- `external_id`:M3 语雀同步时存 doc slug,本地编辑器笔记保持 NULL
- `external_updated_at`:语雀 doc 上次更新时间,增量同步对比基准

## 5.2 `note_chunks`(笔记切片)

一行 = 一个 H2 / H3 chunk(heading-aware chunker 输出)。

```sql
CREATE TABLE note_chunks (
  id            BIGSERIAL PRIMARY KEY,
  note_id       BIGINT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,

  -- 反规范化(避免每次 join):
  folder_path   TEXT[] NOT NULL,
  heading_path  TEXT[] NOT NULL,                       -- ['synchronized','锁升级过程']
  heading_level INTEGER NOT NULL,                      -- 2 = H2 / 3 = H3
  chunk_index   INTEGER NOT NULL,                      -- 在该 note 内的 0-based 序号

  content       TEXT NOT NULL,                         -- chunk 正文 markdown(含 heading)

  -- hybrid search(沿用 v1 alembic 0014 的 char_ngrams 函数):
  content_tsv   TSVECTOR GENERATED ALWAYS AS
                  (to_tsvector('simple', public.char_ngrams(content))) STORED,
  embedding     VECTOR(1024),                          -- text-embedding-v4

  embed_model   VARCHAR(50),                           -- 'text-embedding-v4'
  embed_version VARCHAR(20),                           -- prompt_versions 风格

  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 删笔记 / 重切 chunk 都先 DELETE 再 INSERT:
CREATE INDEX ix_note_chunks_note_id ON note_chunks (note_id);

-- 树节点查 chunk(prefix 匹配走 GIN):
CREATE INDEX ix_note_chunks_folder ON note_chunks USING GIN (folder_path);

-- hybrid search 双索引:
CREATE INDEX ix_note_chunks_embedding_hnsw
  ON note_chunks USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

CREATE INDEX ix_note_chunks_content_tsv
  ON note_chunks USING GIN (content_tsv);
```

设计要点:

- **`folder_path` / `heading_path` 反规范化**:树节点查 chunks(US-5 / US-12)是高频路径,join `notes` 多余
- **`content_tsv` 用 `char_ngrams` 函数**(v1 0014 SQL 函数)解决中文分词:Postgres 默认 parser 把整段 CJK 当一个 token,字符 bigram + ASCII unigram 才能召回子串。`note_chunks` 跟 v1 `profile_chunks` 用同一个 IMMUTABLE 函数
- **embedding nullable**:embedder 异步跑,chunk 入库时 embedding 可能还没算完;hybrid search 端用 `WHERE embedding IS NOT NULL` 过滤
- 编辑笔记的策略:`DELETE FROM note_chunks WHERE note_id = ?` 然后重切重 INSERT。chunk_id 不稳定 — questions.source_chunk_ids 里的 id 失效,通过 `note_id` 查活的 chunks 兜底(详见 §7)

## 5.3 `questions`(题目)

一行 = 一道题(出题 agent 一次生成 N 道,落 N 行)。

```sql
CREATE TABLE questions (
  id                  BIGSERIAL PRIMARY KEY,

  -- 出题时锁定的树节点(可能比 chunk 的 path 更"上层"):
  node_folder_path    TEXT[] NOT NULL,
  node_heading_path   TEXT[] NOT NULL DEFAULT '{}',    -- 节点选到 heading 级时填,否则空

  type                question_type NOT NULL,
  prompt              TEXT NOT NULL,                   -- 题干

  -- 反幻觉锚点:
  source_chunk_ids    BIGINT[] NOT NULL,               -- 出题用到的 chunks(SSoT 顺序)

  -- LLM 生成的 reference:
  reference_answer    TEXT NOT NULL,
  reference_chunk_ids BIGINT[] NOT NULL,               -- 生 reference 用到的 chunks(子集 ⊆ source)

  -- Coverage 用的 N 个采分点(JSONB,schema 见 §6.1):
  reference_points    JSONB NOT NULL DEFAULT '[]'::jsonb,

  gen_model           VARCHAR(50),                     -- 'qwen3.6-flash'
  gen_prompt_version  VARCHAR(20),                     -- 关联 prompt_versions
  gen_tokens_in       INTEGER,
  gen_tokens_out      INTEGER,
  gen_cost_cny        NUMERIC(10, 6),

  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at          TIMESTAMPTZ
);

CREATE INDEX ix_questions_node_folder
  ON questions USING GIN (node_folder_path);

-- M2 题质评测 / SR 复用题:按 source_chunk_ids 反查
CREATE INDEX ix_questions_source_chunks
  ON questions USING GIN (source_chunk_ids);
```

设计要点:

- **`source_chunk_ids` 是 SSoT 数组**:出题 prompt 里以 `[1] ... [2] ...` 编号的 chunk,顺序必须跟数组顺序一致。Judge 用同一份顺序对照
- **`reference_chunk_ids ⊆ source_chunk_ids`**:LLM 生 reference 时被强约束只能引出题用过的 chunk,且必须在 reference 文本里 `[N]` 引用
- **chunk 失效兜底**:笔记编辑会让 source_chunk_ids 里的部分 id 失效。复用旧题时(M3 SR)在 service 层 `SELECT id FROM note_chunks WHERE id = ANY(source_chunk_ids)`,丢失 ≥30% 视为题失效,标 `deleted_at`

## 5.4 `quiz_sessions`(答题会话)

一行 = 用户点 "开始答题" 一次产生的 session。

```sql
CREATE TABLE quiz_sessions (
  id              BIGSERIAL PRIMARY KEY,

  node_folder_path  TEXT[] NOT NULL,
  node_heading_path TEXT[] NOT NULL DEFAULT '{}',

  status          quiz_session_status NOT NULL DEFAULT 'in_progress',

  -- 评分汇总(全部题答完后由 Python 算 + 写回):
  total_score     NUMERIC(5, 2),                       -- 0-100
  coverage_score  NUMERIC(5, 2),
  fidelity_score  NUMERIC(5, 2),
  depth_score     NUMERIC(5, 2),

  -- session 沉淀文件路径(notes/_recall/{id}.md):
  recall_md_path  TEXT,

  started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  submitted_at    TIMESTAMPTZ,
  abandoned_at    TIMESTAMPTZ,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at      TIMESTAMPTZ
);

CREATE INDEX ix_quiz_sessions_status_started
  ON quiz_sessions (status, started_at DESC)
  WHERE deleted_at IS NULL;
```

设计要点:

- **三层分数 + total_score 都存**:Python 算的加权总分写回 DB 是 audit 需要;权重 SSoT 在代码(`0.5 / 0.4 / 0.1`),不让 Judge 算总分(防 LLM 算术错误)
- **`abandoned_at` 字段而非状态**:用户中途退出 → `status='abandoned'` + `abandoned_at=now()`;后续再进入草稿恢复时 service 层判断要不要新开 session
- **不加 `trigger` 字段**:MVP 只有"用户手点出题"这一种来源;M3 上 SR 自动推送时再 `ALTER TABLE ADD COLUMN trigger VARCHAR(20) DEFAULT 'manual'`

## 5.5 `session_answers`(单题答 + 评分)

一行 = session 里的一道题 + 用户答 + 三层评分。

session 出题时立即落 N 行(answer 字段 NULL),用户每答一题 UPDATE 该行,Judge 跑完再 UPDATE 一次评分。中途退出留 NULL 行表示"没答"。

```sql
CREATE TABLE session_answers (
  id                  BIGSERIAL PRIMARY KEY,
  session_id          BIGINT NOT NULL REFERENCES quiz_sessions(id) ON DELETE CASCADE,
  question_id         BIGINT NOT NULL REFERENCES questions(id),
  order_index         INTEGER NOT NULL,                -- 0-based,同 session 内顺序

  user_answer         TEXT,                            -- NULL = 还没答 / 退出未答
  answer_submitted_at TIMESTAMPTZ,

  -- 三层评分(JSONB 字段 schema 见 §6):
  coverage_score      NUMERIC(5, 2),
  coverage_evidence   JSONB,
  fidelity_score      NUMERIC(5, 2),
  fidelity_evidence   JSONB,
  depth_score         NUMERIC(5, 2),
  depth_evidence      JSONB,
  total_score         NUMERIC(5, 2),                   -- Python 算的加权总分

  judge_model         VARCHAR(50),
  judge_prompt_version VARCHAR(20),
  judge_tokens_in     INTEGER,
  judge_tokens_out    INTEGER,
  judge_cost_cny      NUMERIC(10, 6),
  judged_at           TIMESTAMPTZ,

  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT uq_session_question UNIQUE (session_id, question_id),
  CONSTRAINT uq_session_order   UNIQUE (session_id, order_index)
);

CREATE INDEX ix_session_answers_session ON session_answers (session_id, order_index);
```

设计要点:

- **没有 `deleted_at`**:跟 quiz_sessions 走 CASCADE 硬删
- **三层 evidence 是 JSONB**:Judge 输出的结构化证据原样存,前端展开 reference 对照(US-9 / US-10)直接读这里
- **总分由 Python 算**:`total_score = 0.5 * coverage + 0.4 * fidelity + 0.1 * depth`(SSoT 在 service / agents/answer_judge,不在 prompt 里让 LLM 自己算)

## 5.6 `knowledge_gaps`(弱点 + SR 队列)

一行 = 一个被跟踪的知识点节点(`folder_path` + `heading_path`)。session 评分后 upsert。

```sql
CREATE TABLE knowledge_gaps (
  id                  BIGSERIAL PRIMARY KEY,
  folder_path         TEXT[] NOT NULL,
  heading_path        TEXT[] NOT NULL DEFAULT '{}',

  attempt_count       INTEGER NOT NULL DEFAULT 0,      -- 累计在该节点答过的题数
  error_count         INTEGER NOT NULL DEFAULT 0,      -- 累计低分(< 60)题数
  last_score          NUMERIC(5, 2),                   -- 最近一题的 total_score
  last_attempt_at     TIMESTAMPTZ,

  -- SR 状态:
  prev_interval_days  INTEGER NOT NULL DEFAULT 1,
  next_review_at      DATE,                            -- ROADMAP M3 SR 算法

  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT uq_kg_path UNIQUE (folder_path, heading_path)
);

-- "今日复习"按 next_review_at 升序拉:
CREATE INDEX ix_kg_next_review ON knowledge_gaps (next_review_at)
  WHERE next_review_at IS NOT NULL;
```

设计要点:

- **`(folder_path, heading_path)` 是业务主键**:同一节点反复练,所有 attempt 累加到这一行
- **跟踪粒度比 chunk 粗**:即使 chunk 被重切 / id 漂移,只要 `(folder, heading)` 还在,统计连续
- **空 heading_path 也合法**:节点选到 folder 级、不到 heading 级时,`heading_path = '{}'`

## 5.7 `jds`(累积型 JD 库,M2.5)

一行 = 一条用户上传的 JD。**累积型资产**(类比笔记)— 用户陆续上传,长期留存,跨 session 跨时间的"我的 JD 库"。

```sql
CREATE TABLE jds (
  id                  BIGSERIAL PRIMARY KEY,

  source              note_source NOT NULL,            -- 复用 ENUM:'text_paste' / 'image_upload'
  raw_text            TEXT NOT NULL,                    -- JD 原文(截图场景为 OCR 后的文本)
  title               VARCHAR(255),                     -- LLM 自动从 JD 抽 + 用户可改

  -- jd_parser 输出(立即解析,持久化复用):
  parsed_payload      JSONB NOT NULL,                   -- {title, responsibilities[], hard_skills[], soft_skills[], experience_years, education}

  parse_model         VARCHAR(50),
  parse_prompt_version VARCHAR(20),
  parse_tokens_in     INTEGER,
  parse_tokens_out    INTEGER,
  parse_cost_cny      NUMERIC(10, 6),

  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at          TIMESTAMPTZ
);

-- 分页 / 时间排序:
CREATE INDEX ix_jds_created
  ON jds (created_at DESC) WHERE deleted_at IS NULL;

-- 按 title 标签筛选(LLM 抽的或用户改的):
CREATE INDEX ix_jds_title
  ON jds (title) WHERE deleted_at IS NULL AND title IS NOT NULL;
```

设计要点:

- **上传即解析**:POST /api/jds 立即调 jd_parser → parsed_payload 落库,**不延迟分析时机**;后续一键分析直接用 parsed_payload(免重复 LLM)
- **截图场景**:`source='image_upload'`;raw_text 字段存的是 **Qwen 多模态 OCR 后的文本**(截图本身不存 — 太占空间;若需要再截图功能,后续加 file_id 引用)
- **没有 user_id**:沿用 §3.1 单用户 MVP 设计;M4+ SaaS 化时统一 ALTER ADD COLUMN
- **title 可空**:LLM 抽 title 失败或用户清空时,nullable

## 5.8 `jd_analyses`(一键分析报告快照,M2.5)

一行 = 一次"一键分析"的完整产出(聚合 + 学习路径)。**快照型**,不更新 — 用户每次点"一键分析"就新建一行,可对比历史。

```sql
CREATE TABLE jd_analyses (
  id                       BIGSERIAL PRIMARY KEY,

  jd_ids                   BIGINT[] NOT NULL,           -- 这次分析覆盖了哪些 jds.id(快照)
  jd_count                 INTEGER NOT NULL,
  filter_description       VARCHAR(255),                 -- 用户选范围的语义描述(如 "全部" / "title=Java 后端" / "最近 30 条")

  status                   VARCHAR(20) NOT NULL DEFAULT 'in_progress',  -- 'in_progress' / 'done' / 'failed'

  -- 聚合 + 学习路径输出(详见 §6.5 schema):
  aggregated_requirements  JSONB,                        -- canonical list + frequency + raw phrases
  learning_path_md         TEXT,                         -- LLM 生成的 markdown

  -- 成本 audit(map-reduce 多次 LLM 调用累加):
  total_tokens_in          INTEGER,
  total_tokens_out         INTEGER,
  total_cost_cny           NUMERIC(10, 6),
  cache_hit_rate           NUMERIC(4, 3),                -- 0-1

  started_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at             TIMESTAMPTZ,
  failed_at                TIMESTAMPTZ,
  failure_reason           TEXT
);

CREATE INDEX ix_jd_analyses_started ON jd_analyses (started_at DESC);
```

设计要点:

- **jd_ids 是数组快照**:这次分析当时覆盖了哪些 JD;之后用户删除某条 JD 不影响历史报告(jd_ids 里的 id 可能 dangling,前端按需过滤已删 JD)
- **没有 deleted_at**:报告生成后不可改;真要删,直接物理删除整行(用户主动操作)
- **status 状态机**:in_progress → done / failed;in_progress 状态下 aggregated_requirements / learning_path_md 可为 NULL
- **cache_hit_rate 字段**:dogfood 时反复重跑同一批 JD 的命中率,cost 优化指标

## 5.9 `resumes`(简历,M3)

一行 = 一份简历 markdown。**用户可有多份**(不同岗位定制不同简历的需求 — 但实际操作里通常一份就够)。

```sql
CREATE TABLE resumes (
  id                  BIGSERIAL PRIMARY KEY,

  source              VARCHAR(20) NOT NULL,             -- 'markdown_paste' / 'pdf_upload'
  title               VARCHAR(255),                     -- 用户自定义("我的 Java 后端简历")
  content_md          TEXT NOT NULL,                    -- markdown 原文(PDF 场景是 Qwen 多模态 OCR 后的)

  -- 段落切片(简化 chunker,不进 hybrid search 索引):
  parsed_chunks       JSONB NOT NULL DEFAULT '[]'::jsonb,  -- list[{position, type, content}],详见 §6.6

  parse_model         VARCHAR(50),
  parse_cost_cny      NUMERIC(10, 6),

  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at          TIMESTAMPTZ
);

CREATE INDEX ix_resumes_created
  ON resumes (created_at DESC) WHERE deleted_at IS NULL;
```

设计要点:

- **简历 chunks 不进 hybrid search 索引**(对照笔记 chunks):简历短(通常 < 5KB),诊断时直接整 content_md 喂 LLM 即可,不需要 retrieval
- **parsed_chunks 用 JSONB**:不用关系表,因为简历段落不需要跨简历检索;每段记 position(`§3` / `§4.2 项目经历`)+ type(`基础经历` / `技能列表` / `项目经历` / `教育` / `自我评价`)+ content
- **支持多份简历**:用户可上传 v1 / v2 不同版本对比

## 5.10 `resume_analyses`(简历诊断报告,M3)

一行 = 一次"JD 报告 + 简历"诊断的快照。

```sql
CREATE TABLE resume_analyses (
  id                  BIGSERIAL PRIMARY KEY,
  jd_analysis_id      BIGINT NOT NULL REFERENCES jd_analyses(id),
  resume_id           BIGINT NOT NULL REFERENCES resumes(id),

  status              VARCHAR(20) NOT NULL DEFAULT 'in_progress',

  -- 诊断输出(详见 §6.7 schema):
  suggestions         JSONB,                            -- list[ResumeSuggestion]
  anchored_count      INTEGER,                          -- 两方齐 = anchored 的建议数
  unanchored_count    INTEGER,                          -- 缺一方 = unanchored 的建议数
  coverage_summary    JSONB,                            -- {strong, weak, missing} 计数

  llm_model           VARCHAR(50),
  llm_prompt_version  VARCHAR(20),
  total_cost_cny      NUMERIC(10, 6),

  started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at        TIMESTAMPTZ,
  failed_at           TIMESTAMPTZ,
  failure_reason      TEXT
);

CREATE INDEX ix_resume_analyses_started ON resume_analyses (started_at DESC);
CREATE INDEX ix_resume_analyses_resume ON resume_analyses (resume_id);
```

设计要点:

- **jd_analysis_id + resume_id 不要求 unique**:同一对组合可以反复跑(改了简历后重跑诊断)
- **anchored_count / unanchored_count 字段冗余**:从 suggestions JSONB 可算,但冗余存方便 dashboard 列表展示("这次诊断 12 条 anchored / 5 条 unanchored")避免每次解 JSONB
- **没 deleted_at**:同 jd_analyses 历史不可改

# 6. JSONB 字段 schema(SSoT 锁定)

JSONB 内 schema 没法用 DB 约束,Python 端用 Pydantic 校验,文档里锁定结构。

## 6.1 `questions.reference_points`

```json
[
  {
    "id": "p1",
    "text": "synchronized 在锁升级时会经历:无锁 → 偏向锁 → 轻量级锁 → 重量级锁",
    "weight": 0.4,
    "evidence_chunk_ids": [12, 15]
  },
  {
    "id": "p2",
    "text": "偏向锁的撤销发生在...",
    "weight": 0.3,
    "evidence_chunk_ids": [15]
  }
]
```

字段:

- `id`:`p{N}` 顺序号,Coverage evidence 引用
- `text`:这道题应该被覆盖的"采分点"原文
- `weight`:本题内 reference_points 权重之和必须 = 1.0;Coverage 算分用
- `evidence_chunk_ids`:这个采分点出自哪些 chunk;前端展开 reference 时高亮

## 6.2 `session_answers.coverage_evidence`

```json
{
  "points": [
    {"id": "p1", "label": "hit",     "user_excerpt": "锁升级是无锁→偏向→轻量→重量"},
    {"id": "p2", "label": "partial", "user_excerpt": "偏向锁会撤销但没说什么时候"},
    {"id": "p3", "label": "miss",    "user_excerpt": null}
  ],
  "score_raw": 0.65,
  "reasoning": "命中 p1 全分;p2 半分(没讲触发条件);p3 没提"
}
```

label 取值:`hit` / `partial` / `miss`(应用层 ENUM-like)。Python 算 `coverage_score = sum(weight × {hit:1.0, partial:0.5, miss:0}) × 100`。

## 6.3 `session_answers.fidelity_evidence`

```json
{
  "claims": [
    {"text": "synchronized 是基于 Monitor 实现的",     "label": "supported",  "chunk_ids": [12]},
    {"text": "JDK 1.6 之后引入了偏向锁",                "label": "supported",  "chunk_ids": [15]},
    {"text": "偏向锁的实现用了 CAS 加红黑树",          "label": "fabricated", "chunk_ids": []}
  ],
  "score_raw": 0.80,
  "reasoning": "3 条声明里 2 条命中 chunks,1 条凭空,80%"
}
```

label 取值:`supported`(chunk 直接支持)/ `inferred`(合理外推但 chunk 没明说)/ `fabricated`(编造)。Python 算 `fidelity_score = (supported + 0.6 × inferred) / total × 100`,fabricated 比例 > 30% 总分锁顶 50。

## 6.4 `session_answers.depth_evidence`

```json
{
  "dimensions": {
    "tradeoff":  {"covered": true,  "excerpt": "讲了偏向锁省 CAS 但单线程偏多场景才划算"},
    "why":       {"covered": true,  "excerpt": "解释了为什么需要锁升级:减少无竞争场景开销"},
    "boundary":  {"covered": false, "excerpt": null}
  },
  "score_raw": 0.67,
  "reasoning": "3 个深度维度命中 2 个"
}
```

每个维度二值 `covered: true/false`,`depth_score = 命中数 / 3 × 100`。

## 6.5 跨 evidence 共同字段

- 所有 evidence JSONB 里的 `score_raw` 是 Judge 自评 0-1 浮点,**Python 端不直接用**(我们按 evidence 列表自己算 score),只做 audit
- `reasoning` 是 Judge 自然语言总结,**仅展示用**,不参与算分

## 6.6 `jds.parsed_payload`(jd_parser 输出)

```json
{
  "title": "Java 后端工程师",
  "responsibilities": [
    "负责核心业务系统开发与维护",
    "参与系统架构设计与性能优化"
  ],
  "hard_skills": [
    "Java", "JVM", "MySQL 索引", "Redis 集群", "Kafka 消息队列"
  ],
  "soft_skills": [
    "跨团队沟通协作",
    "技术文档撰写"
  ],
  "experience_years": "3-5",
  "education": "本科及以上",
  "extras": {
    "company": "某厂",
    "salary_range": "25k-40k",
    "location": "北京"
  }
}
```

字段约束:

- `title`:LLM 自动从 JD 抽(优先用 JD 里明确的岗位名,兜底"未标注岗位");用户可后续修改
- `hard_skills` / `soft_skills`:**保留原文短语**,不在解析阶段做同义合并(同义合并是 jd_aggregator 的事)
- `extras`:开放对象,LLM 抽到的额外结构化信息(公司 / 薪资 / 地点 等),不强约束 schema

## 6.7 `jd_analyses.aggregated_requirements`(一键分析聚合输出)

```json
[
  {
    "id": "req_1",
    "canonical_text": "Redis 集群 + 分布式锁",
    "category": "硬技能",
    "frequency": 0.75,
    "raw_phrases": [
      "Redis cluster",
      "分布式锁实践",
      "Redis Redlock"
    ],
    "supporting_jd_ids": [101, 102, 103, 105, 108, 110, 113, 116, 117]
  },
  {
    "id": "req_2",
    "canonical_text": "JVM 内存模型 + GC 机制",
    "category": "硬技能",
    "frequency": 0.92,
    "raw_phrases": ["JVM 内存模型", "GC 调优", "Java 虚拟机原理"],
    "supporting_jd_ids": [...]
  }
]
```

字段约束:

- `id`:`req_{N}` 顺序号,被 resume_analyses suggestions 引用作锚点
- `canonical_text`:LLM 同义合并后的 canonical 表达(从 raw_phrases 选最佳代表 + 适当规范化)
- `frequency`:**Python 重算**(不信 LLM 算术)— `len(supporting_jd_ids) / jd_count`
- `supporting_jd_ids`:这条 canonical 在多少条 JD 的 raw_phrases 里命中过(同义匹配由 LLM 在二次 reduce 时建立);**Python 端按这个数组重算 frequency**(SSoT)
- `category`:`硬技能` / `软技能` / `经验` / `学历` 四类

## 6.8 `jd_analyses.learning_path_md`(学习路径 markdown)

LLM 一次调用基于 aggregated_requirements 输出的 markdown,**不约束严格结构**(给用户看,不给程序解析),示例:

```markdown
## 你的学习路径(基于 100 条 Java 后端 JD 聚合)

### 高频硬要求(≥ 80% JD 提到,优先掌握)
1. **JVM 内存模型 + GC**(92%)— 必考
2. **MySQL 索引 + 事务**(85%)— ...

### 中频硬要求(50-80%,加分项)
3. **Redis 集群 + 分布式锁**(75%)— ...

### 软要求 / 综合素养
- 跨团队协作(60%)
- 技术分享 / 文档(40%)
```

不存额外结构化字段 — 学习路径就是给用户读的,不参与下游计算。

## 6.9 `resumes.parsed_chunks`(简历段落)

```json
[
  {
    "position": "§1",
    "type": "header",
    "content": "张三 / 后端工程师 / 138-xxxx-xxxx"
  },
  {
    "position": "§2",
    "type": "summary",
    "content": "3 年 Java 后端经验,熟悉 JVM 调优..."
  },
  {
    "position": "§3",
    "type": "skills",
    "content": "Java / Spring / MySQL / Redis / Kafka..."
  },
  {
    "position": "§4 项目经历 / 项目 A",
    "type": "project",
    "content": "## 电商平台订单系统\n..."
  }
]
```

字段约束:

- `position`:展示用的简短定位标签(`§3` / `§4 项目经历 / 项目 A`),resume_analyses suggestions 锚点引用这个字段
- `type` ∈ {`header`, `summary`, `skills`, `experience`, `project`, `education`, `other`} — 简化分类,方便诊断时按类型组合
- `content`:段落 markdown 原文

## 6.10 `resume_analyses.suggestions`(简历诊断输出)

```json
[
  {
    "req_id": "req_1",
    "req_text": "Redis 集群 + 分布式锁",
    "req_frequency": 0.75,
    "resume_position": "§3",
    "coverage": "weak",
    "diagnosis": "JD 75% 提到这条要求,你简历技能列表(§3)只列了 'Redis',没体现集群 / 分布式锁的实战。",
    "suggestion_topic": "在项目经历中补一段跟 Redis 集群相关的实战(主从切换 / 分布式锁场景),自己写真实做过的。",
    "tag": "anchored"
  },
  {
    "req_id": "req_27",
    "req_text": "大流量分布式系统经验",
    "req_frequency": 0.42,
    "resume_position": null,
    "coverage": "missing",
    "diagnosis": "JD 提到但简历未体现",
    "suggestion_topic": null,
    "tag": "unanchored"
  }
]
```

字段约束(**两方锚点严格**):

- `req_id` + `resume_position` **同时非空** → `tag="anchored"`(主色展示)
- 任一为空 → `tag="unanchored"`(灰色弱化)
- `coverage` ∈ {`strong`, `weak`, `missing`}:基于 LLM 对照 JD req vs 简历段落判断
- `suggestion_topic`:**永远只描述"该补什么主题",不写具体改写文案**(产品级硬约束,违反就是 bug);unanchored 时通常 null
- `diagnosis`:LLM 中文一句话陈述当前覆盖度的事实

# 7. 一些"看着不对劲但其实是对的"的设计

- **chunk_id 不稳定**:笔记一编辑老 chunks 全删新切。`questions.source_chunk_ids` 里的 id 会陆续失效。**这是接受的**,因为(a)题质评测靠新 chunks 重生 reference 校验,(b)SR 复用旧题时少于 70% 命中就标 deleted_at 弃题,(c)笔记编辑频率低
- **`folder_path` 反规范化到 `note_chunks`**:写多了一层但树节点查询是高频路径(出题 + 弱点统计 + dashboard)。同步成本:笔记移动 folder 时一条 SQL `UPDATE note_chunks SET folder_path = ? WHERE note_id = ?`
- **三层评分 evidence 全 JSONB 不拆表**:每条 evidence 跟单题强绑、不会被跨题查询。拆 `coverage_points / fidelity_claims / depth_dimensions` 三张子表只增加 join 成本
- **没有 `tags` 表**:用户分类靠 `folder_path` 维度。tags 是应届生没积累时的兜底,目标用户(1-3 年开发者)的笔记本来就有目录组织
- **没有 `users` 表**(MVP):见 §3.1。M4+ SaaS 化再加,DB schema 平滑迁移

# 8. 索引策略小结

| 表 | 索引 | 用途 |
|----|------|------|
| `notes` | `uq_notes_folder_title (folder_path, title) WHERE deleted_at IS NULL` | 防重复 |
| `notes` | `gin (folder_path)` | 树形导航 |
| `notes` | `(external_id) WHERE external_id IS NOT NULL` | M3 yuque 增量 |
| `note_chunks` | `(note_id)` | 删 / 重切 chunks |
| `note_chunks` | `gin (folder_path)` | 树节点 prefix 匹配 |
| `note_chunks` | `hnsw (embedding vector_cosine_ops)` | 语义召回 |
| `note_chunks` | `gin (content_tsv)` | char_ngram 全文召回 |
| `questions` | `gin (node_folder_path)` | 题质评测 / 复习 |
| `questions` | `gin (source_chunk_ids)` | chunk 失效反查 |
| `quiz_sessions` | `(status, started_at DESC) WHERE deleted_at IS NULL` | 历史 session 列表 |
| `session_answers` | `(session_id, order_index)` | 拉一个 session 的所有题 |
| `session_answers` | `uq_session_question (session_id, question_id)` | 每题最多答一次 |
| `knowledge_gaps` | `uq_kg_path (folder_path, heading_path)` | upsert 主键 |
| `knowledge_gaps` | `(next_review_at) WHERE next_review_at IS NOT NULL` | "今日复习"队列 |

# 9. 沿用的 v1 表(不动)

| 表 | 来源 | 用途 |
|----|------|------|
| `prompt_versions` | alembic 0006 | Prompt 版本号(QuizGenerator / AnswerJudge 各一行) |
| `llm_calls` | alembic 0006 | LLM 调用成本 audit(出题 / 评分 / 嵌入) |
| `llm_response_cache` | alembic 0015 | 同 prompt 重跑命中缓存,降本 |

`char_ngrams(text)` SQL 函数(alembic 0014)也保留 — `note_chunks.content_tsv` 直接复用。

# 10. 迁移路径(v1 → v2)

新建 `alembic/versions/0016_v2_schema.py`,做两件事:

**砍 v1 表(顺序按 FK 依赖反向)**:

```sql
DROP TABLE IF EXISTS resume_versions CASCADE;
DROP TABLE IF EXISTS resumes        CASCADE;
DROP TABLE IF EXISTS matches        CASCADE;
DROP TABLE IF EXISTS profile_chunks CASCADE;
DROP TABLE IF EXISTS profile_skills CASCADE;
DROP TABLE IF EXISTS profile_educations CASCADE;
DROP TABLE IF EXISTS profile_projects   CASCADE;
DROP TABLE IF EXISTS profile_experiences CASCADE;
DROP TABLE IF EXISTS profiles       CASCADE;
DROP TABLE IF EXISTS jds            CASCADE;
DROP TABLE IF EXISTS files          CASCADE;
DROP TABLE IF EXISTS users          CASCADE;

DROP TYPE IF EXISTS profile_source;
DROP TYPE IF EXISTS profile_status;
DROP TYPE IF EXISTS skill_level;
DROP TYPE IF EXISTS chunk_granularity;
-- 其他 v1 ENUM 一并清
```

**留下来不动的**:`prompt_versions` / `llm_calls` / `llm_response_cache` / `char_ngrams()` 函数 / `pgvector` / `vector_cosine_ops` HNSW operator class 等扩展。

**建 v2 表**:本文档 §5 全部 + ENUM 全部,顺序 `notes → note_chunks → questions → quiz_sessions → session_answers → knowledge_gaps`。

**downgrade**:不写(v2 是单向重构,v1 数据已确认无价值,git tag `v0.1-jobcopilot-v1` 留档足够)。

# 11. 已锁定的关键决策

| 项 | 决策 | 备注 |
|----|------|------|
| 路径表示 | `text[]` 一维数组,长度上限 8 层 | 不用 ltree(中文不友好)/ 不用 `text/`(prefix 慢) |
| 单用户(MVP) | 不加 `user_id` 字段;M4+ SaaS 时统一 ALTER | 简化 schema,迁移成本低 |
| 软删 | `deleted_at`(notes / questions / quiz_sessions / knowledge_gaps);chunks / answers 走 CASCADE 硬删 | 子表跟父表生命周期绑死 |
| chunk 元数据反规范化 | `note_chunks.folder_path` 冗余存 | 出题 / 弱点统计是高频查询路径 |
| 三层评分 evidence | 各自 JSONB,不拆子表 | 跟单题强绑、不会跨题查询 |
| 总分计算 | Python 算加权,SSoT 在代码 | `0.5×Cov + 0.4×Fid + 0.1×Dep`;不让 Judge 算 |
| chunk_id 失效 | 笔记编辑后老 chunks 删除新切;旧题命中 < 70% 弃题 | 笔记编辑低频,接受 |
| ORM relationship | 不用 `relationship()`,手写 `select(...).where(parent_id == ?)` | 沿用 v1 ADR-0005 D1 |
| 中文分词 | char_ngrams(bigram + ASCII unigram) | 沿用 v1 alembic 0014 SQL 函数 |
| Embedding 维度 | 1024 / text-embedding-v4 | 沿用 v1 |
| JD 累积模型 | jds 表跨时间累积,parsed_payload 上传即落库 | 类比笔记;不做 batch 概念 |
| JD 一键分析 | jd_analyses 快照表,jd_ids 数组锁定本次范围 | 历史报告可对比 |
| JD 单次分析上限 | 200 条 | hierarchical reduce(map 上传完成 → reduce 分批 + 二次合并 + Python 重算频次)|
| 频次计算位置 | Python(SSoT),不信 LLM 算术 | 同 5-AGENT §4.5 算分原则 |
| 简历存储 | resumes 表 + parsed_chunks JSONB,**不进 hybrid search 索引** | 简历短,直接全文喂 LLM |
| 简历诊断 | resume_analyses 快照,两方锚点严格(req_id + resume_position 双非空 = anchored)| 永不输出改写文案 |
| 截图入库 | jds.raw_text 存 OCR 文本(不存原图)| Qwen 多模态 OCR 后即扔 |

---

# 不在本文档范围

- 表怎么被 service / agent 调用 → `docs/2-TECH_DESIGN.md`
- API 端点和请求 / 响应 schema → `docs/4-API_SPEC.md`
- QuizGenerator / AnswerJudge prompt 全文(包含 reference_points / evidence 输出 schema 的 prompt 约束) → `docs/5-AGENT_DESIGN.md`
- evals/suites/ 数据怎么生成 → `docs/6-EVAL_PLAN.md`
- 里程碑节奏 → `docs/7-ROADMAP.md`
- 仓库结构 / 迁移命令 / CI → `docs/8-ENGINEERING.md`
