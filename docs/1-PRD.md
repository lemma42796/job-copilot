---
title: PRD - JobCopilot v2(JD Intelligence Agent + 笔记面试陪练)
owner: lemma42796
last_updated: 2026-05-17
status: M2.5(JD Intelligence Agent 方向收束)
purpose: 锁产品边界、目标用户、用户故事、NSM、不在范围
---

# 1. 产品一句话

**给学计算机的人:把大量目标岗位 JD 自动读完,沉淀岗位要求地图、学习路径和可练习主题,再用笔记 RAG 面试陪练巩固。**

LLM 不是聊天装饰,而是被 harness 驱动的生产力执行器:自动 OCR / 解析 / 聚合 / 去重 / 频次统计 / 生成学习路径 / 保存报告。你的**JD 库 + 笔记**才是主角;简历诊断、SR、岗位类三源出题等后续分支从路线图移除。

# 2. 目标用户

**学计算机的人**(本科 / 研究生在读 / 1-3 年开发者 / 5+ 年想转岗的工程师都包含)。

为什么这个群体覆盖广:

- **痛点谱系明确**:从"应该学什么"(学生)到"我做的项目跟岗位要求对得上吗"(在职)都是真实问题
- **自我驱动**:愿意写笔记、刷面经、持续收集 JD(技术从业者特性)
- **工程能力**:能 docker compose 起本地服务,愿意 BYOK
- **结果可验证**:offer / 面试通过 / 拿到面试机会都是直接信号

**不是目标用户(明确排除)**:

- 非开发岗(产品 / 运营 / 设计 — JD 语义结构完全不同,聚合分析不可复用)

# 3. 为什么不去 LLM 官网?(产品边界自检)

> 写给未来怀疑这玩意有没有意义时的自己看。

**诚实结论**:直接在 LLM 官网做单次面试问答,80% 场景够用。本产品真正"它有但 LLM 没有"的差异化收束为两件:

1. **累积型 JD Intelligence 工作流**:把 50-200 条 JD 当资产管理,自动读、解析、聚合、去重、统计频次、生成学习路径。
2. **严格锚定笔记的面试陪练**:基于用户笔记出题 / 评分 / 纠偏,不让 LLM 凭训练数据自由发挥。

其他(笔记结构化 / 评分稳定 / 数据沉淀)都是"更顺手"而非"做不到"。

## 3.1 LLM 官网够用的场景(本产品不要去碰)

- **临阵磨枪**(下周面试,刷一晚上)→ 粘 JD + 粘笔记 + 让 LLM 出题答题。打开就用,本产品 docker compose + API Key 门槛高
- **偶尔用一次**(一个月一次)→ JD 累积数据和分析报告沉淀 0 价值
- **GPTs / Claude Project**(可以上传文档当上下文)→ 进一步缩小本产品的"笔记结构化"差异化
- **简历改写,粘一下就行**(GPTs 上 1 分钟搞定)→ 如果你不在意 LLM 是不是给你编了不存在的经验,LLM 官网够用

如果用户画像是"偶尔用一下",这个产品大概率失败。

## 3.2 LLM 官网真正不够用的场景(本产品的存在理由)

**目标用户(学计算机的人,从找方向到求职冲刺整个周期)**:跨度从几周到几个月。这个时间尺度下两个痛点 LLM 官网做不好:

### 痛点 A:JD 是累积型资产,LLM 单会话存不下

学计算机的人在求职周期里看到的 JD 是陆续累积的 — 这周看到一条好 JD,下周又看到几条,可能持续一两个月才形成一批 100+ 的"目标岗位 JD 库"。LLM 单会话粘几条让它分析,跨时间累积做不了。

本产品 jds 表持久化每条 JD 的解析结果(jd_parser 上传即落库),用户随时点"一键分析我的 JD 库"对累积 100+ 条做 hierarchical reduce 聚合,输出岗位要求地图、频次、学习路径和 quiz topic 候选。**这是一个 LLM 官网根本无法提供的工作流。**

### 痛点 B:读大量 JD 很烦,但正适合 LLM 自动干活

用户真正想省下的不是"问 LLM 一句",而是这些重复劳动:截图 / 复制 JD、清洗标题、抽职责、抽要求、合并同义技能、数频次、找最该学的主题、整理成可执行计划。M2.5 的核心就是把这些步骤放进一个 harness,让 LLM 持续调用受控工具直到产出报告。

### 痛点 C:LLM 凭训练数据出题 → 跟你笔记脱节(用户感知不到)

举例:你笔记里 synchronized 锁升级是按 Java 8 写的,简化到 4 阶段;LLM 凭训练数据可能给你出"Java 21 里 synchronized 跟 virtual thread 的交互" — 你听都没听过,以为自己没记住,**其实是题超出你笔记范围**。

更隐蔽的反向:LLM 出了一道你笔记里**有写过**但你**没记住细节**的题,你模糊答了几句,LLM 凭训练数据"补全"了你没说的部分给打 80 分。**你以为自己懂了,其实只是 LLM 帮你脑补了**。

本产品 `source_chunk_ids` / `reference_chunk_ids` / Judge 三层 evidence + `lookup_in_notes_global` tool 是为这点设计:出题严格不超出 chunks,评分严格按 reference_points,凭空声明会被标 fabricated。

## 3.3 本产品壁垒不强(继续诚实)

- GPTs / Project 上传文档 + 严肃 prompt 工程能逼近笔记问答和评分,不是 100% 解决但够用
- JD Intelligence 是当前最难被 LLM 官网填补的部分 — 累积型资产管理需要持久化 + 跨时间聚合 + 可回放报告,跟"chat session"模型本质冲突
- 笔记结构化管理 Obsidian / 语雀做得比本产品好太多,本产品**根本不应该跟它们竞争**

## 3.4 真正的产品定位

> **给学计算机的人,做"读 JD 找方向 → 生成学习路径 → 用笔记面试陪练巩固"的生产力工具。LLM 是被 harness 驱动的执行器,你的 JD 库和笔记才是主角。**

类比:RSS 阅读器没有替代网页,但长期跟踪信息的人不会只靠临时打开几个网页 — 因为收藏、归档、聚合和回看是浏览器单页做不到的工作流。本产品对 LLM 分析 JD 的关系同理。

# 4. 核心闭环(两条主线)

后续功能收束为一条生产力主线 + 一条已落地的练习主线。砍掉 SR / 弱点 dashboard / 岗位类三源出题 / 简历诊断 / 简历上传等后续分支。

```
┌──────────────────────────────────────────────────────────────────┐
│ 主线 A:JD Intelligence Agent(M2.5,生产力主线)                 │
├──────────────────────────────────────────────────────────────────┤
│  上传 JD(文本 / 截图,陆续累积)                                │
│     ↓ jd_parser(立即解析,落库 jds 表)                         │
│  我的 JD 库(累积型资产,可标 title / 删除 / 搜索)              │
│     ↓ 用户点"一键分析"(选范围:全部 / 最近 N 条 / title)     │
│  JDAnalysisAgent harness                                        │
│     load_jds → ocr_if_needed → parse_jd → aggregate_requirements│
│     → dedupe_requirements → recompute_frequency → match_notes?  │
│     → generate_learning_path → write_report                     │
│     ↓                                                              │
│  岗位要求地图 + 高频技能 / 职责 + 学习路径 markdown           │
│     ↓                                                              │
│  quiz topic 候选 / 笔记缺口提示 / 历史报告对比                 │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│ 主线 B:笔记 RAG 面试陪练(M2/M2.1,已落地核心能力)              │
├──────────────────────────────────────────────────────────────────┤
│  写笔记(Web 编辑器 / 本地目录直读)                             │
│     ↓ chunker(heading-aware)+ embedder                          │
│  笔记 chunks(folder_path / heading_path 元数据)                │
│     ↓ 用户在聊天框输 query                                       │
│       · 主题类("考考我多线程")                                  │
│       · 或从 JD Intelligence 报告生成的 quiz topic 候选         │
│  Retrieval pipeline                                                │
│   query rewrite → hybrid + RRF → reranker → governance/blend   │
│   → dynamic clean-context selection;0 命中报"笔记里没这主题"   │
│     ↓ 命中 chunks + 元数据                                       │
│  QuizGenerator(thinking off,反幻觉 source_chunk_ids)           │
│     ↓ 3-10 道题(开放式 + 八股,LLM 自动配比)                  │
│  用户答(笔记面板隐藏 — active recall 强约束;答完恢复)         │
│     ↓ AnswerJudge(thinking on,三层评分 + lookup tool)          │
│  Coverage / Fidelity / Depth + 加权总分                          │
│     ↓ M2.1 InterviewCoachAgent 决策                                │
│  答得好 → 下一题 / 总结;答不好 → 提示缺口 → 补答 → 再判断 │
│     ↓                                                              │
│  session 沉淀到 notes/_recall/{session_id}.md                  │
└──────────────────────────────────────────────────────────────────┘
```

# 5. 用户故事(MVP)

## 5.1 写 / 导入笔记

- **US-1**:作为用户,我可以在浏览器里选本地一个 markdown 文件夹(或单篇 / 多篇 .md),浏览器直接读出来,系统按文件夹层级 + heading 切 chunk 入库 — 不需要先打 zip 上传
- **US-2**:作为用户,我可以在 Web 编辑器(Monaco)里直接写一篇 markdown 笔记,选目标节点(folder),保存后立即入库
- **US-3**:作为用户,我可以在树形导航里看到所有笔记的层级结构(文件夹 → 子文件夹 → 笔记 → heading)
- **US-4**:作为用户,我可以编辑已有笔记;保存后老 chunks 删除新 chunks 入库,不影响其他笔记

## 5.2 出题与答题

- **US-5(M2,主题类 query)**:作为用户,我可以在聊天框输入一个主题("考考我多线程" / "缓存一致性"),系统从全笔记库 RAG 找最相关 chunks(query rewriting → hybrid + RRF → reranker(top50) → post-rerank governance/blend → dynamic clean-context selection → parent-doc 扩展),出 3-10 道题。**0 命中(笔记里没这主题)直接报错,不兜底放宽**
- **US-5a(M2.1,Agentic 面试教练)**:作为用户,我答完一道题后,系统不只是打分,还会基于 Coverage / Fidelity / Depth evidence 决定下一步:答得好进入下一题;漏关键点 / 编造依据 / 深度不足时提示哪里答不好,引导我补答,再对累计答案重新评分。纠偏不设单题固定 1 轮上限,但必须能在达标 / 我选择跳过 / 连续提升很小 / 偏题 / token budget 触发时退出。纠偏必须基于原题的 `source_chunk_ids`、reference points 和 Judge gaps,不允许脱离笔记自由发挥
- **US-5b(M2.5,JD topic 候选)**:作为用户,我可以从 JD Intelligence 报告里一键拿到 quiz topic 候选,再选择其中一个进入主题类 RAG 面试陪练。系统不做岗位类三源出题,只把 JD 分析结果变成可执行练习入口
- **US-6**:作为用户,题型分两类:**开放式**("解释 synchronized 的锁升级过程")+ **八股**("synchronized 的轻量级锁是怎么实现的?")。MVP 不做代码题 / 系统设计题 / 项目深挖题
- **US-7**:作为用户,**答题阶段**笔记面板隐藏(active recall 强约束),只能看题干 + 输入框;答完评分阶段笔记面板恢复(可对照 reference)。笔记面板始终是查看 / 编辑 / 上传入口,但**不再触发出题**
- **US-8**:作为用户,我可以中途退出 session,草稿自动保存,下次进入续写

## 5.3 评分与沉淀

- **US-9**:作为用户,提交答案后看到三层评分(Coverage / Fidelity / Depth)+ 加权总分 + 每层的具体证据(命中的 reference points / 被 fabricate 的陈述)
- **US-10**:作为用户,可以一键展开 reference answer + 关联 chunks 对照
- **US-11**:每个 session 结束生成一篇沉淀 markdown(`notes/_recall/{session_id}.md`),包含题目 / 我的答 / 评分 / reference / 复习建议。**不污染原笔记**

## 5.4 JD Intelligence Agent(M2.5)

- **US-15**:作为用户,我可以单条上传 JD(文本粘贴 / 截图二选一),系统**立即** jd_parser 解析(thinking off,结构化抽取职责 / 硬技能 / 软技能 / 学历经验),解析结果落库 jds 表
- **US-16**:作为用户,我可以查看"我的 JD 库"列表(分页 / 按 title 标签筛选 / 单条删除)
- **US-17**:作为用户,我可以选 JD 库范围(全部 / 最近 N 条 / 某 title),点"一键分析",**单次上限 200 条**;系统自动完成 load_jds / OCR / parser 结果读取 / hierarchical reduce / 同义去重 / 频次重算 / 报告生成
- **US-18**:作为用户,我可以得到一份岗位要求地图:高频技能、职责主题、软技能、经验门槛、业务方向、学习优先级和证据 JD 列表
- **US-19**:作为用户,我可以得到学习路径 markdown 和 quiz topic 候选,并保存历史分析报告(`jd_analyses`)用于对比不同岗位 / 时间窗口

# 6. 功能边界

## MVP(M1 + M2,本地单用户 dogfood)

- US-1 ~ US-4(笔记输入)
- **US-5(M2 主题类 query)** + US-6 / US-7 / US-8(出题答题)
- US-9 / US-10 / US-11(评分沉淀)

## M2.1(Agentic RAG 面试教练)

- **US-5a**:用 `InterviewCoachAgent` 状态机编排 M2 出题 / 等答 / 评分 / 追问 / 总结;把"一次性 RAG 出题"升级为可恢复、可观测、可评测的面试流程

## M2.5(唯一后续主线)

- US-15 ~ US-19 全做(JD Intelligence Agent:累积上传 + 自动解析 + 一键分析 + 学习路径 + quiz topic 候选)

## 已砍掉的后续方向

- 弱点跟踪 / SR / 今日复习 / 空 query 系统自选
- 岗位类三源出题(笔记 + 简历 + JD)
- 简历上传 / 简历诊断 / ResumeAdvisor
- 简历改写或简历相关生产流

## 明确不做(后续路线图都不做)

- 浏览器扩展
- 多用户 / Auth / SaaS 化
- 系统设计题(评分主观,Judge 不靠谱)
- 代码题(评分需要执行环境,工程量爆炸)
- 选择题(active recall 弱)
- 语音输入 / 语音答题
- 笔记 PDF / 图片导入(**笔记**只接 markdown;JD 截图 OCR 例外)
- **所有简历功能**:上传、诊断、改写、按岗位定制、多份简历库都不做
- **笔记面板节点点击触发出题**(永不做 — 出题入口统一走聊天框 query,笔记面板降级为查看 / 编辑 / 导航树)
- **投递追踪**(v1 残留,确认死)
- 语雀 / Notion / 飞书 / Obsidian sync(三方笔记应用各自做得比本产品好)
- 弱点跟踪 / SR / dashboard / 空 query 系统自选
- 岗位类三源出题 / 项目深挖题
- 跨 batch 跨时间增量聚合 JD(MVP 单次上限 200 条已够覆盖)

# 7. 非功能需求(NFR)

- **本地优先**:docker compose 起本地 Postgres + API + Web + Langfuse,数据不出机器
- **BYOK**:用户自带阿里云百炼 API Key,持久化到项目根 `.env`(gitignored)
- **单用户(MVP)**:无 auth,localhost only;后期上 SaaS 再加(M4+)
- **响应延迟**:
  - 出题 P95 ≤ 15s,评分 P95 ≤ 20s(qwen3.6-flash)
  - JD 单条上传(立即解析)≤ 5s
  - JD 一键分析(200 条):P95 ≤ 60s
- **成本**:
  - 单 session(5 题出 + 答 + 评)≤ ¥0.10
  - JD 一键分析(100 条)≤ ¥0.5
- **JD 输入限制**:
  - 单条 JD 文本 ≤ 10k 字符
  - 截图 ≤ 7MB(Base64 编码后受限于阿里云百炼 10MB 上限)
  - 单次一键分析 ≤ 200 条 JD(超过提示拆分多次)
- **数据可移植**:笔记原文 + sessions 沉淀 + JD 原文都在本地,删数据库不丢内容

# 8. NSM(北极星指标)

**短期 dogfood(单用户验证)**:

- **每日 active recall 时长**(目标:每周 ≥ 3 次 session,每次 ≥ 15 分钟)
- **Judge 评分跟人工 ground truth 的 Cohen's kappa**(≥ 0.7)
- **JD Intelligence 省时量**:100 条 JD 一键分析后,用户无需手工整理技能频次与学习路径
- **JD 分析可行动率**:报告中的高频要求能转成学习路径和 quiz topic 候选的比例 ≥ 80%

**长期(SaaS 化后,M4+ 才考虑)**:

- 用户面试通过率提升(自报)
- 周活留存率
- JD 库累积速率(用户每周新增 JD 数)

# 9. 已锁定的关键决策(v2 起)

| 项 | 决策 | 备注 |
|----|------|------|
| 目标用户 | 学计算机的人(全开,只排除非开发岗) | v2 扩展自 v1 锁定的"1-3 年开发者" |
| 笔记输入源 | M1: Web 编辑器 + 本地目录 / 文件直读(File System Access API)| 不做 Notion / 飞书 / Obsidian / 语雀 sync;不接 zip 上传(笔记本来就在本地,免打包)|
| JD 输入源 | 文本粘贴 + 截图(Qwen 多模态)| 累积型,陆续上传;立即解析 |
| JD 单次分析上限 | **200 条**(hierarchical reduce)| 超过提示拆分;不做跨批增量聚合 |
| 后续主线 | **只做 JD Intelligence Agent** | 砍掉 SR / 简历诊断 / 岗位类三源出题 |
| 出题入口 | **聊天框 query**(主题类)或 JD 报告里的 quiz topic 候选;**笔记面板不再触发出题** | 不做岗位类 / 空 query |
| M2 retrieval pipeline | **query rewriting → hybrid + RRF → reranker(top50) → post-rerank governance/blend → dynamic clean-context selection → parent-doc 扩展** | RAG 主战场;每段独立可观测进 Langfuse trace;provider rerank 是 challenger source,不是最终成员裁判 |
| 0 命中策略 | retrieval < 阈值 chunks → **直接报"笔记里没这主题"**,不兜底放宽 | 守住"笔记是主角"边界,LLM 不凭训练数据补 |
| 简历 | **全部砍掉** | 不上传、不诊断、不改写、不参与出题 |
| 题型 | 开放式 + 八股 两类 | 不做代码 / 系统设计 / 选择题 / 项目深挖题 |
| 评分 | LLM-as-Judge 三层(Coverage / Fidelity / Depth) | 权重在 Python,不让 Judge 算 |
| Agent 编排 | M2.1 `InterviewCoachAgent` 状态机 | 高级感来自状态 / 工具 / 分支 / 记忆 / 评测 / 恢复;不做泛化多 Agent 互聊 |
| JD Intelligence Agent | 受控 harness 自动编排 OCR / parser / aggregator / report writer | 目标是替用户读大量 JD 并产出可执行结果 |
| LLM 模型 | qwen3.6-flash(多模态 + 文本一把抓) | Quiz / Judge / JD 截图 / JD 解析共用 |
| LLM thinking | **按 agent 决定,默认 off** | 评分类 / 综合判断类 on,出题 / 解析类 off — 详见 5-AGENT |
| LLM SDK | OpenAI Python SDK,base_url 走百炼 OpenAI 兼容接口 | `from langfuse.openai import OpenAI` 自动 instrument |
| LLM Provider | 阿里云百炼 | 沿用 v1 ADR-0003 |
| Tracing | Langfuse 自部署 | 数据不出本地;详见 2-TECH §6 |
| Tool use | AnswerJudge `lookup_in_notes_global` | 反假阳性 |
| 数据存储 | Postgres 16(pgvector + tsvector)| 沿用 v1 ADR-0002 |
| LangGraph 使用时机 | M2.1 起用 LangGraph 编排面试状态机;M2.5 可扩 JD Intelligence Agent workflow | |
| UI 风格 | macOS 风(Tailwind 自己写,不引组件库) | |
| 部署 | 本地 docker compose(api / web / postgres / langfuse / langfuse-db 五服务)| MVP 单用户 |

# 10. 上次会话遗留的开放问题

- **Q-01** macOS 风具体调色(亮 / 暗双模,毛玻璃 / 圆角具体度数)— M1 启动 Web UI 前再确认
- **Q-02**(已废弃 — 不做语雀同步)
- **Q-03** session 中途换题 / 跳过 / 重答 的 UX — M2 启动前决策
- **Q-04 [已定 2026-05-18]** JD title 标签:LLM 上传时自动从 JD 抽,用户可在 `/jds` 详情页手动改
- **Q-05**(已废弃 — 简历功能全部砍掉)
- **Q-06**(已废弃 — 简历诊断全部砍掉)
- **Q-07 [已定 2026-05-09]** Reranker 走百炼 `qwen3-rerank`(`/compatible-api/v1/reranks`,¥0.0005/千 token,500 doc 上限);本地 `bge-reranker-v2-m3` 作 fallback,M2 不做,有真问题再加 adapter。**坑见 memory `reference_aliyun_dashscope_rerank.md`**:接口路径跟其他 rerank 模型不通用;langfuse.openai 不自动 instrument(同 embedder 要手动包 generation);relevance_score 不可跨请求比较
- **Q-08 [已定 2026-05-09]** Parent-doc **自适应**:命中段长(≥ 阈值)少扩 / 命中段短(< 阈值)多扩到父段;具体阈值 + 父段层级(H2 / H3)在 M2 实施时调,初值倾向"命中段 < 200 字 → 扩到同 H2;≥ 200 字 → 不扩"
- **Q-09**(已废弃 — 岗位类三源出题砍掉;JD 分析只产出 quiz topic 候选)
- **Q-10 [已定 2026-05-09]** 0 命中阈值起步 **< 3**(retrieval 命中 chunks < 3 → 报"笔记里没这主题");dogfood 跑一段看真实分布再调

---

# 不在本文档范围

- 模块分层 / 数据流细节 → `docs/2-TECH_DESIGN.md`
- 表 schema → `docs/3-DATA_MODEL.md`
- API 端点 → `docs/4-API_SPEC.md`
- Prompt 全文 → `docs/5-AGENT_DESIGN.md`
- 评测套件 → `docs/6-EVAL_PLAN.md`
- 里程碑 / 切片节奏 → `docs/7-ROADMAP.md`
- 工程规范 → `docs/8-ENGINEERING.md`
