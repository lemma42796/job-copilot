---
title: PRD - JobCopilot v2(给程序员的 AI 面试陪练)
owner: lemma42796
last_updated: 2026-05-08
status: M0(文档就绪,代码改造未起)
purpose: 锁产品边界、目标用户、用户故事、NSM、不在范围
---

# 1. 产品一句话

**写过笔记的程序员,把你的笔记变成你的面试题。**

LLM 模拟真面试的强度反问你,直到你把笔记里的内容**用自己的话讲清楚**。

# 2. 目标用户

**1-3 年跳槽开发者**,有写技术笔记习惯,正在或即将准备技术面试。

为什么是这个群体:

- 痛点最强:跳槽周期 1-2 月密集刷面经,每天 1-2 小时
- 笔记基础:这阶段大多有自己的语雀 / Obsidian / 飞书笔记沉淀
- 工程能力:能 docker compose 起本地服务,愿意 BYOK
- 付费意愿:面试焦虑驱动,愿意为 marginal 提升付出
- 结果可验证:1-2 月后是否拿到 offer 是直接信号

不是目标用户(明确排除):

- 应届生 v0(没积累笔记,系统冷启动失败)
- 5+ 年资深(刷题已经不是核心痛点)
- 非开发者(产品 / 运营 / 设计 — JD 形态完全不同)

# 3. 核心闭环

```
写笔记(Web 编辑器 / 上传 .md zip)
   ↓ chunker(heading-aware)+ embedder
笔记 chunks(带 folder_path / heading_path 元数据)
   ↓ 用户选知识点节点(树形导航)
节点关联 chunks
   ↓ QuizGenerator(LLM,反幻觉,标 source_chunk_ids)
3-10 道题(开放式 + 八股)
   ↓ 用户答(不能看笔记)
回答(纯文本)
   ↓ AnswerJudge(三层评分)
  - Coverage:覆盖度(reference points 命中率)
  - Fidelity:忠实度(用户讲的是否被 chunks 支持)
  - Depth:深度(讲了 trade-off / why / 边界没有)
   ↓
session 沉淀(独立 markdown)+ 知识点弱点跟踪(folder_path / heading_path 维度)
   ↓ SR 队列排期
下次复习按弱点 + 到期推荐
```

# 4. 用户故事(MVP)

## 4.1 写 / 导入笔记

- **US-1**:作为用户,我可以把本地 markdown 文件夹打成 zip 上传,系统按文件夹层级 + heading 切 chunk 入库
- **US-2**:作为用户,我可以在 Web 编辑器(Monaco)里直接写一篇 markdown 笔记,选目标节点(folder),保存后立即入库
- **US-3**:作为用户,我可以在树形导航里看到所有笔记的层级结构(文件夹 → 子文件夹 → 笔记 → heading)
- **US-4**:作为用户,我可以编辑已有笔记;保存后老 chunks 删除新 chunks 入库,不影响其他笔记

## 4.2 出题与答题

- **US-5**:作为用户,我可以点树形导航某个节点("Java / 并发 / synchronized"),系统从该节点 + 子节点关联的 chunks 出 3-10 道题
- **US-6**:作为用户,题型分两类:**开放式**("解释 synchronized 的锁升级过程")+ **八股**("synchronized 的轻量级锁是怎么实现的?")。MVP 不做代码题 / 系统设计题
- **US-7**:作为用户,答题时**笔记面板隐藏**(active recall 强约束),只能看题干 + 输入框
- **US-8**:作为用户,我可以中途退出 session,草稿自动保存,下次进入续写

## 4.3 评分与沉淀

- **US-9**:作为用户,提交答案后看到三层评分(Coverage / Fidelity / Depth)+ 加权总分 + 每层的具体证据(命中的 reference points / 被 fabricate 的陈述)
- **US-10**:作为用户,可以一键展开 reference answer + 关联 chunks 对照
- **US-11**:每个 session 结束生成一篇沉淀 markdown(`notes/_recall/{session_id}.md`),包含题目 / 我的答 / 评分 / reference / 弱点。**不污染原笔记**

## 4.4 弱点跟踪与复习

- **US-12**:作为用户,首页 dashboard 显示知识点维度的弱点统计("Java 集合 错率 60% / 累计 4 次")
- **US-13**:作为用户,点 "今日复习" 按钮,系统按 SR 队列(`next_review_at <= today` 的弱点)推一个新 session
- **US-14**:答对的题间隔翻倍(最长 60 天),答错的题缩回 1 天

# 5. 功能边界

## MVP(M1 + M2,本地单用户 dogfood)

- US-1 ~ US-11 全做
- US-12 / 13 / 14 简化版(不做美观 dashboard,只做 SQL 查询 + 列表)

## M2 加强

- 完整弱点 dashboard UI
- session 历史回看
- LangGraph 多轮追问 Agent(基于第一轮答案出追问)

## M3 加强

- **语雀 OAuth + 增量同步**(国内程序员最大输入源)
- LLM 自动出题 + 题质评测套件
- 跨笔记知识图谱

## 明确不做(MVP / M2 / M3 都不做)

- 浏览器扩展
- 多用户 / Auth / SaaS 化
- 系统设计题(评分主观,Judge 不靠谱)
- 代码题(评分需要执行环境,工程量爆炸)
- 选择题(active recall 弱)
- 语音输入 / 语音答题
- PDF / 图片导入
- LaTeX 简历导出 / 投递追踪 / 招聘对接(v1 残留,全砍)

# 6. 非功能需求(NFR)

- **本地优先**:docker compose 起本地 Postgres + API + Web,数据不出机器
- **BYOK**:用户自带阿里云百炼 API Key,持久化到项目根 `.env`(gitignored)
- **单用户(MVP)**:无 auth,localhost only;后期上 SaaS 再加(M4+)
- **响应延迟**:出题 P95 ≤ 15s,评分 P95 ≤ 20s(qwen3.6-flash thinking on)
- **成本**:单 session(5 题出 + 答 + 评)总 LLM 成本 ≤ ¥0.10
- **数据可移植**:笔记原文 + sessions 沉淀都在本地 markdown 文件,删数据库不丢内容

# 7. NSM(北极星指标)

**短期 dogfood(单用户验证)**:

- **每日 active recall 时长**(目标:每周 ≥ 3 次 session,每次 ≥ 15 分钟)
- **弱点收敛率**(同一知识点 3 次答题后正确率提升 ≥ 30pp)
- **Judge 评分跟人工 ground truth 的 Cohen's kappa**(≥ 0.7)

**长期(SaaS 化后,M4+ 才考虑)**:

- 用户面试通过率提升(自报)
- 周活留存率

# 8. 已锁定的关键决策(v2 起)

| 项 | 决策 | 备注 |
|----|------|------|
| 目标用户 | 1-3 年跳槽开发者(有写笔记习惯) | v1 同款 |
| 笔记输入源 | M1: Web 编辑器 + .md zip 上传;M3: 语雀 OAuth | 不做 Notion / 飞书 / Obsidian sync |
| 题型 | 开放式 + 八股 两类 | 不做代码 / 系统设计 / 选择题 |
| 评分 | LLM-as-Judge 三层(Coverage / Fidelity / Depth) | 权重在 Python,不让 Judge 算 |
| 评者 / 被评者模型 | 全 qwen3.6-flash thinking on | 简化模型路由,不区分 plus/flash |
| LLM Provider | 阿里云百炼 Qwen3.6 | 沿用 v1 ADR-0003 |
| 数据存储 | Postgres 16 一把梭(pgvector + tsvector) | 沿用 v1 ADR-0002 |
| Agent 编排 | M3 才用 LangGraph(多轮追问);MVP 单 Agent | |
| UI 风格 | macOS 风(Tailwind 自己写,不引组件库) | |
| 部署 | 本地 docker compose;不做 SaaS(M4+ 再说) | |

# 9. 上次会话遗留的开放问题

- **Q-01** macOS 风具体调色(亮 / 暗双模,毛玻璃 / 圆角具体度数)— M1 启动 Web UI 前再确认
- **Q-02** 笔记冲突:用户编辑 Web 笔记 + 语雀同步同一篇笔记的合并策略 — M3 启动前决策
- **Q-03** session 中途换题 / 跳过 / 重答 的 UX — M2 启动前决策

---

# 不在本文档范围

- 模块分层 / 数据流细节 → `docs/2-TECH_DESIGN.md`
- 表 schema → `docs/3-DATA_MODEL.md`
- API 端点 → `docs/4-API_SPEC.md`
- Prompt 全文 → `docs/5-AGENT_DESIGN.md`
- 评测套件 → `docs/6-EVAL_PLAN.md`
- 里程碑 / 切片节奏 → `docs/7-ROADMAP.md`
- 工程规范 → `docs/8-ENGINEERING.md`
