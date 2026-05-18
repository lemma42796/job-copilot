---
title: ROADMAP - JobCopilot v2
owner: lemma42796
last_updated: 2026-05-18
purpose: 已完成面试陪练基础 + 后续唯一 JD Intelligence Agent 主线
---

# 节奏总览

```
M0    仓库改造 + 文档重写                                                    ✅
M1    笔记入库 + chunker + 树形导航 + Langfuse 起步                          ✅
M2    聊天框主题类 query → 全库 RAG → 出题 + Judge 三层评分 + Judge tool use ✅
M2.1  InterviewCoachAgent: Agentic RAG 面试状态机 + 工具调用 + 多轮纠偏分支 ✅
M2.5  JD Intelligence Agent: 自动读 JD → 岗位要求地图 → 学习路径 → quiz topics ← 当前(报告 MVP 已落地,报告 hardening / OCR / 手动 dogfood 待接)
```

不估工时,只讲依赖顺序与最佳实践。M2.5 之后不再规划 SR / 弱点 dashboard / 岗位类三源出题 / 简历诊断;所有后续生产力都收束到 JD Intelligence Agent。

---

# M0:仓库改造 + 文档重写

## 退出标准(DoD)

- [x] README.md 重写为 v2 形态(笔记即题库,面试陪练定位)
- [x] docs/1-PRD.md 重写
- [x] docs/7-ROADMAP.md 重写
- [x] docs/STATUS.md 重置
- [x] AI 协作指令文件导航更新
- [ ] docs/{2-TECH_DESIGN, 3-DATA_MODEL, 4-API_SPEC, 5-AGENT_DESIGN, 6-EVAL_PLAN, 8-ENGINEERING}.md 重写
- [ ] 旧 v1 代码砍除:apps/api/agents/{jd_parser, profile_parser, match_analyst, resume_planner, resume_drafter, resume_reviewer}/、对应 service / router / model / scripts、apps/web 旧页面
- [ ] 保留可复用模块:llm/(client / cache / providers / cache_key)、agents/embedder、services/{tokenize, chunk_service(改造)}、evals/{kappa, judge}.py、alembic 0014/0015
- [ ] 新建 v2 模块骨架:agents/{quiz_generator, answer_judge}/、services/{notes_service, quiz_service, answer_service}/、models/{note, question, session, answer}.py
- [ ] alembic 新 migration:建 v2 表 + 砍 v1 表
- [ ] tag `v0.1-jobcopilot-v1` 锁 v1 末态(git history 留档)

# M1:笔记入库 + chunker + 树形导航

## 范围

- **笔记本地目录直读**:前端 File System Access API(showDirectoryPicker / showOpenFilePicker)→ 浏览器遍历目录读 .md → 按相对路径解析 folder_path → 分批 POST `/api/notes/batch-import` 入库
- **Web Markdown 编辑器**(Monaco)+ 树形位置选择 + 保存入库
- **heading-aware chunker**:按 H2 / H3 切 chunk;每 chunk 带 folder_path / heading_path 元数据
- **embedder**(沿用 v1):text-embedding-v4
- **hybrid search 索引**:pgvector HNSW + tsvector char_ngrams(沿用 v1 alembic 0014)
- **树形导航 UI**(macOS 风格 sidebar):folder → folder → note → heading 四级
- **笔记编辑 / 删除 / 移动**:基础 CRUD
- **Langfuse 自部署 + LLM call trace 起步**:docker compose 加 langfuse + langfuse-db,llm/client 装 `@observe`,每条 embedder 调用进 trace

## 退出标准(DoD)

- [ ] 选总字数 ≥ 10 万字的笔记目录(File System Access API),全部入库,chunk 数符合预期(每 H2 一个 chunk);**字数比篇数更能反映 chunker / embedding / hybrid search 真实压力**(50 篇 × 100 字与 50 篇 × 2000 字差一个数量级)
- [ ] Web 编辑器写一篇新笔记 + 选目标 folder + 保存,3 秒内出现在树形导航 + chunks 入库
- [ ] 编辑老笔记 → 老 chunks 删除 + 新 chunks 入库,不影响其他笔记
- [ ] 树形导航点任意节点能拿到该节点 + 子节点的 chunks 列表
- [ ] hybrid search service 能跑通:`hybrid_search_in_node` / `global_hybrid_search` 双路并发 + RRF 返回 Top-K,vector / lex 任一路异常不挂(烟测,不上指标卡)。**recall@5 / mrr 等指标评测挂账 M2**——M1 hybrid search service 已就绪,但未接入任何用户操作(出题剪枝 + Judge 防假阳性都是 M2),query 来源也来自 M2 真场景,M1 阶段评测就是凭空造数据集测纯契约(见 STATUS.md 永久约束"评测指标必须挂在真正用到该能力的里程碑")
- [ ] Langfuse UI(localhost:3001)能看到每条 embedder 调用的 trace + token + cost
- [ ] alembic 全过 + ruff / mypy / typecheck / next build 全过

# M2:聊天框主题类 query → 全库 RAG → 出题 + Judge 三层评分

## 范围

**产品入口大变**:出题不再走"笔记面板点节点",改走聊天框输 query。M2 只做**主题类 query**(例:"考考我多线程");岗位类三源出题 / 空 query 系统自选已砍掉。笔记面板降级,只剩查看 / 编辑 / 导航树,**不再触发出题**。

- **聊天框出题入口**:用户输入 topic query → 系统跨笔记 RAG → 出题(单一数据源 = 笔记库)
- **Retrieval pipeline(RAG 主战场)**:
  - **Query rewriting / expansion**:LLM 把短 query 扩成同义/相邻概念集(例:"并发" → "并发 / 多线程 / 锁 / 死锁")— 提召回
  - **Hybrid search + RRF**:M1 已就绪 `global_hybrid_search`(BM25 tsvector + pgvector HNSW 双路 + RRF 融合)
  - **Reranker(cross-encoder)**:初筛 top 50 → rerank top 10 — 二段式精排
  - **Parent-doc retriever**:小 chunk 命中 → 扩展回父节点(heading_path 同段)拿更完整上下文喂给 LLM,召回粒度与上下文粒度解耦
- **0 命中处理**:retrieval 命中数 < 阈值(例如 < 3 chunks)→ 直接返回"笔记里没这主题",不兜底放宽,不出题
- **QuizGenerator agent**:输入 (query + retrieved chunks + 命中元数据 heading_path / 笔记标题),输出 N 道题(开放式 + 八股,题型比例 LLM 自动决策),每题带 source_chunk_ids 反幻觉
- **session UI**:聊天框输入 → 出题 → 显示 → 输入答案 → 提交 → 评分;笔记面板始终在边栏(只是不点击触发出题)
- **AnswerJudge agent**:三层评分
  - Coverage:从 reference + chunks 抽 N 个 points,逐 point 判 hit/partial/miss(允许同义改写)
  - Fidelity:用户答案逐句对照 chunks,标 supported/inferred/fabricated
  - Depth:trade-off / why / 边界
  - 加权总分 = 0.5×Coverage + 0.4×Fidelity + 0.1×Depth(权重在 Python 代码)
- **AnswerJudge tool use(`lookup_in_notes_global`)**:Judge 在标 fabricated 前必须调全笔记库 hybrid search 验证,直击 LESSONS §1.1 假阳性(详见 5-AGENT §4.7)
- **reference answer 生成**:LLM 基于 chunks 生 + reference_chunk_ids 强约束
- **session 沉淀**:自动写 `notes/_recall/{session_id}.md`,不动原笔记
- **评测 Judge 自身**:`evals/suites/answer_judge/` 30 条人工标注 + Cohen's kappa ≥ 0.7
- **Langfuse trace 完整化**:agent / service 层全装 `@observe`,SSE session 维度 root trace 可查,kappa 不达标排查直接走 Langfuse UI

## 退出标准(DoD)

- [ ] dogfood 跑 1 个 session(聊天框输 "考考我 Java 集合" → 全库 RAG → 5 题 → 答 → 评分)端到端通过
- [ ] 0 命中场景跑通:输入笔记里没有的主题(例 "考考我 React")→ 系统返回"笔记里没这主题",不出题
- [ ] retrieval pipeline 每段独立可观测:query rewriting 改写后的 query 列表 / hybrid 双路 + RRF 命中 / reranker 前后排序变化 / parent-doc 扩展后的最终 chunks 都进 Langfuse trace
- [ ] Judge 三层证据完整(每层都给 evidence_chunk_ids / reasoning)
- [ ] Judge tool use 跑通:dataset 11-15 那批(用户讲常识)样本 Fidelity kappa 显著优于不带 tool 的 baseline
- [ ] session 沉淀文件生成,内容完整(题 / 答 / 评 / reference)
- [ ] `evals/suites/answer_judge/dataset.jsonl` 收 30 条人工标注样本
- [ ] `scripts/eval_answer_judge.py` 跑通,Coverage / Fidelity kappa ≥ 0.7,Depth accuracy ≥ 0.75
- [ ] `scripts/eval_quiz_generator.py` 跑通,结构合规率 ≥ 0.95
- [x] `apps/api/scripts/eval_hybrid_search_note_smoke.py` smoke 脚本与 12 条主题类 / zero-hit query 标签已落地:数据集含 `direct_evidence_chunk_ids / necessary_context_chunk_ids / expected_heading_paths / evidence_anchors`,脚本支持输出 top chunks、anchor 命中、hard-negative rank、`candidate_recall@15 / selected_recall@10 / mrr@10 / final_context_recall / final_context_precision`;top50 保留为诊断窗口和 provider rerank input,2026-05-16 已接入 `provider_blend` 与 cache-only query embedding policy
- [ ] 正式 `hybrid_search` suite 扩到 50 条 fixture query + ablation 矩阵(vector-only / lex-only / hybrid / rewrite / rerank / parent-doc),阈值以 6-EVAL §7 为准
- [ ] Langfuse 按 session_id 过滤能看到完整 trace 树(query rewriting → hybrid → rerank/blend → parent-doc → 出题 → 工具 → 评分嵌套)

# M2.1:InterviewCoachAgent(Agentic RAG 面试状态机)

## 范围

M2.1 是为了把项目从"RAG 出题系统"升级成"Agentic RAG 面试教练",但**不做泛化多 Agent 炫技**。它是 **interview coaching harness engineering**:LLM 被放进面试陪练专用运行框架里,由系统负责状态、工具、证据、分支、恢复、回放和评测。这里的"追问"升级为 **remediation loop**:提示哪里答不好 → 引导补答 → 对累计答案重新评分 → 再判断继续纠偏或进入下一题。

- **LangGraph 状态机**:`InterviewCoachAgent` 作为 session root orchestrator,串起 retrieval pipeline / QuizGenerator / AnswerJudge / 多轮纠偏 / session 总结
- **State**:
  - `session_id / query / query_type`
  - `retrieved_chunk_ids / expanded_queries`
  - `questions / current_question_index`
  - `user_answers[] / answer_turns[] / judge_results[] / remediation_events[]`
  - `unresolved_gaps[] / question_summaries[] / next_action / final_summary`
- **Nodes**:
  - `retrieve_context`:复用 M2 retrieval pipeline
  - `generate_question`:复用 QuizGenerator
  - `wait_user_answer`:人类输入暂停点,支持续写
  - `build_context_pack`:只拼当前题必要上下文,不塞全量聊天历史
  - `judge_answer`:复用 AnswerJudge + lookup tool
  - `decide_next_action`:按 evidence 决定下一步
  - `generate_remediation_prompt`:提示哪里答不好,并生成针对当前题的补答引导
  - `summarize_session`:输出题 / 答 / 评 / reference / 本场缺口摘要
  - `finish_session`:落库 + SSE done
- **Tools**:
  - `search_notes(query)`:笔记库检索
  - `lookup_claim_in_notes(claim)`:Judge 标 fabricated 前验证
  - `get_source_chunks(question_id)`:展开题目引用
  - `record_session_summary(session_id)`:写 session 沉淀
  - `write_quiz_topic_candidates(...)`:仅接收 JD Intelligence 报告产出的 topic 候选,不写长期弱点 / SR 队列
- **分支策略**:
  - `coverage_score < 60` → 提示漏掉的 reference point,引导用户补答
  - `fabricated_ratio > 0.3` → 指出缺证据 / 冲突声明,追问依据来源,提示用户回到笔记证据
  - depth 缺 `tradeoff / why / boundary` 任一维度 → 深挖缺失维度
  - 不设单题固定 1 轮上限;靠达标、用户跳过、连续提升很小、偏题、token budget 等条件退出
- **长上下文治理**:原始多轮对话全量落库回放;每次 LLM 只拿当前题 context pack:题目、source chunks、reference points、累计答案、上一轮 Judge、未解决 gaps、最近 1-2 轮原文、更早轮次摘要
- **幻觉治理**:纠偏 prompt 必须 evidence-bound,每个 remediation event 记录 `triggered_by`、缺口 id、相关 chunk id / lookup 结果;不能引入当前题 source chunks 之外的新标准答案来源
- **可观测**:Langfuse trace 根节点按 `session_id` 聚合,能看到 `retrieve → generate → context_pack → judge → decide → remediate → summarize`
- **失败恢复**:wait_user_answer 是人类暂停点;LLM / reranker / tool 失败按 M2 降级策略走,状态机必须能在已完成节点后恢复

明确不做:

- Planner / Researcher / Critic / Executor 这类泛化多 Agent 互聊
- 自主浏览网页 / 自动改笔记 / 自动投递 / 自动写简历文案
- 长链路开放式任务规划(超过本产品"面试陪练"边界)

## 退出标准(DoD)

- [ ] M2 端到端 session 改由 `InterviewCoachAgent` 编排,用户体验不退化
- [ ] 第一题答漏 reference point 后进入纠偏循环;用户补答后按累计答案重新评分,达标时进入下一题或结束
- [ ] `fabricated_ratio > 0.3` 场景能追问"依据来自哪里",不直接进入下一题
- [ ] depth 缺 `tradeoff / why / boundary` 时能提示缺失维度并引导补答
- [ ] 多轮纠偏没有固定 1 轮上限,但能在达标 / 用户跳过 / 连续提升很小 / 偏题 / token budget 触发时退出
- [ ] 长 session 不把全量聊天历史塞进 prompt;context pack 保留 source chunks / reference points / unresolved gaps,旧轮次压缩为 per-question summary
- [ ] 纠偏 prompt 不幻觉:每次追问都有 `triggered_by`、缺口 id、chunk id / lookup 证据,不能引入 source chunks 外的新标准答案来源
- [ ] 用户中途退出后重新进入,能从 `wait_user_answer` 状态恢复
- [ ] Langfuse trace 能按 session_id 看到完整状态机节点和每个工具调用
- [ ] `evals/suites/interview_coach/` 至少 10 条流程型样本,覆盖不纠偏 / coverage 纠偏 / fabricated 纠偏 / depth 纠偏 / 多轮无提升退出 / 中途恢复 / 长上下文压缩
- [ ] README / 项目介绍可表述为"Agentic RAG 面试教练",并能现场演示一轮多轮纠偏

# M2.5:JD Intelligence Agent

## 范围

- **JD 单条上传**:文本粘贴 / 截图(Qwen 多模态 OCR)→ jd_parser **立即解析**(thinking off)→ 落库 jds 表(累积型,跨时间留)
- **我的 JD 库**:列表 / 按 title 筛选 / 单条删除;LLM 自动从 JD 抽 title + 用户可改
- **JDAnalysisAgent harness**:用户选范围(全部 / 最近 N 条 / 某 title),系统自动完成:
  - `load_jds`:读取已选 JD 与解析快照
  - `ocr_if_needed`:截图 JD 走 Qwen 多模态 OCR
  - `parse_jd`:缺 parsed_payload 时补解析
  - `aggregate_requirements`:按职责 / 硬技能 / 软技能 / 业务方向分桶
  - `dedupe_requirements`:LLM 同义合并 + Python 频次重算
  - `match_notes`:可选,用笔记库标题 / heading / chunks 粗匹配已有材料;当前不是 RAG
  - `generate_learning_path`:输出可执行 markdown
  - `write_report`:保存 `jd_analyses` 快照,可回看对比
  - **真实 dogfood 规模按最多约 50 条同质 JD 设计**;代码侧保留 200 条 safety cap,超过提示用户拆分
  - 内部走 hierarchical map-reduce:
    - Map 已在上传时完成(parsed_payload 持久化)
    - Reduce:每 batch 500-600 raw skill,LLM 单次聚合 → N 个 partial result
    - 二次合并:LLM 跨 batch 同义词去重
    - 频次 Python 重算(canonical 在多少条 JD 里至少出现一次)
- **产出**:岗位要求地图、高频技能 / 职责 / 软技能、学习路径 markdown、quiz topic 候选、证据 JD 列表、历史报告

## 退出标准(DoD)

- [ ] 上传最多约 50 条同岗位 JD(混合文本 + 截图),全部立即解析入库;截图 OCR 准确率(关键字段)≥ 90%。当前已完成**文本粘贴解析入库**,截图 OCR 未接。
- [ ] "我的 JD 库" 列表能筛选 / 删除;LLM 自动抽 title 准确率 ≥ 80%(主观判断)。当前已完成列表、title 筛选、详情、title 修改、软删,准确率还未正式抽样。
- [ ] 一键分析跑通最多约 50 条同质 JD:hierarchical reduce + 二次合并 + 频次重算。当前已接真实 `/api/jd-analyses` SSE、`jd_aggregator`、报告写入和前端报告详情,但未做多报告手动 dogfood。
- [ ] 学习路径 markdown 输出可读、按频次降序、覆盖主要高频要求,并给出 quiz topic 候选。当前已生成学习路径和最多 12 个 topic 候选,质量只走手动 dogfood,不新增自动化 eval runner。
- [ ] 报告能让用户少做手工整理:至少包含要求频次、证据 JD、学习优先级和已有笔记粗匹配状态。当前报告 MVP 已含这些字段,前端两列布局已优化,仍需多报告验证信息密度。
- [ ] Langfuse 按 jd_analysis_id 过滤能看完整 map-reduce trace

---

# 不在路线图(明确不做)

| 功能 | 原因 |
|------|------|
| 浏览器扩展 | 注意力分散,跟核心闭环正交 |
| 多用户 / SaaS | 当前只做单用户 dogfood,不进入后续主线 |
| 系统设计题 | Judge 评分主观度爆炸,不靠谱 |
| 代码题 | 需要执行环境,工程量爆炸 |
| 选择题 | active recall 弱,产品价值低 |
| 语音输入 | STT + Whisper 工程量大,文本足够 |
| 笔记 PDF / 图片导入 | OCR 链路长,markdown 已够;JD 截图 OCR 是 M2.5 主线的一部分 |
| Notion / 飞书 / Obsidian / 语雀 sync | 三方笔记应用各自做得比本产品好;不竞争 |
| 弱点跟踪 / SR / dashboard / 空 query 系统自选 | 不再追;生产力主线收束到 JD Intelligence |
| 岗位类三源出题 / 项目深挖题 | 不再追;JD 分析只产出 quiz topic 候选 |
| 简历上传 / 简历诊断 / 简历改写 / 简历库 | 全部砍掉;避免回到 v1 失败模式 |
| 笔记面板节点点击触发出题 | 出题入口改为聊天框 query;笔记面板降级为查看 / 编辑 / 导航树,不再是出题入口 |
| 投递追踪(v1 残留)| 已确认产品价值站不住,全砍 |
| 跨 batch 跨时间增量聚合 JD | 单次上限 200 条够用;后续先不做 |
| JD 分析本体 RAG 化 | 最多约 50 条同质 JD 是已选集合归纳问题,不是开放检索问题;RAG 只在 topic 进入 `/quiz` 后发生 |
| M2.5 `jd_aggregator` 自动化 eval runner | 用户已明确不做测试;M2.5 只保留手动 dogfood 验收口径 |

---

# 不在本文档范围

- 产品定义 → `docs/1-PRD.md`
- 技术架构 → `docs/2-TECH_DESIGN.md`
- Agent 设计细节 → `docs/5-AGENT_DESIGN.md`
- 评测套件具体设计 → `docs/6-EVAL_PLAN.md`
