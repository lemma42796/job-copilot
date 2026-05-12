# Query Rewrite 与召回扩展

## 为什么 query rewrite 不是润色

在 RAG 系统里,query rewrite 的核心目标不是把用户问题写得更漂亮,而是把一个短 query 变成更适合检索系统消费的多个检索意图。用户输入通常很短,例如"prompt 版本管理"、"reranker 什么时候掉点"、"Judge 为什么会误判 fabricated"。这些 query 对人来说足够,但对向量检索和全文检索来说信息太少。

一个好的 rewrite 应该补齐三类信息:

- 同义词:例如"提示词版本"、"prompt version"、"prompt registry"。
- 领域锚点:例如 Langfuse、trace、release、rollback、experiment。
- 任务限定:例如"面试题生成"、"评分证据"、"线上回滚"。

但 rewrite 不能无边界扩写。把"prompt 版本管理"扩成"所有 prompt engineering 最佳实践"会引入大量噪声。召回看起来变多了,final context precision 会下降。

## Query rewrite 的输入

rewrite 需要看到的不只是用户 query。更稳的输入包括:

| 输入 | 作用 |
|---|---|
| 原始 query | 保留用户真实意图 |
| 产品模式 | 区分 topic query、JD query、resume query |
| 可用知识库目录 | 防止 rewrite 到库里根本没有的领域 |
| 最近 session 状态 | 多轮场景中补齐省略对象 |
| 禁止扩写边界 | 不要把主题 query 扩成岗位/简历三源 query |

JobCopilot M2 的 query 只支持主题类 query。用户写"Langfuse Prompt 版本管理"时,系统不应该自动拼简历或 JD。岗位类 query 被锁到 M3,必须三源融合:笔记 RAG + 当前简历 + 用户选定 JD 子集。

## Rewrite 输出结构

不要只输出一个重写后的字符串。更推荐输出结构化数组:

```json
{
  "intent": "topic",
  "expanded_queries": [
    "Langfuse prompt version management",
    "prompt 版本管理 回滚 发布 trace",
    "Langfuse prompt registry label production"
  ],
  "must_include_terms": ["Langfuse", "prompt"],
  "avoid_terms": ["resume", "JD", "岗位"]
}
```

`expanded_queries` 用于向量召回和 lexical 召回。`must_include_terms` 可以在 rerank 前做轻量过滤或 boost。`avoid_terms` 用来减少明显跑偏的候选。

## Rewrite 漂移

rewrite drift 是指改写后的 query 比原 query 更宽或更偏,导致检索系统召回了看似相关但不支持当前问题的内容。

典型例子:

| 原 query | 错误 rewrite | 问题 |
|---|---|---|
| `Context Cache TTL` | `LLM 缓存策略` | 扩到响应缓存、embedding 缓存、浏览器缓存 |
| `AnswerJudge fabricated 锁顶` | `LLM 评分系统` | 漏掉 fabricated 和锁顶机制 |
| `M2 为什么只支持主题 query` | `求职应用 query 设计` | 跑到 JD 和简历功能 |

rewrite drift 的危险在于 candidate recall 可能不差,但 final_context_precision 会明显下降。系统拿到了很多"也相关"的 chunk,却没有拿到能回答当前问题的 chunk。

## 多 query 召回

多 query 召回的基本做法:

1. 原始 query 必跑一遍。
2. 每个 expanded query 跑 dense 检索。
3. 原始 query 和 expanded query 都跑 lexical 检索。
4. 用 RRF 或加权融合合并候选。
5. 去重后进入 reranker。

伪代码:

```python
queries = [original_query] + expanded_queries
candidate_groups = []

for q in queries:
    candidate_groups.append(vector_search(q, top_k=40))
    candidate_groups.append(lexical_search(q, top_k=40))

merged = reciprocal_rank_fusion(candidate_groups, k=60)
deduped = dedupe_by_chunk_id(merged)
reranked = rerank(original_query, deduped[:80])
```

注意 reranker 的 query 应该使用用户原始 query 或极轻量的澄清版,不要直接拿扩写后的长 query。reranker 要判断"这个 chunk 是否回答用户原问题",不是判断它是否匹配改写器想象出来的多个主题。

## 召回扩展的几种方式

### 术语扩展

术语扩展适合技术名词:

| 用户词 | 可扩展 |
|---|---|
| `提示词版本` | `prompt version`, `prompt registry`, `prompt label` |
| `重排` | `rerank`, `cross-encoder`, `qwen3-rerank` |
| `上下文缓存` | `context cache`, `prompt cache`, `cache_control` |
| `追问` | `follow-up`, `adaptive interview`, `branch decision` |

术语扩展不应该扩成百科解释。例如 `reranker` 不要扩成"搜索引擎排序算法大全"。

### 层级扩展

如果用户 query 太具体,可以向上补一个父主题:

```text
qwen3-rerank score threshold
→ reranker 阈值
→ RAG 0 命中守门
```

如果用户 query 太宽,可以向下生成几个常见子意图:

```text
RAG 评测
→ recall@k
→ final_context_precision
→ zero_hit_precision
→ unsafe_boundary_rate
```

层级扩展适合 heading-aware 笔记库。因为检索返回 chunk 后,系统还可以通过 heading_path 判断这个候选是在正确章节还是同文档其他章节。

### 中英混合扩展

技术笔记常常中英混写。用户问中文,笔记里可能写英文;用户问英文,笔记里可能是中文解释。

例子:

```text
用户: 提示词注入
扩展: prompt injection, indirect injection, tool output injection

用户: final context precision
扩展: 最终上下文精确率, 相关 chunk 比例, noise chunk
```

中英混合扩展要控制数量。每个 query 生成 2-4 个扩展通常够用,生成 10 个很容易把候选池冲脏。

### 错别字和缩写扩展

对面试输入来说,错别字很常见:

```text
synchroized → synchronized
langfues → Langfuse
rerankr → reranker
pgvecto → pgvector
```

错别字扩展应该只针对高置信技术词。不要把所有中文词都做模糊匹配,否则 lexical 召回会产生大量近邻干扰。

## RRF 融合后的诊断

query rewrite 是否有效,不能只看最终 answer。至少要看这些中间结果:

| 阶段 | 看什么 |
|---|---|
| `vector_top_ids` | 语义路是否召回 expected evidence |
| `lexical_top_ids` | 关键词路是否守住专名、缩写、代码符号 |
| `hybrid_top_ids` | RRF 是否把两路互补结果合在前面 |
| `rerank_top_ids` | reranker 是否把真正证据推到 Top-10 |
| `final_context_ids` | parent-doc 是否补回必要上下文 |

如果 expanded query 召回了证据,但 RRF 后掉出 Top-50,通常是融合权重或 k 值问题。如果 RRF 后在前面,但 reranker 掉出 Top-10,通常是 reranker instruct、候选噪声或 query 漂移问题。如果 Top-10 命中了,但 final context 缺前提,问题在 parent-doc。

## JobCopilot 的 rewrite 边界

JobCopilot 有几个项目私有边界,在 rewrite 时必须保留:

- M2 只接受主题 query,不处理岗位类 query。
- 出题入口只来自聊天框 query,笔记树节点点击不触发出题。
- 0 命中时不让 LLM 兜底编题。
- AnswerJudge 的事实性评分需要 evidence,不能靠模型常识替代笔记证据。
- M2.1 的 InterviewCoachAgent 是面试状态机,不是泛化多 Agent 平台。

这些私有边界不是通用 RAG 知识。评测时如果 query 问"为什么 M2 不接岗位类 query",模型必须从 JobCopilot 笔记中找到这条约束,不能靠通用产品经验回答。

## 失败样本一:扩得太宽

用户 query:

```text
Langfuse Prompt 版本管理
```

错误 expanded query:

```text
prompt engineering best practices
few-shot prompting
chain of thought
prompt injection defense
```

这些词都和 prompt 有关,但不直接回答版本管理。检索结果会混入 few-shot、CoT、注入防御笔记。最终出题可能问"如何防 prompt injection",而不是问 prompt 发布、回滚、trace 关联。

正确 expanded query:

```text
Langfuse prompt version
prompt label production latest
prompt release rollback trace
prompt registry experiment version
```

正确扩展围绕版本、发布、回滚、trace,并保留 Langfuse 这个强锚点。

## 失败样本二:把项目私有约束泛化

用户 query:

```text
JobCopilot M2 为什么保留 RAG
```

错误 rewrite:

```text
长上下文模型是否还需要 RAG
```

这个 rewrite 太通用。它会召回"长上下文 vs RAG"的通用讨论,但漏掉 JobCopilot 的私有原因:AnswerJudge 需要 chunk evidence、0 命中守门、M2.5/M3 多源扩展、final_context_precision 评测。

更好的 rewrite:

```text
JobCopilot M2 RAG 保留原因
AnswerJudge evidence chunk_id 0命中守门
final_context_precision 多源扩展
```

这里仍然可以保留"长上下文 vs RAG"作为辅助 query,但不能让它覆盖项目私有锚点。

## 失败样本三:同义词扩展引入反义

用户 query:

```text
Context Cache 当前为什么默认关闭
```

危险扩展:

```text
如何开启 context cache
prompt cache 最佳实践
cache_control 配置
```

这些扩展会召回开启缓存的教程,但用户问的是"为什么默认关闭"。JobCopilot 的私有答案是:当前一次性答题流不适合 5 分钟 TTL,等 M2.1 多轮讨论面试题时再开启显式 cache。

正确扩展要保留否定与时态:

```text
Context Cache 默认关闭 原因
5分钟 TTL 一次性答题流 不适合
M2.1 多轮讨论 再开启 cache_control
```

带有否定词、时间条件、版本条件的 query 是 high-risk query。rewrite 不能把否定条件洗掉。

## Rewrite 评测指标

query rewrite 本身不单独用"写得像不像"评测,而是通过 retrieval 指标间接评测:

| 指标 | rewrite 相关含义 |
|---|---|
| `candidate_recall@50` | 扩写有没有帮助 expected evidence 进入候选池 |
| `rerank_recall@10` | 扩写有没有引入过多噪声导致 reranker 排不准 |
| `mrr@10` | 首个证据是否被推到足够靠前 |
| `final_context_precision` | 扩写有没有让最终上下文变脏 |
| `zero_hit_precision` | 扩写有没有把无主题 query 硬扩成有主题 |

如果 rewrite 打开后 `candidate_recall@50` 上升但 `final_context_precision` 大幅下降,说明扩写太宽。扩写器不是越积极越好。

## 标注时怎么判断 rewrite 是否该背锅

当一个 case 失败时,不要立刻调模型。先看 per-case 路径:

1. 原始 query 的 lexical 或 vector 是否已经命中。
2. expanded query 是否命中更多 expected evidence。
3. expanded query 是否召回大量 unrelated heading。
4. reranker 前候选里是否有明显噪声。
5. final context 里 noise chunk 是否主要来自 expanded query。

如果只有 expanded query 召回噪声,原始 query 本来很干净,失败原因应标 `rewrite_drift`。如果 expanded query 召回了证据,但 parent-doc 把同文档大量无关段落扩进来,失败原因更接近 `final_context_noise`。

## Query rewrite 的 prompt 模板

一个保守的 rewrite prompt 应该强调边界:

```text
你是 RAG query rewriter。
目标: 生成 2-4 个用于检索的短 query。
必须:
- 保留原始 query 的核心实体、否定词、时间条件、项目名。
- 只扩展同义词、英文术语、常见缩写和直接父主题。
- 如果 query 是 JobCopilot 项目私有问题,保留 JobCopilot/M2/M2.1/M3 等锚点。

禁止:
- 把主题 query 扩成岗位/简历 query。
- 把"为什么关闭"改成"如何开启"。
- 生成宽泛百科 query。

输出 JSON:
{
  "expanded_queries": [],
  "must_include_terms": [],
  "risk_notes": []
}
```

`risk_notes` 不用于检索,用于评测和日志。比如 `"contains_negation"`、`"project_private_constraint"`、`"possible_zero_hit"`。

## 与长上下文的关系

1M context 让"整库塞进去"在小知识库阶段变得可行,但 query rewrite 仍然有价值。它不是为了解决上下文装不下,而是为了让系统知道应该把注意力放在哪里。

长上下文方案的问题:

- 模型要在几十万字中自己找证据,难以稳定输出 chunk_id。
- 0 命中场景不容易判断,因为所有材料都在 prompt 里。
- 成本和延迟受全库大小影响。
- 面试题生成容易被相邻主题污染。

RAG + rewrite 的价值是把"找证据"变成可观测、可评测、可调参的过程。

## 实操配方

冷启动阶段:

```text
expanded_queries: 2-3 个
candidate top_k: vector 40 + lexical 40
RRF k: 60
reranker input: top 80
final context: 6-10 chunks
```

调参时先看:

1. `candidate_recall@50` 是否过线。
2. expanded query 是否贡献 expected evidence。
3. `final_context_precision` 是否低于 0.70。
4. zero-hit 样本是否被扩写成假命中。

不要为了提升一个指标牺牲另一个指标。一个只追 recall 的 rewrite 会把系统变成"什么都像相关"。

## 面试追问

### Q: query rewrite 和 query expansion 有什么区别?

query expansion 更偏检索层,通常是加同义词、缩写、相关词。query rewrite 更偏语义层,会改写成多个可检索意图,有时还会补齐上下文省略。工程上二者常混用,但 rewrite 风险更高,因为它可能改变用户意图。

### Q: 怎么判断 rewrite 引入了噪声?

看打开 rewrite 前后的 per-case 对比。如果 `candidate_recall@50` 上升,但 `rerank_recall@10`、`mrr@10` 或 `final_context_precision` 下降,并且噪声 chunk 主要来自 expanded query,就说明 rewrite 扩太宽。

### Q: 为什么 zero-hit 样本也要跑 rewrite?

因为真实用户不会告诉系统"这个问题库里没有"。rewrite 必须在无主题或近邻干扰下保持克制。zero-hit 样本能暴露一种常见问题:改写器把不存在的主题扩成了库里存在的相邻主题,导致系统强行出题。

### Q: JobCopilot 里 rewrite 最重要的边界是什么?

M2 只支持主题 query。rewrite 不能把用户的主题问题自动扩成岗位类 query,更不能拼简历和 JD。岗位类 query 必须等 M3 三源融合,这是项目私有产品边界。

## 可作为 evidence anchor 的短句

- Query rewrite 的目标不是润色,而是把短 query 变成可检索意图。
- reranker 应判断 chunk 是否回答用户原问题,不是是否匹配扩写器想象出来的主题。
- rewrite drift 会让 candidate recall 看起来变好,但 final_context_precision 变差。
- 带有否定词、时间条件、版本条件的 query 不能在 rewrite 中被洗掉。
- JobCopilot M2 的 rewrite 不能把主题 query 扩成岗位/简历 query。
