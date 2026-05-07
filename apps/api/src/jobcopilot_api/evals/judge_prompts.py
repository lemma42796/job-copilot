"""Judge prompt 模板 + Pydantic 输出 schema(S21 子任务 4-C)。

两个 Judge 任务:

1. **`resume_generate` 6 维 Rubric**(EVAL_PLAN §7.3):JD 对齐 30% / 事实一致
   30% / 结构 15% / 量化 10% / 语言 10% / 长度 5%。Judge 输出每维 0-100 分 +
   reason,Python 端按权重组合 `total_score` — 权重作为 SSoT 在代码里,改权
   重不要求重提示工程。
2. **`match_analysis` evidence_validity**(EVAL_PLAN §6.3):`(claim, chunk)`
   → `supports y/n + reason`,二分类。

Prompt 设计要点:
- **先列证据,再打分** — 强制链式推理顺序,避免"先打分后凑理由"思维
- **打分锚点写死** — 80+ / 60-79 / <60 三档具体描述,减少 Judge 间方差
- **CoT 输出 JSON** — Judge 自己 thinking_mode=on 提供 CoT,JSON 段是最后产出
- **few-shot 用边界情况** — 而非典型情况,典型情况 Judge 自己能处理
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---------- Resume generate 6 维 Rubric ----------

RESUME_RUBRIC_WEIGHTS: dict[str, float] = {
    "jd_alignment": 0.30,
    "factual_consistency": 0.30,
    "structure_readability": 0.15,
    "quantification": 0.10,
    "language_quality": 0.10,
    "length_compliance": 0.05,
}


class JudgeRubricDimension(BaseModel):
    score: int = Field(ge=0, le=100, description="0-100,分档锚点见 prompt")
    reason: str = Field(description="先列证据(简历中具体引用),再说明分档理由")


class JudgeResumeRubric(BaseModel):
    jd_alignment: JudgeRubricDimension
    factual_consistency: JudgeRubricDimension
    structure_readability: JudgeRubricDimension
    quantification: JudgeRubricDimension
    language_quality: JudgeRubricDimension
    length_compliance: JudgeRubricDimension


def weighted_total(rubric: JudgeResumeRubric) -> float:
    """Compose `total_score` from 6 dimensions weighted by `RESUME_RUBRIC_WEIGHTS`.

    Python 端做加权,而非让 Judge 自己算 — Judge 算权重经常算错(0.3+0.3+
    0.15+0.1+0.1+0.05 = 1.0 看似简单,Judge 实测有 5-15% 概率算偏 1-3 分),
    且权重是产品决策,不该混进 prompt-tuned 内容。
    """
    return (
        rubric.jd_alignment.score * RESUME_RUBRIC_WEIGHTS["jd_alignment"]
        + rubric.factual_consistency.score * RESUME_RUBRIC_WEIGHTS["factual_consistency"]
        + rubric.structure_readability.score * RESUME_RUBRIC_WEIGHTS["structure_readability"]
        + rubric.quantification.score * RESUME_RUBRIC_WEIGHTS["quantification"]
        + rubric.language_quality.score * RESUME_RUBRIC_WEIGHTS["language_quality"]
        + rubric.length_compliance.score * RESUME_RUBRIC_WEIGHTS["length_compliance"]
    )


RESUME_RUBRIC_SYSTEM = """你是简历评测员。按 6 个维度对一份针对 JD 定制的中文简历打分(0-100),并为每个维度给出"先列证据、后说理由"的简短分析。

# 6 个维度与打分锚点

1. **JD 对齐度**(30%):简历是否覆盖 JD 必出现的关键词/技能/术语,经历是否能映射到 JD 岗位职责。
   - 80+:JD 关键技能 ≥ 90% 覆盖,经历贴合岗位职责
   - 60-79:JD 关键技能 60-90% 覆盖,部分经历贴合
   - <60:关键技能 < 60% 覆盖,或经历明显错配

2. **事实一致性**(30%):简历每条内容必须能在候选人 profile 中找到证据。**编造任何 profile 没有的技能/项目/数字 = 直接 < 50 分**。
   - 80+:无任何 profile 外内容,所有数字与 profile 一致或显然合理拓展
   - 60-79:个别表述可能拔高(如"参与"→"主导")但 profile 有相关经历
   - <60:存在明显 profile 没有的技能/项目/数字(fabrication / unsupported_number / exaggeration)

3. **结构与可读性**(15%):章节顺序合理(联系方式 → 求职意向 → 工作经历 → 项目 → 教育 / 技能)、bullet 长度均匀(每条 1-3 行)、动词领先。
   - 80+:章节齐全顺序合理,所有 bullet 动词领先 + 长度均匀
   - 60-79:章节顺序基本合理,部分 bullet 拗口或过长
   - <60:章节缺失或顺序混乱,bullet 大段陈述句不动词领先

4. **量化丰富度**(10%):数字、百分比、规模描述(如 "12w QPS"、"提升 30%"、"团队 5 人")在主要经历中的密度。
   - 80+:每段经历 ≥ 2 处量化
   - 60-79:每段经历 ≥ 1 处量化
   - <60:多段经历无任何量化

5. **语言专业性**(10%):中文表达自然、无翻译腔(如 "made"→"制作"而非"制造的")、术语规范(中英混用合理,如 "LLM"、"Kubernetes" 不强译)。
   - 80+:全文表达自然,术语规范,无错别字
   - 60-79:个别句子拗口或术语不规范
   - <60:翻译腔明显或多处错别字

6. **长度合规**(5%):字数(中文按字符数,markdown 标记不计)在 800-1200 之间。
   - 80+:在 800-1200 内
   - 60-79:在 600-800 或 1200-1500
   - <60:< 600 或 > 1500

# 输出要求

输出严格 JSON,每个维度对象含 `score`(整数)和 `reason`(先列简历中具体证据,再说分档理由,≤ 80 字)。**不要**输出 `total_score`(Python 端会算)。"""


def render_resume_rubric_user(*, jd_text: str, profile_summary: str, resume_markdown: str) -> str:
    """USER 段:用 markdown fence 把三段输入隔开,防 Judge 把 profile 当成简
    历评分。`profile_summary` 是 candidate profile 的关键字段拼接(姓名 / 求
    职意向 / educations / 各 chunk content),由调用方汇总后传入 — Judge 视
    角是"事实一致性"参考资料,不是 prompt-time retrieval。"""
    return f"""# JD

```
{jd_text}
```

# Profile(用于事实一致性核查 — 简历内容必须能在此找到证据)

```
{profile_summary}
```

# 待评简历(markdown)

```
{resume_markdown}
```
"""


# ---------- match_analysis evidence_validity ----------


class JudgeEvidenceValidity(BaseModel):
    supports: bool = Field(description="chunk 是否真的支持论点")
    reason: str = Field(description="先列 chunk 中的具体片段,再判定 supports/not")


MATCH_EVIDENCE_SYSTEM = """你是匹配分析评测员。判断一条"优势/差距描述"和它引用的简历 chunk 之间是否具有支持关系。

# 判定标准

- `supports=true`:chunk 中**直接**含有该论点的事实证据(技能名 / 数字 / 项目名 / 工作经历段)。同义改写不影响判定(如论点说 "Python 经验" 而 chunk 写 "Python / Django 后端开发 3 年")。
- `supports=false`:chunk 与论点完全无关(谈论别的技能 / 别的项目),或仅有间接关联但无直接证据。**宁可严格也不放水** — match_analyst 引用错 chunk 是产品体验问题,需要召回。

# 输出要求

输出严格 JSON,`reason` 先抄 chunk 中相关或相关的片段(≤ 30 字),再给判定。"""


def render_match_evidence_user(*, claim: str, chunk: str) -> str:
    return f"""# 论点

{claim}

# 引用 chunk

{chunk}
"""
