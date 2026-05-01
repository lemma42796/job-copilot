---
adr: 0003
title: v1 阶段切换到阿里云百炼 Qwen3.6 系列
owner: lemma42796
status: Accepted
date: 2026-05-01
supersedes: 0001
---

# ADR-0003:v1 阶段切换到 Qwen3.6 作为唯一 LLM Provider

## 上下文

ADR-0001 选择 DeepSeek V4 的核心理由是单价与 Prompt Cache 命中价均显著低于 Qwen,16 周开发期总成本 < ¥200。

当前出现一个改变结论的变量:**作者阿里云百炼账户尚有约 ¥15 的剩余额度**(原属代金券/活动赠送,过期不可提)。

在 16 周开发期总 LLM 成本仍预计在 ¥200 量级的前提下,这 ¥15 立即可用,且 Qwen3.6 系列在中文场景与 DeepSeek V4 实测差距不显著(参考 OpenCompass 2026 中文榜单)。继续坚持 ADR-0001 等于自愿放弃这部分赠款。

## 决策

**v1 阶段唯一 LLM Provider 切换为阿里云百炼 Qwen3.6 系列。** Tier 路由:

| Tier | 模型 | 思考模式 | 用途 |
|------|------|---------|------|
| CHEAP | `qwen3.6-flash` | 关 | 抽取/校验/打分类 |
| STANDARD | `qwen3.6-flash` | 开 | 中等推理 |
| PREMIUM | `qwen3.6-plus` | 开 | 创作 / 深推理 / 面试模拟 |

**调用方式**:OpenAI 兼容端点 `https://dashscope.aliyuncs.com/compatible-mode/v1`,SDK 复用 `openai` Python 包。

**API Key 环境变量**:`DASHSCOPE_API_KEY`(用户需自备百炼 Key,JobCopilot 不托管)。

**多模态**:Qwen3.6-VL-Flash(用于 JD 截图/简历图片 OCR + 抽取一步完成)。

**Embedding**:`text-embedding-v3`(百炼自带,1024 维,与 BGE-M3 同生态),不再依赖 SiliconFlow。

## 替代方案与拒绝理由

### A. 维持 ADR-0001(DeepSeek V4)

**优点**:单价低 3-6 倍,长期总成本最优。

**拒绝理由**:
1. ¥15 阿里云额度过期不可提,不用即损失
2. 16 周开发期 LLM 总成本预估仍在 ¥200 量级,绝对值差异 < ¥150,低于工程切换的机会成本阈值
3. Qwen3.6 在中文 RAG / 长上下文场景实测优于 DeepSeek V4-Pro(参见 OpenCompass 2026/Q1 中文榜单)
4. 百炼控制台自带可观测、计费分析、Cache 命中率统计,省去自建

### B. 多 Provider:DeepSeek + Qwen 混用

**拒绝理由**:与 ADR-0001 §A 同样的工程理由(SDK / 计费 / Cache 语义 / 评测变量爆炸),不再赘述。一个 Provider 是工程纪律。

### C. 直接 BYOK,不绑定 Provider

**拒绝理由**:仍需要在 Prompt / Tier / Cache 策略上 hardcode 一个 Provider 的特性,真正的 Provider 无关化代价过高,放 v2。

## 后果

### 正面

- **立即变现 ¥15 赠款**,等价于早期开发的"免费 16 周"
- **百炼控制台**(Trace / Cache 命中 / 计费按 Feature 拆分)开箱即用,省去自建可观测面板的初期成本
- 多模态(Qwen3.6-VL)、Embedding(`text-embedding-v3`)、Reranker(`gte-rerank-v2`) 在百炼一站到位,**外部依赖只剩 1 个**(原 ADR-0001 仍需 SiliconFlow 提供 BGE-M3)
- `openai` Python SDK 兼容,代码迁移成本几乎为零(只换 `base_url` 与模型名)

### 负面

- **单价上升 3-6 倍**:Qwen3.6-Flash 输出 7.2 元/M(DeepSeek V4-Flash 2 元/M),Cache 命中 0.12 元/M(DeepSeek 0.02 元/M)。各场景成本上限按 §相关页同步上调
- **¥15 用尽后需要再次决策**:届时已积累实际调用数据,可基于真实成本重评是否切回 DeepSeek
- 简历亮点叙事变化:从"成本工程极致优化"变为"利用云厂商赠款做敏捷 MVP"——同样讲得通,但故事不同
- 促销期 Qwen3.6-Plus 的价格仍未公开(2026/05/01),Premium 档真实成本待实测

## 抽象层契约(无变化)

ADR-0001 §抽象层契约保留有效。当前增加 `QwenProvider` 实现,`DeepSeekProvider` 类预留为 ¥15 用尽后的备选实现:

```python
class LLMProvider(Protocol):
    async def complete(self, ...) -> Response: ...
    async def embed(self, ...) -> list[float]: ...

class QwenProvider(LLMProvider): ...    # v1 当前实现
class DeepSeekProvider(LLMProvider): ... # 备选,¥15 耗尽后启用
```

## 复审条件(关键)

满足以下任一条件需重新评审本 ADR:

1. **阿里云百炼账户余额 < ¥1**(主要触发条件,预计 4-8 周内触发)
2. Qwen3.6 系列出现价格上调 > 30%
3. 百炼可用性月度 < 99%(实测)
4. DeepSeek 推出明显性价比反超(例如 V5 + 长缓存 TTL)

**1 触发时的默认动作**:切回 DeepSeek V4(ADR-0001 设定的方案),工作量预估 < 半天(只换 `base_url` + 模型名 + 缓存控制位)。

## 与 ADR-0001 的关系

- ADR-0001 状态改为 `Superseded by ADR-0003`,原文保留作为决策历史与"复审条件触发后的回切方案"
- 选型矩阵、抽象层契约、降级链路设计均继承自 ADR-0001,本 ADR 只覆盖 Provider 标识与 Tier 模型映射

## 相关

- ADR-0001:仅使用 DeepSeek V4(已 Superseded)
- ADR-0002:为什么用 Postgres 当向量库
- 2-TECH_DESIGN.md §4 LLM 调用层设计
