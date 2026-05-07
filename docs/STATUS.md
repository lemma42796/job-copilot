---
title: JobCopilot 项目当前进度(单一可信源)
owner: lemma42796
last_updated: 2026-05-08
purpose: 跨会话续作的状态快照。任何新会话从这里开始读。
---

# 当前阶段

**M0 重构准备 — 文档重写中**

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| M0 | 仓库改造 + 文档重写 | 🔄 5 份核心文档已重写 / 6 份深度设计文档待重写 / 旧代码砍除待启动 |
| M1 | 笔记入库 + chunker + 树形导航 | ⏳ |
| M2 | 出题 + 答题 + Judge 三层评分 | ⏳ |
| M3 | 弱点跟踪 + SR + 多轮追问 + 语雀同步 | ⏳ |

# M0 子任务进度

- ✅ README.md 重写
- ✅ docs/1-PRD.md 重写
- ✅ docs/7-ROADMAP.md 重写
- ✅ docs/STATUS.md 重置
- ✅ CLAUDE.md 文件导航更新
- ⏳ docs/2-TECH_DESIGN.md 重写
- ⏳ docs/3-DATA_MODEL.md 重写
- ⏳ docs/4-API_SPEC.md 重写
- ⏳ docs/5-AGENT_DESIGN.md 重写
- ⏳ docs/6-EVAL_PLAN.md 重写
- ⏳ docs/8-ENGINEERING.md 重写
- ⏳ 砍旧代码 v1:apps/api/agents/{jd_parser,profile_parser,match_analyst,resume_planner,resume_drafter,resume_reviewer}/、对应 service/router/model/scripts、apps/web 旧页面
- ⏳ 新建 v2 模块骨架:agents/{quiz_generator,answer_judge}/、services/{notes_service,quiz_service,answer_service}/、models/{note,question,session,answer,knowledge_gap}.py
- ⏳ alembic 新 migration(v2 表 + 砍 v1 表)
- ⏳ tag `v0.1-jobcopilot-v1` 锁 v1 末态

# 当前 working tree

**待 commit**:5 份核心文档重写(README + 1-PRD + 7-ROADMAP + STATUS + CLAUDE) + 待批量删 v1 旧文档(2-TECH_DESIGN / 3-DATA_MODEL / 4-API_SPEC / 5-AGENT_DESIGN / 6-EVAL_PLAN / 8-ENGINEERING)+ slices/ + adr/ + evals/{suites,specs,reports,fixtures,raw,tmp,scripts}/。

# 已锁定的关键决策(v2)

| 项 | 决策 | 备注 |
|----|------|------|
| 产品形态 | 笔记即题库的 AI 面试陪练 | 见 PRD §1-3 |
| 目标用户 | 1-3 年跳槽开发者(有写笔记习惯) | |
| 笔记输入源 | M1: .md zip 上传 + Web 编辑器;M3: 语雀 OAuth | 明确不做 Obsidian / Notion / 飞书 sync |
| 题型 | 开放式 + 八股 两类 | 不做代码 / 系统设计 / 选择题 |
| 评分 | LLM-as-Judge 三层(Coverage / Fidelity / Depth)| 权重 SSoT 在 Python |
| LLM 模型 | 全 qwen3.6-flash + thinking on | 不区分 plus/flash |
| LLM Provider | 阿里云百炼 Qwen3.6 | 沿用 v1 ADR |
| 数据存储 | Postgres 16(pgvector + tsvector) | 沿用 v1 |
| UI 风格 | macOS 风,Tailwind 自己写 | 不引组件库 |
| Agent 编排 | MVP 单 Agent;M3 才用 LangGraph 多轮追问 | |
| 部署 | 本地 docker compose;不做 SaaS(M4+) | |

风格规矩(中文为主 / 不估工时 / 不加 Co-Author / 测试由用户手动跑 / 大白话回答)见 `CLAUDE.md`。

# 永久约束累积

(M0 启动新内核,v1 永久约束已归档到 git history;v2 新约束在此区累积)

# 文档清单

| 文件 | 用途 |
|------|------|
| `1-PRD.md` ✅ | 产品需求:目标用户、核心闭环、NSM、不在范围 |
| `2-TECH_DESIGN.md` ⏳ | 技术设计:架构、模块分层 |
| `3-DATA_MODEL.md` ⏳ | 表 schema(notes / chunks / questions / sessions / answers / knowledge_gap) |
| `4-API_SPEC.md` ⏳ | REST + SSE 端点 |
| `5-AGENT_DESIGN.md` ⏳ | QuizGenerator + AnswerJudge prompt 全文 |
| `6-EVAL_PLAN.md` ⏳ | answer_judge / quiz_generate suite + Cohen's kappa |
| `7-ROADMAP.md` ✅ | M0-M3 节奏 + 退出标准 |
| `8-ENGINEERING.md` ⏳ | 仓库结构 / 规范 / CI |
| `9-LESSONS.md` | 工程踩坑录(v1 沉淀,持续追加) |
| `STATUS.md` ✅ | 进度快照(本文件) |
| `runbook/` | 部署期写,目前空 |

# v1 历史

JobCopilot v1(M0-M3 W8)做的是 "AI 改简历 + 投递追踪"。
W8 真实评测发现产品价值假设站不住:JD 同质化导致定制简历价值低 + retrieval 错放在不增长的 profile。
v2 在同 repo 重定位:同目标用户(1-3 年跳槽开发者),换更强痛点(面试焦虑),工程能力(笔记 RAG / 知识点弱点跟踪 / 开放式答题 LLM Judge)真正落到对的对象上。

git tag `v0.1-jobcopilot-v1` 将打在 v1 末态(M0 末批量改造前最后一个 commit)。
v1 设计文档 / 切片归档 / ADR / 评测套件全部从仓库删除(git history 永远在,不影响档案)。
v1 工程踩坑沉淀保留在 `docs/9-LESSONS.md`(对作品集叙事价值大)。
