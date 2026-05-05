---
title: S18 简历定制 dogfood 第一轮 — match prompt 三轮迭代 + reviewer 修复 + 重大方法论 audit
status: 进行中(drafter v1.0.1 真 bug 5 类待修;reviewer v1.0.2 落地;match v1.1.2 落地)
date: 2026-05-05
purpose: S18 主线 dogfood 第一轮的完整调研产物。包含 match_analyst / resume_drafter / resume_reviewer 三 prompt 的迭代历史、6 条 dogfood 样本聚合、以及一次重大 audit 暴露的方法论错误(没读 profile 凭反推下判断,导致 30-40% bug 诊断误判)
---

# 切片范围

S18 主线本意:跑 5+ 真实 (JD, profile) 三元组,验证 drafter v1.0.1 4 条强约束 + reviewer v1.0.1 反幻觉,目标 review 通过率 ≥ 50% / 高 finding 平均 ≤ 1。

实际进展:
- ✅ match_analyst prompt v1.0.0 → v1.1.0 → v1.1.1 → v1.1.2(三轮迭代,消费 v1.0.5 引入的 `or_group_id` 字段)
- ✅ resume_reviewer prompt v1.0.1 → v1.0.2(三处判定标准收窄)
- ✅ S18 dogfood 第一轮 6+1 条样本(match #4-#8 + #11 → resume #1-#6)
- ❌ review 通过率仅 17%(1/6)未达目标
- ❌ drafter v1.0.1 真 bug 5 类暴露但未修(留下次)
- ⚠️ **audit 暴露重大方法论问题:之前所有 prompt 调整都建立在"没读 profile 反推 chunks 内容"上,30-40% bug 诊断是误判**

# 产出文件清单

```
apps/api/
├── scripts/seed_jds_from_evals.py                               # S18 dogfood 5 JD seed(从 evals dataset 取 5 个岗位方向差异 JD)
├── src/jobcopilot_api/
│   ├── prompts/
│   │   ├── match_analyst/v1.1.0.j2                              # 7 类 bug pattern 修复(初版)
│   │   ├── match_analyst/v1.1.1.j2                              # OR-group 引入(规则 8)+ E/F 加固
│   │   ├── match_analyst/v1.1.2.j2                              # 4 条规则简化版(8 条压回 4 条)+ name 字段铁律
│   │   ├── resume_drafter/v1.0.1.j2                             # S17 dogfood 沉淀的 4 条强约束(A 单 chunk / B 副词 / C 业余项目 / D skill 硬规则)
│   │   ├── resume_reviewer/v1.0.1.j2                            # 6 类 M 标签(M1-M6)
│   │   └── resume_reviewer/v1.0.2.j2                            # M2/M4/M5 判定标准收窄 + granularity 字段说明
│   └── routers/
│       ├── matches.py                                           # PROMPT_KEY → v1.1.2
│       └── resumes.py                                           # DRAFTER_PROMPT_KEY → v1.0.1 / REVIEWER_PROMPT_KEY → v1.0.2
```

# 设计决策

## match_analyst 三轮迭代

**v1.1.0**(初版,基于 5 张 BOSS-style JD dogfood 沉淀的 7 类 bug):
- 规则 1 互斥铁律 + 隐含命中 + 近义合并
- 规则 2 strength 校准表(0.30-0.39 给"栈背景")
- 规则 3 副词白/禁名单
- 规则 4 反过度包装 + 不在自家项目里塞 JD 缺口
- 规则 5 gap_summary 反"稳定性风险"套话
- 规则 6 suggestions ↔ missing 一致性
- 规则 7 evidence_chunk_ids 必填

**v1.1.1**:加规则 8 OR-group(消费 JDParser v1.0.5 引入的 `or_group_id` 字段)+ E/F 加固。但带来 `name="golang/java/or_group_4"` 字段污染、function calling 从 hit 退步到 missing、`agent 编排` hit 越界等新 bug。

**v1.1.2**(简化版,8 条压回 4 条核心):
- 规则 1:命中判定(决策树 + 校准表 + 越界禁令 + 上层框架蕴含子能力 + 间接概念禁令 + evidence)
- 规则 2:OR-group(name 单一项铁律,4 个 ❌ 反例)
- 规则 3:自由文本字段措辞(副词白名单 + gap 内容铁律)
- 规则 4:suggestions 编写(反包装 + 不在自家项目里塞 + 一致性)

**dogfood 三代对比**(JD #24 + profile #15 / match #9-#11):

| 维度 | v1.1.0(#9) | v1.1.1(#10) | v1.1.2(#11) |
|---|---|---|---|
| score | 68 | 68 | 72 |
| missing 数 | 5 | 3 | 2 |
| name 污染 | — | "golang/java/or_group_4" | 修(group 4 整组不列)|
| hit 越界("agent 编排")| — | 含 | 不含 |
| llamaindex strength | 95 | 40 抖动 | 90 |
| Function Calling | hit 75 | missing 退步 | missing(顽固)|
| advantage 中间夸张词 | "突出/较强/高度契合" | "深厚/高度契合" | "显著优势/完美契合"(LLM 同义词替换游戏) |
| gap 资历套话 | 三连 | 三连 + "需确认学历" | "资历倒挂风险"(顽固) |
| improvement 在 JobCopilot 塞缺口 | 1 处 | MCP 修住 / FC 这条仍犯 | FC 这条仍犯(同一处)|

## reviewer v1.0.2 三处判定收窄

`ReviewFinding.issue_type` Literal 4 值不动(`fabrication / exaggeration / unsupported_number / other`)。只在 prompt 里精修 M2/M4/M5 判定标准:

- **M2**(副词违反):**先在 chunks 任意字段搜词,找到 → 不报**(候选人自用 = drafter 镜像合规);只在 drafter 新增 chunks 没有的词时才报
- **M4**(技能列表无证据):**chunks 任意 granularity 任意字段**(skill 字段 / tech_stack / summary / bullets)有字眼即视为有证据;**仅完全无字眼且无近义词**才报
- **M5**(数字凭空):搜索范围扩到任意字段;**数字"挪用"**(原属 A 写到 B)单独保留 high

新增**关键前提段**:开头说明 chunks 的 4 类 granularity(`summary / experience / project / skill`)及各自字段语义,让 LLM 明确知道 `granularity=skill` 是候选人自报权威清单,不是栈背景。

# S18 dogfood 第一轮 6 条样本

| Resume | Match | JD | drafter 版本 | reviewer 版本 | status | high finding | 性质 |
|---|---|---|---|---|---|---|---|
| #1 | #11 | JD#24 LLM 智能体 Junior | v1.0.1 | v1.0.1 | review_failed | 3 | 全是 M4 误判(技能段栈名词)|
| #2 | #4 | JD#9 AI 应用开发 | v1.0.1 | v1.0.1 | **ready** ✅ | 0 | 唯一通过 |
| #3 | #5 | JD#10 AI Agent | v1.0.1 | v1.0.1 | review_failed | 4 | M4×3 + M5(P99<1.2s 误判)|
| #4 | #6 | JD#11 AI/算法 | v1.0.1 | v1.0.1 | review_failed | 2 | M4 + 数字错位(43% 真错)|
| #5 | #7 | JD#12 AI 校招 | v1.0.1 | v1.0.1 | review_failed | 4 | 求职意向校招错标 + M1 跨 chunk + M4×2 |
| #6 | #8 | JD#13 全栈管培 | v1.0.1 | v1.0.1 | review_failed | 5 | M1 + M3 + M4×3 |
| 重刷 | #10 | JD#24(reviewer v1.0.2 重测)| v1.0.1 | **v1.0.2** | review_failed | 3 | 求职意向抄 JD + Flask 真编造 + Next.js 泛化(全是 drafter 真 bug) |

**通过率 = 1/6 = 17%**(目标 ≥ 50%)❌
**高 finding 平均 = 3.0**(目标 ≤ 1)❌
cost / latency 全达标(¥0.013 中位 / 8.7s)✅

# 重大教训:audit 暴露的方法论错误

## 起因

在 dogfood #1 给出 6 条样本结果后,本来准备出 drafter v1.0.2 修"M4 占 61% high finding" 的"技能段栈背景词水分"问题。**用户问"你看过简历 #15 吗"** — 我没看过,只看了 match / resume 输出反推 chunks 应该有什么。

去看 profile #15 后**重大翻盘**:

| 我之前的判断 | profile #15 实情 |
|---|---|
| "P99 延迟 < 1.2s 凭空捏造[M5]" | summary chunk 162 **原文**有 |
| "Token 43% 数字错配[M5]" | experience chunk 163 **原文**有 |
| "Gin / Datadog / Linear / 阿里云 等是栈背景编造[M4]" | chunks 169-191 **每个 skill 独立 chunk**(`granularity=skill`),候选人自报清单 |
| "'擅长 / 较深积累 / 全栈作者' 副词违反" | summary chunk 162 **原文**就用这些词;project chunk 166 role 字段 = "全栈作者" |
| "drafter 强约束 D 6/6 失守" | drafter 实际**遵守 chunks**(从 skill 字段照搬),不是失守 |

## audit 结论

| Prompt | 规则 | audit 结论 |
|---|---|---|
| match v1.1.2 规则 3.1(副词强夸张禁:精通 / 全栈作者 / 较深积累 等)| 🔴 撤回 chunks 自用词("精通"是 candidate skill.level=expert 字段值;"全栈作者"是 project.role 字段值;"较深积累"是 summary 原文)|
| match v1.1.2 规则 4.2(拔高角色禁:chunks 是工程师就别建议突出 Tech Lead)| 🟡 精修 — chunks 真有 tech lead 角色让候选人突出是合规建议;**chunks 没出现的角色**才禁拔高 |
| match v1.1.2 规则 1.2(strength 校准表 0.30-0.39 给"栈背景")| 🟡 精修 — 优先用 skill 字段的 level 映射 strength,其次才用动作证据强度 |
| drafter v1.0.1 强约束 D(只列"动作型证据"skill)| 🔴 撤回 — 错位最严重,chunks 实际有 skill 字段(权威清单),drafter 应直接照搬 |
| drafter v1.0.1 强约束 B(副词白名单)| 🔴 撤回 chunks 自用词;保留禁:卓越 / 优秀 / 资深(级别)|
| reviewer v1.0.1 M2(副词违反)| 🔴 撤回 — 已落 v1.0.2 |
| reviewer v1.0.1 M4(技能段无动作证据)| 🔴 撤回 — 已落 v1.0.2 |
| reviewer v1.0.1 M5(数字搜索范围)| 🟡 精修 — 已落 v1.0.2 |

# drafter v1.0.1 真 bug 5 类(留下次修)

reviewer v1.0.2 重刷 match #10 / JD #24 后,5 条 finding 全是 drafter 真问题(无误判):

1. **求职意向硬抄 JD title**(含"(Junior)"标识)— drafter 没读 candidate `target_titles` 字段
2. **强约束 D 在"X/Y: 描述"形式下失守**(Flask 没 chunks 证据硬塞)
3. **副词跨 skill 错位**("精通"是 Python skill.level=expert 字段,被错位到 Go(advanced));应当依 chunks skill.level 一致映射
4. **侧项目泛化为通用能力**(Next.js 仅在 JobCopilot 侧项目 tech_stack 里出现,简历技能段写"前端全栈能力 / 复杂交互界面 / Next.js 构建")
5. **chunks 弱措辞强化**(chunks "关注 AI Coding" 被改写成"擅长 AI Coding 落地")

# 期间踩到的坑

1. **prompt 工程边际收益递减**:match v1.1.0 → v1.1.1 大改善但有退步;v1.1.1 → v1.1.2 修退步但顽固问题(中间夸张词同义词替换 / 资历套话 / FC 顽固 / 在自家项目塞缺口)继续;同义词替换是无穷无尽游戏(突出 → 较强 → 深厚 → 显著优势 → 完美契合)
2. **没读真实数据凭反推会大量误判**:整个对话前半段(诊断 7 类 bug pattern + 制定 v1.1.x 三轮调整)都建立在错前提上,30-40% 误判
3. **LLM 输出形式不稳定**:同一 drafter prompt 跑两次,resume #1 vs reviewer v1.0.2 重刷,技能段格式从"堆 24 个栈名词"变"8 项 X/Y: 描述",输出形式不一致
4. **chunks granularity 字段一直在,但 prompt 没显式利用**:profile_chunks 有 4 类 granularity(`summary / experience / project / skill`),drafter / reviewer / match_analyst 都没明确告诉 LLM "skill 字段是权威自报清单",所以 LLM 凭"动作证据"标准判读,大量误判;reviewer v1.0.2 加 granularity 字段说明后立刻起效

# 给后续切片的输入

## S18 真正完成的下一刀

不是 drafter v1.0.2 大改,而是**精准修 5 类真 bug**:

1. drafter prompt 加规则:**优先读 candidate `target_titles` 字段写求职意向,不要硬抄 JD title**
2. drafter 强约束 D 改写:**skills 来源优先 `granularity=skill` 字段(权威),次选 tech_stack 字段;chunks 完全无字眼的 skill 严禁列**
3. drafter 副词 + level 一致映射:**Python(expert)用"精通" / Go(advanced)用"熟练" / TypeScript(advanced)用"熟练" / Rust(beginner)用"入门"或"了解";不要把一个 skill 的 level 字段错位到另一个 skill**
4. drafter 强约束 C 加固:**侧项目的 skill / 技术栈,在简历技能段不要泛化为通用能力**(可以在项目段落里展示,技能段不列 / 列时标"侧项目")
5. drafter 强约束 + reviewer M5/M6:**chunks 弱措辞("关注 / 了解 / 一般")不要被强化("擅长 / 落地")**

跑完上述 5 条修复后,再做一次 dogfood 6 条样本对比 v1.0.1 / v1.0.2 / v1.0.3,目标:review 通过率 ≥ 50% + high finding 平均 ≤ 1。

## match_analyst 已废规则待清理

下次 match prompt 再迭代时:
- 撤回规则 3.1 中 chunks 自用副词的禁令(精通 / 全栈作者 / 较深积累)
- 精修规则 4.2 拔高角色判定(chunks 角色字段已有的不禁)
- 精修规则 1.2 strength 校准表(skill 字段 level 映射优先)

## M2 v1.1 提质刀的输入

剩余顽固问题(资历套话 / 中间夸张词同义词替换 / Function Calling 蕴含执行不稳定)继续调 prompt 边际收益接近 0。**留待 M2 v1.1 提质刀** — Hybrid Search + Reranker 上线后,chunks 召回完整 + 检索抖动消除,prompt 噪音会自然减少;同时考虑升 STANDARD tier。

# 闸门

| 项 | 状态 |
|---|---|
| 后端 `pytest -q` | 未跑(用户跳)|
| 后端 ruff / mypy | 未跑(用户跳)|
| 后端 alembic | N/A(无 migration)|
| 前端 typecheck / biome / next build | N/A(无前端改动)|
| OpenAPI dump + `pnpm gen` | N/A(schema 未变)|
| dogfood 6 条样本 | ✅ 跑完(数据见上)|

期望若跑应当过的:
- pytest 数字不动(无新测试)
- ruff / mypy 全过(本切片仅新增 prompt + 升 PROMPT_KEY)

# 什么没改(本切片范围外)

- drafter v1.0.2(留下次,5 类真 bug 修复)
- profile_chunks 重新生成 / retrieve K 调整
- LLM tier 升级
- 前端 chunks evidence hover(M2 v1.1 提质)
- M2 v1.1 提质刀(Hybrid Search + Reranker + QueryRewriterAgent + STANDARD tier)
