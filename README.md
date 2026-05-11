# JobCopilot

> **给程序员的 AI 面试陪练。把你写过的笔记变成你的面试题。**
>
> 一行 `docker compose up` 启动,本地优先,数据不出机器。

[![Status](https://img.shields.io/badge/status-WIP%20M2-yellow)](docs/STATUS.md)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![LLM](https://img.shields.io/badge/LLM-Qwen3.6%20%40%20DashScope-1f7ae0)]()

---

## 这是什么

写技术笔记的程序员都遇到过同款情境:**学过、写过的东西,面试一问就讲不清楚**。

JobCopilot 把你的笔记当成私人题库,模拟真面试的强度反问你 — 直到把盲区暴露出来。

**核心闭环**:

```
1. 笔记入库 — 上传本地 markdown 文件夹 / 在 Web 编辑器里写
2. 输入主题 — 在聊天框里说 "考考我多线程" / "缓存一致性"
3. 系统全库 RAG 出题 — 3-10 道开放式 + 八股,基于你笔记的 chunks(反幻觉:每题标 source_chunk_ids)
4. 你不能看笔记 — 纯靠记忆答
5. LLM Judge 三层评分 — 覆盖度 / 忠实度 / 深度
6. 弱点入队 — 知识点维度跟踪,下次按 spaced repetition 重点考你
```

**跟现有工具的区别**

| 工具 | 在做什么 | JobCopilot 的差异 |
|------|---------|-------------------|
| Anki | 手动写卡片 | LLM 从你笔记自动出题 |
| 面试鸭 / 牛客 | 公共题库 | 私人题库(你写过的就是你的题) |
| ChatGPT 出题 | 无记忆 / 无评分 / 无校准 | 持续记忆 + 三层评分 + 弱点跟踪 |
| Notion AI | 被动检索(你问它才答) | 主动 active recall(它反问你) |

---

## 一图概览

```
┌────────────────────────────────────────────────────┐
│  apps/web (Next.js 15)  macOS 风格                 │
│  树形导航 / Markdown 编辑器 / 答题 / 弱点 dashboard │
└────────────────────┬───────────────────────────────┘
                     │ REST + SSE
┌────────────────────┴───────────────────────────────┐
│  apps/api (FastAPI)                                 │
│  ├─ services/  notes / quiz / answer / sessions     │
│  ├─ agents/    QuizGenerator / AnswerJudge          │
│  ├─ llm/       Provider 抽象 + Prompt Cache         │
│  └─ infra/     pgvector / hybrid search / kappa     │
└────────────────────┬───────────────────────────────┘
                     │
              ┌──────┴──────┐
              │  Postgres 16 │  notes / chunks / questions /
              │              │  sessions / answers / knowledge_gap
              └──────┬──────┘
                     │
              ┌──────┴───────────┐
              │ 阿里云百炼 API    │ Qwen3.6-Flash(thinking on)
              └──────────────────┘
```

---

## 工程亮点

| 主题 | 实现要点 | 文档 |
|------|---------|------|
| **笔记 RAG** | hybrid search(pgvector + tsvector + RRF)+ 中文 char n-gram | `docs/2-TECH_DESIGN` / `docs/5-AGENT_DESIGN` |
| **反幻觉出题** | 每题强制标 `source_chunk_ids`;chunks 里不存在的术语禁止入题 | `docs/5-AGENT_DESIGN` |
| **LLM-as-Judge 三层** | Coverage(覆盖度)+ Fidelity(反幻觉)+ Depth(深度),先证据后打分 | `docs/6-EVAL_PLAN` |
| **Cohen's kappa 守门** | Judge 自身可靠性指标(po-pe)/(1-pe),≥ 0.7 才上 | `docs/6-EVAL_PLAN` |
| **知识点弱点跟踪** | 用 `folder_path / heading_path` 作 ground truth tag,无需 LLM 抽 | `docs/3-DATA_MODEL` |
| **Prompt Cache** | sha256(prompt+schema) → cache_key,自动失效,异常降级 miss | `docs/2-TECH_DESIGN` |
| **Spaced Repetition** | 简化版 SM-2 思路 + 一行 SQL 排期 | `docs/3-DATA_MODEL` |
| **Agentic RAG 面试教练(M2.1)** | LangGraph 状态机:检索 → 出题 → 等答 → 评分 → 决策 → 追问 / 总结 | `docs/5-AGENT_DESIGN` / `docs/7-ROADMAP` |
| **可观测** | 每次响应附 trace_id,LLM 调用全量入库 `llm_calls` | `docs/2-TECH_DESIGN` |
| **本地优先** | docker compose 一键起 + BYOK,数据不出机器 | `docs/1-PRD` |

---

## 快速开始

### 前置

- Docker(含 Compose v2)
- 阿里云百炼 API Key([申请入口](https://bailian.console.aliyun.com/))

### 启动

```bash
git clone https://github.com/lemma42796/job-copilot.git
cd job-copilot
cp .env.example .env
# 编辑 .env,填入:
#   JOBCOPILOT_DASHSCOPE_API_KEY=sk-xxx
docker compose up -d
```

3-5 分钟后:

```
Web:  http://localhost:3000
API:  http://localhost:8000/v1/health
Docs: http://localhost:8000/v1/docs   # 开发模式
```

---

## 文档导航

| 文件 | 内容 |
|------|------|
| [`docs/STATUS.md`](docs/STATUS.md) | 当前进度的单一可信源 — **新会话从这里开始** |
| [`docs/1-PRD.md`](docs/1-PRD.md) | 产品需求:目标用户、核心闭环、NSM |
| [`docs/2-TECH_DESIGN.md`](docs/2-TECH_DESIGN.md) | 技术设计:架构、模块分层、LLM 调用层 |
| [`docs/3-DATA_MODEL.md`](docs/3-DATA_MODEL.md) | 数据模型:notes / chunks / questions / sessions / answers / knowledge_gap |
| [`docs/4-API_SPEC.md`](docs/4-API_SPEC.md) | API 规范:REST + SSE 端点、错误码、流式协议 |
| [`docs/5-AGENT_DESIGN.md`](docs/5-AGENT_DESIGN.md) | Agent 设计:QuizGenerator / AnswerJudge 输入输出 + Prompt 全文 |
| [`docs/6-EVAL_PLAN.md`](docs/6-EVAL_PLAN.md) | 评测计划:hybrid_search / quiz_generator / answer_judge / interview_coach 等 suite |
| [`docs/7-ROADMAP.md`](docs/7-ROADMAP.md) | M0 / M1 / M2 / M2.5 / M3 节奏与退出标准 |
| [`docs/8-ENGINEERING.md`](docs/8-ENGINEERING.md) | 工程规范:仓库结构、Python+TS 规范、CI/CD |
| [`docs/9-LESSONS.md`](docs/9-LESSONS.md) | 工程踩坑录(8 大类 ~30 条) |

---

## JD 考点对照表

把 LLM 应用工程师 JD 高频能力点映射到本项目实现。

| 招聘考点 | 本项目证据 | 文档 / 代码定位 |
|---------|-----------|----------------|
| LLM 应用工程化端到端落地 | 8 份设计文档 + ROADMAP + 工程踩坑录 | `docs/` |
| Agent 编排(LangGraph 状态机) | InterviewCoachAgent:状态 / 工具 / 分支 / 记忆 / 评测 / 恢复 | `docs/5-AGENT_DESIGN` / `docs/7-ROADMAP` |
| RAG 工程(混合检索 + 重排) | hybrid search:pgvector + tsvector + RRF + char n-gram | `docs/5-AGENT_DESIGN` / `apps/api/src/jobcopilot_api/services/retrieval_pipeline.py` |
| Prompt 工程 + 版本管理 | Prompt 即代码,Jinja2 模板 + `prompt_versions` 表 | `apps/api/src/jobcopilot_api/agents/*/prompts.py` |
| 反幻觉 / 引用追溯 | quiz_generator 强约束 source_chunk_ids;answer_judge fidelity 层 | `docs/5-AGENT_DESIGN` |
| LLM-as-Judge | 三层评分(Coverage / Fidelity / Depth)+ 先证据后打分 | `docs/6-EVAL_PLAN` |
| 评测有效性 | Cohen's kappa 守门 ≥ 0.7;Judge 不引入 prompt 没要求的维度 | `docs/6-EVAL_PLAN` |
| 评测踩坑案例 | 公开记录评测翻车 + 重做经验 | `docs/9-LESSONS.md` |
| 结构化输出 | Pydantic Schema + JSON Schema retry | `docs/4-API_SPEC` / `apps/api/src/jobcopilot_api/llm/` |
| 流式 SSE | EventSource + node 级事件协议 | `docs/4-API_SPEC` |
| Prompt Cache 成本工程 | sha256 cache key + 异常降级 + Postgres 存储 | `docs/2-TECH_DESIGN` |
| 多 Provider 抽象 | LLMProvider Protocol + qwen / dummy 双实现 | `apps/api/src/jobcopilot_api/llm/` |
| 向量数据库工程 | pgvector HNSW + 归一化 + 多粒度 chunk | `docs/3-DATA_MODEL` |
| FastAPI / SQLAlchemy 2.x async | 全异步 IO | `docs/8-ENGINEERING` |
| Next.js 15 + RSC + TS | 服务端组件 + Tanstack Query | `apps/web/` |
| Docker Compose 一键部署 | postgres + api + web + caddy + Langfuse | `docker-compose.yml` |
| 工程纪律(CI / Lint) | ruff / mypy / typecheck / build 强制门槛 | `docs/8-ENGINEERING` |

---

## 当前状态

**阶段**:M2 — 聊天框主题 query → 全库 RAG → 出题 + Judge 三层评分

详见 [`docs/STATUS.md`](docs/STATUS.md)。

---

## 路线图

6 个阶段(详见 [`docs/7-ROADMAP.md`](docs/7-ROADMAP.md)):

```
M0  仓库改造 + 文档重写
M1  笔记入库(.md 上传 + Web 编辑器)+ chunker + 树形导航
M2  聊天框主题 query → 全库 RAG → 出题 + LLM Judge 三层评分
M2.1 InterviewCoachAgent → Agentic RAG 面试状态机 + 追问分支
M2.5 JD 累积上传 + 一键分析 + 学习路径
M3  弱点跟踪 + SR 队列 + 岗位类三源出题 + 简历诊断
```

---

## 项目演进

JobCopilot v1 是 "AI 改简历 + 投递追踪",做到 W8 时通过真实评测发现产品价值假设站不住(JD 同质化 + retrieval 错放在不增长的 profile)。
v2 重新定位为 "AI 面试陪练 + 笔记即题库",同一目标用户(1-3 年跳槽开发者),换更强痛点(面试焦虑)+ 更对的工程能力归宿(笔记 RAG / 知识点弱点跟踪 / 开放式答题 LLM Judge)。

工程踩坑沉淀在 [`docs/9-LESSONS.md`](docs/9-LESSONS.md),v1 → v2 的反思在 [`docs/STATUS.md`](docs/STATUS.md) 末尾。

---

## 许可证

[MIT](LICENSE)

---

## 致谢

- 阿里云百炼:Qwen3.6 与百炼平台
- LangGraph / FastAPI / Next.js / pgvector / SQLAlchemy / Tailwind 等开源社区
