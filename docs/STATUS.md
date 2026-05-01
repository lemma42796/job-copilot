---
title: JobCopilot 项目当前进度(单一可信源)
owner: lemma42796
last_updated: 2026-05-01 (M0 仓库骨架本地落地,待 docker compose 端到端验证)
purpose: 跨会话续作的状态快照。任何新会话从这里开始读。
---

# 当前阶段

**M0 仓库骨架完成(docker compose DoD 已通过,等首次 commit + push)**

- 文档:Batch 1 + Batch 2 已完成(8 份核心文档 + README + 3 份 ADR)
- 代码:`apps/api` / `apps/web` / `packages/schemas` / `docker/` / CI 已落地
- 本地验证:✅ ruff / mypy strict / biome / tsc / pytest(1/1)全部通过
- ✅ `docker compose up -d` DoD 全部通过(2026-05-01):
  - 4 个容器全部 healthy(postgres / api / web / caddy)
  - `curl :8000/v1/health` → `{"status":"ok",...}`
  - `curl :3000/` 渲染 `API: ok v0.0.1 env dev`
  - Caddy 在 :80 同时反代 `/v1/*` → api 与 `/` → web,验证通过
  - Postgres `vector` 扩展已 enable
  - 冷启动总耗时 ~35s(< 3min DoD)
- 🟡 GitHub repo 推送 — 待用户决定是否创建 `lemma42796/job-copilot` 后推

**M0 期间踩到的两个坑(已记录,留作经验)**

1. 选错 postgres 镜像 `ghcr.io/tembo-io/tembo-pg-cnpg`(私有镜像,registry 拒绝匿名拉取)。改用公开 `pgvector/pgvector:pg16`。**pgmq 不在该镜像中,推迟到 M2 用自定义 Dockerfile 装(届时基于 pgvector 镜像 + tembo-io/pgmq 的 .deb 安装)。** docker/postgres/init.sql 与 docker-compose.yml 已注释。
2. Next.js 服务端组件在容器内 SSR 时通过 `localhost:8000` 调 API 失败 —— 容器内 `localhost` 指向 web 自己。修复:在 `apps/web/src/lib/api.ts` 区分 `INTERNAL_API_BASE_URL=http://api:8000`(SSR)与 `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`(浏览器)。

下一步:首个 commit(squash 整个 M0)→ 进入 M1 数据入口贯通。

> 2026-05-01 决策变更:LLM Provider 由 DeepSeek V4 切换为阿里云百炼 Qwen3.6,理由是消耗剩余 ¥15 赠款。详见 ADR-0003。**ADR-0001 复审条件 1(余额 < ¥1)触发时自动回切。**

---

# 已经锁定的关键决策(不要再讨论)

| 项 | 决策 |
|----|------|
| 目标用户 | 1-3 年跳槽开发者(单一画像,应届生 v2 再说) |
| 北极星 NSM | 用户使用前后投递的面试邀约率提升 |
| 短期 Proxy | 端到端完成率(粘 JD → 下载定制简历) |
| MVP 边界(P0) | JD 入库 + 个人档案 + 匹配分析 + 简历定制 + 本地部署 |
| 面试模拟 | P1(Phase 5,Week 11-13) |
| 部署形态 | 本地优先,`docker compose up` 一键启动 |
| 仓库结构 | 单仓 monorepo:`apps/api` + `apps/web` + `packages/schemas` |
| LLM Provider | **仅阿里云百炼 Qwen3.6**(Flash + Plus,见 ADR-0003;ADR-0001 已 Superseded,作为额度耗尽后的回切方案) |
| 数据存储 | **Postgres 16 一把梭**(pgvector + tsvector + pgmq + bytea,见 ADR-0002) |
| Agent 编排 | LangGraph 仅用于简历定制 + 面试模拟两条状态机,其他场景单 Agent |
| 文档 owner | lemma42796(GitHub 用户名) |
| 旧文档处理 | 已删除(JobCopilot-项目设计文档.md / Analysis2-5.md / 三个 SVG) |

---

# 文档清单与状态

```
docs/
├── STATUS.md                    ← 你正在读
├── 1-PRD.md                     ✅ 完成(~350 行)
├── 2-TECH_DESIGN.md             ✅ 完成(~560 行)
├── 3-DATA_MODEL.md              ✅ 完成(964 行)
├── 4-API_SPEC.md                ✅ 完成(959 行)
├── 5-AGENT_DESIGN.md            ✅ 完成(962 行)
├── 6-EVAL_PLAN.md               ✅ 完成(580 行)
├── 7-ROADMAP.md                 ✅ 完成(475 行)
├── 8-ENGINEERING.md             ✅ 完成(599 行)
README.md (项目根)               ✅ 完成(214 行)
├── adr/
│   ├── 0001-only-deepseek.md    ✅ 完成(已 Superseded by 0003)
│   ├── 0002-postgres-as-vector-db.md  ✅ 完成
│   └── 0003-switch-to-qwen.md   ✅ 完成
└── runbook/                     (空,部署期再写)
```

**已完成行数**:~5700 行 / 8 份文档 + README(214) + 3 份 ADR(0001 / 0002 / 0003)
**Batch 1**:✅ 4/4 完成
**Batch 2**:✅ 5/5 完成(API_SPEC / EVAL_PLAN / ROADMAP / ENGINEERING / README)

---

# 续作指引(给下一会话的 Claude)

## 当用户问"开发进度到了?"时

1. 读本文件(STATUS.md)了解当前状态
2. 简短汇报当前进度(完成了什么 / 下一步是什么 / 已锁定决策提醒)
3. **等待用户指示后再行动**,不要自动开工

## Batch 2 写作顺序与依赖(已全部完成)

```
4-API_SPEC.md      ✅ 完成(959 行,2026-05-01)
6-EVAL_PLAN.md     ✅ 完成(580 行,2026-05-01)
7-ROADMAP.md       ✅ 完成(475 行,2026-05-01)
8-ENGINEERING.md   ✅ 完成(599 行,2026-05-01)
README.md          ✅ 完成(214 行,2026-05-01,放在项目根)
```

## Batch 2 完成后的下一步

- 用户 review 通过 → **立即进入编码阶段**
- 编码起点:按 ROADMAP M0-M1,先搭仓库骨架(monorepo + uv + pnpm + Postgres 启动 + Hello FastAPI + Hello Next.js)
- 不要在编码阶段再写新设计文档(除非 ADR 追加)

## 重要风格约定

- 文档元数据头格式见已完成文档,严格遵循
- 中文为主,代码示例与 schema 标识符为英文
- 每份文档末尾写"不在本文档范围"指向相关文档
- ADR 编号顺延(下一个 ADR 是 0003)
- 不要重新讨论已锁定的决策

---

# 待办清单(任务工具)

任务工具中已创建 8 条任务,状态:

| ID | 任务 | 状态 |
|----|------|------|
| 1 | 写 PRD.md | ✅ completed |
| 2 | 写 TECH_DESIGN.md + ADR-0001/0002 | ✅ completed |
| 3 | 写 DATA_MODEL.md | ✅ completed |
| 4 | 写 AGENT_DESIGN.md | ✅ completed |
| 5 | 写 API_SPEC.md | ✅ completed |
| 6 | 写 EVAL_PLAN.md | ✅ completed |
| 7 | 写 ROADMAP.md | ✅ completed |
| 8 | 写 ENGINEERING.md + README.md | ✅ completed |

---

# 项目根目录现状

```
/Users/a123/code/JobCopilot/
├── .DS_Store
├── .claude/
├── README.md
└── docs/
    ├── STATUS.md
    ├── 1-PRD.md
    ├── 2-TECH_DESIGN.md
    ├── 3-DATA_MODEL.md
    ├── 4-API_SPEC.md
    ├── 5-AGENT_DESIGN.md
    ├── 6-EVAL_PLAN.md
    ├── 7-ROADMAP.md
    ├── 8-ENGINEERING.md
    └── adr/
        ├── 0001-only-deepseek.md
        ├── 0002-postgres-as-vector-db.md
        └── 0003-switch-to-qwen.md
```

**项目已 `git init -b main`,尚无任何 commit**。首次 commit 推迟到 docker compose DoD 通过后一起做。

**新增的 M0 代码与配置**:

```
apps/api/                # FastAPI + uv 工程
  pyproject.toml
  src/jobcopilot_api/{__init__.py, main.py, settings.py}
  src/jobcopilot_api/routers/{__init__.py, health.py}
  tests/{__init__.py, conftest.py, unit/test_health.py}
apps/web/                # Next.js 15 + App Router
  package.json, tsconfig.json, next.config.ts, next-env.d.ts, .env.example
  src/app/{layout.tsx, page.tsx, globals.css}
  src/lib/api.ts
packages/schemas/        # OpenAPI → TS 类型(workspace 包)
  package.json, tsconfig.json, README.md
  src/{index.ts, api.ts(generated)}
  scripts/generate.mjs
docker/                  # 容器镜像与初始化
  api.Dockerfile, web.Dockerfile
  postgres/init.sql       # vector / pg_trgm / pgmq
  caddy/Caddyfile
docker-compose.yml       # postgres + api + web + caddy
.github/workflows/       # lint, test-api, test-web, type-sync, docker-smoke
.github/pull_request_template.md
根目录:
  package.json (pnpm workspace), pnpm-workspace.yaml, biome.json
  pyproject.toml (uv workspace + ruff + mypy 配置)
  tsconfig.base.json
  .pre-commit-config.yaml
  .gitignore, .gitattributes, .dockerignore
  .env.example, LICENSE (MIT)
```

---

# 上次会话遗留的开放问题

PRD §9 列出的 4 个开放问题,在 Batch 2 / 编码期再决定:

- Q-01:简历 PDF 模板用现成开源还是自研?(默认:LaTeX `awesome-cv` 中文化)
- Q-02:投递追踪看板要不要做日历提醒?(默认:不做)
- Q-03:MCP Server 暴露的工具粒度?(默认:5 tool + 1 resource)
- Q-04:Web demo 站要不要支持 BYOK 在线试用?(默认:做)

不阻塞 Batch 2,在对应里程碑启动前再决策即可。
