---
title: S18 简历定制 dogfood — match 三轮迭代 + reviewer 修 + 方法论 audit + drafter v1.0.2/v1.0.3 + JDParser v1.0.6
status: 进行中(drafter v1.0.3 + JDParser v1.0.6 已落;待第二轮多条 dogfood)
date: 2026-05-05
purpose: S18 主线 dogfood 完整调研产物。包含三 prompt 迭代史、第一轮 6 条样本聚合、一次重大 audit 教训(没读 profile 凭反推下判断,30-40% 误判)、drafter v1.0.2 修第一轮 5 类真 bug、第二轮第一条样本(JD #24+profile #15)audit 暴露 4 类新问题 → drafter v1.0.3 + JDParser v1.0.6 双线落地
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

# drafter v1.0.2 落地(2026-05-05 当天接续)

## 改动摘要

| 文件 | 改动 |
|---|---|
| `prompts/resume_drafter/v1.0.2.j2` | 新建。改回原 4 条 → 5 条强约束 A/B/C/D/E,详见下方"强约束变更"|
| `agents/resume_drafter/agent.py` | `draft_resume` 加 `target_titles: list[str] \| None = None` 参数;`_chunk_inputs` 不动;render_user 多传 `target_titles` |
| `services/resume_service.py` | `_load_resume_for_generate` 返回元组多一个 `target_titles: list[str]`(从 `profile.target_titles` 字段读);`run_generate` 接住后透传给 `draft_resume` |
| `routers/resumes.py` | `DRAFTER_PROMPT_KEY` v1.0.1 → v1.0.2 |
| `STATUS.md` | last_updated / 切片表 / 当前生效 prompt / 下一刀 / drafter 段全部刷新 |

无 migration、无 schema 变化、无前端改动。

## 强约束变更(v1.0.1 → v1.0.2)

- **A**(单 chunk 引用)— 不变
- **B**(副词)— 重写
  - 撤回 chunks 自用副词的禁令(audit 教训:「精通」是 skill.level=expert 字段值;「全栈作者」是 project.role 字段值;「较深积累」是 summary 原文 — 都是 chunks 字段值,不是 drafter 编造)
  - 新增 **skill.level → 副词标准映射表**:expert=精通 / advanced=熟练 / intermediate-basic=掌握 / beginner=了解-入门
  - 新增**铁律**:某 skill 的 level 副词只能用在该 skill 自己,不要错位(修真 bug 3:Python expert 的「精通」错位到 Go advanced)
  - 永久禁用:卓越 / 优秀 / 顶尖 / 极致 / 完美 / 业界领先 / 资深(级别义)
  - 新增**弱措辞不强化**(修真 bug 5):chunks「关注 / 了解 / 一般」原强度保留,不升级为「擅长 / 落地」
- **C**(业余项目)— 加固
  - 原版只要求「项目段落标签 + 不与正职项目同语调」
  - 新增 C.2:**侧项目 tech_stack 不允许出现在 `## 技能` 章节作为通用能力**(修真 bug 4:Next.js 仅在侧项目 JobCopilot tech_stack,被泛化为「前端全栈能力」)
- **D**(技能段)— 重写
  - 原版只列举什么不算 skill 证据(栈背景 / 公司技术栈 / 教育背景工具)
  - 新版改为**三级来源优先级**:① `granularity=skill` chunks(权威清单,默认全列)② experience/project 的 `技术栈:` 字段 ③ 描述/亮点中的"动作型"陈述
  - 新增禁令:**"X/Y: 描述" 形式中 X 或 Y 在 chunks 无证据则不拼凑**(修真 bug 2:Flask 没 chunks 证据被硬塞)
  - 格式建议:分组 + level 副词 + 年限,`Python(精通 / 5 年)`
- **E**(求职意向)— 全新
  - 优先 `candidate.target_titles`(profile 表独立字段),为空时才用 JD title
  - JD title 必须**剥离**括号内限定词(Junior / P6 / 校招 / 内推 等内部级别)
  - 修真 bug 1:JD title「LLM Agent 工程师 (Junior)」→ 简历硬抄含 (Junior)

## 关键设计决策

1. **target_titles 走参数透传不走 chunks**:`profile.target_titles` 是独立字段(JSONB list[str]),不在 `profile_summary` 里,chunker 也没有把它进 chunks。三选一:① chunker 升 v2 把 target_titles 进 summary chunk(影响 embedding,需要 reindex)② 在 chunks 列表里塞一条假 chunk(granularity 不规整)③ **agent.py 多接一个参数 + prompt 模板渲染**。选 ③,最干净,不动 chunker / embedding / 检索路径,只 drafter 自己用。
2. **不动 chunker / 不重建 chunks**:本切片不需要。skill / project.role / summary 字段值都已在 chunks `content` 里(`水平:expert` / `角色:全栈作者` / `个人简介:...` 直接渲染),audit 教训是 prompt 没"读懂" granularity 字段语义,新版 prompt 在"输入数据语义"段显式声明各 granularity 字段。
3. **保留反注入约束**:同 v1.0.1。
4. **章节顺序与字数限制不变**:7 章节 / 每章 ≤ 6 bullet / ≤ 30 字 / 整篇 800-1200 字。
5. **没改 reviewer**:reviewer v1.0.2 已经在 audit 后落地(M2/M4/M5 判定收窄 + granularity 字段说明),drafter v1.0.2 的修复方向跟它互补 — drafter 更精准生成,reviewer 更精准评审,期望第二轮 review 通过率拉高。

## 第二轮 dogfood 计划(下次会话执行)

跑同样 6 条样本(JD #9-#13 + #24 × profile #15),drafter 用 v1.0.2、reviewer 仍用 v1.0.2,逐条对比 v1.0.1 的 high finding:

| 对比维度 | v1.0.1 第一轮 | v1.0.2 第二轮目标 |
|---|---|---|
| 通过率 | 17%(1/6)| ≥ 50%(3/6)|
| high finding 平均 | 3.0 | ≤ 1 |
| 求职意向硬抄 JD title | 出现 | 0 |
| 技能段编造(无 chunks 证据) | Flask 等出现 | 0 |
| skill level 副词错位 | 出现 | 0 |
| 侧项目技术栈泛化 | 出现 | 0 |
| 弱措辞强化 | 出现 | 0 |

不达阈值 → v1.0.3 / 升 STANDARD tier。

# 第二轮第一条样本 audit(JD #24 + profile #15 / drafter v1.0.2 输出)

跑出第一条 v1.0.2 输出后,按永久约束 #6 先 curl 出 JD #24 + profile #15 的 raw_text + structured 数据,**人工对照** drafter 输出。

## v1.0.2 修住的(真改进)

| Bug | 修住情况 |
|---|---|
| Bug 1 求职意向硬抄 JD title | ✅ 输出"大模型应用工程师 / LLM Application Engineer / AI 全栈工程师"(三项 join),不再含 (Junior) 限定词 |
| Bug 2 Flask 编造 | ✅ JD 强要 Flask 但 candidate 无证据,drafter 正确不列 |
| Bug 3 skill level 错位 | ✅ Python(精通)/ Go(熟练 / 4 年),严格映射 expert→精通 / advanced→熟练 |
| Bug 4 侧项目技术栈泛化 | ✅ JobCopilot 标 (侧项目);Next.js 没出现在技能段(因 C.2)|

## v1.0.2 暴露的 4 类新问题(P1)

### 问题 1:基本信息 [待补充] 占位

简历输出:`姓名:[待补充] / 联系方式:[待补充]`,但 candidate 实际有 full_name="张明远 / Zhang Mingyuan" / email / phone。

**根因**:`profile.full_name / email / phone / location` 是 profile 表顶层字段,**chunker 没渲染进 chunks**(同 v1.0.2 修过的 target_titles 同款问题),drafter 看不到 → 写占位符。

### 问题 2:教育背景 [学校名称] 占位

简历输出:`[学校名称] | [专业名称] | [学位] | [时间]`,但 candidate.educations 实际有上交硕 + 武大学士 + GPA + honors。

**根因**:教育在 `granularity=education` chunks 里,但 retrieve K=20 走相似度召回,JD 是 LLM Agent 工程岗,语义召回偏向工作 / 项目 / 技能,**教育 chunks 排序靠后被挤出 Top-20**。

### 问题 3:TypeScript 漏列(D 规则被自主删减)

candidate skills 清单明确写 `TypeScript advanced/3 年`,JD hard_skills 强要,**简历技能段完全没列**。drafter 自主认为"工作经历里展示了 TypeScript 就不在技能段重复"——错误删减。

### 问题 4:弱→强升级未修透(B.4 没拦住 2 处)

| 简历输出 | chunks 原文 | 性质 |
|---|---|---|
| "**精通** LangChain/LlamaIndex" | summary「**熟悉** LangChain」+ skill `水平=null` | 升级 熟悉→精通,且 level=null 不该配级别词 |
| "AI Coding 与多模态 Agent 方向**落地**" | summary「**关注** AI Coding 与多模态 Agent 方向」 | 加了"落地",升级 |

reviewer v1.0.2 对这两处都给了 pass(没抓到)— M2 副词判定收窄后只要 chunks 任意字段有该词就放过,但**这两类是"chunks 弱 + 简历强"**,reviewer 当前规则识别不出。

# JDParser 解析层 audit(JD #24)

按永久约束 #6 同步 audit JD 解析,发现 1 个真 bug。

## JDParser v1.0.5 真 bug:Python 错挂 OR-group

raw_text:**「精通 Python,熟悉 FastAPI / Flask 任一后端框架」**

| 字段 | 当前输出 | 应该是 |
|---|---|---|
| python | `or_group_id=1, weight=0.33` | `or_group_id=null, weight=1.0` |
| fastapi | `or_group_id=2, weight=0.33` | `or_group_id=2, weight=0.5` |
| flask | `or_group_id=2, weight=0.33` | `or_group_id=2, weight=0.5` |

**根因**:LLM 把整句读成"3 项 mutex group",权重三项均分 0.33;但 or_group_id 又给了不同的 1/2/2 — 自相矛盾(group 不同但权重像在同一 group 内)。

**正确语义**:Python 是单点必须项,FastAPI/Flask 才是 OR-group 共组。

**对 match / drafter 影响**:match v1.1.2 消费 or_group_id 做 OR-group 命中,Python 被错挂独立 group + weight 0.33 → 候选人 Python expert 仍命中但权重计算偏低,影响 score 数字不影响 missing 判定。drafter 不直接用 or_group_id,影响很小。

# drafter v1.0.3 + JDParser v1.0.6 双线落地(2026-05-05 当天接续)

## drafter v1.0.3 改动摘要

| 文件 | 改动 |
|---|---|
| `prompts/resume_drafter/v1.0.3.j2` | 新建。v1.0.2 的 5 条强约束扩为 6 条,新增 F(基本信息 + 教育从 candidate 取);D 段加 D.0 铁律(skill chunks 全列);B.4 加 2 处显式反例;B.2 补"水平=null 不配级别副词";写作铁律 7 加"绝不写占位符" |
| `agents/resume_drafter/agent.py` | `draft_resume` 参数 `target_titles: list[str]` 升级为 `candidate: dict`(向后 None 默认,prompt 模板降级渲染) |
| `services/resume_service.py` | `_load_resume_for_generate` 多 load Profile 顶层字段 + ProfileEducation 关联,组装 `candidate = {full_name/phone/email/location/target_titles/educations}` 透传;返回元组第 4 位从 `target_titles` 改为 `candidate` dict |
| `routers/resumes.py` | `DRAFTER_PROMPT_KEY` v1.0.2 → v1.0.3 |
| `STATUS.md` | last_updated / 切片表 / 当前生效 prompt / 下一刀 / drafter 段全部刷新 |

## drafter v1.0.3 强约束变更(v1.0.2 → v1.0.3)

- **A**(单 chunk 引用)— 不变
- **B**(副词)— 增量
  - B.2 新增子条款:某 skill chunk `水平:null`(LLM 没给 level)→ 简历**不配**任何级别副词,只列名字(修问题 4 LangChain 配"精通")
  - B.4 加 2 处显式反例:① 「关注 AI Coding 方向」 → 「关注 AI Coding 方向**落地**」(禁) ② 「**熟悉** LangChain」 + skill 水平=null → 「**精通** LangChain」(禁)
  - B.4 加机械检查:简历输出每个动词/副词必须在 chunks 里能找到原词或更强的同义词
- **C**(侧项目)— 不变;C.4 新增"无显式标记时 3 条启发判断"
- **D**(技能段)— 重写
  - 新增 **D.0 铁律**:`granularity=skill` chunks 默认**全部**出现在简历技能段,不允许 drafter 自主删减(修 TypeScript 漏列);仅 a) 仅在侧项目 tech_stack 出现 b) 陪衬词 两类排除
  - D.1 来源优先级保留(三级)
- **E**(求职意向)— 不变
- **F**(新增)— 章节 1 基本信息 / 章节 7 教育背景从 `candidate` 字段取,不依赖 chunks
  - 基本信息 4 字段全空才整章节跳过;**绝不写占位符**
  - 教育从 `candidate.educations` 列表渲染;**绝不**从 chunks granularity=education 找数据(retrieve K=20 召回不全)

## drafter v1.0.3 关键设计决策

1. **candidate dict 一次性扩张到 6 字段**(full_name/phone/email/location/target_titles/educations),不再单字段加参数。后续若有"职业概要" / "求职偏好"等同类问题(profile 顶层字段不在 chunks 里)走同一通道。
2. **教育数据 100% 走 candidate 不走 chunks**:retrieve K=20 召回不全的根因是设计层 — chunks 是相似度召回,deterministic 字段(教育 / 基本信息)不该走相似度。短期 prompt 层硬塞 candidate.educations,长期可考虑 retrieval 设计修(M2 v1.1 提质刀)。
3. **不动 chunker / 不重建 chunks**:同 v1.0.2 决策,本轮 audit 暴露的字段都已在 ORM 里,不需要 reindex。
4. **未动 reviewer**:reviewer v1.0.2 漏抓的"弱→强升级" 应该由 drafter 不犯错来解决,不是 reviewer 兜底;若 v1.0.3 仍有漏,再考虑 reviewer v1.0.3 加 M7。
5. **TypeScript 漏列修法选择**:不在 D.1 三级来源里改(三级来源没问题),而是新加 D.0 顶层铁律。原因:三级来源解决"哪些可以列",D.0 解决"哪些必须列",两个不同的问题。

## JDParser v1.0.6 改动

见下面单独段落。

## 第二轮 dogfood 计划(下次会话执行)

跑同样 6 条样本,但 JD #24 **需重新 parse**(用 v1.0.6 让 Python or_group=null)。逐条对比:

| 对比维度 | v1.0.2 第二轮 #1(本次)| v1.0.3 + JDParser v1.0.6 目标 |
|---|---|---|
| 通过率 | 1/1 ready(reviewer 漏抓)| 真通过 ≥ 50%(3/6)|
| 基本信息占位符 | 出现 | 0 |
| 教育占位符 | 出现 | 0 |
| TypeScript 漏列 | 出现 | 0 |
| 弱→强升级 | 2 处 | 0 |
| Python OR-group 错挂 | 出现(JDParser) | 0 |

不达阈值 → v1.0.4 / 升 STANDARD tier;reviewer 加 M7 弱→强升级判定。

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
