# JobCopilot

> **AI 求职助手**——给 1-3 年跳槽开发者的 LLM 应用工程化代表作。
>
> 一行 `docker compose up` 启动,本地优先,数据不出机器。

[![Status](https://img.shields.io/badge/status-WIP%20M0-orange)](docs/STATUS.md)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Provider](https://img.shields.io/badge/LLM-Qwen3.6%20%40%20DashScope-1f7ae0)](docs/adr/0003-switch-to-qwen.md)

---

## 这是什么

JobCopilot 把"找一份更好工作"拆成可被 LLM Agent 处理的一连串任务:

1. **结构化 JD**:粘贴文本 / 上传 PDF / 截图,系统抽 9 个关键字段
2. **建立个人能力档案**:简历→结构化档案→多粒度向量索引
3. **匹配分析**:对一份 JD 给评分、命中技能、能力差距、改进建议(每条结论可追溯到简历原文)
4. **定制简历**:基于档案 RAG + Reviewer 反幻觉,生成针对该 JD 的 markdown / PDF 简历
5. **面试模拟**:多轮技术面试 + 追问 + 评分 + reference answer
6. **投递追踪**:看板管理状态,沉淀面试邀约率(产品 NSM)

---

## 一图概览

```
┌────────────────────────────────────────────────────┐
│  apps/web (Next.js 15)  对话/看板/简历编辑/面试    │
└────────────────────┬───────────────────────────────┘
                     │ REST + SSE
┌────────────────────┴───────────────────────────────┐
│  apps/api (FastAPI)                                 │
│  ├─ services/  业务编排                             │
│  ├─ agents/    JDParser / ProfileParser / Match    │
│  │             ResumeDrafter+Reviewer / Interviewer │
│  ├─ llm/       Tier 路由 + Prompt Cache + 重试     │
│  └─ infra/     pgvector / pgmq / Langfuse trace    │
└────────────────────┬───────────────────────────────┘
                     │
              ┌──────┴──────┐
              │  Postgres 16 │  业务表 + 向量 + 队列 + 文件 bytea
              └──────┬──────┘
                     │
              ┌──────┴───────────┐
              │ 阿里云百炼 API    │ Qwen3.6-Flash / Plus / VL
              └──────────────────┘
```

详见 [`docs/2-TECH_DESIGN.md`](docs/2-TECH_DESIGN.md)。

---

## 核心特性(工程化亮点)

| 主题 | 实现要点 | 详细 |
|------|---------|------|
| **Agent 编排** | LangGraph 状态机(简历定制 + 面试模拟),其余场景单 Agent | `5-AGENT_DESIGN` |
| **RAG** | pgvector + tsvector + RRF + Reranker(`gte-rerank-v2`)| `5-AGENT_DESIGN §6` |
| **反幻觉** | ResumeReviewer 对抗集 fabrication recall ≥ 0.95 | `6-EVAL_PLAN §8` |
| **评测体系** | 200 条评测集 + LLM-as-Judge + GitHub Actions 不退化 | `6-EVAL_PLAN` |
| **成本工程** | Tier 路由 + Prompt Cache(命中率 ≥ 70% 简历定制场景)| `2-TECH_DESIGN §4.3` |
| **多模态** | `qwen3.6-flash` 原生多模态,直接 OCR + 抽取一步完成 | `5-AGENT_DESIGN §3` |
| **MCP Server** | 5 工具 + 1 资源,接 Claude Desktop | `7-ROADMAP M5` |
| **可观测** | Langfuse 自托管 trace,每次响应附 `X-Langfuse-Trace-Id` | `2-TECH_DESIGN §6` |
| **本地优先** | docker compose 一键起 + BYOK,数据不出机器 | `1-PRD §5.3` |

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
#   DASHSCOPE_API_KEY=sk-xxx
docker compose up -d
```

3-5 分钟后:

```
Web:  http://localhost:3000
API:  http://localhost:8000/v1/health
Docs: http://localhost:8000/v1/docs   # 开发模式
```

### 启用可观测面板(可选)

```bash
docker compose --profile observability up -d
# Langfuse: http://localhost:3030
```

### 切回 DeepSeek(¥15 阿里云额度耗尽后)

替换 `.env` 中:

```
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx
```

(实现见 ADR-0001 的回切方案)

---

## 文档导航

| 文档 | 内容 |
|------|------|
| [`docs/STATUS.md`](docs/STATUS.md) | 当前进度的单一可信源 — **新会话从这里开始** |
| [`docs/1-PRD.md`](docs/1-PRD.md) | 产品需求:用户画像、用户故事、NFR、NSM |
| [`docs/2-TECH_DESIGN.md`](docs/2-TECH_DESIGN.md) | 技术设计:架构、模块分层、LLM 调用层、可观测 |
| [`docs/3-DATA_MODEL.md`](docs/3-DATA_MODEL.md) | 数据模型:全部表 schema、索引、生命周期 |
| [`docs/4-API_SPEC.md`](docs/4-API_SPEC.md) | API 规范:REST + SSE 端点、错误码、限流、流式协议 |
| [`docs/5-AGENT_DESIGN.md`](docs/5-AGENT_DESIGN.md) | Agent 设计:每个 Agent 的输入/输出/Prompt/失败处理 |
| [`docs/6-EVAL_PLAN.md`](docs/6-EVAL_PLAN.md) | 评测计划:8 个 suite、200 条样本、CI 回归、Bad Case 闭环 |
| [`docs/7-ROADMAP.md`](docs/7-ROADMAP.md) | 16 周里程碑:M0-M6 节奏与退出标准 |
| [`docs/8-ENGINEERING.md`](docs/8-ENGINEERING.md) | 工程规范:仓库结构、Python+TS 规范、Git 工作流、CI/CD |
| [`docs/adr/`](docs/adr) | 关键架构决策(0001:DeepSeek (Superseded);0002:Postgres 一把梭;0003:Qwen3.6) |

---

## JD 考点对照表

把"招聘 JD 里高频出现的能力点"映射到本项目的具体实现。求职复盘用。

| 招聘考点 | 本项目证据 | 文档 / 代码定位 |
|---------|-----------|----------------|
| LLM 应用工程化端到端落地 | 8 份设计文档 + ADR + 16 周路线图 | `docs/` 全部 |
| Agent 编排(LangGraph / 状态机) | 简历定制 5 节点 + 面试模拟 7 节点 | `5-AGENT_DESIGN §7,§8` / `apps/api/agents/` |
| RAG(混合检索 / 重排)| Hybrid: pgvector + tsvector + RRF + Reranker | `5-AGENT_DESIGN §6` / `apps/api/services/match_service.py` |
| Prompt 工程 + 版本管理 | Prompt 即代码,Jinja2 模板 + `prompt_versions` 表 | `5-AGENT_DESIGN §10` / `apps/api/agents/prompts/` |
| 反幻觉 / 引用追溯 | ResumeReviewer + 引用 chunk_id 强约束 | `5-AGENT_DESIGN §1.2,§7.3.4` |
| 结构化输出 / Function Calling | Pydantic Schema + JSON Schema 强约束 | `4-API_SPEC §1.2` / `5-AGENT_DESIGN §1.4` |
| 评测体系 / Eval-as-Code | 200 条评测集 + promptfoo + CI 不退化 | `6-EVAL_PLAN` / `evals/` |
| LLM-as-Judge | qwen3.6-plus 思考开,Cohen's kappa ≥ 0.7 季度复审 | `6-EVAL_PLAN §6.3,§13.2` |
| 多模态 LLM(OCR / 视觉)| `qwen3.6-flash` 原生多模态,一步出结构化 JD | `5-AGENT_DESIGN §3.4` |
| 流式协议 / 长任务 SSE | EventSource + node_started/token/result/done 事件协议 | `4-API_SPEC §5` |
| Prompt Cache 成本工程 | Tier 路由 + 前缀稳定布局,缓存命中 ≥ 70% | `2-TECH_DESIGN §4.3` |
| 多 Provider 抽象层 | LLMProvider Protocol + Qwen / DeepSeek 双实现 | `2-TECH_DESIGN §4` / `apps/api/llm/` |
| 可观测性(Tracing)| Langfuse 自托管,每请求 X-Langfuse-Trace-Id | `2-TECH_DESIGN §6` |
| 向量数据库工程实践 | pgvector HNSW + 归一化 + 多粒度 chunk | `3-DATA_MODEL §3.8` / `adr/0002` |
| 任务队列 / 异步编排 | pgmq(无 Redis,见 ADR-0002)| `3-DATA_MODEL §3.19` |
| MCP 协议接入 | MCP Server,5 tool + 1 resource,接 Claude Desktop | `7-ROADMAP M5` / `mcp/` |
| 浏览器扩展开发 | Chrome MV3,内容脚本 + 智能粘贴 | `7-ROADMAP M5` / `extension/` |
| FastAPI / SQLAlchemy 2.x async | 全异步 IO,asyncio.to_thread 隔离 CPU 重活 | `8-ENGINEERING §2.4` |
| Next.js 15 App Router + RSC | 服务端组件 + Tanstack Query | `apps/web/` |
| TypeScript 类型一致性 | OpenAPI → datamodel-code-generator → TS 类型,CI 卡口 | `8-ENGINEERING §5.2` |
| Docker Compose 一键部署 | postgres + api + web + caddy + (langfuse) | `docker-compose.yml` |
| ADR 决策文化 | 三份 ADR(provider / 存储 / 切换),含 supersede 关系 | `docs/adr/` |
| 工程纪律(CI / Lint / 覆盖率)| 8 个 GH workflow,8 项强制门槛 | `8-ENGINEERING §5,§6` |

---

## 当前状态

详见 [`docs/STATUS.md`](docs/STATUS.md)。摘要:

- **阶段**:文档撰写完成,准备进入编码阶段(M0 仓库骨架)
- **LLM Provider**:阿里云百炼 Qwen3.6([ADR-0003](docs/adr/0003-switch-to-qwen.md));DeepSeek 备选
- **下一步**:`git init` + 按 [7-ROADMAP M0](docs/7-ROADMAP.md#3-m0--仓库骨架week-1) 搭骨架

---

## 路线图

16 周(M0-M6)。详见 [`docs/7-ROADMAP.md`](docs/7-ROADMAP.md)。

```
W1   M0  仓库骨架
W2-3 M1  数据入口贯通(JD / 个人档案 / chunks)
W4-6 M2  匹配分析 + 评测体系
W7-10 M3 简历定制 GA + v0.5 内测
W11-13 M4 面试模拟 + 投递追踪
W14-15 M5 浏览器扩展 + MCP Server
W16   M6 v1.0 公开发布
```

---

## 贡献与反馈

当前(M0 之前)处于单人作者主导阶段,主要欢迎:

- **试用反馈**:M3 v0.5(预计 W10)开放招募内测,关注本仓库
- **Bug / Idea**:GitHub Issues
- **PR**:小修改(typo / 文档更清晰)直接 PR;功能改动请先开 Discussion 对齐

---

## 许可证

[MIT](LICENSE)

第三方组件许可证清单见 `docs/THIRD_PARTY_NOTICES.md`(M6 完整版)。

---

## 致谢

- 阿里云百炼:Qwen3.6 与百炼平台
- DeepSeek:V4 系列(备选 Provider,见 [ADR-0001](docs/adr/0001-only-deepseek.md))
- LangGraph / FastAPI / Next.js / pgvector / Langfuse / promptfoo / awesome-cv 等开源社区
