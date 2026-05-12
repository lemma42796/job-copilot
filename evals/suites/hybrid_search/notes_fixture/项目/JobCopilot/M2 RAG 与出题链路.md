# JobCopilot M2 RAG 与出题链路

## M2 的产品边界

JobCopilot v2 的 M2 目标是把用户输入的主题 query 变成一次可评分的面试练习。链路是:

```text
聊天框主题 query
→ 全库 RAG
→ QuizGenerator 出题
→ 用户答题
→ AnswerJudge 三层评分
→ session 恢复
```

M2 不处理岗位类 query,也不处理空 query。岗位类 query 放到 M3,因为它必须融合三类来源:

- 笔记 RAG。
- 当前唯一简历。
- 用户选定 JD 子集的职责/要求。

把岗位类 query 在 M2 降级成普通主题 query 会丢掉简历和 JD 约束,题目会变成泛泛八股,不是针对岗位的面试训练。

## 唯一出题入口

M2 锁定的出题入口是聊天框 query。笔记面板只负责查看、编辑、上传、导航,不触发出题。

这个决策避免了两个问题:

1. 用户点一个节点时,系统很难知道他是想阅读、编辑还是出题。
2. 节点点击容易把题目绑定到单篇笔记,违背"主题 query → 全库 RAG"的目标。

因此,如果用户想练 Langfuse Prompt 版本管理,应在聊天框输入主题,系统再从全库检索相关笔记。

## M2 RAG pipeline

M2 的检索链路:

```text
query_rewriter
→ hybrid search(vector + lexical)
→ RRF fusion
→ qwen3-rerank
→ parent-doc 扩展
→ final context
```

每一层都有明确职责:

| 层 | 职责 |
|---|---|
| query_rewriter | 补齐同义词、中英术语、项目锚点 |
| vector search | 找语义相似内容 |
| lexical search | 守住专名、缩写、代码符号 |
| RRF | 融合多路候选 |
| reranker | 细排最能回答 query 的 chunks |
| parent-doc | 补回 chunk 边界切断的上下文 |
| final context | 交付给 QuizGenerator / AnswerJudge 的证据材料 |

这个 pipeline 的价值不是炫技,而是把"找证据"拆成可观测、可评测、可调参的阶段。

## 为什么当前规模仍保留 RAG

当前干净 dogfood 笔记约 12.24 万字。qwen3.6-flash 有 1M context,官方口径约可放 70 万汉字。按纯容量看,当前笔记还没达到必须 RAG 的规模。

但 M2 仍保留 RAG,原因不是"装不下",而是:

- 需要 chunk_id、heading_path、evidence anchors 支持可解释评分。
- 需要 zero-hit 守门,不能让 LLM 用常识兜底编题。
- 需要 final_context_precision 控制上下文干净度。
- 后续 M2.5/M3 会拼 JD、简历和笔记,多源检索比全量上下文更可控。
- RAG 中间结果可以进入 Langfuse trace,便于排查。

所以 M2 的 RAG 是 evidence 和质量控制方案,不是单纯的容量方案。

## 0 命中守门

M2 的 0 命中规则很重要:

```text
命中 chunks < 3 起步直接报"笔记里没这主题",不兜底让 LLM 编。
```

这个规则牺牲了一些覆盖率,但保护了产品可信度。JobCopilot 是"基于我的笔记出题",不是"模型知道什么就问什么"。

典型错误:

```text
用户问 React useEffect。
知识库没有 React,但有 SSE 前端客户端。
系统拿 SSE 代码出 React 题。
```

这会让用户误以为自己的笔记里有 React 知识,也会污染后续弱点跟踪。

## QuizGenerator 的证据要求

QuizGenerator 不应该凭空出题。它应从 final context 中抽取:

- reference answer。
- reference points。
- evidence chunk ids。
- 题型和难度。
- 可追问点。

如果 final context 不足,QuizGenerator 应拒绝或降级,而不是靠模型常识补全。

一个好题目应该能回溯到笔记证据。例如:

```text
问题: JobCopilot 为什么当前默认关闭 Context Cache?
证据: 5 分钟 TTL 不适合一次性答题流;后续 M2.1 多轮讨论再开启。
```

这个题必须依赖项目私有笔记,不是通用 LLM 常识。

## AnswerJudge 三层评分

M2 AnswerJudge 初版已经落地三层 evidence:

| 层 | 判断 |
|---|---|
| Coverage | 用户答案是否覆盖 reference points |
| Fidelity | 用户答案里的 claims 是否被 chunks 支持 |
| Depth | 是否体现 tradeoff、why、boundary 等深度 |

LLM-as-Judge 负责给 evidence 和 label,但总分由 Python 算。不要让 LLM 自己算总分。

Python 算分的原因:

- 权重可控。
- fabricated 锁顶可硬编码。
- 后续调阈值不用改 prompt。
- 报告更稳定。

## fabricated 锁顶

Fidelity 中如果出现 fabricated claim,总分要被锁顶。这样做是为了防止用户答案表面覆盖很多点,但混入关键编造。

例子:

```text
用户答案: RRF 会用梯度下降学习每个召回通路的权重。
```

如果笔记中只写了 RRF 的 reciprocal rank 公式,没有学习权重,这条 claim 应标 fabricated。

fabricated 不能只扣一点小分。面试中编造机制比漏答更危险,尤其是系统设计、数据库、分布式一致性这类题。

## session 恢复

M2 已跑通 `/quiz?session=4` 恢复。session 恢复不是 UI 小功能,它是 M2.1 状态机的前置能力。

恢复时需要保留:

- 原始 query。
- 生成的问题。
- reference answer 和 points。
- retrieved chunks。
- 用户草稿答案。
- Judge 评分结果。
- SSE 过程中的阶段状态。

如果恢复后丢掉 retrieved chunks,AnswerJudge 的 evidence 就无法复现。如果恢复后只保留最终分数,用户无法知道自己哪一点没答到。

## Context Cache 当前默认关闭

百炼 Context Cache 代码已接入,但 M2 当前默认关闭显式 `cache_control`。

原因:

- provider-side 命中已验证,但显式缓存 TTL 只有 5 分钟。
- M2 当前答题流多是一次性生成和提交,重复公共前缀收益有限。
- 显式 cache 不是会话记忆,请求仍需带必要上下文。
- M2.1 多轮讨论面试题时,公共前缀和 stable chunks 更可能重复,那时再开启更合理。

所以不要把 Context Cache 当作 session memory。它只优化 provider 侧重复前缀计算和计费。

## 本地开发形态

当前本地开发形态是:

```text
Docker Postgres + 本机 API
```

避免日常走完整 api 容器 rebuild。api 容器还涉及 `DASHSCOPE_API_KEY` 映射,容易因为 compose key 或环境变量加载顺序踩坑。

Langfuse 相关环境变量也有约束:

```text
LANGFUSE_* env mirror 要早于 routers / agents / llm import
```

否则 SDK 会进入 noop,trace 看起来"没报错但也没数据"。

## GitHub Actions 策略

GitHub Actions 已改为手动触发 `workflow_dispatch`,push 不再自动跑 lint、tests、build。

这个决策与项目开发节奏有关:

- 用户明确所有测试/自动化验证手动跑。
- push 自动跑会产生不必要通知。
- 当前更重视本地 dogfood 和手动验收。

这不代表不需要质量闸门,而是触发权交给用户。

## M2 RAG 质量补测

M2 功能链路跑通后,还需要补 RAG 质量评测。核心指标:

| 指标 | 作用 |
|---|---|
| `candidate_recall@50` | 粗召回别漏证据 |
| `rerank_recall@10` | reranker 后证据仍靠前 |
| `mrr@10` | 第一个相关证据排得够前 |
| `final_context_recall` | 最终上下文覆盖 expected evidence |
| `final_context_precision` | 最终上下文不要混太多噪声 |
| `zero_hit_precision` | 无主题 query 不被错误出题 |
| `unsafe_boundary_rate` | chunker/parent-doc 不切断关键语义 |

其中 `final_context_precision` 是后来补上的,因为只看 recall 容易鼓励系统塞更多 chunk。

## M2.1 的延伸

M2.1 的方向是 `InterviewCoachAgent` 状态机,不是泛化多 Agent 平台。

状态机大致是:

```text
retrieve_context
→ generate_question
→ wait_user_answer
→ judge_answer
→ decide_next_action
→ generate_followup 或 summarize
```

高级感来自状态、工具、分支、记忆、评测、恢复,不是多 Agent 数量。

M2.1 会用到 M2 的能力:

- RAG final context。
- QuizGenerator。
- AnswerJudge。
- session 恢复。
- Langfuse trace。
- Context Cache 的 stable prefix。

## 为什么追问最多一轮起步

M2.1 初期不做无限追问。单题最多 1 轮追问更符合可控原则:

- 评测分支更简单。
- session 恢复更容易。
- 避免用户被困在单题里。
- 更容易收集失败样本。

追问触发条件可以看:

- coverage 是否漏关键 point。
- fidelity 是否有 fabricated。
- depth 是否缺 why/tradeoff/boundary。

如果 fabricated 明显,追问应优先纠错,而不是继续问更深问题。

## 项目私有面试题

### Q: 当前笔记还没超过 1M context,为什么 M2 仍保留 RAG?

参考要点:

- 当前 RAG 不是容量必需,而是 evidence 和质量控制方案。
- AnswerJudge 需要 chunk_id、heading_path、evidence anchors。
- 0 命中守门依赖 retrieval score 和命中数。
- final_context_precision 控制上下文干净度。
- 后续 M2.5/M3 多源融合需要检索架构。

### Q: 为什么 M2 不支持岗位类 query?

参考要点:

- 岗位类 query 需要笔记、当前简历、用户选定 JD 子集三源融合。
- M2 只做主题 query,避免产品边界混乱。
- 把岗位 query 降级成主题 query 会变成泛题,不符合岗位训练目标。

### Q: AnswerJudge 为什么让 Python 算总分?

参考要点:

- LLM 负责 evidence 和 label。
- Python 负责权重、锁顶、聚合。
- fabricated 锁顶需要硬规则。
- 这样 prompt 改版和算分策略可以解耦。

### Q: Context Cache 为什么当前默认关闭?

参考要点:

- 显式缓存 TTL 只有 5 分钟。
- M2 一次性答题流重复公共前缀收益有限。
- cache 不是会话记忆。
- M2.1 多轮讨论更适合开启。

## 可作为 evidence anchor 的短句

- M2 只处理聊天框主题 query,岗位类 query 放到 M3 三源融合。
- 当前 RAG 不是容量必需,而是 evidence 和质量控制方案。
- 0 命中时不让 LLM 兜底编题。
- LLM-as-Judge 给 evidence 和 label,总分由 Python 算。
- Context Cache 当前默认关闭,因为 5 分钟 TTL 不适合 M2 一次性答题流。
