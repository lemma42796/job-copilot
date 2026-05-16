---
title: JobCopilot 工程踩坑录(Lessons Learned)
owner: lemma42796
last_updated: 2026-05-16
purpose: 项目从 M0 骨架到 M3 W8 期间真实遇到的工程问题 + 根因 + 解决方案,按主题分 9 类。每条字段统一为「症状 / 根因 / 修法 / 沉淀」,链接详细切片归档,作为面向外部读者(招聘 / 协作者 / 博客读者)的索引视图。
---

> **单一可信源** = [STATUS.md](STATUS.md)("永久约束累积"区是各坑沉淀的不可变结论);本文档是**索引 + 详述**,原始细节散在 `slices/` 各切片归档。
>
> **核心条目**(标 ⭐)是面试讲故事最值钱的料,99% 候选人没踩过 / 教程不教。

---

# 1. 反幻觉链路

> 简历定制 reviewer-drafter 链路在 W8 第二轮 dogfood 暴露 3 层联动 root cause,**项目最值钱的故事**。

## 1.1 Reviewer 误把候选人真实教育经历标为编造(假阳性)⭐

- **症状**:第二轮 dogfood 5 条 match 重生成(#4/6/8/11),reviewer 反复在「教育」「语言能力」段标 finding,但内容都是真的(候选人真有 985 学历 / 真的会 Python)
- **根因**:reviewer 复用了 drafter 用的 `retrieve_for_match` Top-K chunks。Top-K 按 JD 相关性排序,JD 偏 AI Agent 时 → 教育 / 语言类 chunks 直接被挤出 Top-K → reviewer 视角"chunks 中无证据" = 假阳性。同时 reviewer 没拿到 `candidate` Profile 字段(姓名 / 联系方式 / 求职意向 / educations 这些 deterministic 数据**永远不在 chunks 里**),教育 / 基本信息全部被误判
- **修法**:`retrieval_service.load_all_profile_chunks` + `ResumeGraphState.all_chunks` + `review_resume(candidate=...)`;reviewer prompt **v1.0.3** 把"chunks 是召回子集"改为 profile 全量 + 显式告知 "Profile 字段同等可信",加 M7 教育/基本信息核查与 Profile 字段比对
- **沉淀**:[STATUS.md 永久约束 [来自 S21]](STATUS.md)"Reviewer 是单文档全文事实核查,不走 JD-anchored Top-K"。**教训**:不同 agent 的"信任输入"必须差异化设计 — drafter 要相关性(Top-K),reviewer 要完整性(全量 + deterministic 字段)

## 1.2 Drafter 镜像 JD,把候选人没有的技能抄进简历 ⭐

- **症状**:resume #18 / #19 生成的简历里出现 C++ / Java / OpenAI / Claude / LLaMA 等技能,但候选人 profile / chunks 里完全没有这些
- **根因**:drafter prompt v1.0.4 没有"严禁 JD-only 技能"的硬约束,模型为了凸显 JD 对齐度自动镜像 JD 的 hard_skills。**深层原因见 1.3**(prompt hint 在反向诱导)
- **修法**:drafter prompt **v1.0.5** 加 D.3 严禁 JD-only 技能 + 机械自检("写每条技能前先扫一遍 chunks,无证据立刻删")+ 心智模型("简历 = 真实能力 ∩ JD 关心方向"子集);**v1.0.6** 加 hint 段防注入语
- **沉淀**:这层修不彻底(v1.0.5/1.0.6 加了约束但 hint 仍在诱导),真正治本在 1.3。**教训**:prompt 加约束防镜像是治标,要找污染源

## 1.3 `_compose_hint` 文案反向诱导(prompt hint 鼓励性文案 = 幻觉源头)⭐⭐

- **症状**:1.2 的 v1.0.5/v1.0.6 加约束后仍然偶发镜像 JD,且 #20 出现"12w QPS 保障 AI 服务高可用"这种 chunks 里没有的业务量化
- **根因**:**最反直觉的一层**。`service._compose_hint(match)` 把 match 模块的 `gap_summary + missing_skills` 拼成 drafter prompt USER 段 hint 文本,原版文案是"缺失关键技能(可在简历中**补强**相关项目/课程)"——**这等于明确命令 drafter 把候选人不会的技能写进简历**,prompt 加再多约束都跑不过这条直接指令
- **修法**:`_compose_hint` 改反向警告语义 — gap_summary 加"**只读差距分析**(不要写入简历;gap 信息归 match 模块负责告知用户)";missing_skills 改"**严禁列入简历的技能**(候选人 chunks 没有,JD 要 — 列了就是编造)"。drafter prompt v1.0.6 USER 段 hint 块标题从"参考,辅助强化简历对 JD 的针对性"改成"**只读 — 严禁成为简历内容来源**"
- **验证**:resume #21 (match #4 / JD #9) 一轮 0 finding,JD-only 技能全消失,M1 跨 chunk 错配也顺带消(根因不在 prompt 加约束,在 hint 引导污染)
- **沉淀**:[STATUS.md 永久约束 [来自 S21]](STATUS.md)"hint 是 LLM 视角的权威指令位,文案必须反向警告而非'补强'诱导"。**教训 / 心智模型**:简历 = 候选人真实能力 ∩ JD 关心方向;**凸显交集,不补缺集**。Gap 信息归 match 模块,不归简历

## 1.4 ProfileParser description 幻觉:从 bullets[0] 改写复述

- **症状**:profile_parser v1.0.0 在工作经历的 `description` 字段里复述 / 改写 `bullets[0]` 内容,产生似真而假的描述
- **根因**:prompt 没明示 description 与 bullets 的边界,模型默认认为应该"提供概括",于是去抄 bullet 改写
- **修法**:profile_parser **v1.0.1** prompt 显式禁止描述与 bullets 的内容重叠,description 必须是**简历原文里另起一段的概述句**,找不到就留空
- **沉淀**:[详见 profile-parser-bugs-2026-05.md §B1](slices/profile-parser-bugs-2026-05.md)。**教训**:模型默认会"乐于助人"地补全 — 留空的字段必须明确告诉它"找不到就留空,不要造"

---

# 2. Agent / 状态机

## 2.1 LangGraph checkpointer ormsgpack 序列化炸 ORM 对象 ⭐

- **症状**:首次 W7 dogfood 触发 revise 路径时 raise `Type is not msgpack serializable: Jd`。retrieve / plan / draft / review 节点流式事件先吐到前端(用户看到 4 个 ✓),graph 在 revise 路径要 `aput_writes` 时才 raise → service 捕获 → 前端收 `error → done(ok=false)` 不跳转
- **根因**:**langgraph 0.2.x 所有 checkpointer**(包括 MemorySaver,docstring 误以为不序列化)都走 `JsonPlusSerializer + ormsgpack`。我 state 里放了 SQLAlchemy ORM 行(`Jd` / `ProfileChunk`)+ dataclass(`LLMResult` / `RetrieveResult` / `ResumePlan` / `ResumeReview`),都不在 ormsgpack 内置类型表
- **修法**:`workflow.compile()` **不传 checkpointer**(默认值)。业务上单 SSE 请求生命周期内不需要跨进程恢复,W7 不开 interrupt,本来就不需要持久化 state。运行时依赖通过 `ResumeGraphDeps` 闭包注入节点,不放 state。state 全程内存里直接传引用,允许放 ORM 行 + dataclass
- **沉淀**:[STATUS.md 永久约束 [来自 S19/S20 修订]](STATUS.md)"LangGraph state 字段不放运行时依赖,允许放 ORM 行 + dataclass(因为不带 checkpointer)"。[详见 S19-S20 §坑 #2](slices/S19-S20-w7-resume-graph.md)
- **未来路径**:真要加 checkpointer(中断恢复 / 长时任务),设计 ID-引用 serde — checkpoint 只存 ORM 主键,反序列化时 sessionmaker 重新 load。**教训**:框架文档说"不序列化"不一定是真的,任何"持久化能力"都隐含序列化要求,生产前必须实测 revise / failure 路径

## 2.2 Graph 节点该不该 catch 业务异常(分层错误处理边界)

- **症状**:S19 第一版每个节点都 `try/except`,把错误塞进 state(`state["error"] = str(e)`),后续节点判断 state.error 短路。问题是错误码 / 错误分类全混进 state,service 层无法 by class 分发处理
- **根因**:把错误处理放进 graph 节点 = 把状态推进器和错误处理器揉在一起,职责混乱
- **修法**:形成原则 — **graph 节点不吞业务异常**,raise `LLMUpstreamError / LLMTimeoutError / LLMSchemaInvalidError / ResumeGenerationFailedError` 后冒泡到 service 层。Service 在 except 块按 class 分发错误码 + 调 `_mark_failed`(side-channel commit,sessionmaker 开新事务避免污染主路径)+ re-raise 给 router 转 SSE `error → done(ok=false)`
- **沉淀**:[STATUS.md 永久约束 [来自 S19]](STATUS.md)"LangGraph 节点不吞业务/LLM 异常,由调度层(service)集中 mark_failed"

## 2.3 永久约束写反:把未实测的设计前提当事实

- **症状**:S19 commit 时在 STATUS.md 沉淀的"State 只放可序列化业务数据(ORM 行 + dataclass)"是**假的** — ORM 行不可序列化。当时是把 docstring 抄进 STATUS.md,没实测
- **根因**:写"永久约束"前没有跑通的代码佐证,把未验证的设计前提当事实
- **修法**:S20 修订把约束改成"**无 checkpointer = state 全程内存里直接传引用**,允许放 ORM 行 + dataclass",标 `[来自 S19 / S20 修订]`,记录纠错过程
- **教训**:沉淀永久约束前必须跑过对应代码路径(尤其失败 / revise 等非 happy path)。**约束记错比不记还危险** — 后续切片会基于错约束做设计决策

---

# 3. RAG / Retrieval

## 3.1 Reviewer 不该走 JD-anchored Top-K(本质是信任输入差异化)

- **症状**:见 [1.1](#11)
- **根因**:不同 agent 的"信任输入"语义不同,但 S19 把 reviewer 的 chunks 输入直接复用了 drafter 的 `retrieve_for_match`(JD-anchored Top-K),把"相关性召回"当成"事实核查依据"
- **修法**:reviewer 走 `load_all_profile_chunks`(全量) + 直接拿 candidate Profile 字段(deterministic 数据)。短期是临时兜住;长期根治 = [子任务 4-A](STATUS.md) Hybrid Search + RRF 提整体召回率
- **沉淀**:见 [1.1](#11)。**通用原则**:事实核查类 agent **默认全量 + deterministic 字段并发**,不要复用 drafter 的相关性召回

## 3.2 Retrieval 没有量化召回率(评测盲区)

- **症状**:从 M2 到 M3 W8,retrieval 模块没有任何召回率量化。bug 1.1 暴露后才意识到"我们不知道当前 retrieve_for_match 召回到底多准"
- **根因**:M2 评测扎根阶段优先做了 jd_extract / profile_extract,retrieval 套件被推后但一直没起
- **修法**:子任务 4-A 顺手做 `evals/suites/retrieval/` 20 对 ground-truth(人工标"应该召回哪些 chunks"),量化 `Recall@10 / NDCG@10`,跑 v0(纯向量) vs v1(hybrid + RRF) ablation
- **沉淀**:v1 子任务 4-A。**教训**:RAG 模块没量化召回率 = 在裸奔,bug 1.1 是必然会出现的

## 3.3 中文向量召回对专有名词不稳

- **症状**:dogfood 时 JD 写"LangGraph 经验",候选人 chunks 里有"LangChain / LangGraph 实战",纯 pgvector cosine 召回不到该 chunk(被其他语义相近的内容挤出 Top-K)
- **根因**:中文专有名词(LangGraph / Kubernetes / TypeScript)在 embedding 空间里没有强 anchor,纯向量召回不稳。**Hybrid search(BM25 + 向量)是中文场景的合格线**
- **修法**:子任务 4-A — Postgres `tsvector`(zhparser/pg_jieba 中文分词) + pgvector 双路召回,application 层 RRF 融合(`1/(k+rank)`,k=60)
- **沉淀**:v1 子任务 4-A

## 3.4 评测脚本不要在 noop 观测模式下频繁构造 SDK client

- **症状**:hybrid_search note/chunk smoke 已写出 report / trace,但进程在收尾阶段疑似卡住,需要手动 kill。
- **根因**:CLI 脚本不经过 `main.py` 的 Langfuse env mirror;无 key / noop 场景下仍反复 `Langfuse().generation()`,SDK 仍注册 background consumer 与 atexit shutdown。脚本退出时清理线程比业务路径更难观察。
- **修法**:抽 `infra.langfuse` helper:只有 public/secret key 齐全才构造 Langfuse client,手动 generation 走进程内复用;DashScope OpenAI-compatible client 也改成有 key 才走 `langfuse.openai`,否则走原生 OpenAI client。评测脚本收尾只关闭已存在的 singleton,不为了 cleanup 反向构造 embedder / llm client。
- **沉淀**:观测 SDK 的 noop 模式不能默认等同于"零资源";CLI / eval / batch 脚本要把 observability client lifecycle 当成显式资源管理。

## 3.5 Reranker metadata 不等于结构化过滤(M2.1 评测沉淀)

- **症状**:给 qwen3-rerank 的 document 从纯 content 改成 `folder_path + heading_path + content` 后,hybrid_search note smoke 的 `candidate_recall@50` 不变,但 `rerank_recall@10` 从 73.33% 降到 67.50%。具体表现: `hs_note_001` 丢掉 `#434 Query Rewrite 与召回扩展 > JobCopilot 的 rewrite 边界`;`hs_note_010` 丢掉 `#2214 JobCopilot M2 RAG pipeline`;同时 `hs_note_005` 被 metadata 救回一个 `#2259 Client disconnected`。总通过数从 6/12 到 7/12,但不是净提升。
- **根因**:qwen3-rerank 的 `documents` 只是字符串数组,没有弱 metadata 字段。把 folder / heading 放到 content 前面,等于把它们变成强正文信号。默认 instruct 是通用 web-search QA,不会区分"标题/题库/评测样本语义相近"和"chunk 正文能直接作为 direct evidence 回答 query"。因此题库、anchor 汇总、评测示例、hard-negative 这类 heading 很贴 query 的近邻材料被抬高,挤掉部分真正 direct evidence。
- **修法**:metadata 不再当"无风险增强"处理。已保留的折中方案是 direct-evidence instruct + `content + weak_source_context` 后置格式(report `20260514-092218`:8/12,`rerank_recall@10` 69.17%,成本 ¥0.251666),比 metadata 前置更稳但仍未达标。后续改 reranker 输入时必须做 A/B:至少比较 `content_only`、`content_then_path`、`path_then_content` 与更严格 direct-evidence instruct;同时记录 token 成本和 hard-negative intrusion。
- **后续实验**:intent × chunk-type 本地降权试过两版。初版误把 `rag/Hard Negative 概念对照表` 里的正常定义 chunk 判成 hard-negative;修正后 hard-negative intrusion 从 4/12 到 3/12、MRR 到 75.33%,但 `rerank_recall@10` 只有 65.83%、final recall 69.17%,低于保留版,所以没有进主路。这个方向要先把 `query_intent` / `chunk_type` / provider rank / adjustment / adjusted_score 打进 report,再离线调 penalty 表。
- **沉淀**:RAG 文档里的"标签 / 元数据优化"语义是结构化过滤,不是把 metadata 粗暴拼到 reranker 文本前缀。metadata 能救私有事实样本,也会制造标题相似偏置;本地降权也可能误伤 direct evidence。是否保留要看 per-case trace,不能只看 pass 数或单个 headline。

---

# 4. Streaming / SSE

## 4.1 LangGraph astream 不能挤回 LLM token(asyncio.Queue sidechannel 方案)⭐

- **症状**:S21 子任务 1 要做"drafter token 流式预览到前端",但 LangGraph 的 `graph.astream` 只在节点边界 yield 事件,**LLM token 是节点内部异步事件,挤不回 astream**
- **根因**:graph 框架的事件流和 LLM provider 的 streaming token 是两个独立异步流,graph 抽象不暴露节点内部事件
- **修法**:用 `asyncio.Queue` 做 sidechannel:`_runner` 后台 task 跑 graph + 把 `node_completed` / `final` 入队;outer 协程消费 queue + yield SSE。LLM token 由 `LLMClient.complete(on_token=...)` 回调闭包到 `ResumeGraphDeps.on_drafter_token(phase, delta)`,delta 入同一队列,统一出口走 SSE。失败语义保留:`runner_error: BaseException | None` 捕获后在主协程末尾按原 except 链路 mark_failed + raise(LLMUpstream 502 / SchemaInvalid / ResumeGenerationFailed)。客户端断开时 `runner_task.cancel()` + `contextlib.suppress(BaseException) await runner_task` 保证清理
- **沉淀**:[STATUS.md 永久约束 [来自 S21 子任务 1]](STATUS.md)。**教训**:graph 框架不开放节点内部事件流是**故意的**(职责清晰),硬塞回去会破坏抽象。sidechannel 是合规绕开方式 — 不污染 graph,仍保住失败 / cancellation 语义

## 4.2 客户端断开时的 cancellation propagation

- **症状**:用户点了"生成简历"中途关掉浏览器,后端继续跑 LLM(token 烧完才停),浪费成本
- **根因**:SSE 链路只有上行(server → client),client 断开后 server 不知道,需要主动检测
- **修法**:见 4.1 — `runner_task.cancel()` + `contextlib.suppress` 保证 graph + LLM 调用都终止。LLMClient → Provider 的 cancel token 暂未贯穿(目前靠 asyncio.CancelledError 自然冒泡,没显式 abort HTTP 流)
- **沉淀**:[STATUS.md 永久约束 [来自 S21 子任务 1]](STATUS.md)。**未来**:贯穿 LLMClient → Provider 的 cancel token 链路,真正显式 abort HTTP 流

## 4.3 STANDARD tier 30s timeout 全炸(MatchAnalyst MVP)

- **症状**:S15 dogfood 时 POST /v1/matches SSE 显示"匹配失败:Request timed out",`llm_calls` 表 `error_code='timeout'` `latency_ms=276963`(3 次重试 × 30s 全超时)
- **根因**:STANDARD tier(thinking_mode=True)+ 10 chunks + 大 prompt + 多段输出,单次 LLM 调用 > 30s
- **修法**:`Tier.STANDARD → Tier.CHEAP` + `DEFAULT_TIMEOUT_S = 60.0`(覆盖 CHEAP 默认 30s)。偏离 AGENT_DESIGN §6.2(原描述"STANDARD 起"),归档卡明记
- **沉淀**:[详见 S13-S15 §dogfood 调整 #3](slices/S13-S15-match-mvp.md)。**教训**:tier 选型必须用真实 prompt + 数据量预跑,不能照设计文档抄

---

# 5. Prompt 工程(JDParser / ProfileParser 经 32 类 bug 迭代)

> 完整列表见 [jd-parser-bugs-2026-05.md(26 类)](slices/jd-parser-bugs-2026-05.md) + [profile-parser-bugs-2026-05.md(6 类)](slices/profile-parser-bugs-2026-05.md)。下面挑代表性 7 条。

## 5.1 JDParser OR 关系误抽成 AND(B1)

- **症状**:JD 写"熟悉 React 或 Vue",抽出来 hard_skills 是 [React, Vue] 当成必须都会
- **根因**:prompt 没区分 OR / AND 关系,默认全部入 hard_skills
- **修法**:prompt v1.0.5 加 OR 识别规则,产出 `or_groups: [[React, Vue]]` 让下游(match_analyst)做"任一满足即命中"判断
- **沉淀**:[详见 §B1](slices/jd-parser-bugs-2026-05.md)

## 5.2 JDParser 应届岗位 years_required 不补 0(B3)

- **症状**:JD 明确"应届毕业生",但 `years_required` 字段为 null,下游 match 时按"无要求"处理
- **根因**:prompt 没有"应届 = 0 年"的映射规则
- **修法**:加规则"应届 / 校招 / 实习生 → years_required = 0"

## 5.3 JDParser BOSS 平台标签污染 hard_skills(B4)

- **症状**:hard_skills 里出现"五险一金""周末双休""带薪年假"等平台 tag
- **根因**:OCR 把 BOSS 页面侧栏 tag 一起抠进来,prompt 没过滤平台元素
- **修法**:prompt 加显式排除清单(平台福利 tag / 公司性质 / 招聘平台名)

## 5.4 JDParser 学术 / 产品 / IDE 名当 hard_skill(B7-B10)

- **症状**:抽出"Transformer / GPT-4 / VSCode / Cursor / Boss直聘"当 hard_skill
- **根因**:模型分不清"研究方向 / 厂商产品 / IDE / 平台"vs"实际技能"
- **修法**:prompt 分类清单 + 每类反例。**反直觉发现**:加反例比加正例有效 — LLM 模仿正例容易,识别反例边界更难,显式列反例帮模型校准

## 5.5 JDParser 同源 OCR 跑两次 hard_skills 命名抖动(B22)

- **症状**:同一张 JD 截图跑两次,hard_skills 里"k8s" / "Kubernetes" 命名不一致
- **根因**:prompt 没规定命名归一规则,模型在等价命名间随机选
- **修法**:prompt 加命名权威规则("Kubernetes 优先于 k8s,React 优先于 ReactJS")。**教训**:LLM 输出的稳定性和正确性是两件事,稳定性需要单独的归一规则

## 5.6 ProfileParser end_date 过度 null(B2)

- **症状**:简历写"2020.6 - 2023.5",抽成 `end_date=null`(模型理解成"至今")
- **根因**:prompt 没明示"明确写了结束日期 → end_date 必须非 null"
- **修法**:profile_parser v1.0.1 加 end_date 规则,显式区分"至今 = null"和"明确日期 = 必填"

## 5.7 ProfileParser 中文等级词错位(B3)

- **症状**:简历写"熟练 Python",抽成 `level='intermediate'`(模型按字面英语翻译)
- **根因**:中文"熟练"≈ 英文 advanced,但模型按字面翻译成 intermediate
- **修法**:prompt 加中文等级词映射表(精通/熟练/掌握/了解 → expert/advanced/intermediate/beginner)

## 5.8 修 prompt 必须 bump 版本号

- **沉淀**:不论改多小,prompt 文件名 `vX.Y.Z.j2` 必须 bump,旧版保留(便于回退 + ablation)
- **教训**:prompt 是产品代码,版本控制纪律必须严格

---

# 6. 前端 / UI

## 6.1 Reviewer 引文跨 block 高亮失败(全局偏移重写)⭐

- **症状**:reviewer 给的 finding 带 `quoted_text`(引用简历某段),前端按 markdown block 逐块跑 `findQuotedInText` 匹配高亮。但 reviewer 引文经常跨 block(整章节 / 含 ## H2 / 多个 bullet),逐块匹配各档 fallback 全失败,DOM 里找不到 `<mark>`
- **根因**:逐 block 匹配本质上是把整篇文档分片后单独搜索,跨片引用必然 miss。fuzzy 正则也对中英标点等价化 / 全半角 / 空白归一化处理得很丑
- **修法**:在整段 markdown 上一次拿全局 `[start, end)` 偏移,`parseBlocks` 给每个 block / bullet item 记 `textOffset`,渲染时让每个 block 取本段范围内的交集做高亮 — 跨段引用在每个相关 block 各自高亮自己那一截。fuzzy 匹配用 normalized substring(去空白 + 中英标点等价化 + 反向索引映回原始偏移)替代正则。同时去掉了 `ctx.remaining` 这种渲染期可变状态
- **沉淀**:[STATUS.md 永久约束 [来自 S21 子任务 3]](STATUS.md)"LLM 复述类引用的高亮匹配做在原始整段 markdown 上,不分块后逐块匹配"。**教训**:LLM 引用类 UI 匹配,默认在原始全文上做全局偏移,不要被前端的 block 分片诱导

## 6.2 obsolete vs bogus 区分(跨版本 markdown 求并)⭐

- **症状**:reviewer 的 quoted_text 在当前 markdown 找不到时,UI 一开始统一标"已处理"灰化。但 dogfood 发现两种语义被混淆:**有些 finding 用户从来没动过**,也被标"已处理",误导用户以为自己处理过
- **根因**:`obsoleteFindings` 只看当前 `resume.markdown`,没考虑历史版本。reviewer 凭空捏造 quoted_text(本会话见过 reviewer 凭空写"AWS")应跟"用户编辑后消失"区别开
- **修法**:`obsoleteFindings` 改用所有 `versions[].markdown` 拼成"曾经出现过"全集判定:(a) **obsolete** = 在某个历史版本里出现过,但当前已不在(用户编辑/采纳掉了)→ 标"已处理"绿色;(b) **bogus** = 任何版本都没出现过(reviewer 凭空捏造)→ 标"标记可能有误"黄色。两态显示行为合并(都灰化 + 隐藏"采纳"按钮 + dismiss 改"从列表移除"),但用户提示语必须不同
- **沉淀**:[STATUS.md 永久约束 [来自 S21 第二轮 dogfood]](STATUS.md)"Reviewer findings UI 需要区分 obsolete vs bogus 两态"。**教训**:LLM 引用 + 当前文档对照类 UI,默认要 (a)/(b) 区分,不要简单"是否在当前文本"二分

## 6.3 profile-card 副标题泄漏 parse_model

- **症状**:profile 列表卡片副标题显示"上海 · qwen3.6-flash"(qwen3.6-flash 是内部解析模型名)
- **根因**:S5/S7 拼 meta 字符串时把 `profile.parse_model` 当 metadata 拉进来
- **修法**:`meta = profile.location?.trim()` 去掉 model 字段
- **沉淀**:[STATUS.md M1 永久约束 #22](slices/M1-summary.md)"用户列表页不暴露内部命名(模型名 / call_id / 内部状态码)"。**教训**:列表页加 metadata 字段时过一遍"用户视角是否有意义"

## 6.4 Next dev `.next` cache 时间戳 404 → SVG 铺满屏幕

- **症状**:SVG 图标突然铺满整个屏幕,layout 完全失效
- **根因**:next dev 的 `.next` cache 的 css 时间戳与 SSR 渲染的 `<link href=...?v=N>` 不同步,query string 命中 404,layout.css 未加载,sidebar 的 `<aside class="w-[220px]">` 失效,SVG 不受 `size-4` 约束
- **修法**:`rm -rf apps/web/.next` 后 next dev 重启
- **沉淀**:工具层偶发,不进永久约束。**教训**:Next.js 多进程同时写 `.next` 时偶现,优先重启 dev 而不是怀疑代码

---

# 7. 评测 / 部署

## 7.1 LLM-as-Judge "评委即被评者"偏差(v1 教训)

- **v1 预期问题**:Judge 跟 evaluatee 都是 LLM 产物时,同模型自评容易偏高 5-10pp
- **v1 当时修法**:Judge 与 evaluatee 分模型,并每季度抽 50 条人工复核计算 Cohen's kappa(`κ = (po - pe)/(1 - pe)`,**pe 这一项是关键** — 直接用 accuracy 反映可靠性会高估),低于阈值触发 Judge prompt 改版 + 历史结果重跑。**v2 不沿用分模型**:评委是 LLM、被评者是人类答题文本 / 人类简历,统一 qwen3.6-flash,仍用 kappa 守门。
- **教训**:LLM-as-Judge 落地必须配可靠性验证,否则评测数字本身不可信。v2 详见 `docs/5-AGENT_DESIGN.md` §2.1 与 `docs/6-EVAL_PLAN.md`。

## 7.2 uv workspace 装 apps/api 新依赖必须 `--all-packages`

- **症状**:S19 加 `langgraph>=0.2.50` 到 `apps/api/pyproject.toml`,但 `uv sync` 不装到 `.venv`。表象:API 启动 `ModuleNotFoundError`,uvicorn `--reload` 死循环;前端 SSR 调 API 一并堵死,浏览器看到"打不开前端"假象
- **根因**:仓库是 uv workspace,裸 `uv sync` 默认只 sync root package,workspace member(apps/api)新加 dep 必须 `--all-packages` 或 `--package jobcopilot-api`
- **修法**:`uv sync --all-packages`
- **沉淀**:已入自动 memory `project_uv_sync_workspace.md`。[详见 S19-S20 §坑 #1](slices/S19-S20-w7-resume-graph.md)

## 7.3 uvicorn `--reload` 卡死时 SIGTERM 无效要 SIGKILL

- **症状**:LangGraph serde 错误触发后,uvicorn 母进程一直 reload-loop 但 worker 都死着,SIGTERM(`kill <pid>`)没反应。同时 next dev 也"看似 LISTEN 实则不响应"(SSR 在等已死的 API)
- **修法**:`pkill -9 -f "uvicorn jobcopilot_api"` + `pkill -9 -f next` 一起清,再启
- **教训**:dev 进程卡死时优先 SIGKILL,不要乐观 SIGTERM 等。**通用**:uvicorn `--reload` + 子进程 raise 异常的组合容易出僵尸状态

## 7.4 DATA_MODEL §3.9 与 SSE 实现冲突(matches.score nullable)

- **症状**:DATA_MODEL §3.9 规定 `matches.score NOT NULL`,但永久约束 #4 要求 SSE 起手必须 INSERT pending 行(score 还没算)
- **根因**:文档 schema 设计先于 SSE 实现,没考虑"phase-1 INSERT 时 resource 还没数据"的两阶段模型
- **修法**:偏离 DATA_MODEL,加 `match_status` enum(pending / scored / failed),`score` 改 nullable + check constraint(NULL OR 0..100)。migration / model docstring 写明偏离原因
- **沉淀**:[详见 S13-S15 §设计决策](slices/S13-S15-match-mvp.md)。**教训**:文档 schema 与实现冲突时,优先 SSE 实现倒推 schema(因为 SSE 是用户体验路径,schema 可以加列)

## 7.5 git rm 自动 stage 但 Edit/Write 不自动 stage,混合操作必须 git status 确认

- **症状**:M0 砍 v1 commit `82fe749` 漏带 5 个文件(main.py / models/__init__.py / sidebar.tsx / titlebar.tsx / page.tsx)的 Edit/Write 修改,HEAD 处于 broken 状态:main.py 还 import 已删的 v1 routers,models/__init__.py 还 import 已删的 v1 models,sidebar 还引用已删的 v1 路由 — FastAPI 启动 ImportError,Next.js typed-routes 编译炸
- **根因**:操作顺序是先 `git rm -rq v1_*`(自动 stage 删除)→ 再 Edit/Write 改沿用层(**不会自动 stage**)→ 最后 `git add docs/STATUS.md`(只 stage STATUS)→ `git commit`。结果只 110 个删除 + STATUS 进 commit,5 个修改留在 working tree。当时没有 commit 前跑 `git status` 确认,直接 commit message 描述了"修改 5 文件"但实际没 stage
- **修法**:紧跟下一个 commit `32af0db` 把漏带 5 文件 + SDK 切换一起补(原本应该在新 commit 里 push,所以 fix 不影响生产);commit message 老实写明"上次漏带 + 同步落 SDK 切换",不掩饰
- **沉淀**:工程纪律 — **commit 前永远 `git status --short` 确认每个该进的都在 staged 列(M / D / A)**;不要假设"我做了 X 操作 staged 自然带"。`git rm` 跟 `git add` 才自动 stage,Edit/Write 不会。复合操作(git rm + Edit 混跑)更要确认。**教训**:大批量操作(110 文件)一气呵成才 commit 容易漏;混合操作时把 `git rm` 跟 Edit/Write 拆成两个独立 commit 也是合理的(各自纯一种操作类型,不会混淆 stage 状态)

## 7.6 阿里云百炼 OpenAI 兼容接口集成关键事实(v2 LLM SDK 切换)

详细参考:`memory/reference_aliyun_dashscope_openai_compat.md`(reference 类 memory)+ 8-ENGINEERING §11.1 + 5-AGENT §2 通用约定。

- **base_url**:`https://dashscope.aliyuncs.com/compatible-mode/v1`(注意是 `compatible-mode` 不是 `compatible-api`,后者是 reranker 的)
- **thinking 模式**:走 `extra_body={"enable_thinking": True/False}`(OpenAI 标准协议没有 thinking 参数,百炼通过 `extra_body` 透传)
- **tools / function_call**:走 OpenAI 标准 `tools=[...]` + `tool_choice` 协议,Qwen 兼容良好(详见 5-AGENT §4.7 AnswerJudge tool use)
- **qwen3.6 系列整体是多模态视觉模型**:同一个 model id(`qwen3.6-flash`)同时吃文本 / 图像 / tool use,不需要切 vision-only 变体
- **流式响应**:走 OpenAI 标准 `stream=True` + delta token chunk 协议
- **JSON 强制输出**:走 `response_format={"type": "json_object"}`,Qwen 兼容良好(~95% 合规)
- **Context Cache 不是会话记忆**:多轮对话仍要把历史 / chunks 放进本次请求上下文;cache 只复用公共前缀的 provider 侧计算和计费。不要误以为"第一次发过 chunks,第二次模型自己记得"。
- **qwen3.6-flash 支持 cache 缓存**:OpenAI 兼容 Chat / DashScope 原生 / Anthropic Messages 支持显式与隐式缓存;Responses API 走 Session 缓存。项目当前走 OpenAI 兼容 Chat,无需为 cache 迁移到 DashScope 原生。
- **显式 cache 形态**:`messages[*].content` 改数组,在稳定长文本 content 上加 `cache_control: {"type":"ephemeral"}`;最少 1024 tokens,最多 4 个 marker,有效期 5 分钟。Quiz / Judge 应把 session chunks 放到稳定公共前缀,动态任务放后面。
- **联网搜索**:本项目**禁用**(`extra_body={"enable_search": False}` 显式关)— Quiz / Judge 必须严格基于用户笔记 chunks,联网会引入超笔记范围内容,直接撞 §1.1 假阳性

**教训**:OpenAI 兼容接口 ≠ 100% 等价于官方 OpenAI。Qwen 特有的 thinking / search 等能力走 `extra_body` 透传,**不是 OpenAI 标准参数,切换到真 OpenAI 会被忽略或报错**。多 provider 抽象的真实成本见 §8.7。

## 7.7 百炼 reranker 接口与 embedding 同样需手动 instrument(M2 设计阶段沉淀)

详细参考:`memory/reference_aliyun_dashscope_rerank.md`(reference 类 memory,2026-05-09 校对)+ 5-AGENT §2.7.5 + 8-ENGINEERING §11.1。

- **`langfuse.openai` auto-patch 只覆盖 chat / completions / responses 共 11 个方法**,不覆盖 `embeddings.create` 也不覆盖 reranker(reranker 不在 OpenAI 协议标准里)
- M1 第 9 步已经踩过一次:embedder 走 `langfuse.openai` 不进 trace,改成手动 `Langfuse().generation()` 包成功 / 失败两路径
- M2 retrieval pipeline 加 reranker 时**复用同款手动 generation 模式**,不是再次踩同样的坑

**坑提前防御清单**(后续每加一类新 LLM 调用类型都过一遍):
1. `langfuse.openai` 是否自动 instrument 该端点?— **不是 chat/completions/responses 默认就不是**
2. 接口走哪个 base path?— Qwen 系产品里 chat 走 `/compatible-mode/v1`,reranker 走 `/compatible-api/v1/reranks`,**不是同一套**
3. langfuse SDK 锁 <3.0(server v2 不支持 OTLP),env mirror 必须早于 routers / agents / llm 的 import,见 STATUS 永久约束
4. `relevance_score` 类得分**不可跨请求比较**(reranker 文档明确说),不要存 DB 当跨 session 指标

**教训**:任何"持久化能力"都隐含序列化要求(§8.1);任何"自动 instrument 能力"都隐含**协议覆盖范围**要求,加新调用类型前先确认 langfuse 是否支持自动 instrument,不支持就走手动路径。

---

# 8. 跨切片普适教训(meta-lessons)

> 不针对单个 bug,是踩多次后形成的元认知。

## 8.1 任何"持久化"能力都隐含序列化要求

- LangGraph checkpointer / Redis cache / Postgres jsonb / 任何"中间状态保留"机制,**必须实测 revise / failure 路径**才能确认 ORM 行 / 复杂对象能否过这道关
- 反面教材:[2.1](#21) MemorySaver 文档说"内存不序列化",实测发现走 ormsgpack

## 8.2 Prompt 是产品代码,有版本号 / 测试集 / 防回归

- 改 prompt 必须 bump 版本号,旧版保留(便于回退 + ablation)
- 每类 bug 进 dataset.jsonl 防回归 — JDParser 26 类 bug → 26 条 fixture
- 不能"凭感觉改完跑一次能用就 commit",要跑全套 evals

## 8.3 不同 agent 的"信任输入"必须差异化设计

- drafter 要相关性(JD-anchored Top-K)
- reviewer 要完整性(全量 chunks + deterministic 字段)
- match_analyst 要并发(笛卡尔积候选)
- 一刀切复用同一 retrieval 接口是 [1.1](#11) 的根因

## 8.4 LLM 视角的权威指令位 = prompt USER 段 hint / instruction / 提示

- "鼓励性"文案在权威指令位 = 命令 LLM 执行该行为(见 [1.3](#13))
- 默认走**反向警告语义**,不要"提示性"鼓励
- Gap / 缺失 / 错误信息归对应业务模块,不要通过 prompt hint 注入到不相关 agent 的指令位

## 8.5 沉淀永久约束前必须有跑通的代码路径

- 不要把未验证的设计前提当事实(见 [2.3](#23))
- happy path 跑通 ≠ 约束已验证,必须包括 failure / revise / cancellation 等非 happy path
- 约束记错比不记还危险

## 8.6 状态推进器和错误处理器分层

- Graph / state machine 节点 = 推进状态,不吞业务异常
- Service / 调度层 = 集中错误处理,by class 分发错误码 + side-channel commit
- Router / SSE 出口层 = 把错误转 protocol 事件
- 三层职责清晰,任意一层揉进别层职责都是后续维护痛点

## 8.7 多 provider 抽象的真实成本(v2 设计阶段沉淀)

- **诱惑**:面试讲 harness 时多 provider 听起来高级("LLMClient 抽象 + Qwen / Claude / GPT 可切")
- **真实成本**:prompt **不是 portable 的**
  - 中文标记 `【硬约束】` Qwen 适配最好,Claude / GPT 也读懂但行为漂移
  - JSON 严格输出可靠性各家不同(Qwen ~95% / Claude 偶尔加 markdown 包装 / GPT 偶尔加前缀)
  - 中文 token 效率差 2-3 倍(Qwen 0.6 字/token vs GPT 1.5-2 字/token),context 占用爆
  - 中文常识广度差异显著:Judge 评 fidelity 时,Claude 可能把"javac 是 Java 编译器"标 fabricated(直接撞 [1.1](#11) 假阳性同款风险)
  - reasoning 模式不同(Qwen `thinking on` 隐式 / Claude `extended thinking` 显式 budget / GPT o1 系列)
- **决策**(v2 M0 设计阶段):**LLMClient 接口抽象保留**(扩展点),**只 ship Qwen**;真切 provider 是 1-2 周工作量(3-5 天 prompt 重写 / provider × 30 条 dataset 跑 kappa / 维护 N 套 prompt 版本号),**不是 1 天**
- **教训**:harness 的设计抽象 ≠ 实际多实例。设计 portable 是 free 的,实际 portable 是 expensive 的。**别为了简历加 feature**,trade-off 论证比真做出来更值钱

## 8.8 Tool use 用在反假阳性场景最直接(v2 设计阶段沉淀)

- **背景**:[1.1](#11) Reviewer 把候选人**真实**教育经历标编造,根因是只看局部 chunks 不知道用户其他笔记里写过同样内容
- **v2 修法**:AnswerJudge 在标 fabricated 前**强制**调 `lookup_in_notes_global(claim, top_k=3)` 工具(全笔记库 hybrid search),命中即降级 supported / inferred,不命中才标 fabricated
- **设计要点**:
  - Tool **不是放给 LLM 自由调**,是 prompt 强约束"标 fabricated 前必调一次"+ service 层 post-check(没调直接重跑)
  - Tool 调用 ≤ 5 次/answer,防 LLM 滥调
  - 评测路径**不禁工具** + 跑 baseline(tool=off)对照,没对照不知道工具有没有真实价值
- **教训**:Tool use 不是为加而加,得跟具体失败模式对得上。"反幻觉强化"是工具最自然的入口 — 让 LLM 在最容易出错的判定上**有验证手段**而非纯靠 prompt 约束

## 8.9 累积型资产 vs batch 型上传的设计分歧(v2 设计阶段沉淀)

- **背景**:v2 加 JD 分析功能时,起初设计为 "用户一次上传 N 条 JD,系统 batch 分析" — 加了 jd_batches 表
- **澄清**:用户场景实际是 **陆续上传(几周累积)+ 某天一键分析全部** — JD 跟笔记一样是用户长期资产
- **修法**:
  - 数据模型从 batches → 累积型(jds 表跨时间留)
  - 解析时机:**上传即解析**(parsed_payload 持久化),不延迟到分析时
  - 一键分析:走 jd_analyses 快照表,jd_ids 数组锁定本次范围
  - 100+ 条聚合走 hierarchical map-reduce(分批 → 二次合并 → Python 重算频次),单次上限 200
- **教训**:**不要先入为主把"批量数据处理"等同于"batch 上传"**。先问清楚用户行为(一次性 / 累积),再选数据模型 — 累积型资产对应"我的 X 库 + 一键 Y" 的工作流,比 batch 模型更符合用户真实使用 LLM 工具的方式
- **关联**:LLM 官网做不到累积型资产管理(单 session 用完即弃),这是本产品 vs LLM 官网差异化的痛点 C(见 PRD §3.2)

## 8.10 简历 / JD 类输出绝不替写文案(v2 设计阶段沉淀)

- **背景**:v1 W8 实测发现"JD 同质化导致定制简历建议价值低" — 根因是 LLM 倾向于编经验("建议补充 Redis 项目经历"但用户没做过)
- **v2 修法**:ResumeAdvisor 输出 schema 显式拆 `diagnosis`(陈述事实 — 简历缺什么)+ `suggestion_topic`(只描述"该补什么主题");**禁止替写文案**
- **多层防御**:
  1. **Prompt 硬约束**:SYSTEM 显式禁"建议改写为 XXX"句式
  2. **schema 字段命名引导**:`suggestion_topic`(主题)而非 `suggestion_text`(文案)
  3. **service 层 forbidden_pattern 拦截**:正则检测越界句式,触发即 retry + trace warning
  4. **评测 dataset 红队样本**:专门测 prompt injection 场景,触发 = M3 DoD 不通过
- **教训**:**对抗 LLM 默认行为(替写)需要多层防御,单靠 prompt 不够**。schema / forbidden_pattern / dataset 红队样本三层叠加才能稳住。**任何"看着像专业建议但没事实依据"的输出对用户都是负价值** — 用户分不清是真建议还是 hallucinate

## 8.11 事实核查类问题三件证据齐再下结论 ⭐(v1 §1.1 引申)

§1.1 reviewer 假阳性是 v1 项目最值钱的故事。从这个 bug 提炼的方法论:

**所有 reviewer / 事实核查 / 假阳性类问题,定位 root cause 必须把三件证据齐了再判**:

1. **原始输入**(JD 原文 / 用户答案原文 / 简历原文 / 笔记 markdown)
2. **LLM 输出 / 草稿**(reviewer finding / Judge evidence / Drafter 简历 markdown)
3. **被引用的事实源**(retrieval 命中的 chunks / Profile 字段 / source_chunk_ids 反查)

少一件就**不要推断**。否则极易把"镜像 JD"误判成"凭空 hallucinate",把"reviewer 没拿到 Profile 字段"误判成"prompt 不够严",修错地方。

**反面教材**:v1 W6 第一次见 reviewer 假阳性时,只看 reviewer finding + 招了个 "prompt 加更狠的反向警告"的修法,一周后 dogfood 同类 bug 再现。第二次按"三件齐"重查,才发现根因不在 prompt 而在 retrieval 输入差异化(§3.1)+ Profile 字段没传(§1.1)。

**教训**:LLM 类 bug 的根因往往不在 prompt(看得见的)而在数据流(隐性的)。只看 prompt 改的修法是治标。

## 8.12 批量 LLM 调用前必须 dry-run 三件校验(v1/v2 多次踩坑沉淀)

跑批量 LLM 调用(评测 dataset / 一键分析 / dogfood pipeline 等)**前**必须先校验:

1. **模型 ID 对**:用项目锁定 ID(`qwen3.6-flash` v2 唯一),不要复制粘贴 v1 历史的旧视觉 / plus 模型名(8-ENGINEERING §6.1 model-id-lint 也防这条,但**本地跑评测 / 脚本绕过 workflow**)
2. **Provider 接口实际存在**:`curl /models` 或 `curl /api/v1/services/...` 验证当前可用 + 当前模型 ID 在列表里(2026-05-09 百炼 reranker 选型时差点踩 — `gte-rerank-v2` 2026-05-30 下线,印象推荐没踩 dry-run 的话上线就崩)
3. **schema / prompt 完整跑通**:**单样本 dry-run**(只跑 dataset 第 1 条,看输出 schema / Pydantic 校验 / `[N]` 编号 / token 用量是否符合预期),**通过**了再放开全量

**反面教材**:v1 跑 30 条 dataset 评测,第 5 条 prompt 占位符忘填(`{question}` 字面输入)→ LLM 把"{question}"当字面看跑出一堆乱评分,29 条 cache 命中错值。手动重跑还得清 cache。

**教训**:批量调用是**可以倒车的**(单样本 dry-run 不过就停)、**不可以盲冲的**(全量跑炸 30 条不仅烧钱还污染 cache)。**模型 ID / Provider / Prompt 三件事任何一件靠记忆推断不靠 dry-run 校验**,都是踩坑入口。

---

# 9. 困难复盘与面试故事素材

> 本节记录"遇到困难 → 怎么拆 → 怎么解决"的过程。目标不是流水账,而是把 RAG、评分、Agent 状态机、多工具编排这类难题沉淀成以后面试能讲清楚的故事。

## 9.1 复杂问题的四阶段解法

后续遇到系统性难题,按下面四阶段推进,避免陷入"看资料很多,本地问题没变"。

1. **先解本项目的具体失败样本**:从本地 trace / report / 原始输入 / LLM 输出 / 证据源入手,先判断问题到底在数据、召回、排序、prompt、schema、状态机还是产品口径。
2. **再用官方文档校准供应商能力**:只查跟当前失败模式直接相关的 provider 文档,确认限制、推荐参数、接口语义和计费/延迟边界,防止把能力用反。
3. **看开源项目借工程形态**:参考 LlamaIndex / Haystack / LangChain / RAGFlow / Dify / Open WebUI 等项目怎么做 metadata filter、rerank、context packing、tool boundary、状态恢复,只借结构,不照搬抽象。
4. **最后看论文找方法灵感**:论文用于补充思路,例如 hybrid retrieval、multi-stage retrieval、corrective RAG、agentic RAG、judge calibration;不把论文方案直接当产品方案。

**沉淀**:资料调研不能替代本地证据。最优顺序是"先解自己的 case,再用外部资料校准",不是反过来。

## 9.2 困难复盘卡片模板

每个值得讲的困难都按同一张卡记录,后续评分、追问、多 Agent / 状态机也复用:

- **背景**:在哪个里程碑 / 哪条用户链路 / 为什么重要。
- **症状**:用户或评测实际看到什么失败。
- **证据**:report、trace、输入、输出、chunk、prompt、状态字段等。
- **误导方向**:最开始容易以为是什么问题,但后来证明不完整或不对。
- **根因**:最终定位到哪一层。
- **尝试过的修法**:保留成功和失败方案,失败方案要写为什么失败。
- **最终决策**:当前采用什么方案,哪些方案暂时不做。
- **可迁移教训**:以后遇到同类问题怎么更快判断。
- **面试讲法**:一句话冲突 + 两三步行动 + 结果数字 + trade-off。

## 9.3 RAG 紧窗口召回不是"多捞一点"能解决(M2.1)⭐⭐

- **背景**:M2 已跑通"聊天框主题 query → 全库 RAG → 出题 → 答题 → Judge 三层评分 → session 恢复"。M2.1 要做 `InterviewCoachAgent` 状态机,但状态机的高级感依赖可靠 evidence;如果 RAG 上下文脏或漏证据,后续追问、评分、纠偏都会被污染。
- **症状**:粗排大窗口看起来有希望,但产品真实窗口不可用。2026-05-15 粗排 smoke 显示:`top10 selected_recall@K=54.17%`,`top30=89.17%`,`top50=92.50%`;同时 hard-negative intrusion 从 `top10 1/12` 增到 `top30 3/12`、`top50 7/12`。这说明问题不是"完全搜不到",而是正样本进不了紧窗口,扩大窗口又会把噪声带给下游。
- **证据**:
  - `evals/reports/hybrid-search-note-smoke-20260515-115607.md`:粗排 top10,`selected_recall@K=54.17%`,precision `17.00%`,hard-negative `1/12`。
  - `evals/reports/hybrid-search-note-smoke-20260515-115630.md`:粗排 top30,`selected_recall@K=89.17%`,precision `10.33%`,hard-negative `3/12`。
  - `evals/reports/hybrid-search-note-smoke-20260515-115648.md`:粗排 top50,`selected_recall@K=92.50%`,precision `6.60%`,hard-negative `7/12`。
  - `hs_note_005` 的 `candidate@50=25.00%`,说明至少一类 query 是粗召回本身没捞够,不能靠 reranker 修。
  - zero-hit 仍为 `0/2`,说明"没证据就停"还不是可靠能力。
  - `evals/reports/hybrid-search-note-smoke-20260516-080038.md`:post-rerank governance/blend 初版,12/12,`candidate_recall@50=97.50%`,`selected_recall@K=86.67%`,`mrr@K=67.00%`,`final_context_precision=29.00%`,hard-negative `0/12`,cache-only。
  - `evals/reports/hybrid-search-note-smoke-20260516-095536.md`:当前稳定生产口径,12/12,`candidate_recall@15=91.67%`,`selected_recall@10=95.00%`,`final_context_recall=95.00%`,`final_context_precision=40.00%`,hard-negative `0/12`,zero-hit `2/2`,parent-doc 默认关闭,query embedding cache-only。
  - `evals/reports/hybrid-search-note-smoke-20260516-100624.md`:selected topK 从 10 缩到 8 的 A/B 为负收益,`final_context_precision 40.00% → 41.75%`,但 `selected_recall@10 / final_context_recall 95.00% → 90.00%`,已回滚。
- **误导方向**:一开始容易继续调 `top_k`、query rewrite、reranker prompt 或 metadata 拼接,看 headline pass 数。但 top50 指标变好不等于产品可用,因为真实上下文预算更像"粗排 top10 → 精排 top5"。
- **已尝试修法**:
  - 2026-05-13:补 chunk-level smoke dataset / report / `direct_evidence_chunk_ids`。
  - 2026-05-14:做 reranker metadata A/B,metadata 前置能救个别 case,但会把题库 / 评测样本 / hard-negative 抬高,不是净提升。
  - 2026-05-14:做 intent × chunk-type 本地降权实验,hard-negative 有下降,但 direct evidence 覆盖也下降,没有进主路。
  - 2026-05-15:接 Query Understanding v2 + weighted RRF,用户原话 q0 固定两票,改写 query 限权;粗排 top30 有改善,但 top10 仍不够。
  - 2026-05-15 第一刀:加 `source/type governance`,只对 `project_fact / boundary_question` 做轻量 source multiplier,把项目事实优先级略抬、题库 / 评测样本 / hard-negative 降权。`evals/reports/hybrid-search-note-smoke-20260515-140350.md` 显示 top10 `selected_recall@K 54.17% → 64.17%`,`mrr@K 31.52% → 45.33%`,`final_context_precision 17.00% → 20.00%`,hard-negative 仍 `1/12`,是净收益。
  - 2026-05-15 第二刀失败:尝试 `protected_anchor_search` 用 exact anchor 补召回。`evals/reports/hybrid-search-note-smoke-20260515-142902.md` 把 `candidate_recall@50 92.50% → 96.67%`,但 top10 `selected_recall@K 64.17% → 60.83%`。`hs_note_005` 的 `#2259/#2220/#2299/#1066` 都进入 candidate@50,却只排到 `#14/#16/#17/#33`,没有进 top10。失败原因不是 anchor 判断错,而是补召回太宽:凡是 `JobCopilot + SSE/session/恢复` 的泛相关项目事实都被第四路 RRF 强投票抬高,紧窗口被近邻占满。
  - 2026-05-15 第三刀修复:保留第一刀,把 `protected_anchor_search` 收窄成"只在 `JobCopilot + SSE/断线 + 恢复/重连` 语义下触发的状态恢复强锚点路由",只返回 `top4`,要求 entity + transport + recovery evidence,并按 `SSE 断开不等于业务失败`、`SSE 只是通知通道`、`前端重连后查 DB 当前状态`、`/quiz?session` 等短句打分;同时把 `追问样本` 归为题库 / 样本类,避免被当 canonical project fact 抬权。`evals/reports/hybrid-search-note-smoke-20260515-144102.md` 显示 top10 `selected_recall@K 71.67%`,`mrr@K 55.33%`,`final_context_precision 23.00%`,hard-negative 仍 `1/12`;`hs_note_005` 变 PASS,top10 命中 `#1066/#2259/#2299`,anchor `4/4` 命中。
  - 2026-05-16 第四刀修复:裸 provider rerank 不能启用,但也不能因为 top15 干净就把 rerank input 收窄,因为有正样本排在粗排 30 多位。当前改成 `粗排 top50 → provider rerank top50 → post-rerank governance/blend → dynamic clean-context selection(3-10)`。provider 只提供 challenger 排序;最终成员还要看 coarse rank、rerank score、source/type、anchor coverage、contrast route、hard-negative clamp。smoke 指标正式名也从 `candidate_recall@50 / selected_recall@K` 收口为 `candidate_recall@15 / selected_recall@10`,top50 只保留为诊断窗口和 rerank input。
  - 2026-05-16 第五刀修复:关闭 parent-doc 默认扩展。原因是 parent-doc 会把同标题兄弟 chunk 带进 final context,对出题 / 评分来说很多只是背景,不是可负责证据。关闭后不再把背景 chunk 交给 QuizGenerator,稳定口径达到 `final_context_precision=32.75%`,`selected_recall@10=95.00%`,hard-negative `0/12`。
  - 2026-05-16 第六刀修复:不继续缩 provider rerank input top50,而是在 post-rerank selection 上收口。`POST_RERANK_DYNAMIC_TARGET_K 8 → 6`,3 个 chunk 后做 note / heading 去重,超过 target 只允许高置信 provider challenger 或明确 route evidence 进入。最终 `final_context_precision 32.75% → 40.00%`,同时守住 `selected_recall@10=95.00%` 和 hard-negative `0/12`。
  - 2026-05-16 负收益实验:尝试把 final selected topK 从 10 缩到 8,precision 只涨到 `41.75%`,但 `hs_note_003` 丢 direct evidence,整体 recall 掉到 `90.00%`,所以回滚。结论:最终 topK 不是越小越好,要看 per-case trace 里的 direct evidence 是否被截掉。
  - 2026-05-16 出题侧兜底:新增 QuizGenerator evidence verifier,在保存题目前校验 `reference_answer` 必须引用 `[N]`,`reference_chunk_ids / evidence_chunk_ids` 不能越界,采分点文本和声明的 evidence chunks 至少有基础词面 / 中文 bigram 重合。这样 retrieval 负责把证据送进上下文,service 再阻止 LLM 把非证据 chunk 伪装成采分点依据。
- **source/type governance 大白话解释**:普通 hybrid search 只看"这段文字跟 query 像不像"。但 JobCopilot 的笔记库里同一个词会出现在很多不同来源里:正式项目笔记、项目边界文档、面试题库、eval 样本、hard-negative 清单、通用技术笔记。它们语义都可能很像,但回答私有事实问题时可信度不同。比如用户问"当前版本是否支持按目标岗位定制面试题",正式产品文档里写的是"当前版本暂不支持,放到下一阶段";题库里可能有一道练习题也在讨论岗位定制;eval 样本里可能有为了测试系统而写的假场景;hard-negative 甚至可能故意写成"当前已经支持岗位定制"。source/type governance 做的事就是先给候选 chunk 粗分来源,再在 protected intent 下轻轻调分。
- **source/type 分类**:当前不做 schema migration,先从 `folder_path / heading_path / content` 推断粗类型。`canonical_project_fact` 是放在 `项目/JobCopilot/...` 下的正式项目事实;`project_doc` 是其他带 JobCopilot 的工程映射笔记;`interview_question_bank` 是题库 / 追问题库 / 追问样本;`eval_case` 是评测 / fixture / smoke / baseline;`hard_negative` 是明确写着 hard negative / 对抗样本的材料;剩下是 `generic_background`。这不是完美分类器,但足够把"正式事实"和"练习/评测/干扰材料"分开。
- **为什么是轻量 multiplier,不是硬过滤**:硬过滤风险太高,因为有些通用背景笔记确实是 direct evidence,例如 `hs_note_005` 的 `Linux IO epoll 与异步后端 > FastAPI SSE`。如果只保留 `项目/JobCopilot` 目录,会把这种真实证据砍掉。当前做法是对 `project_fact / boundary_question` 这种私有事实问题启用 multiplier:正式项目事实略加分,普通项目映射小幅加分,题库 / eval / hard-negative 降权,通用背景基本保持原状。它像"候选排序上的交通规则",不是"把路封死"。
- **为什么只对 protected intent 生效**:如果用户问 MVCC、Outbox、epoll 这类普通技术主题,正式项目事实并不天然比专业技术笔记更可信。source/type governance 只在 Query Understanding 判断为 `project_fact / boundary_question` 时开启,避免把所有问题都变成"JobCopilot 项目笔记优先"。这也是第一刀能成为净收益的关键:它只在需要保护私有事实边界的时候动排序,不全局改检索偏好。
- **source/type governance 面试讲法**:我不是直接调 embedding 或把 topK 拉大,而是发现同一个候选池里混着正式事实、题库、eval 和 hard-negative。对项目私有问题来说,这些来源的"可信角色"不同。所以我加了一层轻量候选治理:先推断 chunk source/type,再只在私有事实 / 边界问题里给正式项目事实小幅加权、给题库和干扰样本降权,但不硬过滤通用技术笔记。结果 top10 recall 从 54.17% 提到 64.17%,MRR 从 31.52% 提到 45.33%,hard-negative 没增加。
- **根因**:retrieval 缺少结构化候选治理。题库、评测样本、项目事实、hard-negative、泛背景笔记混在同一个候选池里,只靠向量 / lexical / RRF / reranker 文本相关性,无法稳定区分"能直接回答 query 的证据"和"语义很像但会污染答案的近邻"。对 `hs_note_005` 这类项目状态恢复 query,普通 hybrid 会漏掉短项目事实;但补召回如果不够窄,又会把泛相关状态机 / SSE / session 近邻抬进 top10。
- **最终决策**:停止把 `candidate_recall@50` 当主胜利指标,但保留 top50 作为 provider rerank input 和诊断窗口。当前生产路径是:`query rewrite → hybrid + RRF → qwen3-rerank(top50 challenger) → post-rerank governance/blend → dynamic clean-context selection(3-10) → QuizGenerator evidence verifier`。parent-doc 默认关闭,provider rerank 不是最终成员裁判,final selected 不为凑满 top10 塞低置信 chunk。
- **可迁移教训**:RAG 不是"召回越多越好",而是"正确证据要能进入候选池,最终上下文又必须干净"。粗排 top15 看紧窗口质量,top50 看长尾召回和 reranker 可救空间;当 top50 有、top10 没有时,问题是排序和候选治理;当 top50 本身漏时,才回到 query rewrite / tokenization / chunker / embedding。补召回、精排和 context packing 都必须有治理层兜底,不能让它们绕过 source/type、contrast、hard-negative 和 evidence 引用规则。
- **泛化边界**:这 12 条 query 只能证明关键路径 smoke 和已知高风险样本不回退,不能证明任意新 query 都已经泛化。真正可迁移的是来源区分、reranker 只做参考、parent-doc 默认关闭、最终材料去重收口、出题前引用校验这些通用治理;`protected_anchor_search` 这类规则只解决明确失败族,不能全局放大。后续要补 query 改写集、新主题 holdout、强干扰集,验证系统不是只记住这 12 条 query。
- **面试讲法**:我遇到一个 RAG 质量卡点,表面看扩大 topK 可以提高 recall,但产品真正需要的是一个小而干净的材料包,否则大模型会拿泛相关材料出题和评分。我先搭 chunk-level eval 和 trace,把问题拆成粗召回、粗排排序、provider rerank、post-rerank governance、zero-hit 五层。最开始粗排 top10 recall 只有 54.17%,top50 虽到 92.50%,但 hard-negative 到 7/12,说明"多捞一点"会污染下游。后来我加 source/type governance、窄 protected anchor、zero-hit support gate,再把 provider rerank 从最终裁判改成 challenger,最后用 deterministic governance/blend 做最终成员裁决。最新稳定结果是 12/12,`selected_recall@10=95.00%`,`final_context_recall=95.00%`,`final_context_precision=40.00%`,hard-negative `0/12`,zero-hit `2/2`。我还试过把 final topK 从 10 缩到 8,precision 只涨 1.75 个点但 recall 掉 5 个点,所以回滚。这个取舍说明我不是盲调参数,而是按 trace 做可解释的质量工程。

**面试 60 秒版本**:

> 我在做 JobCopilot 的笔记 RAG 面试陪练时,遇到的核心问题不是"搜不到",而是"最后交给大模型的那几段材料不够干净"。粗排 top50 recall 看起来有 92.50%,但 final context 里 hard-negative 到 7/12,LLM 会拿泛相关内容出题和评分。
>
> 我先做了 chunk-level eval,把每条 query 标清楚哪些片段是真正能回答问题的材料、哪些是必须避开的干扰材料,并在 trace 里记录 hybrid rank、provider rank、post-rank 和 governance flags。定位后我没有简单调大 topK,而是做了四层治理:source/type governance 区分正式项目事实、题库、eval 和 hard-negative;protected anchor 只补特定失败样本;zero-hit gate 防止无证据硬出题;provider rerank 只做 challenger,最后由 deterministic governance/blend 选最终材料包。
>
> 最终稳定口径是 12/12 通过,`selected_recall@10=95%`,`final_context_recall=95%`,`final_context_precision=40%`,hard-negative `0/12`,zero-hit `2/2`。我还做过 topK=8 的反例实验,precision 只小涨但 recall 掉到 90%,所以回滚。这个项目里我真正解决的是 RAG 从"能搜到"到"能安全喂给下游 Agent"的问题。

**一句话版本**:我不是接了一个 reranker,而是把 RAG 质量拆成可观测、可诊断、可回滚的工程链路,最后用规则治理 + provider challenger 把证据召回和上下文干净度同时守住。

## 9.4 后续困难也按同一条线记录

未来优先沉淀这些故事,每个都要写清楚"失败样本 → 证据 → 取舍 → 指标":

- **AnswerJudge 评分**:LLM 只给 evidence 和 label,总分由 Python 算;重点记录 fabricated 锁顶、证据不足、评分漂移、judge prompt 与 deterministic 权重的边界。
- **InterviewCoachAgent 状态机**:不要讲"多 Agent 数量",要讲状态、工具、分支、恢复、追问依据、wait_user_answer 人类暂停点;重点记录追问什么时候继续、什么时候总结、什么时候承认证据不足。
- **Tool use 边界**:tool 不是给 LLM 自由发挥,而是在最容易错的判定上提供验证手段;重点记录强制调工具、调几次、调不到怎么办、如何防循环。
- **多源岗位类 query**:M3 会融合笔记 RAG、单条简历、JD 子集;重点记录三源 evidence 对齐、缺源降级、不能编造简历/JD 内容。
- **zero-hit / insufficient evidence**:"答不上来"是能力,不是失败;重点记录怎样用 core entity、anchor、source diversity 和 score 组合守住边界。

---

# 不在本文档范围

- **当前阶段进度 / 下一刀** → [STATUS.md](STATUS.md)
- **永久约束完整列表** → STATUS.md "永久约束累积"区(M1 25 条已归 [M1-summary.md](slices/M1-summary.md),M2 6 条已归 [M2-summary.md](slices/M2-summary.md),M3 期间在 STATUS.md 累积)
- **设计文档**(PRD / TECH / DATA / API / AGENT / EVAL / ROADMAP / ENGINEERING)→ `docs/{1..8}-*.md`
- **架构决策完整列表** → `docs/adr/`
- **prompt 完整 bug 清单**(JDParser 26 类 / ProfileParser 6 类) → `slices/{jd,profile}-parser-bugs-2026-05.md`
- **切片完整产出 + 设计决策** → `slices/{S0.5,S1..S21}-*.md`(本文档只摘代表性坑)
