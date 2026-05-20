# JobCopilot

> 给学计算机的人用的本地优先 AI 求职准备工具:把目标岗位 JD 累积成岗位要求地图和学习路径,再用自己的笔记做 RAG 面试陪练。

[![Status](https://img.shields.io/badge/status-M2.5%20JD%20Intelligence-yellow)](docs/STATUS.md)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Stack](https://img.shields.io/badge/stack-FastAPI%20%2B%20Next.js%20%2B%20Postgres-2f6fef)](#技术栈)
[![LLM](https://img.shields.io/badge/LLM-Qwen3.6%20%40%20DashScope-1f7ae0)](#配置)

[English](#english-summary) | [文档导航](#文档导航) | [快速开始](#快速开始)

---

## 目录

- [这是什么](#这是什么)
- [核心功能](#核心功能)
- [架构](#架构)
- [快速开始](#快速开始)
- [配置](#配置)
- [本地开发](#本地开发)
- [API 与页面](#api-与页面)
- [项目状态](#项目状态)
- [路线图](#路线图)
- [文档导航](#文档导航)
- [贡献](#贡献)
- [许可证](#许可证)
- [English Summary](#english-summary)

## 这是什么

JobCopilot 面向正在准备跳槽、实习或校招的计算机学习者和开发者。它解决两件很具体的事:

1. **大量 JD 看不过来**:把陆续收集的目标岗位 JD 持久化到本地库里,自动解析、聚合、去重、统计频次,生成岗位要求地图、学习路径和 quiz topic 候选。
2. **学过的笔记面试时讲不清楚**:用你的 Markdown 笔记做 RAG 出题,让 InterviewCoachAgent 按真实面试方式追问、评分、纠偏和总结。

项目是单用户、本地优先形态。业务数据默认放在本机 Postgres 和本地笔记目录里;LLM 调用通过你自己的 API Key 走 DashScope/Qwen。

## 核心功能

| 功能 | 当前实现 |
|------|----------|
| JD 库 | 文本粘贴上传 JD,立即调用 `jd_parser` 解析并落库;支持列表、详情、title 修改、筛选和软删 |
| JD 一键分析 | `POST /api/jd-analyses` 通过 SSE 运行固定 harness:读取已解析 JD、批量聚合、同义合并、Python 重算频次、生成学习路径和 quiz topic 候选 |
| 报告回看 | `/jds` 页面可查看历史报告、岗位要求地图、学习路径、成本/token 审计和 topic 跳转 |
| 笔记入库 | 浏览器读取本地 Markdown,按 folder/heading 切 chunk,写入 Postgres + pgvector |
| RAG 出题 | 聊天框输入主题 query,经过 query rewrite、hybrid search、rerank、治理筛选后生成题目 |
| LLM-as-Judge | AnswerJudge 输出 Coverage/Fidelity/Depth 三层 evidence,Python 负责总分计算 |
| 面试教练 | M2.1 多轮补答/追问分流已落地:补答会重评,追问教练只解释反馈,每轮消息可回放 |
| 本场总结 | 已评分 session 可生成 `_recall/{session_id}.md` 复习沉淀 |
| 可观测 | LLM 调用、SSE 进度、token/cost、trace id 和关键状态入库;Langfuse 可选 |

明确不做:简历上传/诊断/改写、投递追踪、岗位类三源融合出题、长期弱点 dashboard、spaced repetition、自动化 JD aggregator eval runner。

## 架构

```text
apps/web (Next.js 15 / React 19)
  notes / quiz / jds UI, SSE client, macOS-style shell
        |
        | REST + SSE
        v
apps/api (FastAPI / SQLAlchemy 2.x async)
  routers/      notes, quiz, jd, health
  services/     retrieval, quiz, answer, interview, jd, recall
  agents/       quiz_generator, answer_judge, jd_parser, jd_aggregator, coach_chat
  llm/          provider abstraction, cache, pricing, logging
  infra/        Postgres, pgvector, Langfuse, request id
        |
        v
Postgres 16 + pgvector
  notes / note_chunks / questions / quiz_sessions / session_answers
  session_events / jds / jd_analyses / llm_response_cache / llm_calls
        |
        v
DashScope / Qwen
  generation, embedding, rerank, future JD screenshot OCR
```

## 技术栈

| 层 | 技术 |
|----|------|
| Web | Next.js 15, React 19, TypeScript, Tailwind CSS, lucide-react |
| API | FastAPI, Pydantic v2, SQLAlchemy 2.x async, SSE |
| DB | Postgres 16, pgvector, Alembic |
| LLM | DashScope/Qwen, OpenAI-compatible client, structured output, prompt cache |
| RAG | pgvector + tsvector + RRF, query rewrite, provider rerank, deterministic governance |
| Observability | request id, `llm_calls`, token/cost audit, optional Langfuse v2 |
| Tooling | Docker Compose, uv, pnpm, Biome, ruff, mypy, pytest |

## 快速开始

### 前置条件

- Docker with Compose v2
- DashScope API Key: [阿里云百炼控制台](https://bailian.console.aliyun.com/)

### 启动完整栈

```bash
git clone https://github.com/lemma42796/job-copilot.git
cd job-copilot
cp .env.example .env
```

编辑 `.env`:

```bash
DASHSCOPE_API_KEY=sk-your-key
LLM_PROVIDER=dashscope
JOBCOPILOT_ENV=dev
```

启动:

```bash
docker compose up -d
```

访问:

```text
Web:      http://localhost:3000
API:      http://localhost:8000/v1/health
API Docs: http://localhost:8000/v1/docs
Langfuse: http://localhost:3001
Caddy:    http://localhost
```

## 配置

| 变量 | 必填 | 说明 |
|------|------|------|
| `DASHSCOPE_API_KEY` | 是 | DashScope/Qwen API Key;compose 会映射为 `JOBCOPILOT_DASHSCOPE_API_KEY` |
| `LLM_PROVIDER` | 否 | 默认 `dashscope`;保留 `deepseek` 备选 |
| `JOBCOPILOT_ENV` | 否 | `dev` / `prod` / `test`,默认 `dev` |
| `JOBCOPILOT_NOTES_FS_ROOT` | 否 | recall 文件和本地 notes 逻辑根目录 |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | 否 | 留空时 SDK noop,不影响主流程 |
| `LANGFUSE_HOST` | 否 | compose 内默认 `http://langfuse:3000` |

## 本地开发

仓库是 monorepo:

```text
apps/api   FastAPI backend
apps/web   Next.js frontend
packages   shared TypeScript schemas
docs       product, technical, API, agent, eval, roadmap docs
evals      manually triggered evaluation suites and fixtures
docker     compose images and service config
```

常用入口:

```bash
pnpm --filter @jobcopilot/web dev
uv run uvicorn jobcopilot_api.main:app --reload --app-dir apps/api/src --port 8000
```

项目约束是**手动闸门**:lint、typecheck、build、pytest、Playwright 和 eval runner 都保留入口,但不在 push 时自动触发。需要验证时由维护者显式运行。

## API 与页面

| Surface | 说明 |
|---------|------|
| `/notes` | Markdown 笔记导入、树形导航、编辑 |
| `/quiz` | 主题 query 出题、答题、补答、追问教练、整场总结 |
| `/jds` | JD 上传、JD 库、分析范围选择、历史报告与 topic 跳转 |
| `POST /api/quiz/sessions` | 主题类 RAG 出题 SSE |
| `POST /api/quiz/sessions/{id}/answers/{order_index}/turns` | M2.1 单题 turn SSE,支持 `auto` 分流 |
| `POST /api/quiz/sessions/{id}/finish` | 整场总结 SSE |
| `POST /api/jds` | 文本 JD 上传并立即解析 |
| `POST /api/jd-analyses` | JD 一键分析 SSE |
| `GET /v1/health` | compose healthcheck |

完整接口契约见 [`docs/4-API_SPEC.md`](docs/4-API_SPEC.md)。

## 项目状态

当前阶段:**M2.5 - JD Intelligence Agent**。

已完成:

- M0 仓库改造、v2 文档和基础骨架
- M1 Markdown 笔记入库、chunker、树形导航、Langfuse 起步
- M2 主题 query → 全库 RAG → 出题 → LLM-as-Judge
- M2.1 InterviewCoachAgent 的多轮补答、追问教练、恢复和整场总结
- M2.5 文本 JD 入库、`jd_parser`、JD 库、`jd_aggregator` 报告 MVP 和 `/jds` 报告查看

仍在 M2.5 内推进:

- 报告质量 hardening 和手动 dogfood
- JD 截图 OCR 输入链路
- 报告详情信息密度、筛选和 topic 批量进入 `/quiz`

最新事实以 [`docs/STATUS.md`](docs/STATUS.md) 为准。

## 路线图

```text
M0    仓库改造 + 文档重写                                      done
M1    笔记入库 + chunker + 树形导航 + Langfuse 起步              done
M2    聊天框主题 query -> 全库 RAG -> 出题 + Judge 三层评分       done
M2.1  InterviewCoachAgent: 状态机 + 多轮纠偏 + 总结              done
M2.5  JD Intelligence Agent: JD 库 -> 要求地图 -> 学习路径       current
```

M2.5 之后不再规划 M3 的 SR、弱点 dashboard、岗位类三源出题或简历诊断。后续生产力主线收束到 JD Intelligence Agent。

## 文档导航

| 文件 | 内容 |
|------|------|
| [`docs/STATUS.md`](docs/STATUS.md) | 当前进度、已锁定决策、下一刀 |
| [`docs/1-PRD.md`](docs/1-PRD.md) | 产品定位、用户故事、功能边界 |
| [`docs/2-TECH_DESIGN.md`](docs/2-TECH_DESIGN.md) | 架构、模块分层、数据流、可观测 |
| [`docs/3-DATA_MODEL.md`](docs/3-DATA_MODEL.md) | 表结构、JSONB schema、迁移边界 |
| [`docs/4-API_SPEC.md`](docs/4-API_SPEC.md) | REST + SSE API 契约 |
| [`docs/5-AGENT_DESIGN.md`](docs/5-AGENT_DESIGN.md) | Agent prompt、输出契约、M2.1/M2.5 编排原则 |
| [`docs/6-EVAL_PLAN.md`](docs/6-EVAL_PLAN.md) | 评测套件与手动 dogfood 口径 |
| [`docs/7-ROADMAP.md`](docs/7-ROADMAP.md) | 里程碑范围、退出标准、不做清单 |
| [`docs/8-ENGINEERING.md`](docs/8-ENGINEERING.md) | 工程规范、本地开发、CI 策略 |
| [`docs/9-LESSONS.md`](docs/9-LESSONS.md) | v1/v2 工程踩坑与产品反思 |

## 贡献

这是维护者本地 dogfood 优先的开源项目。欢迎通过 issue 或 PR 讨论以下类型的改动:

- README、文档和 onboarding 修正
- 明确复现路径的 bug fix
- 不扩大产品边界的 M2.5/JD Intelligence 改进
- 手动验证路径更清楚的开发体验改进

提交前请保持改动聚焦,并在 PR 中说明你手动验证过的页面、API 或命令。自动 CI 默认不会在 push 时运行。

## 许可证

[MIT](LICENSE)

## 致谢

- 阿里云百炼 / DashScope / Qwen
- LangGraph, FastAPI, Next.js, pgvector, SQLAlchemy, Tailwind CSS, Langfuse 和开源社区

---

## English Summary

JobCopilot is a local-first AI job-preparation tool for computer science learners and developers.

It has two connected workflows:

1. **JD Intelligence Agent**: persist job descriptions, parse them, aggregate repeated requirements, recompute frequencies, generate a learning path, and save report snapshots.
2. **Note-based interview coach**: use your Markdown notes as the RAG source, generate grounded interview questions, judge answers with evidence, support multi-turn remediation, and save session summaries.

The project runs as a single-user local stack with FastAPI, Next.js, Postgres/pgvector, Docker Compose, and DashScope/Qwen via your own API key.

Current phase: **M2.5 JD Intelligence Agent**. See [`docs/STATUS.md`](docs/STATUS.md) and [`docs/7-ROADMAP.md`](docs/7-ROADMAP.md) for the latest implementation state and roadmap boundaries.
