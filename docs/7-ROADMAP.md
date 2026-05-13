---
title: ROADMAP - JobCopilot v2
owner: lemma42796
last_updated: 2026-05-13
purpose: 6 个里程碑 + 退出标准 + 下一刀
---

# 节奏总览

```
M0    仓库改造 + 文档重写                                                    ✅
M1    笔记入库 + chunker + 树形导航 + Langfuse 起步                          ✅
M2    聊天框主题类 query → 全库 RAG → 出题 + Judge 三层评分 + Judge tool use ✅
M2.1  InterviewCoachAgent: Agentic RAG 面试状态机 + 工具调用 + 追问分支 ← 当前
M2.5  JD 累积上传 + 一键分析 + 学习路径(独立有价值)
M3    弱点跟踪 + SR(空 query 系统自选)+ 岗位类出题(三源融合)+ 简历诊断
```

不估工时,只讲依赖顺序与最佳实践。每个 M 完成 → DoD 跑通 → 提交 commit + 推 GitHub release tag。

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
- [ ] 新建 v2 模块骨架:agents/{quiz_generator, answer_judge}/、services/{notes_service, quiz_service, answer_service}/、models/{note, question, session, answer, knowledge_gap}.py
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

**产品入口大变**:出题不再走"笔记面板点节点",改走聊天框输 query。M2 只做**主题类 query**(例:"考考我多线程"),岗位类 / 空 query 挂账 M3。笔记面板降级,只剩查看 / 编辑 / 导航树,**不再触发出题**。

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
- [x] `apps/api/scripts/eval_hybrid_search_note_smoke.py` smoke 脚本与 12 条主题类 / zero-hit query 标签已落地:数据集含 `direct_evidence_chunk_ids / necessary_context_chunk_ids / expected_heading_paths / evidence_anchors`,脚本支持输出 top chunks、anchor 命中、hard-negative rank、`candidate_recall@50 / rerank_recall@10 / mrr@10 / final_context_recall / final_context_precision`;2026-05-13 已生成首份 chunk-level report,随后完成一轮标签口径收紧,待重跑新基线
- [ ] 正式 `hybrid_search` suite 扩到 50 条 fixture query + ablation 矩阵(vector-only / lex-only / hybrid / rewrite / rerank / parent-doc),阈值以 6-EVAL §7 为准
- [ ] Langfuse 按 session_id 过滤能看到完整 trace 树(query rewriting → hybrid → rerank → parent-doc → 出题 → 工具 → 评分嵌套)

# M2.1:InterviewCoachAgent(Agentic RAG 面试状态机)

## 范围

M2.1 是为了把项目从"RAG 出题系统"升级成"Agentic RAG 面试教练",但**不做泛化多 Agent 炫技**。高级感来自跨多轮任务的状态、工具、分支、记忆、评测、可恢复,不是 Agent 数量。

- **LangGraph 状态机**:`InterviewCoachAgent` 作为 session root orchestrator,串起 retrieval pipeline / QuizGenerator / AnswerJudge / 追问 / session 总结
- **State**:
  - `session_id / query / query_type`
  - `retrieved_chunk_ids / expanded_queries`
  - `questions / current_question_index`
  - `user_answers[] / judge_results[] / followups[]`
  - `next_action / final_summary`
- **Nodes**:
  - `retrieve_context`:复用 M2 retrieval pipeline
  - `generate_question`:复用 QuizGenerator
  - `wait_user_answer`:人类输入暂停点,支持续写
  - `judge_answer`:复用 AnswerJudge + lookup tool
  - `decide_next_action`:按 evidence 决定下一步
  - `generate_followup`:只针对当前题追问一轮
  - `summarize_session`:输出题 / 答 / 评 / reference / 弱点摘要
  - `finish_session`:落库 + SSE done
- **Tools**:
  - `search_notes(query)`:笔记库检索
  - `lookup_claim_in_notes(claim)`:Judge 标 fabricated 前验证
  - `get_source_chunks(question_id)`:展开题目引用
  - `record_session_summary(session_id)`:写 session 沉淀
  - `update_knowledge_gap(...)`:M3 接入,本阶段先留接口不做 SR 排期
- **分支策略**:
  - `coverage_score < 60` → 追问基础概念 / 漏掉的 reference point
  - `fabricated_ratio > 0.3` → 追问依据来源,提示用户回到笔记证据
  - depth 缺 `tradeoff / why / boundary` 任一维度 → 深挖一轮
  - 单题最多 1 轮追问,整场 session 不做无限自主循环
- **可观测**:Langfuse trace 根节点按 `session_id` 聚合,能看到 `retrieve → generate → judge → decide → followup → summarize`
- **失败恢复**:wait_user_answer 是人类暂停点;LLM / reranker / tool 失败按 M2 降级策略走,状态机必须能在已完成节点后恢复

明确不做:

- Planner / Researcher / Critic / Executor 这类泛化多 Agent 互聊
- 自主浏览网页 / 自动改笔记 / 自动投递 / 自动写简历文案
- 长链路开放式任务规划(超过本产品"面试陪练"边界)

## 退出标准(DoD)

- [ ] M2 端到端 session 改由 `InterviewCoachAgent` 编排,用户体验不退化
- [ ] 第一题答漏 reference point 后触发 1 轮追问;答得好时直接下一题或结束
- [ ] `fabricated_ratio > 0.3` 场景能追问"依据来自哪里",不直接进入下一题
- [ ] 用户中途退出后重新进入,能从 `wait_user_answer` 状态恢复
- [ ] Langfuse trace 能按 session_id 看到完整状态机节点和每个工具调用
- [ ] `evals/suites/interview_coach/` 至少 10 条流程型样本,覆盖追问 / 不追问 / fabricated / 中途恢复四类
- [ ] README / 简历可表述为"Agentic RAG 面试教练",并能现场演示一轮追问

# M2.5:JD 累积上传 + 一键分析 + 学习路径

## 范围

- **JD 单条上传**:文本粘贴 / 截图(Qwen 多模态 OCR)→ jd_parser **立即解析**(thinking off)→ 落库 jds 表(累积型,跨时间留)
- **我的 JD 库**:列表 / 按 title 筛选 / 单条删除;LLM 自动从 JD 抽 title(Q-04 倾向方案)+ 用户可改
- **一键分析**:用户选范围(全部 / 最近 N 条 / 某 title),触发聚合
  - **单次上限 200 条 JD**,超过提示用户拆分
  - 内部走 hierarchical map-reduce:
    - Map 已在上传时完成(parsed_payload 持久化)
    - Reduce:每 batch 500-600 raw skill,LLM 单次聚合 → N 个 partial result
    - 二次合并:LLM 跨 batch 同义词去重
    - 频次 Python 重算(canonical 在多少条 JD 里至少出现一次)
- **学习路径生成**:聚合输出 → LLM 直接出 markdown(不依赖笔记库)
- **历史报告**:jd_analyses 表存每次分析快照,可对比

## 退出标准(DoD)

- [ ] 上传 50+ 条同岗位 JD(混合文本 + 截图),全部立即解析入库;截图 OCR 准确率(关键字段)≥ 90%
- [ ] "我的 JD 库" 列表能筛选 / 删除;LLM 自动抽 title 准确率 ≥ 80%(主观判断)
- [ ] 一键分析跑通 100 条 JD:hierarchical reduce 5 batch + 二次合并 + 频次重算,P95 ≤ 60s
- [ ] 学习路径 markdown 输出可读、按频次降序、覆盖至少 80% 高频要求
- [ ] `evals/suites/jd_aggregator/` 数据集:30 条人工标 ground truth(聚合后的 canonical 列表 + 频次),同义合并准确率 ≥ 0.85
- [ ] 200 条 JD 一键分析总成本 ≤ ¥1.0,LLM cache 命中率 ≥ 50%(重跑场景)
- [ ] Langfuse 按 jd_analysis_id 过滤能看完整 map-reduce trace

# M3:弱点跟踪 + SR + 岗位类出题(三源融合)+ 简历诊断

## 范围

### 笔记复习增强(SR + dashboard + 空 query 系统自选)

- **knowledge_gap 表**:`(folder_path, heading_path, error_count, last_score, next_review_at)`,每次 session 评分后 upsert
- **SR 简化算法**:
  - score ≥ 80 → next_review_at = today + min(prev_interval × 2, 60d)
  - 60-79 → next_review_at = today + prev_interval
  - < 60 → next_review_at = today + 1d
- **dashboard UI**:首页显示 "今日复习" + 知识点弱点排行 + 历史 session 列表
- **空 query → 系统自选**:用户在聊天框输入"来模拟面试吧" / 留空 → SR 调度从 knowledge_gap 弱点排行选 1 个 heading_path 末段当 query → 走 M2 主题类 RAG 流程出题(复用 M2 pipeline,仅 query 来源从用户改成系统)
- **InterviewCoachAgent 扩展**:复用 M2.1 状态机,把 `update_knowledge_gap` 接到真实 SR 队列;空 query 时由 SR 选题后进入同一编排

### 岗位类 query 出题(三源融合检索)

用户在聊天框输"模拟一面 Java 后端" / "应聘字节后端实习" 这类岗位类 query → 系统拼**三源**出题:**笔记 + 那一份简历 + 用户选定的 JD 子集**(从 M2.5 jds 表选)。这是 RAG 主战场升级形态:多源、多类型、多 query。

- **简历单条记录**(不是"简历库"):全库就一条 resumes 行(本地单用户工具)。简历不按岗位定制 — "一个人就一份简历",岗位类 query 拼的就是这一份简历 + 选定的 JD 子集
- **岗位类 query 解析**:LLM 从 query 抽 (job_title, target_companies?, JD 候选范围?);用户也可在 UI 显式选 JD 库子集(全部 / 最近 N 条 / 某 title)
- **三源检索 pipeline**:
  - **路 1 笔记库 RAG**:query → query rewriting → hybrid search + rerank + parent-doc(复用 M2 pipeline)→ 命中 chunks
  - **路 2 简历内容**:那一份简历全文(简历短,直接喂 LLM,不进 hybrid search 索引)+ 重点段落(项目 / 技能写了什么)— **重点考用户简历上写的东西**(直击"自己不会的也往简历上写,问到答不出"问题)
  - **路 3 JD 子集聚合**:用户选定的 JD 候选 → 从 jds 表读 parsed_payload(M2.5 已 map 完成)→ 抽**职责 + 要求两方面**(LESSONS:"有的人只看要求不看职责,职责上的东西没复习就挂了"),按频次聚合
- **三源结果合并**:三路 chunks / 内容片段并入 quiz_generator,prompt 明确告诉 LLM:"基于这份简历(用户写了什么)+ 这些 JD(岗位要什么)+ 这些笔记(用户复习了什么)出 N 道题,优先考'简历写了但 JD 也要'的交集 + '简历没写但 JD 强要求'的缺口"
- **题型扩展**:岗位类多出"项目深挖题"(基于简历项目段落,问技术选型 / 难点 / 量化数据 — 模拟面试官追问简历)

### 简历诊断(求职流)

- **简历上传**:markdown 直接 / PDF 走 Qwen 多模态 OCR 转 markdown;**全库就一条 resumes 行**,新上传覆盖旧的(留 history 表存历次诊断快照),无"多份简历切换"概念
- **简历段落 chunker**:按段落切(基础经历 / 技能 / 项目 / 教育 等),resume_chunks 入库供岗位类 query 路 2 复用;**不进 hybrid search 索引**(简历短,直接全文喂 LLM)
- **ResumeAdvisor agent**(thinking on):输入(JD 分析报告 + 简历)→ 输出诊断
  - 两方锚点严格:每条建议必须有 `req_id` + `resume_position`(可空,空标 unanchored)
  - **永不输出改写文案** — 只说"该补什么主题",不替用户编经验
  - LLM 凭 JD 通用要求 + 简历段落做覆盖度判断 + 诊断陈述
- **诊断结果展示**:每条 JD 通用要求一行,coverage(strong/weak/missing)+ 简历位置 + 建议主题;anchored 主色 / unanchored 灰色弱化

## 退出标准(DoD)

### 笔记复习增强

- [ ] dogfood 1 个月,每周 3+ session,弱点排行收敛(同一知识点 3 次后正确率 +30pp)
- [ ] 空 query → SR 自选跑通:聊天框输"来模拟面试吧" → 系统从弱点排行选主题 → 出题,Langfuse trace 能看到 SR 选中的 heading_path
- [ ] M2.1 追问分支接入 SR:第一轮答漏 trade-off → 系统追问 → 用户补充 → 更新对应 knowledge_gap
- [ ] dashboard 数据准确(SR 推送的题确实是到期的)

### 岗位类 query 出题

- [ ] 输入"模拟一面 Java 后端" → 三源检索全过(笔记路 hybrid 命中 + 简历段落注入 + JD 子集职责/要求两方面均覆盖)→ 出 5 题
- [ ] 出题质量主观:5 题里至少 2 题"直击简历写了但用户答不出"(LESSONS §"自己不会的也往简历上写"反向验证)
- [ ] Langfuse trace 能看到三源各自命中(三路并列 + 合并节点)

### 简历诊断

- [ ] 上传简历(markdown 或 PDF)→ 段落切片 + 入库 ≤ 5s
- [ ] 选 JD 分析报告 + 简历 → 触发诊断,P95 ≤ 30s
- [ ] anchored 比例 ≥ 70%(诊断输出里两方齐的建议占比)
- [ ] dogfood 自查:LLM 没有出现一条"替写文案"(发现就当 prompt 漏洞修)
- [ ] `evals/suites/resume_advisor/` 数据集:15 条人工标(JD 报告 + 简历)对照,anchored ratio + 主观诊断准确率两个维度

---

# 不在路线图(明确不做)

| 功能 | 原因 |
|------|------|
| 浏览器扩展 | 注意力分散,跟核心闭环正交 |
| 多用户 / SaaS | M0-3 单用户 dogfood,M4+ 再考虑 |
| 系统设计题 | Judge 评分主观度爆炸,不靠谱 |
| 代码题 | 需要执行环境,工程量爆炸 |
| 选择题 | active recall 弱,产品价值低 |
| 语音输入 | STT + Whisper 工程量大,文本足够 |
| PDF / 图片导入 | OCR 链路长,markdown 已够 |
| Notion / 飞书 / Obsidian / 语雀 sync | 三方笔记应用各自做得比本产品好;不竞争 |
| 笔记 PDF / 图片导入 | OCR 链路长,markdown 已够(简历 PDF 是要做的) |
| 替用户写简历改写文案 | 直接撞 v1 失败模式;系统只做诊断,真实经验用户自己写 |
| 按岗位定制多份简历(简历库)| 一个人就一份简历;岗位类 query 拼"那一份简历 + 用户选定 JD 子集"已足够,多份切换增加产品复杂度无价值 |
| 笔记面板节点点击触发出题 | 出题入口改为聊天框 query;笔记面板降级为查看 / 编辑 / 导航树,不再是出题入口 |
| 投递追踪(v1 残留)| 已确认产品价值站不住,全砍 |
| 跨 batch 跨时间增量聚合 JD | M3+ 才考虑;MVP 单次上限 200 条够用 |

---

# 不在本文档范围

- 产品定义 → `docs/1-PRD.md`
- 技术架构 → `docs/2-TECH_DESIGN.md`
- Agent 设计细节 → `docs/5-AGENT_DESIGN.md`
- 评测套件具体设计 → `docs/6-EVAL_PLAN.md`
