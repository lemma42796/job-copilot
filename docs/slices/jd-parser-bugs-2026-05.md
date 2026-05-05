---
title: JDParser prompt v1.0.1 bug 清单(基于 13 张 BOSS JD dogfood)
owner: lemma42796
date: 2026-05-05
purpose: prompt v1.0.2 设计输入。在 M2 主线 S18(简历定制 dogfood)之后落实修复。
status: open(只调研,不动代码)
---

# 背景

S18 dogfood 准备阶段(灌真实 BOSS JD 数据)期间,把 `evals/raw/boss/` 13 张 BOSS 截图通过浏览器粘贴流程逐张过 JDParser 验证,**生成了 13 条 OCR 干净版 JD(jds.id 9-21)**。同时复用 `evals/suites/jd_extract/dataset.jsonl` 的 5 条做了**同源对照**(JD#4-#8 dataset 脏 OCR 版 vs JD#9/#10/#11/#12/#19 干净 OCR 版)。

发现 26 类 bug + 4 类正确处理对照。本文档归档,作为后续 prompt v1.0.2 + dataset 扩 + evals 达阈那刀(M2 待办累积 #1)的设计输入。

**不在本次范围**:不动 `apps/api/src/jobcopilot_api/agents/jd_parser/` 任何代码。仅记录。

---

# Bug 清单(按严重度排)

## 🔴 严重(影响下游匹配/简历定制正确性)

### B1 — OR 关系误抽成 AND
- **症状**:原文"Java/Python/Go 中至少一门"→ 全 3 个进 hard_skills
- **复现**:JD#5/#10/#11/#15/#17/#18/#19/#21 — **9 次**
- **后果**:简历技能 hit 率虚高,匹配分失真

### B2 — 段落归属错乱
- **症状**:加分项段(`者优先 / 或...`)的术语被抽进 hard_skills 而非 bonus_skills
- **复现**:JD#11(MCP/Multi-Agent/NLP) / JD#21(LoRA/P-Tuning/Q-LoRA/TensorRT/ONNX 全错位)
- **后果**:加分项变硬要求,候选人匹配阈值被人为拉高

### B3 — 应届/校招 years_required 不补 0
- **症状**:原文"在校/应届/校招/2026届"→ years_required = 空(应该是 0)
- **复现**:**12 张全部**(应届岗 100% 复现)
- **后果**:匹配阶段无法用经验年数过滤,应届岗和资深岗混排

### B4 — BOSS 平台标签污染 hard_skills
- **症状**:正文上方的 BOSS 筛选标签 `Java` / `React` 被当 hard_skill 抽
- **复现**:JD#16(Java) / JD#20(React)— **2 次**
- **后果**:正文不要求的技能被当核心技能,误匹配
- **修法**:**前端粘贴预处理**(剥离孤立 token 块),prompt 修不了

### B5 — 标题处理 6 种行为完全不一致
| JD | 原文 | 抽出 | 行为 |
|----|------|------|------|
| #13 | `(AI Agent & 全栈开发...)` | `(AI Agent & 全栈开发)` | 删 `...` 关括号补全 |
| #14 | `AI Agent研发(A62744)` | `AI Agent研发` | 删括号代号 |
| #16 | `ai应用开发工程师` | `ai应用开发工程师` | 保留小写 |
| #17 | `(26届) (MJ004075)` | `(26届)` | 删一括号留一括号 |
| #18 | `开发工...` | `开发工` | 直接停在最后汉字 |
| #19 | `（算法/infra/Agent）` | 完整保留 | 全留 |

**5 张特殊标题 6 种不同处理策略,稳定性 = 0**

---

## 🟡 中等(语义错抽,但前端可读)

### B6 — 厂商/产品名当 hard_skill
- 例:OpenAI / Anthropic / Google Gemini / Llama / Claude / Qwen / LazyLLM
- 复现:JD#9(4 个厂商)/ JD#11(GPT/Claude/Gemini/Llama)/ JD#18(LazyLLM)— **3 张**

### B7 — AI 技术方向当 hard_skill
- 例:神经网络/计算机视觉/路径规划/推荐系统/生成式AI(原文是"包括但不限于:..."的领域例举)
- 复现:JD#21(6 个方向全抽)、JD#11(program synthesis/software testing/program repair/devops)

### B8 — 学术 paradigm 当 hard_skill
- 例:`react`(ReAct)/ `self-correction` / `cot`
- 复现:JD#7/#11/#17 — **3 张**;尤其 `react` 会和 React.js 误匹配

### B9 — 基础课目当 hard_skill
- 例:数据结构/算法/网络/操作系统
- 复现:JD#7/#12/#13/#15/#17/#21 — **6 张**

### B10 — IDE/编辑器当 hard_skill
- 例:Cursor / Claude Code / VS Code / IntelliJ / Copilot / OpenCode
- 复现:JD#13/#15/#18/#21 — **4 张**
- **不一致**:同样的工具在 JD#14 没抽 / JD#19+#20 抽进 bonus → **同概念在 hard / bonus / 不抽 之间飘**

### B11 — 业务领域当 hard_skill
- 例:DevOps / AI4SE / 网络安全 / 跨境电商 / 私有化部署
- 复现:JD#11/#19/#18

### B12 — soft_skills 错抽对象
- **整句话当 soft_skill**:"适应快节奏迭代" / "需求不完全明确的情况下推动事情落地" / "强烈技术好奇心" / "AI Native 基因"
- **hard skill 错放 soft**:`系统设计`(JD#18/#20 两次)/ `代码审查`(JD#13)/ `Bad Case 归因分析`(JD#18)
- **智能体能力误抽成候选人 soft**:"逻辑推理 / 任务规划 / 场景理解"(原文是"智能体的能力",JD#7 错抽)

### B13 — bonus_skills 错抽行为/平台名
- 例:"GitHub有高质量AI项目贡献"/"参与过开源贡献"/"Hackathon"/"Hugging Face"/"GitHub"
- 复现:JD#14/#17/#19/#21

### B14 — bonus_skills 与 hard_skills 重复
- JD#11:bonus 8 个里 6 个跟 hard 重复(FastAPI/Docker/Multi-Agent/Function Calling/Tool Calling/NLP)

### B15 — 凭空编造原文没提的 soft_skills
- 例:JD#15 抽出"沟通 / 团队",但原文 5 条"能力特质"完全没提

### B16 — 同句多并列项处理飘忽
- 同一类"句内 5-7 个并列项":
  - JD#10/#11 全展开成独立 hard_skills(激进)
  - JD#19 只抽 1 个代表(保守)
  - JD#21 抽 6/7 漏 1(混乱)

### B17 — description 抽不抽飘忽
- JD#10/#15/#17 抽出 description
- JD#11/#16/#18/#19 同样无独立"职位描述"标题段时却抽空
- 同样有总览段,抽与不抽**完全不一致**

### B18 — 职责拆/不拆飘忽
- JD#15:6 条长职责保留 6 条
- JD#19:2 条长职责被主动断句拆成 4 条
- 同类输入,不同输出

---

## 🟢 轻微(争议性 / 边缘)

### B19 — typo 不纠正
- 例:"OpenClaw"(应为 OpenChat?) / "OpenCode" / "Prompt Enginering"
- 行为**一致**(全保留)— 是好事(无幻觉)还是坏事(用户体验差)取决于设计取舍

### B20 — 斜杠合并怪命名
- 例:`Agent 产品/应用` / `E2E测试/日志追踪`(JD#20)
- 影响 fuzzy match — 候选人写"Agent 应用"是否命中"Agent 产品/应用"?后续匹配阶段不可处理

### B21 — 产品名/namespace 名当独立 skill
- 例:"Agent Skills"(JD#13)/ "Skill"(JD#15)— 实际是 namespace,被错抽成独立 skill

### B22 — 同源跑两次 hard_skills 命名抖动
- JD#4 vs #9:"LLM"vs"大语言模型(LLM)";"团队协作"vs"协作"
- LLM 非确定性的常态,但 prompt 没规定"缩写优先"等命名约束

---

## ⚙️ Schema/前端层(prompt 修不了)

### B23 — 学历 enum 不全
- "学历不限" → schema 无映射,落空字符串 → 匹配时疑似"未达本科"
- "本硕博均可"/"本科/硕士均可" → 只抽下限,损失 OR-higher 信息
- 复现:JD#14(空)/ JD#20(本科)/ JD#21(本科)
- **修法**:schema enum 扩 `unspecified` / `bachelor_or_higher` / `flexible`

### B24 — confidence 全是 95% 不校准
- 13 张全部 95%,跟实际抽取质量(6 hard skill 全对 vs 31 hard skill 错抽 6 个)毫无关联 → 该字段失效
- **修法**:prompt 要求按字段加权计算 confidence,或干脆删掉这个字段

### B25 — 缺 raw_url / source_publisher 元数据
- 没存 BOSS 链接 / 平台标识,后续追溯 / 反爬要手动补
- **修法**:schema 加 `source_url` / `source_publisher` 字段;前端粘贴时让用户填 URL

### B26 — 没区分 OR 关系
- schema 用 flat hard_skills 列表,无法表达"Java OR Python OR Go" — 即使 prompt 修了 OR→AND 错抽,**schema 也没地方放 OR group 信息**
- **修法**:schema 加 `or_groups: [{name: str, members: [str]}]` 或在 hard_skill 上加 `group_id` 字段

---

## ✅ 处理正确的(对比项,反映 prompt 干对的事)

- **斜杠拆开**:`Embedding/Reranker` 拆 2 个 hard_skill ✓(JD#16/#18)
- **截断半句忽略**:JD#13/#17 底部"...功能模块的"半句没误抽进任何字段 ✓
- **职责"标题:内容"拆**:`打造超级"数字员工":...` — 删除诙谐 label,只保留冒号后内容 ✓(JD#14)
- **岗位职责段无独立标题但能识别换段**:JD#21 的 1.-7. 任职要求接 8.-9. 加分项,LLM 没误把它们当职责 ✓

---

# 优先级建议(给 prompt v1.0.2)

按 ROI 排序,**前 5 条解决 70% 问题**:

## P0(必修,影响下游正确性)

1. **B1 OR 规则**(出现 9 次)+ **B16 同句并列项**(关联问题)
   - prompt 加规则:JD 文本里 "X / Y / Z" 或 "X、Y 或 Z 等" → 抽 1 个代表性,其余进 description
   - 例外:在 schema 加 `or_groups` 字段记录 OR 关系(对应 B26)

2. **B2 段落归属**(严重错位)
   - prompt 加规则:加分项段(`者优先 / 或...`)的术语**只能进 bonus_skills,不能进 hard_skills**

3. **B3 应届/校招 → 0**(12 次复现)
   - prompt 加映射:`在校/应届/校招/2026届/2027届` → years_required = 0
   - schema 加 `is_fresh_grad` boolean(可选)

4. **黑名单一发命中**:**B6 厂商名 / B7 技术方向 / B9 基础课目 / B10 IDE / B11 业务领域**(总共 12+ 张涉及)
   - prompt 加黑名单清单,这些**不抽 hard_skill**,落到 description

5. **B12 soft_skills 整句话拒入** + **B13 bonus 行为名拒入**
   - prompt 规则:超过 8 字符或含动词的字符串不能当 skill 名,丢回 description

## P1(中等,改善前端可读)

6. **B5 标题清理统一规则**:删括号内的内部代号(数字 ID + 大写字母 6+ 长度);保留语义括号(届数/方向);截断 `...` 删除;小写归一化大写
7. **B14 去重约束**:bonus_skills 与 hard_skills 集合差≥0
8. **B17 description 必抽**:有总览段必抽,无总览段抽空 → 行为一致

## P2(schema 改造,而非 prompt)

9. **B23 学历 enum 扩**:加 `unspecified` / `bachelor_or_higher` / `flexible`
10. **B26 OR 关系建模**:hard_skill 加 `or_group_id`
11. **B24 confidence 校准**:让 LLM 别 default 0.95
12. **B25 source_url / source_publisher 字段**

## P3(前端预处理,不是 prompt)

13. **B4 BOSS 标签污染** → 前端粘贴时识别并剥离孤立 token 块

---

# 数据 / 数字总结

| 指标 | 范围 | 倍差 |
|------|------|------|
| cost | ¥0.0060(JD#16) — ¥0.0300(JD#21) | **5x** |
| hard_skills 数量 | 6(JD#16) — 31(JD#21) | **5x** |
| confidence | **全部 95%** | LLM 自评跟实际质量(6 干净 vs 31 错抽 6 个)**严重不符** |

## 干净 OCR vs 脏 OCR(4 组同源对比)

| 对比 | 干净版 vs 脏版 | 谁更对 |
|------|-----------------|--------|
| #4 vs #9 | 几乎同 | 平 |
| **#5 vs #10** | 干净抽更多 | **脏更对**(避免 OR→AND) |
| **#6 vs #11** | 干净 hard +11 个 | **脏更对**(避免学术 paradigm) |
| **#7 vs #12** | 干净抽更少 | **干净更对** |

**反直觉规律**:OCR 干净度 ≠ 解析质量。**根因是 prompt v1.0.1 缺取舍规则,导致输入扰动直接放大成输出抖动**。

---

# 行动

- **现在不动 jd_parser** — 推到 prompt v1.0.2 + dataset 扩 + evals 达阈那刀(M2 待办累积 #1)
- **S18 dogfood 用清理后的 13 条 JD**(`/jds` 删旧的 #3-#8,留 #9-#21,B 选项 5 条选 #9 / #10 / #11 / #12 / #19)
- 新 dataset 50 条扩充时(M2 待办 #2)优先纳入这 13 条作为 BOSS 真实样本

# 不在本文档范围

- prompt v1.0.2 的具体改写方案 → 真要修时另开切片或 commit
- evals 评测达阈数据 → 见 EVAL_PLAN.md
- jd_parser agent 当前实现细节 → 见 `apps/api/src/jobcopilot_api/agents/jd_parser/`
