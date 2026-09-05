---
title: JobCopilot 项目最新状态
owner: lemma42796
last_updated: 2026-09-05
purpose: 只记录项目当前已经实现、已经验证的最新事实。
---

# 当前状态

- 产品功能链路已完成,当前形态为单用户、本地优先、单 API 进程。
- 下一阶段目标是并发改造,方案见 `CONCURRENCY_PLAN.md`;方案尚未开始实施,阶段编号已按实测上游配额重排。
- 当前分支为 `main`。

# 当前已实现能力

- Markdown 笔记导入、树形导航、编辑、heading-aware chunk 和异步 embedding。
- 主题 query → hybrid RAG → QuizGenerator → AnswerJudge 三层评分。
- InterviewCoach 多轮纠偏、补答重评、教练追问、session 恢复和整场总结。
- 文本 JD 入库、批量技术栈聚合、知识库覆盖矩阵、学习路径、quiz topics 和历史报告。
- `/jds` 报告筛选、覆盖证据、“优先补齐”清单和 topic 批量进入 `/quiz`。
- JD 分析执行与 SSE 观察已解耦:断线不取消任务,可按稳定 `analysis_id` 恢复订阅,API 重启后会重新启动 `in_progress` 分析。
- JD 分析和文本 LLM 调用分别有进程内并发闸门;普通 Agent 与 AnswerJudge 工具调用链共享 LLM 并发额度。
- LLM 与 JD 分析生命周期写结构化日志,包含功能、关联 id、延迟、Token、成本、缓存和成功 / 失败终态。

# 当前边界

- 单 API 进程 MVP:并发闸门、任务恢复和进度缓冲都只在进程内生效,多实例可能重复领取同一 `in_progress` 记录。
- 后台 worker 跑在 API 进程内,尚未拆出;没有真队列、幂等键、lease / heartbeat 或待执行容量上限。`embed_worker` 领取 chunk 的查询无 `FOR UPDATE SKIP LOCKED`,多副本会重复计费。
- 无限流、无熔断、无成本上限;过载行为未定义。上游 qwen3.6-flash 配额实测 TPM 10,000,000 / RPM 30,000(该模型独享,rerank 与 embedding 各有独立配额池),相对当前 `llm_max_concurrency=4` 余量约 83 倍,不会通过限流帮我们刹车。
- 数据库连接池与 uvicorn 进程数均为默认值,未按并发目标显式配置。四个 SSE 接口(出题、答题回合、结束总结、提交评分)全程持有数据库 session。
- `services/reranker.py` 自建 `httpx.AsyncClient` 直接请求上游,不经过 `llm/client.py`,不受 `llm_max_concurrency` 约束。
- `agents/answer_judge` 是 tool-calling 循环,`MAX_JUDGE_ROUNDS = 14`,单次答案提交最多触发 14 次 LLM 调用,闸门在循环体内逐次获取释放。
- 无用户体系:`models/` 下无 user 表,笔记 / quiz_session / jd / resume 等业务表均无 `user_id` 列,无认证,`notes_fs_root` 为单一根目录。当前形态下多个使用者会读写同一份数据。
- 无余额与扣费:三条计费链路的成本均已能计算(`llm/pricing.py`、`services/reranker.py`、`llm/embedders.py`),但 rerank 与 embedding 未落 `llm_calls` 表;`llm_calls.user_id` 为可空字段且无对应用户表。
- 单用户本地 dogfood 结果不能外推为多用户、线上高可用或大规模并发。
- `CONCURRENCY_PLAN.md` 中除上游配额外的数字均为读码推算,未经压测,不得对外引用。

# 相关入口

- 当前与未完成任务 → `TASKS.md`
- 技术架构 → `TECH_DESIGN.md`
- 并发改造方案 → `CONCURRENCY_PLAN.md`
- 评测规范与最新证据 → `../evals/EVAL_GUIDE.md`
