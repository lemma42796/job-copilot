---
title: JobCopilot 数据模型设计
owner: lemma42796
status: Draft
version: 0.1.0
created: 2026-05-01
last_updated: 2026-05-01
related:
  - 1-PRD.md
  - 2-TECH_DESIGN.md
  - 4-API_SPEC.md
  - adr/0002-postgres-as-vector-db.md
---

## 修订记录

| 版本 | 日期 | 作者 | 变更说明 |
|------|------|------|---------|
| 0.1.0 | 2026-05-01 | lemma42796 | 初版 |

---

## 1. 概览

### 1.1 数据存储统一性

按 ADR-0002,所有数据存于单一 Postgres 16 实例:

```
postgres-16
├── public               -- 业务表
├── pgvector             -- 向量(以列形式嵌入业务表)
├── pgmq                 -- 任务队列(独立 schema)
└── extensions
    ├── vector
    ├── pgmq
    ├── pg_jieba(可选,中文分词)
    └── pg_stat_statements
```

### 1.2 命名约定

| 类型 | 约定 | 示例 |
|------|------|------|
| 表名 | snake_case 复数 | `jds`, `profiles` |
| 列名 | snake_case | `created_at`, `user_id` |
| 主键 | `id`,统一 BIGINT GENERATED ALWAYS AS IDENTITY | - |
| 外键 | `<entity>_id` | `user_id`, `jd_id` |
| 时间戳 | `created_at`, `updated_at`, `deleted_at` (可空,软删) | - |
| 布尔 | `is_*` | `is_active` |
| 枚举 | Postgres ENUM 类型,大写 | `JOB_LEVEL`, `MATCH_STATUS` |
| 索引 | `idx_<table>_<cols>` | `idx_jds_user_id_created_at` |
| 外键约束 | `fk_<table>_<refs>` | `fk_jds_user_id` |
| 唯一约束 | `uq_<table>_<cols>` | `uq_users_email` |
| 检查约束 | `ck_<table>_<rule>` | `ck_match_score_range` |
| 触发器 | `tg_<table>_<event>` | `tg_jds_updated_at` |

### 1.3 通用列(所有业务表)

```sql
id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
deleted_at  TIMESTAMPTZ,        -- 软删除,NULL 表示未删
```

每张业务表统一挂触发器 `tg_<table>_set_updated_at` 自动维护 `updated_at`。

---

## 2. 实体关系图(ERD)

```
                    ┌─────────────┐
                    │    users    │
                    └──────┬──────┘
                           │ 1
            ┌──────────────┼──────────────┬─────────────┐
            │              │              │             │
            │ N            │ N            │ N           │ N
       ┌────▼────┐    ┌────▼────┐    ┌────▼────┐  ┌─────▼──────┐
       │   jds   │    │ profiles│    │  files  │  │ llm_calls  │
       └────┬────┘    └────┬────┘    └─────────┘  └────────────┘
            │ 1            │ 1
            │              ├───────────────────────────────────┐
            │              │ 1                                 │ 1
            │              ├──► profile_chunks (向量索引)      │
            │              │                                   │
            │              ├──► profile_projects               │
            │              ├──► profile_experiences            │
            │              ├──► profile_skills                 │
            │              └──► profile_educations             │
            │
       ┌────┴──────────────────────────────────────────────────┐
       │                                                        │
       │ N:1                                                    │ 1:N
   ┌───▼────┐  ┌─────────┐  ┌──────────┐  ┌───────────────────┐
   │matches │  │ resumes │  │applications│  │interview_sessions │
   └────────┘  └─────┬───┘  └──────────┘  └─────┬─────────────┘
                     │                            │
                     │ 1:N                        │ 1:N
                     ▼                            ▼
              ┌──────────────┐           ┌────────────────┐
              │resume_versions│           │interview_turns │
              └──────────────┘           └────────────────┘

              ┌──────────────────┐
              │  prompt_versions │  全局,Prompt 版本管理
              └──────────────────┘

              ┌──────────────────┐
              │   eval_runs      │  全局,评测回归记录
              └──────────────────┘
```

---

## 3. 表结构详细设计

### 3.1 users(用户)

单用户本地部署默认只有一条记录(id=1, email=local@local)。多用户场景启用真实注册。

```sql
CREATE TABLE users (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email       VARCHAR(255) NOT NULL,
    name        VARCHAR(100),
    locale      VARCHAR(10) NOT NULL DEFAULT 'zh-CN',
    settings    JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- LLM API Key 不存数据库,只存 .env

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at  TIMESTAMPTZ,

    CONSTRAINT uq_users_email UNIQUE (email)
);

CREATE INDEX idx_users_email ON users(email) WHERE deleted_at IS NULL;
```

`settings` JSONB 字段用于存放可变设置(模型偏好、UI 主题等),避免频繁 schema 变更。

### 3.2 jds(职位描述)

```sql
CREATE TYPE JD_SOURCE AS ENUM ('text_paste', 'pdf_upload', 'image_upload', 'extension_paste');
CREATE TYPE JD_STATUS AS ENUM ('parsing', 'parsed', 'parse_failed');

CREATE TABLE jds (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         BIGINT NOT NULL,

    -- 来源与状态
    source          JD_SOURCE NOT NULL,
    status          JD_STATUS NOT NULL DEFAULT 'parsing',

    -- 原始内容(脱敏前)
    raw_text        TEXT,
    raw_file_id     BIGINT,            -- 关联 files 表(若来自 PDF/图片)

    -- 结构化字段(LLM 抽取)
    company         VARCHAR(200),
    title           VARCHAR(200),
    location        VARCHAR(100),
    salary_min      INTEGER,           -- 月薪下限,单位:元
    salary_max      INTEGER,
    salary_currency VARCHAR(10) DEFAULT 'CNY',
    salary_period   VARCHAR(10) DEFAULT 'monthly',
    job_level       VARCHAR(50),       -- 'junior' / 'middle' / 'senior'
    years_required  INTEGER,
    education       VARCHAR(50),
    hard_skills     JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{"name":"Python","required":true,"weight":1.0}]
    soft_skills     JSONB NOT NULL DEFAULT '[]'::jsonb,
    bonus_skills    JSONB NOT NULL DEFAULT '[]'::jsonb,
    responsibilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    description     TEXT,

    -- 抽取元信息
    parse_confidence NUMERIC(4,3),     -- 0.000 - 1.000
    parse_model     VARCHAR(50),       -- 'qwen3.6-flash'
    parse_tokens    INTEGER,
    parse_cost_cny  NUMERIC(10,4),

    -- 全文检索
    search_tsv      tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(company,'') || ' ' || coalesce(description,''))
    ) STORED,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ,

    CONSTRAINT fk_jds_user_id FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_jds_raw_file_id FOREIGN KEY (raw_file_id) REFERENCES files(id) ON DELETE SET NULL,
    CONSTRAINT ck_jds_salary_range CHECK (salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max)
);

CREATE INDEX idx_jds_user_id_created_at ON jds(user_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX idx_jds_status ON jds(status) WHERE status IN ('parsing', 'parse_failed');
CREATE INDEX idx_jds_search_tsv ON jds USING GIN (search_tsv);
```

### 3.3 profiles(个人档案)

每个用户一份当前版本档案。历史版本归档到 `profile_snapshots`(M3 启用)。

```sql
CREATE TABLE profiles (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         BIGINT NOT NULL,

    -- 基本信息
    full_name       VARCHAR(100),
    phone           VARCHAR(50),       -- 加密(M2 启用),v1 明文
    email           VARCHAR(255),
    location        VARCHAR(100),
    summary         TEXT,              -- 个人简介

    -- 求职意向
    target_titles   JSONB NOT NULL DEFAULT '[]'::jsonb,
    target_locations JSONB NOT NULL DEFAULT '[]'::jsonb,
    target_salary_min INTEGER,
    target_salary_max INTEGER,

    -- 原始来源
    raw_file_id     BIGINT,
    raw_text        TEXT,

    parse_model     VARCHAR(50),
    parse_tokens    INTEGER,
    parse_cost_cny  NUMERIC(10,4),

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ,

    CONSTRAINT fk_profiles_user_id FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_profiles_raw_file_id FOREIGN KEY (raw_file_id) REFERENCES files(id) ON DELETE SET NULL,
    CONSTRAINT uq_profiles_user_id UNIQUE (user_id) -- 一用户一档案
);
```

### 3.4 profile_experiences(工作经历)

```sql
CREATE TABLE profile_experiences (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    profile_id      BIGINT NOT NULL,

    company         VARCHAR(200) NOT NULL,
    title           VARCHAR(200) NOT NULL,
    location        VARCHAR(100),
    start_date      DATE,
    end_date        DATE,            -- NULL 表示在职
    is_current      BOOLEAN NOT NULL DEFAULT FALSE,

    description     TEXT NOT NULL,   -- 简介
    bullets         JSONB NOT NULL DEFAULT '[]'::jsonb,  -- ["主导xxx,提升yy"]
    tech_stack      JSONB NOT NULL DEFAULT '[]'::jsonb,
    achievements    JSONB NOT NULL DEFAULT '[]'::jsonb,  -- 量化成果

    sort_order      INTEGER NOT NULL DEFAULT 0,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_pe_profile_id FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
    CONSTRAINT ck_pe_dates CHECK (end_date IS NULL OR start_date IS NULL OR start_date <= end_date)
);

CREATE INDEX idx_pe_profile_id ON profile_experiences(profile_id);
```

### 3.5 profile_projects(项目经历)

```sql
CREATE TABLE profile_projects (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    profile_id      BIGINT NOT NULL,
    experience_id   BIGINT,           -- 关联工作经历(NULL 为独立项目)

    name            VARCHAR(200) NOT NULL,
    role            VARCHAR(100),
    start_date      DATE,
    end_date        DATE,

    description     TEXT NOT NULL,
    bullets         JSONB NOT NULL DEFAULT '[]'::jsonb,
    tech_stack      JSONB NOT NULL DEFAULT '[]'::jsonb,
    achievements    JSONB NOT NULL DEFAULT '[]'::jsonb,

    repo_url        VARCHAR(500),
    demo_url        VARCHAR(500),

    sort_order      INTEGER NOT NULL DEFAULT 0,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_pp_profile_id FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
    CONSTRAINT fk_pp_experience_id FOREIGN KEY (experience_id) REFERENCES profile_experiences(id) ON DELETE SET NULL
);

CREATE INDEX idx_pp_profile_id ON profile_projects(profile_id);
```

### 3.6 profile_skills(技能)

```sql
CREATE TYPE SKILL_LEVEL AS ENUM ('beginner', 'intermediate', 'advanced', 'expert');

CREATE TABLE profile_skills (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    profile_id      BIGINT NOT NULL,

    name            VARCHAR(100) NOT NULL,         -- 归一化后的技能名
    name_raw        VARCHAR(100),                  -- 原始字符串
    category        VARCHAR(50),                   -- 'language' / 'framework' / 'tool' / ...
    level           SKILL_LEVEL,
    years           NUMERIC(4,1),

    -- 关联证据(从哪些项目/经历推断出该技能)
    evidence_project_ids BIGINT[] NOT NULL DEFAULT '{}',
    evidence_experience_ids BIGINT[] NOT NULL DEFAULT '{}',

    sort_order      INTEGER NOT NULL DEFAULT 0,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_ps_profile_id FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
    CONSTRAINT uq_ps_profile_name UNIQUE (profile_id, name)
);

CREATE INDEX idx_ps_profile_id_category ON profile_skills(profile_id, category);
```

### 3.7 profile_educations(教育背景)

```sql
CREATE TABLE profile_educations (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    profile_id      BIGINT NOT NULL,

    school          VARCHAR(200) NOT NULL,
    degree          VARCHAR(50),
    major           VARCHAR(100),
    start_date      DATE,
    end_date        DATE,
    gpa             NUMERIC(4,2),
    honors          JSONB NOT NULL DEFAULT '[]'::jsonb,

    sort_order      INTEGER NOT NULL DEFAULT 0,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_pe2_profile_id FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
);

CREATE INDEX idx_pe2_profile_id ON profile_educations(profile_id);
```

### 3.8 profile_chunks(向量索引,RAG 核心)

```sql
CREATE TYPE CHUNK_GRANULARITY AS ENUM ('experience', 'project', 'skill', 'summary');

CREATE TABLE profile_chunks (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    profile_id      BIGINT NOT NULL,

    granularity     CHUNK_GRANULARITY NOT NULL,
    source_table    VARCHAR(50) NOT NULL,           -- 'profile_projects' 等
    source_id       BIGINT NOT NULL,

    content         TEXT NOT NULL,                  -- 用于 embedding 的文本
    content_tsv     tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    embedding       vector(1024),                   -- BGE-M3 输出 1024 维

    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,

    embed_model     VARCHAR(50),
    embed_version   VARCHAR(20),

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_pc_profile_id FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
);

-- HNSW 向量索引(余弦距离)
CREATE INDEX idx_pc_embedding_hnsw
    ON profile_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- 全文检索 GIN
CREATE INDEX idx_pc_content_tsv ON profile_chunks USING GIN (content_tsv);

-- 业务过滤
CREATE INDEX idx_pc_profile_granularity ON profile_chunks(profile_id, granularity);
```

### 3.9 matches(匹配分析记录)

```sql
CREATE TABLE matches (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    jd_id           BIGINT NOT NULL,
    profile_id      BIGINT NOT NULL,

    -- 评分(0-100 整数)
    score           SMALLINT NOT NULL,

    -- 子项
    matched_skills      JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{"name":"Python","strength":0.9,"evidence":[...]}]
    missing_skills      JSONB NOT NULL DEFAULT '[]'::jsonb,
    advantage_summary   TEXT,                                  -- 优势分析(LLM 生成)
    gap_summary         TEXT,                                  -- 差距分析(LLM 生成)
    suggestions         JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- 元信息
    model           VARCHAR(50),
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    cost_cny        NUMERIC(10,4),
    latency_ms      INTEGER,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ,

    CONSTRAINT fk_matches_user_id FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_matches_jd_id FOREIGN KEY (jd_id) REFERENCES jds(id) ON DELETE CASCADE,
    CONSTRAINT fk_matches_profile_id FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
    CONSTRAINT ck_matches_score_range CHECK (score >= 0 AND score <= 100)
);

CREATE INDEX idx_matches_user_jd ON matches(user_id, jd_id, created_at DESC) WHERE deleted_at IS NULL;
```

### 3.10 resumes(定制简历)

```sql
CREATE TYPE RESUME_STATUS AS ENUM ('generating', 'review_failed', 'ready', 'failed');

CREATE TABLE resumes (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    jd_id           BIGINT NOT NULL,
    profile_id      BIGINT NOT NULL,
    match_id        BIGINT,

    status          RESUME_STATUS NOT NULL DEFAULT 'generating',

    -- 内容(markdown 是源)
    title           VARCHAR(200),
    markdown        TEXT,
    pdf_file_id     BIGINT,

    -- 模板
    template        VARCHAR(50) NOT NULL DEFAULT 'awesome-cv-zh',

    -- 生成元数据
    generation_model VARCHAR(50),
    review_model    VARCHAR(50),
    revisions       SMALLINT NOT NULL DEFAULT 0,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    cached_tokens   INTEGER,
    cost_cny        NUMERIC(10,4),
    latency_ms      INTEGER,

    -- Reviewer 报告
    review_passed   BOOLEAN,
    review_findings JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{"section":"...","issue":"...","severity":"high"}]

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ,

    CONSTRAINT fk_resumes_user_id FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_resumes_jd_id FOREIGN KEY (jd_id) REFERENCES jds(id) ON DELETE CASCADE,
    CONSTRAINT fk_resumes_profile_id FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
    CONSTRAINT fk_resumes_match_id FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE SET NULL,
    CONSTRAINT fk_resumes_pdf_file_id FOREIGN KEY (pdf_file_id) REFERENCES files(id) ON DELETE SET NULL
);

CREATE INDEX idx_resumes_user_jd ON resumes(user_id, jd_id, created_at DESC) WHERE deleted_at IS NULL;
```

### 3.11 resume_versions(版本快照)

每次用户编辑保存或重新生成,产生一个 version。

```sql
CREATE TABLE resume_versions (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    resume_id       BIGINT NOT NULL,
    version_number  INTEGER NOT NULL,

    markdown        TEXT NOT NULL,
    pdf_file_id     BIGINT,

    edit_type       VARCHAR(20),         -- 'generated' / 'edited' / 'regenerated'
    edit_note       TEXT,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_rv_resume_id FOREIGN KEY (resume_id) REFERENCES resumes(id) ON DELETE CASCADE,
    CONSTRAINT fk_rv_pdf_file_id FOREIGN KEY (pdf_file_id) REFERENCES files(id) ON DELETE SET NULL,
    CONSTRAINT uq_rv_resume_version UNIQUE (resume_id, version_number)
);

CREATE INDEX idx_rv_resume_id ON resume_versions(resume_id, version_number DESC);
```

### 3.12 applications(投递追踪)

```sql
CREATE TYPE APPLICATION_STATUS AS ENUM (
    'draft',          -- 已生成简历未投递
    'submitted',      -- 已投递
    'in_contact',     -- 沟通中
    'interview',      -- 面试中
    'offer',          -- 拿到 offer
    'rejected_byme',  -- 我拒绝
    'rejected_byhr',  -- 对方拒绝
    'expired'         -- 流程结束未跟进
);

CREATE TABLE applications (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    jd_id           BIGINT NOT NULL,
    resume_id       BIGINT,

    status          APPLICATION_STATUS NOT NULL DEFAULT 'draft',
    channel         VARCHAR(50),         -- 'boss' / 'lagou' / 'linkedin' / 'referral' / 'other'
    submitted_at    TIMESTAMPTZ,

    notes           TEXT,
    next_action     TEXT,
    next_action_at  TIMESTAMPTZ,

    -- 用户回填的真实结果(用于 NSM 计算)
    received_invite BOOLEAN,             -- 是否收到面试邀约

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ,

    CONSTRAINT fk_app_user_id FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_app_jd_id FOREIGN KEY (jd_id) REFERENCES jds(id) ON DELETE CASCADE,
    CONSTRAINT fk_app_resume_id FOREIGN KEY (resume_id) REFERENCES resumes(id) ON DELETE SET NULL,
    CONSTRAINT uq_app_user_jd UNIQUE (user_id, jd_id)
);

CREATE INDEX idx_app_user_status ON applications(user_id, status, updated_at DESC) WHERE deleted_at IS NULL;
```

### 3.13 interview_sessions(面试模拟会话)

```sql
CREATE TYPE INTERVIEW_STATUS AS ENUM ('running', 'completed', 'abandoned');

CREATE TABLE interview_sessions (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    jd_id           BIGINT NOT NULL,
    profile_id      BIGINT NOT NULL,

    status          INTERVIEW_STATUS NOT NULL DEFAULT 'running',
    interviewer_persona VARCHAR(50) NOT NULL DEFAULT 'tech_senior',

    -- 评分(完成后回填)
    final_score     SMALLINT,
    summary         TEXT,
    strengths       JSONB,
    improvements    JSONB,

    -- 元数据
    total_turns     INTEGER NOT NULL DEFAULT 0,
    total_tokens_in INTEGER NOT NULL DEFAULT 0,
    total_tokens_out INTEGER NOT NULL DEFAULT 0,
    total_cost_cny  NUMERIC(10,4) NOT NULL DEFAULT 0,

    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ,

    CONSTRAINT fk_is_user_id FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_is_jd_id FOREIGN KEY (jd_id) REFERENCES jds(id) ON DELETE CASCADE,
    CONSTRAINT fk_is_profile_id FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
    CONSTRAINT ck_is_score CHECK (final_score IS NULL OR (final_score >= 0 AND final_score <= 100))
);

CREATE INDEX idx_is_user ON interview_sessions(user_id, created_at DESC) WHERE deleted_at IS NULL;
```

### 3.14 interview_turns(面试单轮)

```sql
CREATE TYPE TURN_KIND AS ENUM ('basic', 'advanced', 'system_design', 'behavioral', 'follow_up');

CREATE TABLE interview_turns (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id      BIGINT NOT NULL,
    turn_number     INTEGER NOT NULL,

    kind            TURN_KIND NOT NULL,
    question        TEXT NOT NULL,
    user_answer     TEXT,
    reference_answer TEXT,
    score           SMALLINT,
    feedback        TEXT,

    -- 元数据
    model           VARCHAR(50),
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    latency_ms      INTEGER,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_it_session_id FOREIGN KEY (session_id) REFERENCES interview_sessions(id) ON DELETE CASCADE,
    CONSTRAINT uq_it_session_turn UNIQUE (session_id, turn_number),
    CONSTRAINT ck_it_score CHECK (score IS NULL OR (score >= 0 AND score <= 100))
);

CREATE INDEX idx_it_session ON interview_turns(session_id, turn_number);
```

### 3.15 files(文件存储)

二进制文件用 `bytea` 存,小到中等(≤ 10MB)直接 inline。

```sql
CREATE TABLE files (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         BIGINT NOT NULL,

    name            VARCHAR(255) NOT NULL,
    mime_type       VARCHAR(100) NOT NULL,
    size_bytes      BIGINT NOT NULL,
    sha256          CHAR(64) NOT NULL,

    -- 内容(lz4 压缩)
    content         BYTEA NOT NULL,

    purpose         VARCHAR(50) NOT NULL,    -- 'resume_source' / 'jd_source' / 'resume_pdf'

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ,

    CONSTRAINT fk_files_user_id FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT ck_files_size CHECK (size_bytes <= 100 * 1024 * 1024)  -- 100MB 上限
);

CREATE INDEX idx_files_user_purpose ON files(user_id, purpose) WHERE deleted_at IS NULL;
CREATE INDEX idx_files_sha256 ON files(sha256);

-- 启用 lz4 压缩(Postgres 14+)
ALTER TABLE files ALTER COLUMN content SET COMPRESSION lz4;
```

### 3.16 llm_calls(LLM 调用日志,成本归因)

```sql
CREATE TABLE llm_calls (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         BIGINT,                  -- 可空(系统级调用如评测)

    feature         VARCHAR(50) NOT NULL,    -- 'jd_parse' / 'resume_generate' / ...
    tier            VARCHAR(20) NOT NULL,    -- 'cheap' / 'standard' / 'premium'
    model           VARCHAR(50) NOT NULL,
    thinking_mode   BOOLEAN NOT NULL DEFAULT FALSE,

    tokens_in       INTEGER NOT NULL,
    cached_tokens   INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL,

    cost_cny        NUMERIC(10,6) NOT NULL,
    latency_ms      INTEGER NOT NULL,

    success         BOOLEAN NOT NULL,
    error_code      VARCHAR(50),

    -- 关联
    trace_id        VARCHAR(100),            -- Langfuse trace
    related_entity  VARCHAR(50),             -- 'jd' / 'resume' / 'interview_turn'
    related_id      BIGINT,

    -- Prompt 版本(用于 A/B 与回归)
    prompt_version_id BIGINT,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_lc_user_id FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT fk_lc_prompt_version_id FOREIGN KEY (prompt_version_id) REFERENCES prompt_versions(id) ON DELETE SET NULL
);

CREATE INDEX idx_lc_user_feature_created ON llm_calls(user_id, feature, created_at DESC);
CREATE INDEX idx_lc_created ON llm_calls(created_at DESC);
CREATE INDEX idx_lc_feature_success ON llm_calls(feature, success);
```

### 3.17 prompt_versions(Prompt 版本管理)

```sql
CREATE TABLE prompt_versions (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    agent_name      VARCHAR(100) NOT NULL,    -- 'jd_parser' / 'resume_drafter' / ...
    version         VARCHAR(50) NOT NULL,     -- 'v1.2.0' or git sha
    template        TEXT NOT NULL,            -- 完整 Prompt(带 placeholder)
    variables       JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- 评测分(每次回归后回填)
    eval_score      NUMERIC(5,2),
    eval_run_id     BIGINT,

    is_active       BOOLEAN NOT NULL DEFAULT FALSE,
    notes           TEXT,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_pv_agent_version UNIQUE (agent_name, version)
);

CREATE INDEX idx_pv_agent_active ON prompt_versions(agent_name, is_active);
```

### 3.18 eval_runs(评测运行记录)

```sql
CREATE TABLE eval_runs (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    suite_name      VARCHAR(100) NOT NULL,     -- 'jd_extract' / 'resume_match' / 'e2e'
    git_sha         VARCHAR(40),
    triggered_by    VARCHAR(20) NOT NULL,      -- 'manual' / 'ci' / 'scheduled'

    total_cases     INTEGER NOT NULL,
    passed_cases    INTEGER NOT NULL,
    failed_cases    INTEGER NOT NULL,

    metrics         JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 自定义指标
    duration_ms     INTEGER NOT NULL,
    cost_cny        NUMERIC(10,4),

    -- 与上次运行的对比
    diff_from_id    BIGINT,
    regressed       BOOLEAN NOT NULL DEFAULT FALSE,

    started_at      TIMESTAMPTZ NOT NULL,
    completed_at    TIMESTAMPTZ,

    CONSTRAINT fk_er_diff_from_id FOREIGN KEY (diff_from_id) REFERENCES eval_runs(id) ON DELETE SET NULL
);

CREATE INDEX idx_er_suite_created ON eval_runs(suite_name, started_at DESC);
```

### 3.19 任务队列(pgmq schema)

由 `pgmq` 扩展自动创建。业务侧使用三个队列:

| 队列名 | 用途 |
|-------|------|
| `q_jd_parse` | 入库 JD 异步解析 |
| `q_profile_parse` | 简历异步解析 |
| `q_resume_generate` | 异步简历生成(也支持同步,异步是 fallback) |

队列消息体使用 JSONB,统一格式:

```json
{
  "task_id": "uuid",
  "user_id": 1,
  "entity_id": 123,
  "retries": 0,
  "enqueued_at": "2026-05-01T10:00:00Z"
}
```

---

## 4. 索引策略

### 4.1 索引清单与理由

| 表 | 索引 | 类型 | 理由 |
|----|------|------|------|
| jds | (user_id, created_at desc) | btree | 看板列表查询 |
| jds | search_tsv | GIN | 全文检索 |
| profile_chunks | embedding | HNSW | 向量近似搜索 |
| profile_chunks | content_tsv | GIN | BM25 风格全文 |
| profile_chunks | (profile_id, granularity) | btree | 按粒度过滤 |
| matches | (user_id, jd_id, created_at desc) | btree | 历史匹配查询 |
| applications | (user_id, status, updated_at desc) | btree | 看板筛选 |
| llm_calls | (user_id, feature, created_at desc) | btree | 成本归因 |

### 4.2 partial index 使用

软删除场景统一用 partial index:

```sql
CREATE INDEX idx_xxx ON tab(...) WHERE deleted_at IS NULL;
```

只索引未删除行,缩小索引体积、加速查询。

### 4.3 不建索引的列

- `description` 等 TEXT 列:不建普通索引(用 tsvector 代替)
- `metadata` JSONB:仅当具体路径有查询需求时,建 expression index
- 高基数低查询的列:不建

---

## 5. Schema 演进策略

### 5.1 工具

- **Alembic** 管理迁移
- 迁移文件位置:`apps/api/alembic/versions/`
- 命名:`{timestamp}_{slug}.py`,例如 `20260615_120000_add_application_invite.py`

### 5.2 规范

- **每个 PR 最多包含一个迁移**
- **不允许在已发布迁移中修改**(只能写新迁移)
- **DDL + DML 分离**:schema 变更与数据迁移用两个迁移文件
- **可逆**:每个 `upgrade()` 必须有对应的 `downgrade()`(除非显式不可逆,需注释说明)
- **零停机原则**:加列允许 NULL,默认值用 `server_default`,改列名用「新增 + 双写 + 切读 + 删旧」三步

### 5.3 破坏性变更流程

| 操作 | 流程 |
|------|------|
| 删表 | 先标记 deprecated 一个 release,下个 release 删 |
| 删列 | 同上 |
| 改列名 | 新增列 → 双写 → 切读 → 删旧列(三个 release) |
| 改类型 | 新增列 → 后台脚本 backfill → 切读 → 删旧列 |

---

## 6. 数据生命周期

### 6.1 创建

通过 API 写入,经过 Pydantic 校验。LLM 异步处理类(JD 解析、简历解析)入库时 status=parsing,处理完成后更新。

### 6.2 更新

软删除 + 版本快照(简历)。普通业务表通过 `updated_at` 触发器维护时间戳。

### 6.3 归档

不主动归档。用户级别数据导出后,用户可手动选择删除老数据。

### 6.4 删除

- **软删除**:业务表 `deleted_at` 字段,默认 partial index 过滤
- **硬删除**:用户在「设置 - 清空数据」执行,级联清空全部用户数据
- **审计**:硬删除前可选导出全量 JSON

### 6.5 导出

支持的格式:

- 全量 JSON(包含所有表关联,但不含 LLM 调用日志默认)
- 压缩 tar.gz(带原始文件)
- 简历单独导出(markdown + PDF)

---

## 7. 多租户与隔离

### 7.1 当前模式

**单机单用户为主**。所有业务表都有 `user_id`,逻辑上支持多用户;但单机部署默认只用 `user_id=1`。

### 7.2 隔离手段

- 应用层强制注入 `user_id` 过滤(repository 基类统一处理)
- 不依赖 Postgres RLS(避免增加复杂度,需要时再启用)
- 测试用例覆盖跨用户数据访问场景

### 7.3 多用户云端 Demo 场景

- 启用 RLS:`CREATE POLICY user_isolation ON tab USING (user_id = current_setting('app.user_id')::bigint)`
- 应用启动时 `SET LOCAL app.user_id = ?`
- 单数据库实例 + 应用层多租户

---

## 8. 性能预期

### 8.1 数据量(单用户预估)

| 表 | 行数(运行 6 个月后) |
|----|---------------------|
| users | 1 |
| jds | 200-500 |
| profiles | 1 |
| profile_experiences | 5-10 |
| profile_projects | 10-20 |
| profile_skills | 20-50 |
| profile_chunks | 50-200 |
| matches | 200-500 |
| resumes | 200-500 |
| applications | 200-500 |
| interview_sessions | 30-50 |
| interview_turns | 300-500 |
| llm_calls | 数千 |

### 8.2 查询 P95

| 查询 | 预期 |
|------|------|
| JD 列表(分页 20 条) | < 30ms |
| RAG Top-K=10(HNSW) | < 5ms |
| 全文 + 向量 Hybrid(RRF) | < 50ms |
| 成本月度统计 | < 100ms |
| 简历完整加载(含 versions) | < 50ms |

---

## 9. 备份与恢复

### 9.1 本地部署

- `pg_dump --format=custom` 输出到 `~/.jobcopilot/backups/`
- 每天定时(用户在设置中开启)
- 保留最近 7 天 + 每周一份保留 4 周

### 9.2 恢复

```bash
docker compose exec postgres pg_restore -d jobcopilot < backup.dump
```

### 9.3 Disaster Recovery

- RPO:1 天
- RTO:< 5 分钟(本地恢复)
- 用户主动负责备份导出(本地优先模式下,JobCopilot 不提供云备份)

---

## 10. 不在本文档范围

- API 字段映射 → `4-API_SPEC.md`
- 各 Agent 输入输出 schema → `5-AGENT_DESIGN.md`
- 评测数据存储格式 → `6-EVAL_PLAN.md`
