---
title: ROADMAP - JobCopilot v2
owner: lemma42796
last_updated: 2026-05-08
purpose: 5 个里程碑 + 退出标准 + 下一刀
---

# 节奏总览

```
M0    仓库改造 + 文档重写            ← 当前
M1    笔记入库 + chunker + 树形导航 + Langfuse 起步
M2    出题 + 答题 + Judge 三层评分 + Judge tool use + Trace 完整化
M2.5  JD 累积上传 + 一键分析 + 学习路径(独立有价值)
M3    弱点跟踪 dashboard + SR + 多轮追问 + 简历诊断(两方锚点严格)
```

不估工时,只讲依赖顺序与最佳实践。每个 M 完成 → DoD 跑通 → 提交 commit + 推 GitHub release tag。

---

# M0:仓库改造 + 文档重写

## 退出标准(DoD)

- [x] README.md 重写为 v2 形态(笔记即题库,面试陪练定位)
- [x] docs/1-PRD.md 重写
- [x] docs/7-ROADMAP.md 重写
- [x] docs/STATUS.md 重置
- [x] CLAUDE.md 文件导航更新
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

# M2:出题 + 答题 + Judge 三层评分

## 范围

- **QuizGenerator agent**:输入 (节点 chunks),输出 N 道题(开放式 + 八股,题型比例 LLM 自动决策),每题带 source_chunk_ids 反幻觉
- **session UI**:出题 → 显示 → 输入答案 → 提交;答题时笔记面板隐藏
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

- [ ] dogfood 跑 1 个 session(选 "Java 集合" 节点 → 5 题 → 答 → 评分)端到端通过
- [ ] Judge 三层证据完整(每层都给 evidence_chunk_ids / reasoning)
- [ ] Judge tool use 跑通:dataset 11-15 那批(用户讲常识)样本 Fidelity kappa 显著优于不带 tool 的 baseline
- [ ] session 沉淀文件生成,内容完整(题 / 答 / 评 / reference)
- [ ] `evals/suites/answer_judge/dataset.jsonl` 收 30 条人工标注样本
- [ ] `scripts/eval_answer_judge.py` 跑通,Coverage / Fidelity kappa ≥ 0.7,Depth accuracy ≥ 0.75
- [ ] `scripts/eval_quiz_generator.py` 跑通,结构合规率 ≥ 0.95
- [ ] `scripts/eval_hybrid_search.py` 跑通,**ablation 三路**(vector-only / lex-only / hybrid)各自 recall@5 / recall@10 / mrr 都出数,**hybrid recall@5 ≥ 0.85 / recall@10 ≥ 0.95 / mrr ≥ 0.6**(从 M1 挂账继承,详见 6-EVAL §7);数据集 30 条 (query, expected_chunk_ids),query 来源:① quiz 剪枝场景的节点路径拼接 query ② Judge tool use 场景的"学生答案 claim"风格 query 各 15 条
- [ ] Langfuse 按 session_id 过滤能看到完整 trace 树(出题 → 工具 → 评分嵌套)

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

# M3:弱点跟踪 + SR + 多轮追问 + 简历诊断

## 范围

### 笔记主线加强

- **knowledge_gap 表**:`(folder_path, heading_path, error_count, last_score, next_review_at)`,每次 session 评分后 upsert
- **SR 简化算法**:
  - score ≥ 80 → next_review_at = today + min(prev_interval × 2, 60d)
  - 60-79 → next_review_at = today + prev_interval
  - < 60 → next_review_at = today + 1d
- **dashboard UI**:首页显示 "今日复习" + 知识点弱点排行 + 历史 session 列表
- **多轮追问 Agent**(LangGraph):
  - State:`current_question / user_answers[] / interviewer_followups[] / score`
  - Nodes:出题 → 等答 → 判断要不要追问(`coverage < 60 AND ≥1 depth 维度 covered=false`)→ 追问 → 等答 → 评分
  - 最多 1 轮追问

### 简历诊断(求职流)

- **简历上传**:markdown 直接 / PDF 走 Qwen 多模态 OCR 转 markdown(Q-05 倾向方案)
- **简历段落 chunker**:按段落切(基础经历 / 技能 / 项目 / 教育 等),resume_chunks 入库;**不进 hybrid search 索引**(简历短,直接全文喂 LLM)
- **ResumeAdvisor agent**(thinking on):输入(JD 分析报告 + 简历)→ 输出诊断
  - 两方锚点严格:每条建议必须有 `req_id` + `resume_position`(可空,空标 unanchored)
  - **永不输出改写文案** — 只说"该补什么主题",不替用户编经验
  - LLM 凭 JD 通用要求 + 简历段落做覆盖度判断 + 诊断陈述
- **诊断结果展示**:每条 JD 通用要求一行,coverage(strong/weak/missing)+ 简历位置 + 建议主题;anchored 主色 / unanchored 灰色弱化

## 退出标准(DoD)

### 笔记主线

- [ ] dogfood 1 个月,每周 3+ session,弱点排行收敛(同一知识点 3 次后正确率 +30pp)
- [ ] 多轮追问跑通:第一轮答漏 trade-off → 系统追问 → 用户补充 → 评分
- [ ] dashboard 数据准确(SR 推送的题确实是到期的)

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
| 投递追踪(v1 残留)| 已确认产品价值站不住,全砍 |
| 跨 batch 跨时间增量聚合 JD | M3+ 才考虑;MVP 单次上限 200 条够用 |

---

# 不在本文档范围

- 产品定义 → `docs/1-PRD.md`
- 技术架构 → `docs/2-TECH_DESIGN.md`
- Agent 设计细节 → `docs/5-AGENT_DESIGN.md`
- 评测套件具体设计 → `docs/6-EVAL_PLAN.md`
