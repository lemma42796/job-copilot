---
adr: 0001
title: 仅使用 DeepSeek V4 系列作为唯一 LLM Provider
owner: lemma42796
status: Superseded by ADR-0003
date: 2026-05-01
superseded_by: 0003
---

> **历史决策(已被 ADR-0003 取代)**:作者阿里云百炼账户尚有 ¥15 待消耗,v1 阶段切换到 Qwen3.6 系列。本文保留作为决策历史与"百炼额度耗尽后回切 DeepSeek"的参考方案。


# ADR-0001:仅使用 DeepSeek V4 作为唯一 LLM Provider

## 上下文

JobCopilot 是一个 LLM 重度应用,所有核心能力(JD 解析、个人档案抽取、匹配分析、简历定制、面试模拟、评测 Judge)均依赖 LLM 调用。

候选 Provider 包括:
- 阿里云百炼:Qwen3.6 Flash / Plus / Max-Preview
- DeepSeek:V4-Flash / V4-Pro
- Anthropic:Claude Sonnet 4.6 / Opus 4.7
- OpenAI:GPT-5 系列
- 字节豆包、智谱 GLM、月之暗面 Kimi 等
- 本地 Ollama:Qwen3-8B/32B、DeepSeek-V3.1-Distill 等

作者(求职者)预算紧张,Qwen 免费额度已用尽。需要在 16 周开发期内做出交付,且简历/面试讲述需要架构决策的清晰理由。

## 决策

**v1 阶段仅使用 DeepSeek V4 系列**,Tier 路由如下:

| Tier | 模型 | 思考模式 | 用途 |
|------|------|---------|------|
| CHEAP | deepseek-v4-flash | 关 | 抽取/校验/打分类 |
| STANDARD | deepseek-v4-flash | 开 | 中等推理 |
| PREMIUM | deepseek-v4-pro | 开 | 创作/深推理 |

**不引入** Qwen / Claude / GPT / 本地 Ollama,即便它们在某些维度更优。

## 替代方案与拒绝理由

### A. 多 Provider 路由(Qwen Flash + DeepSeek Pro + Claude Sonnet 等)

**优点**:每档选当前价格/质量最优,理论成本最低,简历亮点"多 Provider 容灾"。

**拒绝理由**:
1. **维护多份 SDK + 多份计费 + 多份 token 计算逻辑**,单人项目运营成本高
2. **每个 Provider 的 Prompt Cache 语义不同**(隐式 vs 显式、TTL、key 设计),跨 Provider 难以保证缓存命中
3. **评测集横向跑**需要严格控制变量,Provider 多了无法解释指标差异
4. 成本差异在 16 周开发期总额 < ¥200 的量级,不值得

### B. 仅使用 Qwen3.6 系列

**拒绝理由**:
1. 无免费额度后,**Qwen3.6-Flash 单价输出 7.2 元/M,DeepSeek V4-Flash 仅 2 元/M**,差 3.6 倍
2. **Qwen 缓存命中价 0.12 元/M,DeepSeek 仅 0.02 元/M**,差 6 倍。LLM 应用核心成本杠杆是缓存,DeepSeek 在这点上完胜
3. 没有结构化的成本工程故事可讲

### C. 仅使用 Claude / GPT

**拒绝理由**:
1. 价格高 1-2 个数量级
2. 网络访问需要代理,本地优先部署不友好
3. 中文场景上 DeepSeek V4 实测不输于 Claude Sonnet,无理由付溢价

### D. 本地 Ollama 跑 Qwen3-32B 或 DeepSeek-V3-Distill

**拒绝理由**:
1. 用户笔记本硬件参差,部署门槛与体验问题大
2. 简历定制等创作类任务,本地 32B 模型质量明显低于 DeepSeek V4-Pro
3. 增加部署文档复杂度,违背"docker compose up"一键启动原则

### E. 不做选择,LLM 抽象层接所有 Provider

**拒绝理由**:
1. "我什么都支持"等于"我什么都没认真做"
2. 抽象层确实保留 Provider Protocol(见 ADR-0001 后续小节),但**当前实现只绑定 DeepSeek**

## 后果

### 正面

- **架构与代码极度简化**:一个 SDK、一个计费、一种 Prompt Cache 语义
- **评测集结果可解释**:无 Provider 间噪声
- **运营成本最低**:开发期总成本预估 < ¥200
- **故事干净**:面试时能清晰说明"为什么不用 X"
- **DeepSeek V4 是 2026 年中文场景上性价比最优解**,这本身就是合理的工程选择

### 负面

- 单点依赖:DeepSeek API 不可用时全系统不可用(已通过明确错误提示而非伪装可用来缓解)
- 简历不能写"多 Provider 路由"(但可以写"成本敏感的 Tier 路由 + 75% Prompt Cache 命中率",更具体)
- DeepSeek V4-Pro 在 2026/05/31 后促销结束,Premium 档成本会上升 4 倍。届时需要重新评估(可能切回 V4-Flash 思考模式作为 Premium 等价物)

## 抽象层契约(为未来留口子)

虽然当前只绑定 DeepSeek,代码层面仍保持 Provider 无关:

```python
class LLMProvider(Protocol):
    async def complete(self, ...) -> Response: ...
    async def embed(self, ...) -> list[float]: ...

# 当前唯一实现
class DeepSeekProvider(LLMProvider): ...

# 未来可加:
# class QwenProvider(LLMProvider): ...
# class ClaudeProvider(LLMProvider): ...
```

**未来切换 Provider 的工作量预估:1 周以内**(主要是 Prompt 在新模型上的回归调优,不是代码改动)。

## 复审条件

满足以下任一条件需重新评审本 ADR:

1. DeepSeek API 价格上调 > 50% 且无可用促销
2. DeepSeek 服务可用性月度 < 99%(实测)
3. 出现一个在中文场景明显优于 V4-Pro 的开源/低价模型
4. 项目用户增长到需要按地域分布部署(海外用户访问 DeepSeek 不便)

## 相关

- ADR-0002:为什么用 Postgres 当向量库
- 2-TECH_DESIGN.md §4 LLM 调用层设计
