---
title: JobCopilot 项目当前进度(单一可信源)
owner: lemma42796
last_updated: 2026-05-22
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

当前阶段:**M2.5 — JD Intelligence Agent:自动读 JD → 岗位要求地图 → 知识库覆盖 → 学习路径 → quiz topics**。

最新状态:

- **M2.5 第二刀代码已落地**:`/api/jd-analyses` 不再是 placeholder。现在会解析 `all/title/ids/recent` filter → 创建 `jd_analyses(in_progress)` → 读取 `jds.parsed_payload` → 调 `jd_aggregator` 做 batch reduce / merge / Python 频次重算 / 学习路径 → 做知识库覆盖匹配 → 生成 quiz topic 候选 → 写报告 `done`;失败会标 `failed` 并 SSE 返回 `error + done(false)`。
- **`/jds` 前端已接一键分析与报告查看**:左侧工作台包含上传、分析范围、JD 列表、历史报告;右侧只展示当前 JD 详情或分析报告,已从原先拥挤三列改成两列。报告详情展示岗位要求地图、学习路径、quiz topics,topic 可跳到 `/quiz?topic=...`。
- **M2.5 报告打磨已快速收口**:`/jds` 报告详情支持 requirement 类别筛选 + 短语搜索、quiz topic 优先级筛选、当前筛选 topic 前 5 个批量进入 `/quiz`;JD 文本粘贴框支持复制格式清洗和疑似重复提示。
- **JD-to-Knowledge 覆盖分析 MVP 已落地**:`note_match_summary` 从粗 `matched_note_ids` 升级为覆盖矩阵:每个 requirement 输出 `covered / partial / missing / unknown`、`coverage_score`、命中短语、证据 chunk snippet 和匹配笔记;`/jds` 报告页展示覆盖统计、缺口与证据。
- **JD-to-Knowledge 覆盖评测指标脚本已落地**:`apps/api/scripts/eval_jd_coverage.py` 只读 `jd_analyses.note_match_summary` 和人工 JSONL 标签,输出 `coverage_macro_f1 / missing_recall / false_covered_rate / evidence_precision@k / evidence_recall@k / evidence_mrr@k`;默认标签路径 `evals/suites/jd_coverage/dataset.jsonl`,报告写入 `evals/reports/`。
- **本轮最小后端 dogfood 已走安全版**:数据库已有 `jd#1`;新增合成 `jd#2`,用本地 stub 聚合器生成 `analysis#2(done)`,验证报告写入 / SSE 状态 / frequency / note match / topic 生成链路。真实 LLM 聚合因会导出 JD 内容被跳过。
- **本轮人工路径已走到报告可见**:用户浏览器里已看到 `报告 #1`、要求数 / topics / 成本与岗位要求地图;后续"Failed to fetch / 暂无报告"不应再作为当前事实,若复现优先查 API/Web dev server 是否仍在和 `NEXT_PUBLIC_API_BASE_URL`。
- **JD 文本入库闭环仍有效**:JD 文本粘贴 → `jd_parser` LLM 立即解析 → `jds.parsed_payload` 入库 → `/jds` 列表 / 详情 / title 修改 / 软删已接通;截图 OCR 已砍,不再作为 M2.5 输入能力。
- **本轮产品决策已锁定**:M2.5 不恢复大而全的 `jd_aggregator` 自动化 eval runner,但保留 `jd_coverage` 最小指标脚本,用于给简历上的"知识库覆盖分析"提供可追溯量化证据。后续质量判断仍以用户手动 dogfood 为主。
- **本轮 RAG 边界已锁定**:真实规模最多约 50 条同质 JD,JD 分析本体不做 RAG 化;一键分析继续走"已选 parsed JD → LLM 同义合并 → Python 频次重算 → 覆盖矩阵 → 学习路径 / topic"。RAG 只在 topic 进入 `/quiz` 后发生;`match_notes` 只做 evidence-bound 覆盖判断,不是检索增强生成。
- **JD 截图链路已砍**:当前工作区和 git 历史没有 JD 截图原图;旧 commit `6825e6b` 只保留 OCR 文本样本。后续 M2.5 不再接截图 OCR,JD 输入只保留文本粘贴。
- **本轮未跑自动化闸门**:按项目约束,未主动跑 pytest / mypy / ruff / pnpm typecheck / pnpm build / Playwright,也未跑 `eval_jd_coverage.py`;只根据用户手动浏览器反馈和 Next dev 热更新收敛代码。新会话不要假设 API/Web dev server 仍在,需要时重新启动本机 API `:8000` 与 Next Web `:3000`。
- **M2.1 已由用户确认收口**:收口 tag 为 `v0.5-m2.1-end`;下一阶段切到 M2.5。
- **后续路线已收束**:M2.5 之后不再规划 SR / 弱点 dashboard / 岗位类三源出题 / 简历诊断 / 简历上传 / 截图 OCR 等分支;唯一生产力主线改为 `JDAnalysisAgent` 自动编排解析 / 聚合 / 去重 / 频次重算 / 知识库覆盖 / 学习路径 / 报告保存。
- **本轮 pivot 文档已同步**:`docs/1-PRD.md` / `docs/2-TECH_DESIGN.md` / `docs/3-DATA_MODEL.md` / `docs/4-API_SPEC.md` / `docs/5-AGENT_DESIGN.md` / `docs/6-EVAL_PLAN.md` / `docs/7-ROADMAP.md` / `docs/8-ENGINEERING.md` / `docs/9-LESSONS.md` 均已从旧后续路线改为 JD Intelligence Agent;新会话从本文档和 Roadmap 接续即可。
- **M2 已由用户确认完成**:聊天框主题 query → 全库 RAG → 出题 → 答题 → Judge 三层评分 → session 恢复已跑通。
- Context Cache 已验证 provider-side 命中,但因 5 分钟 TTL 不适合当前一次性答题流,已默认关闭显式 `cache_control`;后续多轮讨论面试题时再打开。
- 本轮 QuizGenerator 引用 schema 已收口:prompt/schema 从 `v1.1` bump 到 `v1.2`;LLM 只输出 `reference_answer` 的 `[N]` 和 `reference_points[].evidence_chunk_ids`,后端派生 `reference_chunk_ids / source_chunk_ids` 并映射真实 `note_chunks.id`。
- 本轮真实出题已验证:最新题目行 `19/20/21` 均为 `gen_prompt_version=v1.2`,reference answer 有 `[N]`,reference points 有 evidence,落库后的 source/reference chunk id 都是真实 chunk id。
- 本轮真实评分已验证:session #12 / answer #19 返回 Coverage 100 / Fidelity 100 / Depth 33.33 / Total 93.33;depth remediation 提示缺 `tradeoff / why`,说明多轮补答分支按预期触发。
- 本轮 `/quiz` UI 已改为聊天流:一次只显示当前题,左侧按“主题文件夹 → 题目列表”分组;用户 turn 在右、教练反馈在左;补答作为新消息追加,不再要求用户编辑“累计完整答案”。
- 本轮 `/quiz` 题数改为下拉候选 `1 / 3 / 5`;API `question_count` 下限同步放到 1。
- 本轮全局 sidebar 已支持 macOS 风格折叠,并移除红黄绿窗口点;根布局改为 flex,内容区随 sidebar 宽度伸缩。
- 本轮教练自然语言反馈已后端化:`AnswerJudgeOutput.coach_message` 随评分一起返回并落 `session_answers.coach_message`;`question_done / judge_done / result / GET session detail` 返回该字段,前端只展示,不再按 score/evidence heuristic 拼主反馈。
- 本轮字段命名已收口:`retrieved_chunk(s)` 改为 `final_context_chunk(s)`,`source_chunk_ids` 改为 `evidence_chunk_ids`,`reference_chunk_ids` 改为 `reference_answer_chunk_ids`,`reference_points` 改为 `scoring_points`,`evidence_chunk_ids`(采分点内)改为 `supporting_chunk_ids`;新增迁移 `0022/0023` 做表字段与 JSONB 存量字段迁移。
- 本轮重要上下文:出题与评分使用同一批 `final_context_chunks`;`evidence_chunk_ids` 只是某题从这批 chunks 中真正引用到的 DB id 子集,不是另一批检索结果。当前统计口径里,历史 QuizGenerator 输入 7 个 session 平均 6 chunks / chunk content 合计 2,753.86 字;Judge 多轮评分仍带同一批 chunks,但每次补答只带"累计用户答案",不带上一轮 `coach_message`。
- 本轮产品决策已实现:补答和追问教练不混写同一状态。补答追加 `user_answer` 并重评;追问教练走 `coach_question` + `coach_chat`,只解释上一轮反馈 / 纠偏提示,不改答案、不重评、不推进题目状态。
- 本轮前端 dev server 在 Codex 沙箱内会因端口绑定报 `listen EPERM`;用授权方式启动后 Next dev 可正常到 `Ready`。未按项目约束跑 build/typecheck/lint/playwright。
- 最新保存主题:`quiz: backendize coach feedback and rename chunk fields`;M2 tag `v0.4-m2-end` 仍待用户确认。
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
| M2.1 | `InterviewCoachAgent`:Agentic RAG 面试状态机 + 多轮纠偏分支 | ✅ `v0.5-m2.1-end` |
| M2.5 | JD Intelligence Agent:自动读 JD → 岗位要求地图 → 知识库覆盖 → 学习路径 → quiz topics | ⏳ 当前:文本 JD 入库 + 一键分析报告 + 覆盖矩阵 + 报告筛选/批量 topic + 覆盖指标脚本已落地;截图 OCR 已砍;剩余主要是最小标签 / 手动跑报告 / 可选 trace |
| M3 | 弱点跟踪 + SR + 岗位类三源出题 + 简历诊断 | 🪓 已砍,不再规划 |

# 当前已落地

- **M2 schema / retrieval / quiz pipeline 初版**:0017 migration、quiz router、query rewriter、retrieval pipeline、reranker、quiz service 编排已入库。
- **M2 AnswerJudge 初版**:`answer_judge` schema / prompt / agent、`answer_service.submit_session_sse`、三层分 + session 汇总、答题草稿 / abandon 端点已入库。
- **M2 quiz/session UI 已落地**:`/quiz` 支持主题出题、答题、草稿保存、提交评分、结构化 evidence、样例模式、最近练习与 session 恢复。
- **百炼 Context Cache 代码已接入但默认关闭**:保留稳定 chunks 前缀渲染与审计字段;后续多轮面试讨论再开启显式 cache。
- **M2.1 Agentic RAG 方向锁定**:`InterviewCoachAgent` 不做泛化多 Agent,而是 interview coaching harness:检索 → 出题 → 等答 → 评分 → 决策 → 多轮纠偏 / 总结;系统负责状态、工具、证据、分支、恢复、回放和评测。
- **M2.1 单题 turn 最小闭环已落地**:`0020_interview_coach_state.py` + `SessionEvent` + 单题 turn SSE + `/quiz` 前端接入。`GET /quiz/sessions/{id}` 已返回 `agent_state / answer_turns / judge_turns / coach_turns / remediation_state / remediation_prompt`,用于刷新后从 `wait_user_answer` 继续并回放每轮 LLM 消息。
- **M2.1 退出与上下文压缩已落地**:连续多轮无明显提升会以 `no_meaningful_improvement` 收住;长轮次会生成 deterministic `prior_turn_summary` 并记录 `context_compacted`,预算退出用 `token_budget`。
- **M2.1 整场总结闭环已落地**:`finish_session` SSE 基于已完成单题 Judge 结果生成 deterministic session summary,写 `agent_state.final_summary / question_summaries / summary_context_pack` 与 `session_events`,并通过 `record_session_summary` 写本地 `_recall/{session_id}.md`;前端在所有题已评分后显示"生成总结"并渲染总分、反复缺口、补答修正、复习建议。
- **interview_coach flow smoke runner 已落地**:`evals/suites/interview_coach/dataset.flow_smoke.jsonl` 固定 harness 行为标签;`apps/api/scripts/eval_interview_coach.py` 离线验证状态机分支 / context pack / 事件落库 / finish summary,不重新评 Judge label 质量。最新 10/10 通过。
- **QuizGenerator v1.3 引用收口已落地**:LLM 输出从多套引用字段收敛为 reference answer citations + `scoring_points[].supporting_chunk_ids`;`quiz_service` 统一派生 `reference_answer_chunk_ids / evidence_chunk_ids`,降低 citation/source/reference/evidence 漂移。
- **AnswerJudge v1.4 反馈字段已落地**:评分 LLM 一次返回三层 evidence + `coach_message`;Python 仍负责总分计算、引用映射与完整性校验,前端只展示后端反馈。
- **/quiz 聊天式练习 UI 已落地**:题数下拉 `1/3/5`;一个主题多题用左侧分组切换;主面板用 Apple Messages 风格聊天流;单输入框自动分流补答 / 追问;评分卡折叠到教练反馈下。
- **hybrid_search smoke 标签已升级并复核一轮**:`evals/suites/hybrid_search/dataset.note_smoke.jsonl` 覆盖 M2 / M2.1 RAG 边界、已砍掉的岗位类 query 判断、Context Cache、reranker/query rewrite、AnswerJudge、SSE 恢复、MVCC、Outbox、epoll、provider timeout/429、zero-hit;`direct_evidence_chunk_ids` 只放可直接回答 query 的 chunk。
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
- **M2.5 JD 文本上传解析闭环已落地**:`jd_parser` prompt / agent 接真实 LLM(`Tier.CHEAP`,temperature 0.3);`POST /api/jds` 文本粘贴立即解析入库;`GET/PATCH/DELETE /api/jds` 支持列表、详情、title 修改、软删;`/jds` 页面支持样例粘贴、解析入库、title 筛选、详情展示、删除。
- **M2.5 JDAnalysis 报告 MVP 已落地**:`JdAnalysisCreateIn` 支持 `all/title/ids/recent` filter,单次上限 200;`POST /api/jd-analyses` SSE 已接真实 `jd_aggregator`、知识库覆盖匹配、quiz topic 候选和报告写入;`GET /api/jd-analyses*` 与 `/jds` 报告详情已能回看岗位要求地图 / 覆盖矩阵 / 学习路径 / topics。
- **M2.5 报告详情 / 文本输入小打磨已落地**:报告页可按 requirement category / 搜索词过滤,展示知识库覆盖统计与证据 snippets,topic 可按 priority 过滤并批量带入 `/quiz`;文本 JD 粘贴会先清洗复制格式符,列表预览命中时提示疑似重复。
- **M2.5 覆盖指标脚本已落地**:`eval_jd_coverage.py` 不调 LLM,只读 DB 快照和人工标签,headline 指标收敛为 6 个:覆盖分类、缺口召回、误报覆盖率、证据 P/R/MRR@k。

# 下一刀

等待用户指示再开工。推荐下一刀:

1. **M2.5 最小验收**:补 `evals/suites/jd_coverage/dataset.jsonl` 5-10 条人工标签,手动跑 `uv run python apps/api/scripts/eval_jd_coverage.py`,生成一份覆盖度报告;M2.5 不再新增大功能。

备选:

- 若继续 M2.5 可观测,补 `jd_analysis_id` 贯穿 map-reduce trace;这是 ROADMAP 剩余 DoD,可选。
- 若继续 M2.5 输入能力,只做更强文本清洗 / 样例导入;截图 JD OCR 已砍,不要再作为下一刀。
- 若继续验收,手动完成一场 `/quiz` 后点"生成总结",确认 `test-notes/llm-notes/_recall/<session_id>.md` 生成,且 `GET /api/quiz/sessions/<id>/recall` 返回同一份 markdown。
- 若继续验收,刷新 `/quiz?session=13` 应看到初答评分 89.67 与补答评分 100 两条 LLM 反馈都保留;刷新 `/quiz?session=14` 应看到已评分总分 95 与整场总结。
- 若继续手动验自动分流,在 `/quiz` 一题评分后输入“为什么 HashMap 的数组大小必须是 2 的幂次?”应按追问处理;输入“我补充一下...”或大段技术陈述应按补答重评。

# 已锁定关键决策

| 项 | 决策 |
|----|------|
| 出题入口 | 只走聊天框 query;笔记面板只查看 / 编辑 / 上传 / 导航,不触发出题。 |
| M2 query | 仅主题类 query;岗位类三源出题与空 query 系统自选已砍掉。 |
| RAG pipeline | `query_rewriter → hybrid + RRF → reranker(top50 challenger) → post-rerank governance/blend → dynamic clean-context selection → evidence verifier` + 0 命中守门;parent-doc 默认关闭。 |
| 0 命中 | 命中 chunks < 3 起步直接报"笔记里没这主题",不兜底让 LLM 编。 |
| Reranker | 百炼 `qwen3-rerank`(`/compatible-api/v1/reranks`);本地 fallback 暂不做。 |
| M2.1 Agent | `InterviewCoachAgent` 状态机;高级感来自状态 / 工具 / 分支 / 记忆 / 评测 / 恢复,不是多 Agent 数量。 |
| M2.1 纠偏 | 不设单题固定 1 轮上限;答不好进入 remediation loop,靠达标 / 用户跳过 / 无明显提升 / 偏题 / token budget 退出。 |
| 后续主线 | 只做 `JDAnalysisAgent`;LLM 被 harness 驱动去自动读 JD、聚合要求、生成学习路径和 quiz topic 候选。 |
| JD 分析 / RAG | JD 聚合本体不接 RAG;RAG 只在报告 topic 进入 `/quiz` 后发生。知识库覆盖矩阵是 evidence-bound 判断,不是检索增强生成。 |
| 简历 | 全部砍掉:不上传、不诊断、不改写、不参与出题。 |
| 岗位类 query | 全部砍掉:不做笔记 + 简历 + JD 三源融合;JD 分析只产出 quiz topic 候选。 |
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
- **[来自 M2.5] 后续功能收束到 JD Intelligence Agent**:不再做 SR、弱点 dashboard、岗位类三源出题、简历上传 / 诊断 / 改写;生产力来自 LLM 被 harness 自动编排工具完成 JD 分析任务。
- **[来自 M2.5] M2.5 不恢复大而全的自动化评测主线**:`jd_aggregator` eval runner 仍暂缓;`jd_coverage` 只作为简历指标用的最小读库脚本,跑法由用户手动触发。
- **[来自 M2.5] JD 分析本体不 RAG 化**:最多约 50 条同质 JD,聚合是已选集合的归纳统计;RAG 只服务 `/quiz` 出题,或未来可选的笔记覆盖度语义查找。
- **[来自 M2.5] JD 截图 OCR 已砍**:历史 BOSS 截图只留下 OCR 文本样本;后续 M2.5 不再接截图输入,JD 库只保留文本粘贴主线。
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
- **[来自 M2.1] QuizGenerator 引用编号是 prompt-local**:`[N]` / `evidence_chunk_ids` / `reference_answer_chunk_ids` / `supporting_chunk_ids` 都先是本次 `final_context_chunks` 的 `1..K`,入库前才映射到真实 `note_chunks.id`;后端派生重复字段,不要信 LLM 维护多份一致性。
- **[来自 M2.1] 出题和评分必须使用同一批 final context chunks**:评分阶段可以追加题目证据子集缺失的 chunk id 作兼容保护,但产品语义上不要把它描述成另一批 retrieved chunks。
- **[来自 M2.1] 补答和追问教练不能混写同一状态**:只有明确补答才写入 `user_answer` 并重评;追问解释应走 coach chat,不改变分数 / 轮次推进。
- **[来自 M2.1] 单输入框只是 UX,状态边界仍在后端**:`turn_type=auto` 可自动分流,但模糊输入默认追问;不能把 coach 解释或用户追问混进正式答案评分。
- **[来自 M2.1] 每轮 LLM 回答都必须是消息**:多轮模拟面试不能只保留最新 `coach_message`;Judge 评分反馈与 coach 追问解释都要按 turn/event 回放,后续长上下文靠 summary/context pack 压缩,不是覆盖历史。
- **[来自 M2.1] session recall 文件路径必须后端固定生成**:只写 `notes/_recall/{session_id}.md`;filesystem root 可配置,但不接受请求传任意路径。

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

JobCopilot v1 是"AI 改简历 + 投递追踪",已用 tag `v0.1-jobcopilot-v1` 留档。v2 收束为"JD Intelligence Agent + 笔记 RAG 面试陪练";简历诊断 / 改写不再进入后续路线。v1 失败复盘与工程教训保留在 `docs/9-LESSONS.md`。

# 不在本文档范围

- 详细产品定义 → `docs/1-PRD.md`
- 技术架构与代码目录 → `docs/2-TECH_DESIGN.md`
- Agent prompt / schema 全文 → `docs/5-AGENT_DESIGN.md`
- 评测设计细节 → `docs/6-EVAL_PLAN.md`
- 完整里程碑 DoD → `docs/7-ROADMAP.md`
