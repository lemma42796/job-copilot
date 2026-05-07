# resume_review 对抗集(S21 子任务 4-D)

20 条故意注入幻觉的简历草稿,验 reviewer agent 的检出能力(EVAL_PLAN §8.1)。

**4-C 不评这条**:reviewer 是被评对象、不是 Judge;本 suite 评的是 reviewer
的 precision/recall,通过把 reviewer 输出的 findings 与人工标注的 ground truth
findings 比对算,**不需要 LLM-as-Judge**。框架代码留待 4-D 与数据集一起做。

## 数据集构造方法(EVAL_PLAN §8.1)

| 类型 | 注入数量 | 示例 |
|------|---------|------|
| `fabrication` | 8 | "主导设计千万 DAU 系统"(profile 没有) |
| `exaggeration` | 6 | 原"参与"→"独立负责" |
| `unsupported_number` | 4 | 凭空写"提升 35%" |
| `clean` | 2 | 验证不会误报 |

W8 第二轮 dogfood 收集的对抗例可作种子:#18 "具备高并发架构设计能力" 模糊
能力陈述 / #19 C++/Java/OpenAI/Claude/LLaMA JD-mirror skills / #20 跨 chunk
业务 context 错配 / 凭空 "AWS"。

## 阈值(EVAL_PLAN §8.2)

| 指标 | 阈值 |
|------|------|
| 幻觉检出 precision | ≥ 0.90 |
| 幻觉检出 recall | ≥ 0.85 |
| `fabrication` 子类 recall | ≥ 0.95 |
| 严重度判定准确率 | ≥ 0.80 |

**precision 优先于 recall** — 误报 → 用户烦 → 关 review → 失去保护。
