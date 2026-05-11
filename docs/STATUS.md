---
title: JobCopilot 项目当前进度(单一可信源)
owner: lemma42796
last_updated: 2026-05-12
purpose: 跨会话续作的短状态快照。只放接力必要信息,细节指向其他文档。
---

# STATUS 维护规则(防膨胀)

`STATUS.md` 是**短接力页**,不是第二份 PRD / TECH / ROADMAP。

- **长度上限**:目标控制在 150 行以内;超过时先删历史细节,不追加流水账。
- **只写当前事实**:当前阶段 / working tree / 下一刀 / 已锁定决策 / 永久约束摘要。
- **历史不展开**:完成过的里程碑只保留一行 + tag / commit;详细历史看 git log / release tag / `docs/9-LESSONS.md`。
- **约束不写长文**:永久约束每条最多 2 行;需要细节时指向对应文档章节。
- **下一步不列长计划**:只保留 1 个推荐下一刀 + 最多 5 个备选子任务;完整 DoD 看 `docs/7-ROADMAP.md`。
- **文档不复制**:PRD / TECH / AGENT / EVAL 的 schema、prompt、接口细节不搬进本文档。
- **更新时机**:用户问进度 / 续作 / 里程碑完成时更新;平时不要把每次小改都写进来。

# 当前快照

当前阶段:**M2.1 — `InterviewCoachAgent` Agentic RAG 面试状态机 + 追问分支**。

最新状态:

- **M2 已由用户确认完成**:聊天框主题 query → 全库 RAG → 出题 → 答题 → Judge 三层评分 → session 恢复已跑通。
- 本次收口前 `git status --short` 为空;本次只改 `docs/STATUS.md`,等待用户决定是否 commit / push / tag。
- Context Cache 已验证 provider-side 命中,但因 5 分钟 TTL 不适合当前一次性答题流,已默认关闭显式 `cache_control`;后续多轮讨论面试题时再打开。
- 最新功能提交主题:`feat: summarize quiz progress UI`;push 后 working tree 应清空。
- M2 retrieval quiz pipeline 代码已提交:`103d882 feat: add m2 retrieval quiz pipeline`。
- M2.1 Agentic RAG 文档已提交:`fd892fa docs: add agentic interview coach roadmap`。
- M2 AnswerJudge 初版已落地:三层 evidence prompt / agent / submit SSE / Python 算分 / fabricated 锁顶。
- 真实验收:用户已跑 `/quiz` 主题 `Langfuse Prompt 版本管理`,session #4 出题 / 保存 / Judge 评分 / `/quiz?session=4` 恢复通过。
- GitHub Actions 已改为**手动触发**(`workflow_dispatch`),push 不再自动跑 lint / tests / build。
- 本地开发形态改为**Docker Postgres + 本机 API**;避免 api 容器 rebuild 与 compose key 映射坑。

# 里程碑状态

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| M0 | 仓库改造 + 文档重写 + v2 schema + 模块骨架 | ✅ `v0.2-m0-end` |
| M1 | 笔记入库 + chunker + 树形导航 + Langfuse 起步 | ✅ `v0.3-m1-end` |
| M2 | 主题 query → 全库 RAG → 出题 + Judge 三层评分 | ✅ 待 tag `v0.4-m2-end` |
| M2.1 | `InterviewCoachAgent`:Agentic RAG 面试状态机 + 追问分支 | ⏳ 当前 |
| M2.5 | JD 累积上传 + 一键分析 + 学习路径 | ⏳ |
| M3 | 弱点跟踪 + SR + 岗位类三源出题 + 简历诊断 | ⏳ |

# 当前已落地

- **M2 schema / retrieval / quiz pipeline 初版**:0017 migration、quiz router、query rewriter、retrieval pipeline、reranker、quiz service 编排已入库。
- **M2 AnswerJudge 初版**:`answer_judge` schema / prompt / agent、`answer_service.submit_session_sse`、三层分 + session 汇总、答题草稿 / abandon 端点已入库。
- **M2 quiz/session UI 已落地**:`/quiz` 支持主题出题、答题、草稿保存、提交评分、结构化 evidence、样例模式、最近练习与 session 恢复。
- **百炼 Context Cache 代码已接入但默认关闭**:保留稳定 chunks 前缀渲染与审计字段;后续多轮面试讨论再开启显式 cache。
- **M2.1 Agentic RAG 方向锁定**:`InterviewCoachAgent` 不做泛化多 Agent,只做面试状态机:检索 → 出题 → 等答 → 评分 → 决策 → 追问 / 总结。
- **CI 策略调整**:所有 GitHub workflow 改为手动触发,避免 push 自动跑测试和邮件通知。

# 下一刀

等待用户指示再开工。推荐下一刀:

1. **M2.1 状态机骨架**:新增 `InterviewCoachAgent` session root orchestrator,先串 `retrieve_context → generate_question → wait_user_answer → judge_answer`。

备选:

- 接 `decide_next_action`:按 coverage / fabricated / depth evidence 决定追问或下一题。
- 接 `generate_followup`:单题最多 1 轮追问,不做无限循环。
- 做中途退出恢复:从 `wait_user_answer` 状态恢复真实 session。
- 补 Langfuse trace 颗粒度:`retrieve → generate → judge → decide → followup → summarize`。
- 预留 `update_knowledge_gap(...)` 接口,真实 SR 队列放 M3。

# 已锁定关键决策

| 项 | 决策 |
|----|------|
| 出题入口 | 只走聊天框 query;笔记面板只查看 / 编辑 / 上传 / 导航,不触发出题。 |
| M2 query | 仅主题类 query;岗位类与空 query 放 M3。 |
| RAG pipeline | `query_rewriter → hybrid + RRF → reranker → parent-doc 扩展` + 0 命中守门。 |
| 0 命中 | 命中 chunks < 3 起步直接报"笔记里没这主题",不兜底让 LLM 编。 |
| Reranker | 百炼 `qwen3-rerank`(`/compatible-api/v1/reranks`);本地 fallback 暂不做。 |
| M2.1 Agent | `InterviewCoachAgent` 状态机;高级感来自状态 / 工具 / 分支 / 记忆 / 评测 / 恢复,不是多 Agent 数量。 |
| 简历 | 全库单条记录,不做简历库 / 多份切换。 |
| 岗位类 query | M3 三源融合:笔记 RAG + 那一份简历 + 用户选定 JD 子集职责/要求。 |
| 评分 | LLM-as-Judge 给 evidence;总分权重在 Python,不让 LLM 算。 |
| 测试 / CI | 用户手动跑验证;GitHub Actions 只手动触发。 |

# 永久约束摘要

- **[来自 M1] 不接 zip 笔记上传**:笔记走 File System Access API / JSON 批量导入;不做 Notion / 飞书 / Obsidian / 语雀 sync。
- **[来自 M1] 不新增测试代码**:用户明确所有测试 / 自动化验证手动跑;已有测试不删,新切片不主动写测试。
- **[来自 M1] 负载按字数 / token 衡量**:笔记 / dataset / dogfood 压力看总字数,不看篇数。
- **[来自 M1] Langfuse SDK 锁 `<3.0`**:server 锁 v2;不能单独升 SDK 3.x。
- **[来自 M1] `LANGFUSE_*` env mirror 要早于 routers / agents / llm import**:否则 SDK 进入 noop。
- **[来自 M1] embeddings / rerank 不自动 instrument**:Langfuse 需要手动 `generation()` 包成功 / 失败路径。
- **[来自 M1] 评测指标挂到能力首次真实消费的里程碑**:例如 hybrid recall 挂 M2,不挂 M1 service 就绪阶段。
- **[来自 M2] 聊天框 query 是唯一出题入口**:不要回退到节点点击出题。
- **[来自 M2] 简历单条记录**:岗位类 query 只拼当前简历 + JD 子集,不做多简历 UX。
- **[来自 M2] 岗位类 query 必须三源融合**:不要把岗位类降级成普通主题类 query。
- **[来自 M2] Context Cache 不是会话记忆**:请求仍需带必要上下文;cache 只优化重复公共前缀的 provider 侧计算 / 计费。
- **[来自 M2] Context Cache 当前默认关闭**:一次性答题流不依赖 5 分钟 TTL;等 M2.1 多轮面试讨论再开启显式 cache。
- **[来自 M2] 本地开发优先 Docker Postgres + 本机 API**:api 容器需额外处理 `DASHSCOPE_API_KEY` 映射,日常避免走全 compose。
- **[来自 M2.1] Agent 不做炫技多 Agent**:只做与面试陪练闭环直接相关的状态机、工具、分支、恢复、评测。

# 文档导航

| 文件 | 用途 |
|------|------|
| `docs/1-PRD.md` | 产品需求 / 用户故事 / 边界 |
| `docs/2-TECH_DESIGN.md` | 架构 / 模块分层 / 数据流 |
| `docs/3-DATA_MODEL.md` | 表结构 / JSONB schema |
| `docs/4-API_SPEC.md` | REST + SSE 契约 |
| `docs/5-AGENT_DESIGN.md` | Agent prompt / 输出契约 / M2.1 编排 |
| `docs/6-EVAL_PLAN.md` | 评测套件 / kappa / branch accuracy |
| `docs/7-ROADMAP.md` | 里程碑范围与 DoD |
| `docs/8-ENGINEERING.md` | 工程规范 / 本地开发 / CI |
| `docs/9-LESSONS.md` | v1/v2 踩坑沉淀 |
| `docs/STATUS.md` | 当前短接力页(本文档) |

# 历史定位

JobCopilot v1 是"AI 改简历 + 投递追踪",已用 tag `v0.1-jobcopilot-v1` 留档。v2 转向"JD 找方向 + 笔记 RAG 面试陪练 + 简历诊断"。v1 失败复盘与工程教训保留在 `docs/9-LESSONS.md`。

# 不在本文档范围

- 详细产品定义 → `docs/1-PRD.md`
- 技术架构与代码目录 → `docs/2-TECH_DESIGN.md`
- Agent prompt / schema 全文 → `docs/5-AGENT_DESIGN.md`
- 评测设计细节 → `docs/6-EVAL_PLAN.md`
- 完整里程碑 DoD → `docs/7-ROADMAP.md`
