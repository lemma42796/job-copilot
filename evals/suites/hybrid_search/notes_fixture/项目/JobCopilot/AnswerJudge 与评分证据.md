# AnswerJudge 与评分证据

## AnswerJudge 的定位

AnswerJudge 不是简单给用户答案打一个分。它的任务是把用户回答拆成可解释证据:

```text
用户回答
→ coverage evidence
→ fidelity evidence
→ depth evidence
→ Python 聚合总分
```

JobCopilot 的面试陪练需要告诉用户:

- 哪些 reference point 答到了。
- 哪些 claim 有笔记支持。
- 哪些 claim 是推断或编造。
- 答案是否有深度,例如原因、取舍、边界。

只给一个 82 分没有意义。用户需要知道下一轮该补哪一点。

## 三层 evidence

### Coverage

Coverage 看用户答案是否覆盖题目的 reference points。

标签:

| label | 含义 |
|---|---|
| `hit` | 明确覆盖 |
| `partial` | 提到但不完整 |
| `miss` | 没覆盖 |

例子:

```text
reference point: RAG 仍保留是因为 AnswerJudge 需要 evidence chunk。
user answer: RAG 可以让评分有依据。
```

这可以是 partial 或 hit,取决于是否明确说到 chunk/evidence。若只说"更准确",通常不是 hit。

### Fidelity

Fidelity 看用户答案里的 claim 是否被证据支持。

标签:

| label | 含义 |
|---|---|
| `supported` | chunks 明确支持 |
| `inferred` | chunks 没明说,但合理推断 |
| `fabricated` | chunks 不支持或相矛盾 |

Fidelity 是防幻觉层。用户答得流畅不代表正确。

### Depth

Depth 看答案是否有更高级的理解。JobCopilot 初版看三个维度:

- `why`: 是否解释原因。
- `tradeoff`: 是否讲取舍。
- `boundary`: 是否讲适用边界。

Depth 不适合用 Cohen's kappa,因为三个布尔维度类别分布不稳。M2 评测用 accuracy 更直观。

## 为什么不用 LLM 算总分

LLM 擅长读文本和给解释,但不适合掌握最终分数规则。原因:

- 同样 evidence 下,LLM 每次算分可能波动。
- fabricated 锁顶必须硬执行。
- 分数权重是产品策略,不应该藏在 prompt 里。
- 后续调整权重时,不应该改 Judge 的语义判断。

因此:

```text
LLM → evidence labels
Python → weighted score
```

这是 M2 的锁定决策。

## fabricated 锁顶规则

fabricated 是最危险的 label。一个答案如果编造关键机制,即使覆盖多个 point,也不应拿高分。

例子:

```text
题目: RRF 怎么融合多路召回?
用户: RRF 会训练一个神经网络学习每个检索通路的权重。
```

如果笔记只讲 reciprocal rank 公式,这条就是 fabricated。它不是"表达不精确",而是机制编造。

fabricated 锁顶能防止模型因为 coverage 高而给出虚高分。

## supported 与 inferred 的边界

`supported` 要求笔记直接支持。

`inferred` 是合理推断,但不能当成笔记明示事实。

例子:

```text
chunk: M2 只支持主题 query,岗位类 query 放 M3。
claim: 当前 M2 不应该根据 JD 自动出岗位题。
```

这可以是 supported,因为含义直接对应。

```text
claim: 这样做能减少研发排期压力。
```

如果笔记没有说排期,最多 inferred,不能 supported。

## 常识答案的处理

面试中用户可能用通用知识答对一部分。例如题问 RAG recall,用户说 recall 是检索到相关文档的比例。即使 chunk 没逐字写,这也可能是 inferred 或 supported,取决于笔记是否有指标定义。

但 JobCopilot 私有问题不能用常识替代。

例子:

```text
题目: JobCopilot 为什么当前默认关闭 Context Cache?
用户: 因为缓存可能过期。
```

这是泛常识,没有命中项目私有原因。正确证据是 5 分钟 TTL 不适合 M2 一次性答题流。用户答案最多 partial,不能 hit。

## evidence 引用

Judge 输出必须引用 evidence。每条 coverage point 或 fidelity claim 最好关联:

- chunk_id。
- heading_path。
- 短 evidence quote 或 anchor。
- label reason。

示例:

```json
{
  "claim": "Context Cache 当前默认关闭是因为 TTL 不适合一次性答题流",
  "label": "supported",
  "evidence_chunk_ids": [42],
  "reason": "chunk 明确写到 5 分钟 TTL 与 M2 一次性答题流不匹配"
}
```

没有 evidence 的 supported 是不可信的。

## Judge prompt 的输入

AnswerJudge 输入包括:

- question prompt。
- reference answer。
- reference points。
- retrieved chunks。
- user answer。

不要只给 reference answer 和 user answer。否则 Judge 会变成纯语义相似度评分,无法判断用户 claim 是否被笔记支持。

## 三层评分的例子

题目:

```text
解释 JobCopilot M2 为什么保留 RAG,以及它和长上下文的关系。
```

reference points:

1. 当前 12.24 万字还没到 qwen3.6-flash 1M context 容量上限。
2. RAG 保留是为了 evidence、0 命中和 final context 干净度。
3. 后续 M2.5/M3 多源扩展需要检索架构。

用户答案:

```text
因为长上下文模型容易 lost in the middle,所以必须用 RAG。RAG 还能省成本。
```

Coverage:

- p1: miss。
- p2: partial,提到 RAG 但没说 evidence/0 命中/precision。
- p3: miss。

Fidelity:

- "长上下文容易 lost in the middle": inferred 或 supported,看笔记是否有。
- "所以必须用 RAG": fabricated 或 unsupported,因为项目笔记说当前不是容量强迫。
- "RAG 能省成本": inferred,但不是 M2 的核心理由。

Depth:

- why: partial。
- tradeoff: false。
- boundary: false。

## 错误类型一:把 inferred 当 fabricated

Judge 太严格时,会把合理常识推断误判成 fabricated。

例子:

```text
chunk: reranker 可以把正确 evidence 推到 Top-10。
claim: reranker 能提升下游生成质量。
```

如果笔记没有逐字写"提升下游生成质量",这仍然是合理推断,不应标 fabricated。

这种问题要通过 answer_judge dataset 的 11-15 类"专业常识答案"守门。

## 错误类型二:把项目私有编造当 inferred

Judge 太宽时,会把编造的项目事实当合理推断。

例子:

```text
claim: JobCopilot M2 已经支持上传多份简历并按岗位切换。
```

项目约束写明简历是全库单条记录,不做多份切换。这条应标 fabricated,不是 inferred。

项目私有事实要比通用知识更严格。

## 错误类型三:coverage 被流畅表达骗到

用户答案很流畅,但没有覆盖 reference point。

题目问:

```text
final_context_precision 和 final_context_recall 怎么互补?
```

用户答:

```text
它们都是 RAG 评测指标,能帮助系统提升检索质量。
```

这句话正确但太泛。没有说 recall 看证据有没有来,precision 看上下文是否干净。Coverage 应该 partial 或 miss,不能 hit。

## 错误类型四:忽略否定条件

项目笔记里很多关键事实是否定式:

- 不接 zip 笔记上传。
- 不新增测试代码。
- M2 不处理岗位类 query。
- Context Cache 当前默认关闭。
- 不做泛化多 Agent。

Judge 必须保留否定条件。用户如果答成"支持 zip 上传"或"M2 会根据 JD 出题",应标 fabricated。

## Judge 与 RAG 的耦合

AnswerJudge 的质量强依赖 RAG:

| RAG 问题 | Judge 后果 |
|---|---|
| 漏 evidence | 正确答案被判 inferred/miss |
| final context 脏 | 噪声干扰 fidelity |
| chunk boundary unsafe | 否定/数值/表格被误判 |
| zero-hit false positive | Judge 基于错误材料评分 |

因此 M2 补 RAG 评测是 AnswerJudge 质量的前置工作。

## Judge 报告指标

answer_judge suite 的核心指标:

| 指标 | 阈值 |
|---|---|
| Coverage kappa | ≥ 0.7 |
| Fidelity kappa | ≥ 0.7 |
| Depth accuracy | ≥ 0.75 |

kappa 用来衡量 Judge 和人工标注一致性。Depth 用 accuracy,因为二值维度分布不稳定。

## tool=on 与 tool=off baseline

AnswerJudge 在评测中保留 `lookup_in_notes_global` 工具,因为真实产品就是 tool=on。

但需要额外跑 tool=off baseline。这样才能知道工具是否真的提升 Fidelity,而不是只增加成本。

期望:

- tool=on 的 Fidelity kappa 更高。
- 用户讲常识子集准确率更好。
- 成本上升可接受。

## 与 M2.1 追问的关系

M2.1 的 `decide_next_action` 会依赖 Judge evidence。

规则示例:

```text
coverage miss 多 → 追问漏掉的 reference point
fidelity fabricated → 先纠错
depth 缺 boundary → 追问适用边界
整体足够好 → 总结并进入下一题
```

如果 Judge evidence 不稳定,InterviewCoachAgent 的分支就会不稳定。

## 面试追问

### Q: 为什么 AnswerJudge 不直接输出总分?

因为总分是产品策略,需要稳定、可控、可回归。LLM 输出 evidence 和 label,Python 聚合分数,可以硬执行 fabricated 锁顶和权重。

### Q: supported、inferred、fabricated 怎么区分?

supported 是笔记明确支持;inferred 是笔记没明说但合理推出;fabricated 是笔记不支持或相矛盾。项目私有事实要严格,不能用通用常识脑补。

### Q: 为什么 Judge 需要 retrieved chunks?

没有 chunks,Judge 只能比较用户答案和参考答案,无法判断事实是否来自笔记。JobCopilot 要的是 grounded scoring,不是作文评分。

### Q: fabricated 为什么要锁顶?

因为编造关键机制在面试中风险很高。一个答案即使覆盖了不少点,只要混入关键编造,就不应拿高分。

## 可作为 evidence anchor 的短句

- AnswerJudge 不是简单打分,而是生成可解释评分证据。
- LLM 负责 evidence labels,Python 负责 weighted score。
- 项目私有事实不能用通用常识替代。
- fabricated 不能只扣小分,需要触发锁顶。
- M2.1 的追问分支依赖 AnswerJudge evidence。
