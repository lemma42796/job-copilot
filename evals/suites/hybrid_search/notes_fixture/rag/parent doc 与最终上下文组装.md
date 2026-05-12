# Parent Doc 与最终上下文组装

## final context 是 RAG 的真正交付物

很多 RAG 系统只关注检索 Top-K,但真正喂给 LLM 的不是候选池,而是 final context。候选池可以很大,可以有噪声,可以包含多个召回通路的中间结果;final context 必须小、准、完整。

在 JobCopilot 里,final context 会被 QuizGenerator 和 AnswerJudge 使用:

- QuizGenerator 根据 final context 出题。
- AnswerJudge 根据 final context 判断用户回答是否覆盖 reference points。
- InterviewCoachAgent 在 M2.1 中根据评分 evidence 决定追问或总结。

所以 final context 的质量直接决定用户体验。召回阶段漏证据会导致题目浅;上下文太脏会导致题目跑偏;chunk 边界切断会导致 Judge 误判。

## 什么是 parent doc

chunk 通常是从一篇笔记切出来的片段。parent doc 是 chunk 所属的更大语义单元,可以是:

| parent 粒度 | 例子 |
|---|---|
| 同一小节 | 当前 `##` heading 下的所有段落 |
| 同一父 heading | 当前 `###` 的上级 `##` |
| 前后邻居 chunk | 命中 chunk 前后各 1 个 chunk |
| 整篇文档 | 只适合很短的文档 |

parent-doc 扩展的目标是补回被 chunker 切断的上下文,不是把整篇文章塞回去。

## 为什么命中 chunk 还不够

命中 chunk 可能只包含结论,缺少前提:

```text
因此当前默认关闭显式 cache_control。
```

这句话本身是答案,但没有解释原因。它的上一段可能写着:

```text
百炼 Context Cache 的显式缓存 TTL 只有 5 分钟,而 M2 的答题流通常是一次性生成、一次性提交。
```

如果只拿结论 chunk,QuizGenerator 可能会问"如何开启 cache_control";如果补回 parent context,它会问"为什么当前一次性答题流不适合开启显式 Context Cache"。

## parent-doc 扩展的风险

parent-doc 扩展最常见的风险是把邻近但无关的内容带进 final context。

例子:

```text
## Prompt Cache
### Provider 显式缓存
### LLM 响应缓存
### Embedding 缓存
### Retrieval 缓存
```

用户 query 是 `Context Cache TTL 为什么不适合 M2`。命中在 "Provider 显式缓存"。如果 parent-doc 扩到整个 `## Prompt Cache`,就会混入响应缓存、embedding 缓存和 retrieval 缓存。它们是同一大主题,但不是当前问题的直接证据。

这类噪声会拉低 `final_context_precision`。

## final_context_recall 与 final_context_precision

`final_context_recall` 问的是:该来的 evidence 有没有来。

```text
final_context_recall = 覆盖 expected evidence 的样本数 / 非 zero-hit 样本数
```

`final_context_precision` 问的是:最终上下文有多干净。

```text
final_context_precision =
  final context 中 direct_evidence + necessary_context 的 chunk 数
  /
  final context chunk 总数
```

这两个指标会互相拉扯。把 parent-doc 扩得很宽,recall 容易上升,precision 会下降。把 final context 收得太窄,precision 可能上升,recall 或 unsafe_boundary_rate 会变差。

## direct_evidence

`direct_evidence` 是能直接支持当前 query 的 chunk。它通常包含:

- 定义
- 结论
- 步骤
- 对比
- 公式
- 参数
- 项目私有决策
- 代码实现逻辑
- 失败样本原因

例子:

用户 query:

```text
JobCopilot M2 为什么 0 命中不让 LLM 兜底?
```

direct evidence:

```text
0 命中时命中 chunks < 3 起步直接报"笔记里没这主题",不兜底让 LLM 编。
```

这个 chunk 直接回答问题,应计入 relevant。

## necessary_context

`necessary_context` 本身不能直接回答 query,但删掉它会导致 direct evidence 断义、歧义或容易误判。

常见 necessary context:

| 类型 | 例子 |
|---|---|
| 表格 header | 只有 header 才知道数字列含义 |
| 代码函数签名 | 后续代码块只展示函数体 |
| 前置否定条件 | "不支持岗位 query"的原因在上一段 |
| 版本范围 | "M2 当前默认关闭"不代表永远关闭 |
| 父 heading | 标题说明这一节讨论的是 Context Cache 而非普通缓存 |
| 列表开头 | 后续 chunk 是列表第 3-5 项 |

necessary context 要保守。只是同一篇文章、同一大标题,不自动算 necessary。

## noise

`noise` 是被带进 final context 但不支持当前 query 的 chunk。

noise 的几种常见来源:

- parent-doc 扩太宽。
- RRF 召回多个相邻主题。
- reranker 被关键词骗到。
- query rewrite 扩成泛主题。
- 去重只按 chunk_id,没有按语义合并。

例子:

用户 query:

```text
final_context_precision 怎么算?
```

噪声 chunk:

```text
NDCG 使用折损增益衡量排序质量,适合多级相关性标注。
```

这个 chunk 是 RAG 评测相关,但不能支持 final_context_precision 的定义。它是 noise。

## 组装 final context 的流程

推荐流程:

1. 从 reranker Top-N 取候选。
2. 按 score、source diversity、heading_path 去重。
3. 对高分命中做 parent-doc 扩展。
4. 给扩展 chunk 标记来源:hit / parent_before / parent_after / same_heading。
5. 重新排序,让 direct hit 靠前,parent context 紧跟对应 hit。
6. 控制总 token budget。
7. 输出 final_context_ids 和 assembly_trace。

伪代码:

```python
hits = reranked[:10]
groups = []

for hit in hits:
    parent = load_parent_context(hit)
    trimmed = trim_parent_context(parent, around=hit, max_chunks=3)
    groups.append(ContextGroup(hit=hit, context=trimmed))

final = merge_groups(groups, max_chunks=8, max_tokens=6000)
final = reorder_by_group(final)
```

关键点:parent context 应围绕命中 chunk 展开,而不是按文档顺序随手切一段。

## source diversity

final context 不是只拿最高分 chunk。多个高分 chunk 如果来自同一 heading,可能只是重复表达。source diversity 的目标是让上下文覆盖不同证据来源,而不是重复同一段。

但 source diversity 也不能机械平均。用户问一个非常具体的项目私有问题时,所有证据可能都在同一文档中。此时强行跨文档会引入噪声。

一个实用策略:

```text
先保证 top direct evidence
再补同 heading necessary context
再补不同 heading 的互补证据
最后才考虑其他文档
```

## heading_path 的作用

heading_path 可以帮助判断 chunk 的语义位置:

```json
{
  "folder_path": ["rag"],
  "heading_path": ["Parent Doc 与最终上下文组装", "necessary_context"]
}
```

heading_path 的用途:

- 判断候选是否来自正确章节。
- parent-doc 扩展时找到同一小节。
- 评测失败时定位切片问题。
- UI 中展示证据来源。

如果 chunk 只有内容没有 heading_path,AnswerJudge 的 evidence 可解释性会下降。用户看到"根据笔记第 12 块"不如看到"根据 rag / Parent Doc / necessary_context"。

## chunk 边界安全

chunk boundary unsafe 指 final context 中出现关键语义被切断,且 parent-doc 没补回。

高风险结构:

- 否定句:只保留"可以开启",丢了"当前不建议"。
- 数值条件:只保留"Top-K",丢了 K 的取值。
- 表格:只保留数据行,丢了表头。
- 代码块:只保留调用,丢了函数定义。
- 有序列表:只保留第 3 步,丢了第 1-2 步。
- 对比表:只保留一列,丢了比较对象。

`unsafe_boundary_rate` 就是为了抓这种问题。它不是检索模型问题,通常应该调 chunker、overlap、markdown 结构保护或 parent-doc 扩展。

## JobCopilot 的 final context 要求

JobCopilot 的 final context 需要支持两类下游:

| 下游 | 对 final context 的要求 |
|---|---|
| QuizGenerator | 能出有证据的题,不能把近邻主题当题源 |
| AnswerJudge | 能判断 coverage/fidelity/depth,并输出 evidence |

QuizGenerator 更怕噪声。噪声多会让题目跑题。

AnswerJudge 更怕漏证据。漏证据会把用户的正确回答误判为 inferred 或 fabricated。

因此 M2 的检索不能只追一个指标。`final_context_recall`、`final_context_precision`、`unsafe_boundary_rate` 必须一起看。

## 0 命中与 final context

0 命中样本的正确 final context 通常应该为空,或明确标记为 insufficient evidence。不要为了给 LLM 一点材料而塞近邻内容。

例子:

用户 query:

```text
React useEffect 依赖数组
```

如果当前知识库没有 React,但有 streaming、SSE、前端事件流,系统不应该拿 SSE 前端客户端代码来出 React 题。

这种 case 的指标是 `zero_hit_precision`,不是 `final_context_precision`。如果 expected_zero_hit=true 且 final context 为空,它不参与 final_context_precision。

## final context 的报告字段

评测报告里每个 case 至少保留:

```json
{
  "id": "s001",
  "rerank_top_ids": [12, 15, 21],
  "final_context_ids": [12, 13, 15],
  "final_context_relevance": [
    {"chunk_id": 12, "label": "direct_evidence", "counted_relevant": true},
    {"chunk_id": 13, "label": "necessary_context", "counted_relevant": true},
    {"chunk_id": 15, "label": "noise", "counted_relevant": false}
  ],
  "failure_reason": null
}
```

如果没有 `final_context_relevance`,就无法解释 precision 低是因为 parent-doc 太宽、reranker 噪声、还是 rewrite drift。

## 失败样本一:召回对但上下文脏

用户 query:

```text
RAG 为什么要 final_context_precision?
```

final context:

1. final_context_precision 定义。
2. final_context_recall 定义。
3. NDCG 公式。
4. LLM-as-Judge position bias。
5. Prompt injection 防御。

这里 1 和 2 是 relevant,3-5 是 noise。虽然系统召回了正确证据,但 final context precision 只有 0.4。下游模型可能把检索评测、Judge 评测、prompt 防御混在一起出题。

失败原因应标 `final_context_noise`。

## 失败样本二:上下文干净但漏前提

用户 query:

```text
Context Cache 为什么当前默认关闭?
```

final context 只包含:

```text
Context Cache 当前默认关闭,后续多轮讨论面试题时再打开。
```

这段很干净,precision 高,但缺少"5 分钟 TTL 不适合一次性答题流"这个原因。final_context_recall 或 unsafe_boundary_rate 可能失败。

失败原因应标 `parent_context_missing`。

## 失败样本三:表格切断

原文:

```markdown
| 指标 | 含义 | 阈值 |
|---|---|---|
| final_context_precision | 相关 chunk / 总 chunk | ≥ 0.70 |
```

如果 chunk 只包含:

```markdown
| final_context_precision | 相关 chunk / 总 chunk | ≥ 0.70 |
```

表头丢了。人还能猜,模型不一定稳定。parent-doc 应补回表头,否则 boundary unsafe。

## 面试追问

### Q: parent-doc 扩展是不是越大越好?

不是。扩展越大,越容易补回前提,但也越容易引入噪声。应该围绕命中 chunk 做局部扩展,并用 final_context_precision 约束噪声比例。

### Q: final_context_precision 为什么比 candidate_precision@50 更重要?

candidate Top-50 是粗召回池,本来允许有噪声。真正影响 LLM 输出的是 final context。只要 final context 干净,候选池有噪声问题不大;如果 final context 脏,下游题目和评分都会受影响。

### Q: necessary_context 和 noise 的区别?

necessary_context 删除后会导致 direct evidence 断义、歧义或误判。noise 删除后不影响回答当前 query。是否同文档、同标题不是判定标准。

### Q: AnswerJudge 更怕 recall 低还是 precision 低?

AnswerJudge 更怕漏证据,因为漏证据会把用户正确回答判成 inferred 或 fabricated。但 precision 低也会污染判断,尤其是噪声里有相邻但矛盾的说法。因此 Judge 路径要同时守 recall 和 precision。

## 可作为 evidence anchor 的短句

- final context 是 RAG 的真正交付物。
- parent-doc 扩展的目标是补回被 chunker 切断的上下文,不是把整篇文章塞回去。
- `direct_evidence` 能直接支持当前 query,`necessary_context` 防止证据断义。
- final_context_precision 防止系统靠堆无关 chunk 换 recall。
- 0 命中样本不应该为了给 LLM 材料而塞近邻内容。
