# JobCopilot

> Local-first AI job-prep workspace for CS learners and developers. Build a reusable JD library, turn job descriptions into requirement maps and learning paths, then practice with a note-grounded RAG interview coach.

[![Status](https://img.shields.io/badge/status-M2.5%20JD%20Intelligence-yellow)](docs/7-ROADMAP.md)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Stack](https://img.shields.io/badge/stack-FastAPI%20%2B%20Next.js%20%2B%20Postgres-2f6fef)](#tech-stack)
[![LLM](https://img.shields.io/badge/LLM-Qwen%20%40%20DashScope-1f7ae0)](#configuration)

[中文概览](#中文概览) | [Documentation](#documentation) | [Quick Start](#quick-start)

---

## Table of Contents

- [Overview](#overview)
- [Why JobCopilot](#why-jobcopilot)
- [Features](#features)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Usage](#usage)
- [API Surface](#api-surface)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Development](#development)
- [Testing and Evaluation](#testing-and-evaluation)
- [Roadmap](#roadmap)
- [Limitations](#limitations)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

## Overview

JobCopilot is a local-first AI workspace for people preparing for software engineering roles.

It combines two workflows:

1. **JD Intelligence**: collect job descriptions over time, parse them into structured data, aggregate repeated requirements, recompute frequencies, and generate a learning path plus quiz topic candidates.
2. **Note-grounded interview practice**: import Markdown notes, retrieve only relevant note chunks, generate interview questions, judge answers with evidence, and support multi-turn remediation.

The project is designed for single-user dogfood first. Your notes, JD records, reports, sessions, and recall files stay in your local workspace and Postgres database. LLM calls use your own DashScope/Qwen API key.

## Why JobCopilot

LLM chat products are already good enough for one-off interview prompts. JobCopilot focuses on workflows that are awkward to keep inside a single chat session:

- JD research is cumulative: a real job search may collect dozens of similar JDs over several weeks.
- Requirement frequency matters: the useful output is not one answer, but a repeatable requirement map and learning priority list.
- Interview practice should stay grounded: questions and scores should be tied to your own Markdown notes, not generic model memory.
- Multi-turn remediation needs state: each answer, coach message, score, gap, and summary should be replayable after refresh.

## Features

### JD Intelligence

| Feature | Status |
|---------|--------|
| JD library | Text-paste JD upload, immediate `jd_parser` extraction, list/detail/filter/edit/delete |
| JD analysis reports | SSE report flow over selected JDs: load parsed payloads, aggregate requirements, dedupe, recompute frequency, generate learning path, produce quiz topics |
| Report history | Saved `jd_analyses` snapshots with requirement maps, evidence JD ids, cost/token audit, and note coverage summary |
| Quiz topic handoff | Report topic candidates can be used as normal `/quiz` topic queries |
| Screenshot OCR | Planned M2.5 slice; images are not persisted, only OCR text will be stored |

### Interview Coach

| Feature | Status |
|---------|--------|
| Markdown note ingestion | Browser reads local Markdown, backend chunks by folder and heading, Postgres stores notes and chunks |
| Retrieval pipeline | Query rewrite, hybrid search, RRF, provider rerank, deterministic governance, zero-hit guard |
| Question generation | `QuizGenerator` produces source-grounded questions from selected chunks |
| Answer judging | `AnswerJudge` returns Coverage, Fidelity, Depth evidence; Python computes final score |
| Multi-turn coach | Initial answers and remediation answers are scored; coach questions are separated from scored answers |
| Session recall | Finished sessions can write `_recall/{session_id}.md` summaries |

### Engineering

| Feature | Status |
|---------|--------|
| Local-first stack | Docker Compose for Postgres, API, Web, Caddy, and optional Langfuse |
| Streaming UX | Long-running quiz, judge, finish, and JD analysis flows use SSE |
| Observability | `trace_id`, LLM call logs, token/cost audit, cache metadata, optional Langfuse |
| Manual gates | CI workflows and validation commands are kept manual to avoid noisy automatic runs |

## Quick Start

### Prerequisites

- Docker with Compose v2
- DashScope API key from [Alibaba Cloud Bailian](https://bailian.console.aliyun.com/)

### Run with Docker Compose

```bash
git clone https://github.com/lemma42796/job-copilot.git
cd job-copilot
cp .env.example .env
```

Edit `.env`:

```bash
DASHSCOPE_API_KEY=sk-your-key
LLM_PROVIDER=dashscope
JOBCOPILOT_ENV=dev
```

Start the stack:

```bash
docker compose up -d
```

Open:

```text
Web app:  http://localhost:3000
API:      http://localhost:8000/v1/health
API docs: http://localhost:8000/v1/docs
Langfuse: http://localhost:3001
Caddy:    http://localhost
```

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `DASHSCOPE_API_KEY` | Yes | DashScope/Qwen API key. Docker maps it to `JOBCOPILOT_DASHSCOPE_API_KEY` |
| `LLM_PROVIDER` | No | Defaults to `dashscope`; `deepseek` remains a fallback option in the template |
| `JOBCOPILOT_ENV` | No | `dev`, `prod`, or `test`; defaults to `dev` |
| `JOBCOPILOT_NOTES_FS_ROOT` | No | Local notes/recall root. Dev mode falls back to `test-notes/llm-notes` when unset |
| `LANGFUSE_PUBLIC_KEY` | No | Optional Langfuse public key. Empty key means SDK noop |
| `LANGFUSE_SECRET_KEY` | No | Optional Langfuse secret key |
| `LANGFUSE_HOST` | No | Optional Langfuse host. Compose defaults to `http://langfuse:3000` inside containers |

## Usage

### Analyze job descriptions

1. Open `http://localhost:3000/jds`.
2. Paste a JD. The backend parses it immediately and stores the structured payload.
3. Repeat for a set of related roles.
4. Choose an analysis scope: all JDs, recent JDs, explicit ids, or a title filter.
5. Run one-click analysis.
6. Review the requirement map, learning path, note coverage summary, and quiz topic candidates.

### Practice from your notes

1. Open `http://localhost:3000/notes`.
2. Import or write Markdown notes.
3. Open `http://localhost:3000/quiz`.
4. Enter a topic query, or jump from a JD report topic.
5. Answer one question at a time.
6. Use remediation turns to improve an answer, or ask the coach to explain feedback.
7. Finish the session to generate a recall summary.

## API Surface

| Endpoint | Purpose |
|----------|---------|
| `GET /v1/health` | Service healthcheck |
| `POST /api/notes/batch-import` | Import Markdown notes read by the browser |
| `GET /api/notes/tree` | Read the note tree |
| `POST /api/quiz/sessions` | Start a topic-based RAG quiz session over SSE |
| `GET /api/quiz/sessions/{id}` | Restore a quiz session and replay state |
| `POST /api/quiz/sessions/{id}/answers/{order_index}/turns` | Submit an answer/remediation/coach-question turn over SSE |
| `POST /api/quiz/sessions/{id}/finish` | Generate a session summary over SSE |
| `POST /api/jds` | Upload and parse a text JD |
| `GET /api/jds` | List stored JDs |
| `POST /api/jd-analyses` | Run one-click JD analysis over SSE |
| `GET /api/jd-analyses/{id}` | Read a saved JD analysis report |

See [`docs/4-API_SPEC.md`](docs/4-API_SPEC.md) for full request/response contracts.

## Architecture

```text
apps/web (Next.js 15 / React 19)
  notes, quiz, jds UI
  SSE client
  macOS-style app shell
        |
        | REST + SSE
        v
apps/api (FastAPI / SQLAlchemy 2.x async)
  routers:  notes, quiz, jd, health
  services: retrieval, quiz, answer, interview, jd, recall
  agents:   quiz_generator, answer_judge, jd_parser, jd_aggregator, coach_chat
  llm:      provider abstraction, cache, pricing, logging
  infra:    Postgres, pgvector, Langfuse, request id
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

## Tech Stack

| Layer | Technologies |
|-------|--------------|
| Frontend | Next.js 15, React 19, TypeScript, Tailwind CSS, lucide-react |
| Backend | FastAPI, Pydantic v2, SQLAlchemy 2.x async, sse-starlette |
| Database | Postgres 16, pgvector, Alembic |
| LLM | DashScope/Qwen, OpenAI-compatible SDK, structured output, prompt cache |
| Retrieval | pgvector, tsvector, RRF, query rewrite, provider rerank, deterministic governance |
| Observability | request id, `llm_calls`, token/cost audit, optional Langfuse v2 |
| Tooling | Docker Compose, uv, pnpm, Biome, ruff, mypy, pytest |

## Project Structure

```text
apps/
  api/              FastAPI backend, agents, services, models, migrations
  web/              Next.js frontend
packages/
  schemas/          shared TypeScript schemas
docs/               product, technical, API, agent, eval, roadmap docs
evals/              evaluation datasets and reports
docker/             Dockerfiles and service config
test-notes/         local dogfood notes and recall output
```

## Development

Install dependencies in the usual workspace style:

```bash
pnpm install
uv sync
```

Run frontend and backend locally:

```bash
pnpm --filter @jobcopilot/web dev
uv run uvicorn jobcopilot_api.main:app --reload --app-dir apps/api/src --port 8000
```

For local development, the preferred shape is Docker Postgres plus local API/Web processes. Full Compose remains useful for a clean end-to-end stack.

## Testing and Evaluation

This repository keeps validation commands available, but runs them manually. Pushes do not trigger automatic test, typecheck, lint, build, or eval jobs.

Common manual gates:

```bash
uv run pytest -q
uv run ruff check apps/api
uv run mypy apps/api/src
pnpm typecheck
pnpm build
```

Evaluation details live in [`docs/6-EVAL_PLAN.md`](docs/6-EVAL_PLAN.md). M2.5 JD report quality currently uses manual dogfood instead of a new `jd_aggregator` automation branch.

## Roadmap

```text
M0    Repository rewrite and v2 documentation                       done
M1    Markdown notes, chunking, tree navigation, Langfuse basics     done
M2    Topic query -> RAG -> questions -> LLM-as-Judge                done
M2.1  InterviewCoachAgent state machine and remediation loop         done
M2.5  JD Intelligence Agent and report hardening                     current
```

The project no longer plans SR, weakness dashboard, job-mode three-source question generation, resume upload, resume diagnosis, or resume rewriting. The ongoing productivity line is JD Intelligence.

## Limitations

- Single-user local dogfood project; no auth or SaaS mode.
- JD screenshot OCR is planned, but current JD upload is text-first.
- JD aggregation is not a RAG pipeline. It is bounded aggregation over selected parsed JDs; RAG happens later when a topic enters `/quiz`.
- The project intentionally does not implement resume generation, resume tailoring, or application tracking.
- Automated validation is not run on every push; maintainers run manual gates when needed.

## Documentation

| Document | Purpose |
|----------|---------|
| [`docs/STATUS.md`](docs/STATUS.md) | Short current handoff and locked decisions |
| [`docs/1-PRD.md`](docs/1-PRD.md) | Product positioning, user stories, scope boundaries |
| [`docs/2-TECH_DESIGN.md`](docs/2-TECH_DESIGN.md) | Architecture, module boundaries, data flow, observability |
| [`docs/3-DATA_MODEL.md`](docs/3-DATA_MODEL.md) | Tables, JSONB schemas, migration boundaries |
| [`docs/4-API_SPEC.md`](docs/4-API_SPEC.md) | REST and SSE API contracts |
| [`docs/5-AGENT_DESIGN.md`](docs/5-AGENT_DESIGN.md) | Agent prompts, output contracts, M2.1/M2.5 orchestration |
| [`docs/6-EVAL_PLAN.md`](docs/6-EVAL_PLAN.md) | Evaluation suites and manual dogfood policy |
| [`docs/7-ROADMAP.md`](docs/7-ROADMAP.md) | Milestones, exit criteria, and non-goals |
| [`docs/8-ENGINEERING.md`](docs/8-ENGINEERING.md) | Engineering conventions, local development, CI strategy |
| [`docs/9-LESSONS.md`](docs/9-LESSONS.md) | v1/v2 lessons and product reversals |

## Contributing

Issues and focused pull requests are welcome. Good contributions are usually:

- README, docs, and onboarding improvements.
- Reproducible bug fixes.
- M2.5 JD Intelligence improvements that do not expand product scope.
- Developer-experience fixes that keep validation explicit and manual.

Please keep changes narrow and describe any manual verification you performed.

## License

[MIT](LICENSE)

## Acknowledgements

- Alibaba Cloud Bailian / DashScope / Qwen
- LangGraph, FastAPI, Next.js, pgvector, SQLAlchemy, Tailwind CSS, Langfuse, and the open-source community

---

## 中文概览

JobCopilot 是一个本地优先的 AI 求职准备工具,面向正在准备软件工程岗位的计算机学习者和开发者。

它把两条工作流连在一起:

1. **JD Intelligence Agent**:把目标岗位 JD 当成可累积资产,自动解析、聚合、去重、统计频次,生成岗位要求地图、学习路径和 quiz topic 候选。
2. **笔记 RAG 面试陪练**:用你的 Markdown 笔记作为唯一知识来源之一,出题、评分、追问、补答、总结都围绕笔记证据展开。

当前阶段是 **M2.5 JD Intelligence Agent**。最新实现边界见 [`docs/7-ROADMAP.md`](docs/7-ROADMAP.md) 和 [`docs/STATUS.md`](docs/STATUS.md)。
