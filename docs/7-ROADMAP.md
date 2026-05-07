---
title: ROADMAP - JobCopilot v2
owner: lemma42796
last_updated: 2026-05-08
purpose: 4 个里程碑 + 退出标准 + 下一刀
---

# 节奏总览

```
M0  仓库改造 + 文档重写            ← 当前
M1  笔记入库 + chunker + 树形导航
M2  出题 + 答题 + Judge 三层评分
M3  弱点跟踪 dashboard + SR + 多轮追问 + 语雀同步
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

- **笔记 .md zip 上传**:前端拖拽 → 后端 unzip + 校验 → 按文件夹层级入库
- **Web Markdown 编辑器**(Monaco)+ 树形位置选择 + 保存入库
- **heading-aware chunker**:按 H2 / H3 切 chunk;每 chunk 带 folder_path / heading_path 元数据
- **embedder**(沿用 v1):text-embedding-v4
- **hybrid search 索引**:pgvector HNSW + tsvector char_ngrams(沿用 v1 alembic 0014)
- **树形导航 UI**(macOS 风格 sidebar):folder → folder → note → heading 四级
- **笔记编辑 / 删除 / 移动**:基础 CRUD

## 退出标准(DoD)

- [ ] 上传 50+ 篇笔记的 zip,全部入库,chunk 数符合预期(每 H2 一个 chunk)
- [ ] Web 编辑器写一篇新笔记 + 选目标 folder + 保存,3 秒内出现在树形导航 + chunks 入库
- [ ] 编辑老笔记 → 老 chunks 删除 + 新 chunks 入库,不影响其他笔记
- [ ] 树形导航点任意节点能拿到该节点 + 子节点的 chunks 列表
- [ ] hybrid search 能跑通:`SELECT ... ORDER BY rrf` 返回 Top-K chunks
- [ ] alembic 全过 + ruff / mypy / typecheck / next build 全过

# M2:出题 + 答题 + Judge 三层评分

## 范围

- **QuizGenerator agent**:输入 (节点 chunks, 题型混合比例),输出 N 道题(开放式 + 八股),每题带 source_chunk_ids 反幻觉
- **session UI**:出题 → 显示 → 输入答案 → 提交;答题时笔记面板隐藏
- **AnswerJudge agent**:三层评分
  - Coverage:从 reference + chunks 抽 N 个 points,逐 point 判 hit/partial/miss(允许同义改写)
  - Fidelity:用户答案逐句对照 chunks,标 supported/inferred/fabricated
  - Depth:trade-off / why / 边界
  - 加权总分 = 0.5×Coverage + 0.4×Fidelity + 0.1×Depth(权重在 Python 代码)
- **reference answer 生成**:LLM 基于 chunks 生 + reference_chunk_ids 强约束
- **session 沉淀**:自动写 `notes/_recall/{session_id}.md`,不动原笔记
- **评测 Judge 自身**:`evals/suites/answer_judge/` 30 条人工标注 + Cohen's kappa ≥ 0.7

## 退出标准(DoD)

- [ ] dogfood 跑 1 个 session(选 "Java 集合" 节点 → 5 题 → 答 → 评分)端到端通过
- [ ] Judge 三层证据完整(每层都给 evidence_chunk_ids / reasoning)
- [ ] session 沉淀文件生成,内容完整(题 / 答 / 评 / reference)
- [ ] `evals/suites/answer_judge/dataset.jsonl` 收 30 条人工标注样本
- [ ] `scripts/judge_eval.py` 跑通,Cohen's kappa ≥ 0.7

# M3:弱点跟踪 + SR + 多轮追问 + 语雀

## 范围

- **knowledge_gap 表**:`(folder_path, heading_path, error_count, last_score, next_review_at)`,每次 session 评分后 upsert
- **SR 简化算法**:
  - score ≥ 80 → next_review_at = today + min(prev_interval × 2, 60d)
  - 60-79 → next_review_at = today + prev_interval
  - < 60 → next_review_at = today + 1d
- **dashboard UI**:首页显示 "今日复习" + 知识点弱点排行 + 历史 session 列表
- **多轮追问 Agent**(LangGraph):
  - State:`current_question / user_answers[] / interviewer_followups[] / score`
  - Nodes:出题 → 等答 → 判断要不要追问(基于答案的不足处)→ 追问 → 等答 → 评分
- **语雀 OAuth + 增量同步**:
  - OAuth 接入 → 拉取知识库列表 → 用户选要同步的库
  - 增量同步:按文档 updated_at 拉变更 → diff chunks → 增删改

## 退出标准(DoD)

- [ ] dogfood 1 个月,每周 3+ session,弱点排行收敛(同一知识点 3 次后正确率 +30pp)
- [ ] 多轮追问跑通:第一轮答漏 trade-off → 系统追问 → 用户补充 → 评分
- [ ] 语雀同步:30+ 篇文档无丢失入库
- [ ] dashboard 数据准确(SR 推送的题确实是到期的)

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
| Notion / 飞书 / Obsidian sync | 各家 API 不同,语雀就够覆盖国内目标用户 |
| 投递追踪 / 简历定制(v1 残留)| 已确认产品价值站不住,全砍 |

---

# 不在本文档范围

- 产品定义 → `docs/1-PRD.md`
- 技术架构 → `docs/2-TECH_DESIGN.md`
- Agent 设计细节 → `docs/5-AGENT_DESIGN.md`
- 评测套件具体设计 → `docs/6-EVAL_PLAN.md`
