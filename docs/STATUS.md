---
title: JobCopilot 项目当前进度(单一可信源)
owner: lemma42796
last_updated: 2026-05-08
purpose: 跨会话续作的状态快照。任何新会话从这里开始读。
---

# 当前阶段

**M0 重构准备 — 文档群已重写完毕(v2 + JD/简历扩展)**

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| M0 | 仓库改造 + 文档重写 | 🔄 8 份核心文档全部重写完;旧代码砍除待启动 |
| M1 | 笔记入库 + chunker + 树形导航 + Langfuse 起步 | ⏳ |
| M2 | 出题 + 答题 + Judge 三层评分 + Judge tool use + Trace 完整化 | ⏳ |
| M2.5 | JD 累积上传 + 一键分析 + 学习路径(独立有价值) | ⏳ |
| M3 | 弱点跟踪 + SR + 多轮追问 + 简历诊断(两方锚点严格) | ⏳ |

# M0 子任务进度

- ✅ README.md 重写
- ✅ docs/1-PRD.md 重写(扩展 JD 分析 + 简历诊断;目标用户全开)
- ✅ docs/2-TECH_DESIGN.md 重写(SDK / 模块 / 数据流)
- ✅ docs/3-DATA_MODEL.md 重写(+ jds / jd_analyses / resumes / resume_analyses 4 张新表)
- ✅ docs/4-API_SPEC.md 重写(+ JD 分析 + 简历诊断端点)
- ✅ docs/5-AGENT_DESIGN.md 重写(+ JdParser / JdAggregator / ResumeAdvisor + thinking 矩阵)
- ✅ docs/6-EVAL_PLAN.md 重写(+ jd_aggregator / resume_advisor 两个新 suite)
- ✅ docs/7-ROADMAP.md 重写(+ M2.5 + 重写 M3,砍语雀)
- ✅ docs/STATUS.md 重置(本文件)
- ✅ CLAUDE.md 文件导航更新
- ✅ docs/8-ENGINEERING.md 重写(仓库结构 / 工具链 / CI / 迁移 / 本地开发 / 部署 / Langfuse 实操)
- ✅ 砍旧代码 v1(110 文件 / -20.7K LoC):apps/api/agents 整目录 + services v1(7 个) + routers v1 + _deps + schemas 整目录 + prompts 整目录(6 个 v1 子目录) + models v1(7 个) + infra/{upload,pdf}.py + 配套 17 个 v1 单测 + 9 个 v1 集成测试 + apps/web/{jds,matches,profiles,resumes}/ + components/list/;沿用层留:llm/(全)/ infra/{db,embedder,llm,logging,prompts,request_id} / models/{base,llm_call,llm_response_cache,prompt_version} / services/tokenize / routers/health / web/{shell,ui,lib,layout,globals.css,page.tsx 文案改 v2}
- 🔄 新建 v2 模块骨架(详见 2-TECH §4.1):
  - ✅ models/{note, note_chunk, question, quiz_session, session_answer, knowledge_gap, jd, jd_analysis, resume, resume_analysis} 10 张 ORM 落地;__init__.py 更新 export(alembic env.py 通过 Base.metadata 看到全部)
  - ⏳ agents/{quiz_generator, answer_judge, jd_parser, jd_aggregator, resume_advisor, embedder, followup_orchestrator}
  - ⏳ services/{notes_service, chunk_service, search_service, quiz_service, answer_service, jd_service, resume_service, knowledge_gap_service}
  - ⏳ schemas/(Pydantic IO 校验 + SSE 事件 schema + agent IO)
  - ⏳ workers/embed_worker
- ✅ LLM SDK 切换:OpenAI Python SDK(走百炼 base_url)+ Langfuse OpenAI wrapper(`from langfuse.openai import AsyncOpenAI` 自动 instrument);settings 加 langfuse 三件套字段;main.py 启动镜像 LANGFUSE_*
- ✅ 新建 alembic 0016 migration(DROP v1 表 / ENUM + CREATE v2 ENUM × 3 + v2 表 × 10 + 8 个 updated_at 触发器);test_migrations.py EXPECTED_TABLES 同步换 v2;downgrade 不实做(NotImplementedError,DATA_MODEL §10)
- ⏳ docker-compose.yaml 加 langfuse + langfuse-db(详见 2-TECH §6.5)
- ✅ tag `v0.1-jobcopilot-v1` 锁 v1 末态(`390efe9` HEAD,含 v2 全套设计文档)
- ✅ M0 sanity check:百炼 OpenAI 兼容接口三件验证通过(thinking 4096 reasoning_tokens / tool_calls.function.name / 图像识别 prompt_tokens 2522)

# 当前 working tree

**待 commit**:v2 model 骨架(10 个 ORM 文件)+ models/__init__.py 重写 + alembic 0016 v2 schema + test_migrations.py 重写 + 本 STATUS.md。

**M0 schema 阶段完结**,DB 层从 v1(users / files / profiles / matches / resumes 等 12 张)切到 v2(笔记 SR 6 张 + JD 2 张 + 简历 2 张 + 沿用 LLM cost 3 张)。下一步进入"agent / service / schema 骨架"+ docker-compose 加 langfuse。

LLM SDK 切换 + sanity check 已落地;前面 commit 历史:
- `390efe9` 8-ENGINEERING 文档完工 + tag `v0.1-jobcopilot-v1`
- `82fe749` M0 砍 v1(110 删,但 5 个修改没 stage)
- `32af0db` 补漏 + LLM SDK 切换 langfuse

# 已锁定的关键决策(v2 起,完整版见各文档)

| 项 | 决策 | 备注 |
|----|------|------|
| 产品形态 | 学计算机的人的"找方向 + 笔记练习 + 简历诊断"全闭环 | 见 PRD §1-4 |
| 目标用户 | 学计算机的人(本科 / 研究生 / 1-3 年 / 5+ 年都包含,只排除非开发岗)| 见 PRD §2 |
| 笔记输入源 | M1: .md zip 上传 + Web 编辑器 | 不做 Notion / 飞书 / Obsidian / **语雀** sync |
| JD 输入源 | 文本粘贴 + 截图(Qwen 多模态)| 累积型,陆续上传;立即解析 |
| JD 单次分析上限 | 200 条 / hierarchical reduce | 见 PRD §9 + 7-ROADMAP M2.5 |
| 简历诊断 | 两方锚点严格(JD req + 简历位置);**永不输出改写文案** | 见 PRD §9 + 5-AGENT §7 |
| 题型 | 开放式 + 八股 两类 | 不做代码 / 系统设计 / 选择题 |
| 评分 | LLM-as-Judge 三层(Coverage / Fidelity / Depth)| 权重 SSoT 在 Python |
| LLM 模型 | qwen3.6-flash(多模态 + 文本一把抓);thinking 按 agent | 见 5-AGENT §2.1 |
| LLM SDK | OpenAI Python SDK 走百炼兼容接口;Langfuse 自动 instrument | 见 reference memory |
| LLM Provider | 阿里云百炼 | 沿用 v1 ADR |
| 数据存储 | Postgres 16(pgvector + tsvector)| 沿用 v1 |
| Tracing | Langfuse 自部署(docker compose 5 服务) | 见 2-TECH §6 |
| Tool use | AnswerJudge `lookup_in_notes_global` 反假阳性 | 见 5-AGENT §4.7 |
| UI 风格 | macOS 风,Tailwind 自己写 | 不引组件库 |
| Agent 编排 | MVP 单 Agent;M3 才用 LangGraph 多轮追问 | |
| 部署 | 本地 docker compose 5 服务(api / web / postgres / langfuse / langfuse-db);不做 SaaS(M4+) | |

风格规矩(中文为主 / 不估工时 / 不加 Co-Author / 测试由用户手动跑 / 大白话回答)见 `CLAUDE.md`。

# 永久约束累积

(M0 启动新内核,v1 永久约束已归档到 git history;v2 新约束在此区累积)

# 文档清单

| 文件 | 用途 |
|------|------|
| `1-PRD.md` ✅ | 产品需求:三阶段闭环 / 目标用户 / 用户故事 / NSM |
| `2-TECH_DESIGN.md` ✅ | 技术架构 / 模块分层 / 数据流 / 错误分层 / Langfuse |
| `3-DATA_MODEL.md` ✅ | 表 schema(notes / chunks / questions / sessions / answers / gaps / **jds / jd_analyses / resumes / resume_analyses**) |
| `4-API_SPEC.md` ✅ | REST + SSE 端点 + JD 分析 + 简历诊断 |
| `5-AGENT_DESIGN.md` ✅ | 7 个 agent prompt + thinking 矩阵 + tool use |
| `6-EVAL_PLAN.md` ✅ | 5 个 suite + Cohen's kappa + jd_aggregator + resume_advisor |
| `7-ROADMAP.md` ✅ | M0 / M1 / M2 / M2.5 / M3 节奏 + DoD |
| `8-ENGINEERING.md` ✅ | 仓库结构 / 工具链 / 代码规范 / Git / CI 6 条 / Alembic / 本地开发 / Docker / 测试 / Langfuse |
| `9-LESSONS.md` | 工程踩坑录(v1 沉淀 + v2 设计阶段沉淀,持续追加) |
| `STATUS.md` ✅ | 进度快照(本文件) |
| `runbook/` | 部署期写,目前空 |

# 下一步建议

1. commit v2 model + alembic 0016 + STATUS → push
2. 新建 v2 agent / service / schema / worker 骨架:
   - agents/{quiz_generator, answer_judge, jd_parser, jd_aggregator, resume_advisor, embedder, followup_orchestrator}(子目录 + agent.py + prompts.py 占位)
   - services/{notes_service, chunk_service, search_service, quiz_service, answer_service, jd_service, resume_service, knowledge_gap_service}(空函数 + 类型 stub)
   - schemas/(Pydantic IO + SSE 事件 schema + agents/ IO schema)
   - workers/embed_worker(后台轮询 embedding=NULL chunks 批量算)
   - main.py lifespan 挂 embed_worker 启动钩子
3. docker-compose.yaml 加 langfuse + langfuse-db 两服务(端口 3001 / 5433);api 服务 environment 加 JOBCOPILOT_LANGFUSE_*
4. M0 完成 → tag `v0.2-m0-end` → 开 M1(笔记入库 + chunker + 树形导航 + Langfuse 起步)

# v1 历史

JobCopilot v1(M0-M3 W8)做的是 "AI 改简历 + 投递追踪"。
W8 真实评测发现产品价值假设站不住:JD 同质化导致定制简历价值低 + retrieval 错放在不增长的 profile。
v2 在同 repo 重定位:目标用户从 1-3 年开发者扩展到"学计算机的人"全谱;产品定位从"AI 改简历"转向"找方向(JD 分析)+ 笔记练习(出题 SR)+ 简历诊断(两方锚点)"三阶段闭环;工程能力(笔记 RAG / Cohen's kappa / hierarchical map-reduce / tool use 反幻觉 / Langfuse 可观测)真正落到对的对象上。

git tag `v0.1-jobcopilot-v1` 将打在 v1 末态(M0 末批量改造前最后一个 commit)。
v1 设计文档 / 切片归档 / ADR / 评测套件全部从仓库删除(git history 永远在,不影响档案)。
v1 工程踩坑沉淀保留在 `docs/9-LESSONS.md`(对作品集叙事价值大)。
