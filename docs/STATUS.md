---
title: JobCopilot 项目当前进度(单一可信源)
owner: lemma42796
last_updated: 2026-05-16
purpose: 跨会话续作的短状态快照。只放接力必要信息,细节指向其他文档。
---

# STATUS 维护规则(防膨胀)

`STATUS.md` 是**短接力页**,不是第二份 PRD / TECH / ROADMAP。

- **长度上限**:目标控制在 200 行以内;超过时先压缩历史实验细节,不追加流水账。
- **只写当前事实**:当前阶段 / working tree / 下一刀 / 已锁定决策 / 永久约束摘要。
- **历史不展开**:完成过的里程碑只保留一行 + tag / commit;详细历史看 git log / release tag / `docs/9-LESSONS.md`。
- **约束不写长文**:永久约束每条最多 2 行;需要细节时指向对应文档章节。
- **下一步不列长计划**:只保留 1 个推荐下一刀 + 最多 5 个备选子任务;完整 DoD 看 `docs/7-ROADMAP.md`。
- **文档不复制**:PRD / TECH / AGENT / EVAL 的 schema、prompt、接口细节不搬进本文档。
- **更新时机**:用户问进度 / 续作 / 里程碑完成时更新;平时不要把每次小改都写进来。

# 当前快照

当前阶段:**M2.1 — `InterviewCoachAgent` Agentic RAG 面试状态机 + 多轮纠偏分支**。

最新状态:

- 本地 dogfood 笔记库仍为 `test-notes/llm-notes` **119 篇 / 532,999 字符**;Docker Postgres 对齐 active notes 119 / chunks 2,090 / embedded 2,090 / pending 0。
- `evals/suites/hybrid_search/` smoke 资产已进入 chunk-level 口径:15 篇 fixture + 12 条全库 chunk/anchor 标签;`direct_evidence_chunk_ids` 只放可直接回答 query 的 chunk。
- `eval_hybrid_search_note_smoke.py` 已支持 live report + `.trace.jsonl` + `--score-trace` 离线重算;只改标签 / 打分口径时复用 trace,不重复调用 query rewrite / rerank。
- 评测指标分母已澄清并落代码:`candidate_recall@15 / selected_recall@10 / mrr@10 / final_context_recall` 对有 direct evidence 的非 0 命中样本做 macro average;expected zero-hit 排除,unexpected zero-hit 的 final recall 记 0。top50 继续保留为诊断窗口 / rerank input,不再作为正式 candidate 指标名。
- 0 命中分支已保存 `candidate_chunk_ids`:即使 `predicted_zero_hit=true`,也能检查系统是否召回了 1-2 个正确证据但未达出题阈值。
- 新标签基线已按用户指令跑过:`evals/reports/hybrid-search-note-smoke-20260514-063244.md` + `.trace.jsonl`;12 cases 通过 6/12,`candidate_recall@50` 95.00%,`rerank_recall@10` 73.33%,`mrr@10` 70.83%,`final_context_recall` 73.33%,`final_context_precision` 6.63%,zero-hit 0/2,成本 ¥0.195307。
- reranker metadata 已完成一轮 A/B:content-only 基线 `20260514-063244` 为 6/12、`rerank_recall@10` 73.33%;metadata 前置 `20260514-084642` 为 7/12、67.50%,不是净提升;当前保留代码为 direct-evidence instruct + `content + weak_source_context` 后置格式。
- 当前保留版 smoke `hybrid-search-note-smoke-20260514-092218.md`:8/12,`candidate_recall@50` 95.00%,`rerank_recall@10` 69.17%,`mrr@10` 59.58%,`final_context_recall` 72.50%,zero-hit 0/2,成本 ¥0.251666;比前置 metadata 稳,但不是最终达标方案。
- 本地 intent × chunk-type 降权已试跑但**未保留**:初版 `20260514-111912` 8/12、`rerank_recall@10` 63.33%;修正 hard-negative 误判后 `20260514-112042` 8/12、65.83%,hard-negative intrusion 从 4/12 降到 3/12、MRR 到 75.33%,但 direct evidence 覆盖下降,已回滚到弱 source context 方案。
- smoke 脚本新增诊断/A-B 开关:`--rerank-mode provider|provider_blend|none`、`--rerank-input-top-k`、`--selected-top-k`、`--parent-doc-mode on|off`、`--query-embedding-cache-policy cache-only|live-on-miss`;trace/report 保存 `hybrid_rank → provider_rank → post_rank`、`rank_delta`、`rerank_score`、`final_score`、`governance_score`、`governance_flags`。
- 纯 hybrid、无 parent-doc 曲线已跑:top20/30/40 均 7/12,`selected_recall@K` 72.50%/85.00%/91.67%,`final_context_precision` 13.00%/10.33%/8.50%,hard-negative 5/12,zero-hit 0/2。
- provider rerank、无 parent-doc 曲线已跑:top20/30/40 为 6/12、6/12、5/12,`selected_recall@K` 84.17%/89.17%/95.00%,`final_context_precision` 15.50%/11.33%/9.00%,hard-negative 6/12、6/12、7/12,成本仍约 ¥0.251666。
- 粗排→精排窄口径已跑:`10→5` 为 8/12、recall 56.67%、precision 30.00%、成本 ¥0.020096;`20→10` 为 8/12、61.67%、21.00%、¥0.040543;`30→20` 为 7/12、81.67%、15.00%、¥0.059062。
- 当前判断:hybrid **召回强但排序不够好**。有些 direct evidence 落在 hybrid 30 名后,所以生产路径不能把 rerank input 收到 15;top50 仍要喂给 provider rerank。provider 只能当 challenger source,不能独占最终成员资格。
- 粗排诊断代码已补强:smoke trace/report 记录 per-query vector / lexical / hybrid rank、跨 query RRF contribution、query vote、hard-negative/relevant 支持情况,并保留 q0 原话加权的 labeled-only 模拟。
- Query Understanding v2 已接入代码:query_rewriter 输出 `intent / core_entities / must_keep_terms / weighted_queries`;用户原话 q0 固定 `weight=2.0`,改写 query 按 role 限权;`project_fact / boundary_question` 缺少保护词或 `zero_hit_candidate` 时保守只用原 query。
- M2.1 RAG 第一刀 `source/type governance` 已保留:只在 `project_fact / boundary_question` 等 protected intent 下轻量调整候选来源权重。粗排 top10 从 `54.17% → 64.17%`,MRR `31.52% → 45.33%`,precision `17.00% → 20.00%`,hard-negative 仍 `1/12`。
- 宽版 `protected_anchor_search` 已判定为负收益并回滚/收窄:它把 `candidate_recall@50 92.50% → 96.67%`,但 top10 从 `64.17%` 掉到 `60.83%`;根因是 anchor 补召回太宽,会把泛相关项目事实挤进紧窗口。
- 当前保留的强锚点补召回是窄路由:状态恢复 query 只在 `JobCopilot + SSE/断线 + 恢复/重连` 语义同时出现时触发;provider failure query 只在 `provider/API + timeout + 429/rate-limit/retry-after` 同时出现时触发。二者都是精确失败样本修复,不是全局宽召回。
- zero-hit 守门已从"候选数量"升级为"核心证据覆盖":`assess_query_support` 会检查 query 的强技术锚点是否被 top10 候选覆盖。Rust borrow checker / Kubernetes Operator 这类库内无证据 query 已能判 0 命中。
- 对比型 query governance 已保留:`Outbox 和 MQ 有什么区别?` 这类 query 会识别两侧概念,优先抬含双方且有近距离 contrast 信号的直接证据,压只覆盖 MQ 一侧的泛相关内容。最终粗排 top10 report `20260515-163721`:12/12,`selected_recall@10 86.67%`,`MRR 67.00%`,`precision 29.00%`,hard-negative `0/12`,zero-hit `2/2`。
- Provider 精排 top100→top10 已跑并判定不能裸用:`20260515-164421` 为 10/12,`selected_recall@10 69.17%`,`MRR 59.76%`,`precision 22.00%`,hard-negative `2/12`;问题不是 timeout/429,而是 reranker 忽略 source/type 与 contrast governance,会把已被粗排压下去的 hard-negative / 泛相关内容重新抬进 top10。
- post-rerank governance/blend 已接入生产路径:`粗排 top50 → qwen3-rerank top50 → coarse/provider/governance blend → dynamic clean-context selection(3-10) → QuizGenerator evidence verifier`。粗排 top10 是 floor,top50 里的高置信 provider challenger 可以进最终上下文,低置信候选不为凑满 top10 被塞给下游;parent-doc 默认永久关闭,只保留作手动 A/B 诊断。
- `provider_blend` 稳定生产口径已跑:`evals/reports/hybrid-search-note-smoke-20260516-095536.md` 为 12/12,`candidate_recall@15 91.67%`,`selected_recall@10 95.00%`,`final_context_recall 95.00%`,`final_context_precision 40.00%`,hard-negative `0/12`,zero-hit `2/2`,parent-doc off,query embedding cache-only。
- selected topK=8 已判定为负收益并回滚:`evals/reports/hybrid-search-note-smoke-20260516-100624.md` precision `40.00% → 41.75%`,但 `selected_recall@10 / final_context_recall 95.00% → 90.00%`,说明最终材料包不能只追求更短,要守住 direct evidence。
- query embedding cache 已接入 `search_service`:粗排每个 expanded query 先查 `llm_response_cache(feature=query_embedding)`,cache key 包含 `normalized_query + model + embed_version + dimensions`。smoke/eval 默认 `cache-only`,cache miss 直接失败,避免重复跑时继续请求 `text-embedding-v4`;产品链路默认仍允许 miss 后实时计算。
- 已补 `docs/6-EVAL_PLAN.md` 第 7 节:trace schema、离线 rescore 跑法、macro average 口径、0 命中 candidate 保存语义。
- 已补 `docs/9-LESSONS.md` §3.4:CLI 评测脚本不要在 Langfuse noop 模式下频繁构造 SDK client。
- 本轮代码侧已缓解 smoke 脚本收尾卡住:无 Langfuse key 时不构造 Langfuse SDK / `langfuse.openai` client;评测脚本 cleanup 只关闭已存在的 embedder / llm singleton 并 shutdown Langfuse singleton。后续多次 smoke 已正常结束并写出 report / trace。
- **M2 已由用户确认完成**:聊天框主题 query → 全库 RAG → 出题 → 答题 → Judge 三层评分 → session 恢复已跑通。
- Context Cache 已验证 provider-side 命中,但因 5 分钟 TTL 不适合当前一次性答题流,已默认关闭显式 `cache_control`;后续多轮讨论面试题时再打开。
- 最新保存主题:`retrieval: add post-rerank governance blend`;M2 tag `v0.4-m2-end` 仍待用户确认。
- M2 retrieval quiz pipeline 代码已提交:`103d882 feat: add m2 retrieval quiz pipeline`。
- M2.1 Agentic RAG 文档已提交:`fd892fa docs: add agentic interview coach roadmap`。
- 本轮 M2.1 文档决策已更新:删除"单题最多 1 轮追问"限制,改成 `remediation loop`(提示哪里答不好 → 补答 → 累计答案重评 → 再判断);补齐长上下文 context pack、纠偏幻觉治理、`session_events`、单题 turn SSE、interview_coach harness 评测口径。
- 本轮 M2.1 单题 turn 已接到 `/quiz`:前端可提交本题 / 补答,消费 `started → progress(context_pack_built) → judge_done → decision_done → result → done` SSE,显示单题分数、`remediation_prompt`、轮次状态,刷新 session 后恢复 `answer_turns / remediation_state / next_action`。
- 本轮后端已补 stale context 保护:`interview_service` 在写入 turn 前先确认 session 引用的 `NoteChunk` 仍存在;旧 session 若引用重建前 chunks,返回明确的"重新出题"错误,避免先写脏 `answer_turns`。
- 本轮 QuizGenerator 引用漂移已热修:`reference_answer` 中的 `[N]` 会被解析为 `reference_chunk_ids`;`source_chunk_ids` 归一为 answer citations、reference chunks、reference point evidence 的并集;越界仍 hard fail,采分点与 evidence 弱文本重合降为 warning。
- 本轮按用户指令跑过 migration / API smoke:`alembic upgrade head` 已到 `0020`;schema 列存在;validation-only 单题 turn smoke 返回 `VALIDATION_ERROR 答案不能为空`。未跑完整测试回归;正向 turn smoke 会改真实 session 并触发外部 LLM,未自动执行。
- 本轮顺手修了旧单测断言漂移:`qwen3.6-flash` 价格期望更新,`prompts_loader` 不再依赖已删除的真实 `jd_parser/v1.0.0.j2` 模板。用户要求停止后未重跑单测。
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
| M2.1 | `InterviewCoachAgent`:Agentic RAG 面试状态机 + 多轮纠偏分支 | ⏳ 当前 |
| M2.5 | JD 累积上传 + 一键分析 + 学习路径 | ⏳ |
| M3 | 弱点跟踪 + SR + 岗位类三源出题 + 简历诊断 | ⏳ |

# 当前已落地

- **M2 schema / retrieval / quiz pipeline 初版**:0017 migration、quiz router、query rewriter、retrieval pipeline、reranker、quiz service 编排已入库。
- **M2 AnswerJudge 初版**:`answer_judge` schema / prompt / agent、`answer_service.submit_session_sse`、三层分 + session 汇总、答题草稿 / abandon 端点已入库。
- **M2 quiz/session UI 已落地**:`/quiz` 支持主题出题、答题、草稿保存、提交评分、结构化 evidence、样例模式、最近练习与 session 恢复。
- **百炼 Context Cache 代码已接入但默认关闭**:保留稳定 chunks 前缀渲染与审计字段;后续多轮面试讨论再开启显式 cache。
- **M2.1 Agentic RAG 方向锁定**:`InterviewCoachAgent` 不做泛化多 Agent,而是 interview coaching harness:检索 → 出题 → 等答 → 评分 → 决策 → 多轮纠偏 / 总结;系统负责状态、工具、证据、分支、恢复、回放和评测。
- **M2.1 单题 turn 最小闭环已落地**:`0020_interview_coach_state.py` + `SessionEvent` + 单题 turn SSE + `/quiz` 前端接入。`GET /quiz/sessions/{id}` 已返回 `agent_state / answer_turns / remediation_state / remediation_prompt`,用于刷新后从 `wait_user_answer` 继续。
- **hybrid_search smoke 标签已升级并复核一轮**:`evals/suites/hybrid_search/dataset.note_smoke.jsonl` 覆盖 M2/M3 边界、Context Cache、reranker/query rewrite、AnswerJudge、SSE 恢复、MVCC、Outbox、epoll、provider timeout/429、zero-hit;`direct_evidence_chunk_ids` 只放可直接回答 query 的 chunk。
- **hybrid_search note/chunk smoke 脚本已落地**:`apps/api/scripts/eval_hybrid_search_note_smoke.py` 只读 DB / 写本地 report + trace(`evals/reports/` gitignore),输出 top notes、top chunks、heading/anchor coverage、hard-negative intrusion、zero-hit、召回指标与成本;`--score-trace` 支持只按新标签离线重算。
- **hybrid_search A/B 诊断开关已落地**:可单独比较 provider rerank / 纯 hybrid、rerank 输入池大小、selected topK、parent-doc on/off,用于拆分"召回 / 粗排排序 / 精排 / parent-doc"责任。
- **hybrid_search 粗排诊断已落地**:`search_service` 暴露 diagnostics-only 路径;smoke report 可解释 direct evidence 为什么在 top20/top50 后、哪条 expanded query 贡献 hard-negative、q0 加权会让哪些 labeled chunks 上下移动。
- **Query Understanding v2 + weighted RRF 已落地**:query_rewriter 输出 intent / core entities / must-keep terms / weighted queries;跨 query RRF 已支持 query weights,用户原话固定两票。
- **M2.1 retrieval governance 已落地**:`retrieval_governance.py` 统一承载 source/type multiplier、窄 protected anchor route、zero-hit support gate、contrast query governance、post-rerank governance/blend、dynamic clean-context selection;`retrieval_pipeline` / `quiz_service` / smoke eval 已接入同一套逻辑。
- **parent-doc 默认禁用 + 出题引用归一化已落地**:QuizGenerator 只拿 post-rerank 选出的干净 seed chunks;题目保存前以后端归一化 `source_chunk_ids / reference_chunk_ids / evidence_chunk_ids`,越界 hard fail,弱文本重合仅 warning,避免 LLM 四个引用字段各说各话。
- **query embedding cache 已落地**:`search_service` 通过 `embed_query_cached` 复用 `llm_response_cache`;smoke/eval 默认 cache-only,重复跑同一套粗排 / 精排时,相同 expanded query 不再重复请求 embedding provider,miss 直接暴露。
- **eval 脚本资源收尾已补强**:`infra.langfuse` 避免无 key/noop 场景构造 Langfuse SDK client;DashScope client 有 Langfuse key 才走 `langfuse.openai`;smoke cleanup 不再为关闭而懒加载 embedder / llm singleton。
- **百炼价格 / rerank 限制已记录**:`qwen3.6-flash` 控制台价格、Responses 工具价、`qwen3-rerank` 500 docs / token 上限 / `gte-rerank-v2` 下线提醒已写入代码注释与常量;rerank 请求本地截断到 500 docs;当前 reranker document format 为 `content + weak_source_context`。
- **CI 策略调整**:所有 GitHub workflow 改为手动触发,避免 push 自动跑测试和邮件通知。

# 下一刀

等待用户指示再开工。推荐下一刀:

1. **QuizGenerator 引用 schema 收口**:不要再让 LLM 同时维护 `source_chunk_ids / reference_chunk_ids / reference_answer citations / reference_points evidence` 四套真相;下一刀应改 prompt/schema,让 LLM 只输出 `reference_answer` 的 `[N]` 和 `reference_points[].evidence_chunk_ids`,后端派生 `reference_chunk_ids / source_chunk_ids`。

备选:

- 若继续后端,先把 `interview_service` 私用 `answer_service._*` helper 整理成可复用公共 helper,再接 `summarize_session / finish_session`。
- 若做评测,补 `evals/suites/interview_coach/` 最小 10 条流程型样本,覆盖不纠偏 / coverage 纠偏 / fabricated 纠偏 / depth 纠偏 / 多轮无提升退出 / 中途恢复 / 长上下文压缩。
- 若只做人工验证,重点看 `/quiz?session=<id>` 单题按钮、纠偏提示、补答后累计答案重评、刷新恢复。
- 若继续跑 smoke,先看 trace 里的 `governance_flags` 和 `post_rank`,不要只看 headline pass。
- 如 cache-only 因 query miss 失败,先确认是不是 query rewrite 内容变了;不要为了跑通悄悄切回 live-on-miss。
- 如果继续看 rerank,优先调 blend / governance 阈值 / dynamic selection,不要把 rerank input 收到 15。
- 如果继续扩 zero-hit,保持 core entity / anchor coverage 守门:Rust、Kubernetes Operator 这类核心实体缺失时不能只靠向量近邻过门。

# 已锁定关键决策

| 项 | 决策 |
|----|------|
| 出题入口 | 只走聊天框 query;笔记面板只查看 / 编辑 / 上传 / 导航,不触发出题。 |
| M2 query | 仅主题类 query;岗位类与空 query 放 M3。 |
| RAG pipeline | `query_rewriter → hybrid + RRF → reranker(top50 challenger) → post-rerank governance/blend → dynamic clean-context selection → evidence verifier` + 0 命中守门;parent-doc 默认关闭。 |
| 0 命中 | 命中 chunks < 3 起步直接报"笔记里没这主题",不兜底让 LLM 编。 |
| Reranker | 百炼 `qwen3-rerank`(`/compatible-api/v1/reranks`);本地 fallback 暂不做。 |
| M2.1 Agent | `InterviewCoachAgent` 状态机;高级感来自状态 / 工具 / 分支 / 记忆 / 评测 / 恢复,不是多 Agent 数量。 |
| M2.1 纠偏 | 不设单题固定 1 轮上限;答不好进入 remediation loop,靠达标 / 用户跳过 / 无明显提升 / 偏题 / token budget 退出。 |
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
- **[来自 M2.1] CLI / eval 脚本显式管理观测 SDK 生命周期**:Langfuse noop 不等于零资源;无 key 时不要构造 SDK client。
- **[来自 M2.1] 项目私有事实 query 必须实体保真**:Query Understanding 不能把 JobCopilot / M2 / AnswerJudge 等私有实体泛化成行业常识;用户原话在跨 query RRF 中权重大于改写。
- **[来自 M2.1] RAG 调参先判断失败层再改代码**:先看 trace 区分召回、粗排排序、精排、hard-negative、zero-hit,不要只凭单条 query 加宽规则。
- **[来自 M2.1] Provider rerank 不默认等于净收益**:启用前必须验证它不会绕过 source/type、contrast、hard-negative 治理把噪声重新抬进紧窗口。
- **[来自 M2.1] Provider rerank 是 challenger source,不是最终成员裁判**:粗排 top50 可喂给精排,但最终 context 必须再过 deterministic governance/blend。
- **[来自 M2.1] smoke/eval 默认 query embedding cache-only**:重复实验不得静默请求 embedding provider;cache miss 要显式失败或由用户指定 live-on-miss。
- **[来自 M2.1] 正式指标名固定为 `candidate_recall@15 / selected_recall@10`**:top50 只作诊断窗口和 rerank input,不要再把 `candidate_recall@50` 当主 headline。
- **[来自 M2.1] parent-doc 默认关闭**:出题 / 评分只能引用 post-rerank 选出的 seed chunks;parent-doc 只作为人工 A/B 背景诊断,不能进入 source/reference/evidence ids。
- **[来自 M2.1] RAG 指标必须说明泛化边界**:12 条 smoke 是关键路径防回归,不是任意 query 泛化证明;下一步要用改写集 / holdout / 强干扰集补证据。
- **[来自 M2.1] 面试追问是多轮纠偏循环**:提示缺口 → 补答 → 累计答案重评 → 再判断;删除"单题最多 1 轮"产品限制,但必须有明确退出条件。
- **[来自 M2.1] 多轮对话不靠塞全量历史**:原始 transcript / events 落库回放,LLM 当前输入只拿 context pack;source chunks / reference_points / unresolved_gaps 优先级最高。
- **[来自 M2.1] 纠偏 prompt 必须 evidence-bound**:每次 remediation 记录 `triggered_by`、缺口 id、chunk id / lookup 结果,不能引入当前题 source chunks 之外的新标准答案来源。
- **[来自 M2.1] M2.1 是 harness engineering,不是 prompt demo**:LLM 只在明确节点做局部生成 / 判断;可靠性来自状态机、工具边界、证据约束、恢复、回放和评测。
- **[来自 M2.1] QuizGenerator 引用编号是 prompt-local**:`[N]` / `source_chunk_ids` / `reference_chunk_ids` / `evidence_chunk_ids` 都先是本次精排上下文的 `1..K`,入库前才映射到真实 `note_chunks.id`;后端应派生重复字段,不要信 LLM 维护多份一致性。

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
