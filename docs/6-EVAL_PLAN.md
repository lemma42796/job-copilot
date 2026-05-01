---
title: JobCopilot 评测计划
owner: lemma42796
status: Draft
version: 0.1.0
created: 2026-05-01
last_updated: 2026-05-01
related:
  - 1-PRD.md
  - 2-TECH_DESIGN.md
  - 5-AGENT_DESIGN.md
  - 4-API_SPEC.md
  - adr/0003-switch-to-qwen.md
---

## 修订记录

| 版本 | 日期 | 作者 | 变更说明 |
|------|------|------|---------|
| 0.1.0 | 2026-05-01 | lemma42796 | 初版 |

---

## 1. 目的与原则

### 1.1 为什么必须有评测

JobCopilot 是 LLM 重度应用,Prompt 是核心代码。Prompt 改动若没有量化保护,等于裸奔上线。本文档定义"什么是好"的可执行标准,并把这些标准接入 CI,让每次 Prompt / 模型 / RAG 改动都被卡口。

### 1.2 设计原则

1. **每个 Agent 至少一套 suite**(JDParser、ProfileParser、QueryRewriter、MatchAnalyst、ResumePlanner+Drafter+Reviewer、Interviewer、InterviewEvaluator)
2. **指标必须可自动计算**:精确率、F1、BERTScore、LLM-as-Judge 评分、规则触发计数
3. **评测集 = 代码**:版本化 git,变更走 PR review
4. **不退化优先**:每次 PR 跑回归,**指标下降即 fail**,即使绝对值仍达标
5. **Bad Case 闭环**:线上 + 用户反馈进入 bad case 池,每月并入评测集
6. **总开销可控**:全套评测在 CI 跑完 ≤ 10 分钟,**单次回归 LLM 成本 ≤ ¥3**

### 1.3 工具栈

| 用途 | 工具 | 备注 |
|------|------|------|
| 评测编排 | `promptfoo` 0.x | YAML 定义 suite,CLI + GitHub Actions |
| LLM-as-Judge | `qwen3.6-plus`(开思考) | 主观题用,详见 §6 |
| BERTScore | `bert-score` Python 包 | 用 `bert-base-chinese` |
| 规则匹配 | Python `re` + 自写断言 | 关键词覆盖、长度、JSON schema 校验 |
| 离线索引 | 评测样本预先 chunk + embed,**不**复用线上库 | 避免污染 |
| Bad Case 收集 | `bad_cases` 表(见 3-DATA_MODEL §扩展)+ 用户 👎 反馈 | 见 §9 |

---

## 2. 评测集总览

### 2.1 Suite 清单(目标 200 条样本)

| Suite | 样本数 | 数据来源 | 评测 Agent | 主指标 |
|-------|-------|---------|----------|--------|
| `jd_extract` | 50 | 真实 JD(Boss/拉勾/邮件)脱敏后人工标注 | JDParserAgent | 字段精确率、硬技能 F1 |
| `profile_extract` | 30 | 自己 + 5 位志愿者的真实简历(脱敏) | ProfileParserAgent | 块级召回率、技能 F1 |
| `query_rewrite` | 20 | 由 jd_extract 派生 | QueryRewriterAgent | 检索 nDCG@10 提升 |
| `match_analysis` | 30 | jd × profile 笛卡尔抽样 + 人工标"匹配等级" | MatchAnalystAgent | 评分 MAE、技能命中精确率 |
| `resume_generate` | 25 | 同 match_analysis 一部分 | Resume 状态机端到端 | LLM-as-Judge 综合分、Reviewer 通过率 |
| `resume_review` | 20 | 故意注入幻觉的草稿 | ResumeReviewerAgent | 幻觉检出 F1 |
| `interview_ask` | 15 | 多种岗位 × persona | InterviewerAgent | 题目质量 LLM-as-Judge |
| `interview_eval` | 10 | 已知好/差答案配对 | InterviewEvaluatorAgent | 评分排序一致性 |
| **合计** | **200** | - | - | - |

### 2.2 文件组织

```
evals/
├── promptfoo.config.ts          # 全局配置(provider、cache、并发)
├── suites/
│   ├── jd_extract/
│   │   ├── promptfoo.yml        # suite 入口
│   │   ├── dataset.jsonl        # 50 条样本
│   │   └── assertions.py        # 自定义断言
│   ├── profile_extract/
│   ├── query_rewrite/
│   ├── match_analysis/
│   ├── resume_generate/
│   ├── resume_review/
│   ├── interview_ask/
│   └── interview_eval/
├── bad_cases/                   # 月度并入主集前的暂存区
│   └── 2026-05.jsonl
├── reports/                     # CI artifact 落地
│   └── <run_id>/
│       ├── summary.json
│       └── per_case.html
└── README.md
```

### 2.3 样本元数据约定

每条样本统一字段:

```json
{
  "id": "jd_extract_001",
  "tags": ["intern_friendly", "ambiguous_salary"],
  "input": { /* suite 特定 */ },
  "expected": { /* 答案 / 评分基准 / 检索 ground truth */ },
  "source": "boss_zhipin_2026q2",
  "added_at": "2026-05-01",
  "added_by": "lemma42796",
  "notes": "薪资为'面议',应输出 null"
}
```

---

## 3. Agent Suite:`jd_extract`

### 3.1 数据集构造

50 条按以下分布:

| 类型 | 数量 | 说明 |
|------|------|------|
| 标准格式中文 JD(Boss/拉勾) | 25 |  |
| 邮件长文本 JD | 8 | 正文夹杂寒暄 |
| 截图 OCR 后文本 | 7 | 模拟图片转文本失败的情况 |
| 英文 JD | 5 | 测试中英混用 |
| 极短/极长 JD | 3 | 边界 |
| 含薪资模糊词("面议"、"15-25k·14薪") | 2 | 边界 |

每条人工标注 ground truth(`JDStructured`),由作者 + 1 位志愿者交叉核对。

### 3.2 指标定义

| 指标 | 定义 | 阈值(初始) | 阈值(M3 GA) |
|------|------|-----------|-------------|
| `title_exact` | 标题完全匹配率 | ≥ 0.92 | ≥ 0.95 |
| `company_exact` | 公司名完全匹配率(含 None 一致性) | ≥ 0.95 | ≥ 0.97 |
| `salary_match` | 薪资范围 ±10% 算一致 | ≥ 0.85 | ≥ 0.90 |
| `hard_skill_f1` | 硬技能 set 的 F1(归一化后) | ≥ 0.85 | ≥ 0.90 |
| `level_acc` | job_level 5 类多分类准确率 | ≥ 0.80 | ≥ 0.85 |
| `confidence_calibration` | 置信度与实际正确率的 Pearson | ≥ 0.5 | ≥ 0.6 |
| `latency_p95` | 端到端延迟 P95(ms) | ≤ 10000 | ≤ 8000 |
| `cost_per_call_cny` | 单次成本 | ≤ 0.05 | ≤ 0.04 |

### 3.3 断言示例

```yaml
# evals/suites/jd_extract/promptfoo.yml
description: JD 抽取回归
prompts:
  - file://../../apps/api/agents/prompts/jd_parser/v1.j2
providers:
  - id: dashscope:qwen3.6-flash
    config:
      temperature: 0.0
      response_format:
        type: json_schema
        json_schema: file://./schemas/jd_structured.json
tests:
  - vars: { input: file://./dataset.jsonl }
    assert:
      - type: javascript
        value: |
          const out = JSON.parse(output);
          return out.title === context.expected.title;
      - type: python
        value: file://./assertions.py:hard_skill_f1
      - type: latency
        threshold: 10000
```

### 3.4 失败样本归类

每条失败样本自动打 fail tag:

- `wrong_title` / `wrong_company` / `wrong_salary` / `missed_skill` / `extra_skill` / `wrong_level` / `low_confidence_correct` / `high_confidence_wrong` / `timeout` / `schema_invalid`

PR 评论自动汇总 top 3 失败 tag。

---

## 4. Agent Suite:`profile_extract`

### 4.1 数据集

30 条简历,经如下脱敏后入库:

- 真实姓名 → 化名映射表
- 手机号 / 邮箱 → 占位符
- 公司名只保留行业 + 规模标签(如"互联网中厂")

ground truth:`ProfileStructured` 完整对象,人工标注。

### 4.2 指标

| 指标 | 阈值(初始) | 阈值(GA) |
|------|------------|----------|
| `experience_recall` | 工作经历召回(漏掉一段算 miss) | ≥ 0.95 |
| `project_recall` | 项目召回 | ≥ 0.90 |
| `skill_f1` | 技能 F1 | ≥ 0.85 / ≥ 0.90 |
| `time_range_acc` | 起止日期匹配率 | ≥ 0.90 |
| `chunk_count_drift` | 与 ground truth chunk 数差异 ≤ 20% | ≥ 0.90 |
| `latency_p95_per_page` | 单页 P95 延迟 | ≤ 5s |

### 4.3 端到端断言:Chunk + Embedding 后能召回

ProfileParser 之后立即跑一个轻量 RAG 测试:用 5 个预设 query 检索 chunk,断言 ground truth chunk 在 Top-K 中:

```python
def assert_chunk_retrievable(profile_id: int, query: str, expected_chunk_text: str, k=5):
    hits = pgvector_search(query, profile_id=profile_id, k=k)
    assert any(expected_chunk_text in h.content for h in hits), \
        f"chunk 不可召回:{query}"
```

这把"解析正确 + 切块正确 + embed 正确"作为整体保护起来。

---

## 5. Agent Suite:`query_rewrite`

### 5.1 数据集

20 条:每条提供 `(jd, profile)` 对 + 一组人工标注的"理想检索结果 chunk_ids"作为 ground truth。

### 5.2 指标

| 指标 | 定义 | 阈值 |
|------|------|------|
| `ndcg_at_10` | 改写后检索 nDCG@10 | ≥ baseline + 5pp |
| `mrr` | Mean Reciprocal Rank | ≥ baseline + 0.05 |
| `query_diversity` | 重写出多 query 时,query 间余弦相似度均值 ≤ 0.85(避免重复) | ≥ 0.90 满足率 |

baseline = 不做改写,直接用 JD title 检索。

### 5.3 端到端价值证明

每次回归对比"无改写 / 改写"两组,**改写组必须严格优于无改写组**,否则评测失败,Prompt 改动不能 merge。这把这个 Agent 的存在价值持续证明给 PR reviewer 看。

---

## 6. Agent Suite:`match_analysis`

### 6.1 数据集

30 条 `(jd, profile)` 配对,人工标"匹配等级":

| 等级 | 描述 | 期望评分区间 |
|------|------|-------------|
| 高匹配 | 硬技能覆盖 ≥ 80% + 经验对口 | 80-100 |
| 中匹配 | 50-80% 硬技能 + 部分经验 | 55-79 |
| 低匹配 | < 50% 硬技能 / 经验明显不足 | 0-54 |

每等级 10 条,跨多种岗位类型。

### 6.2 指标

| 指标 | 定义 | 阈值 |
|------|------|------|
| `score_mae` | 与人工标注中位数的 MAE | ≤ 8 |
| `bucket_acc` | 高/中/低分桶准确率 | ≥ 0.85 |
| `hit_skills_precision` | 命中技能列表精确率 | ≥ 0.90 |
| `gap_skills_recall` | 缺失技能列表召回率 | ≥ 0.85 |
| `evidence_validity` | 引用 chunk_id 全部存在 + 与论点相关(LLM Judge) | ≥ 0.90 |

### 6.3 LLM-as-Judge 模板

evidence 相关性走 Judge:

```
你是匹配分析评测员。判断:给定一条"优势/差距描述" 与它引用的简历 chunk,chunk 是否真的支持这条论点?

输入:
- 论点:{{ claim }}
- 引用 chunk:{{ chunk }}

输出 JSON:
{ "supports": true|false, "reason": "..." }
```

Judge 用 `qwen3.6-plus` 思考开,温度 0.2。Judge 自身的可靠性每季度抽 50 条人工复核(Cohen's kappa ≥ 0.7)。

---

## 7. Agent Suite:`resume_generate`(端到端)

### 7.1 设计取舍

简历定制涉及 4 个 Agent + 状态机,**单 Agent 评测无法保证端到端质量**。本 suite 直接评测 markdown 简历产出物。

### 7.2 数据集

25 条 `(jd, profile)` 对(其中 15 条与 match_analysis 共用)。

### 7.3 评测维度(LLM-as-Judge,Rubric 化)

| 维度 | 权重 | Judge 提示要点 |
|------|------|---------------|
| **JD 对齐度** | 30% | 必出现关键词、岗位术语贴合度 |
| **事实一致性** | 30% | 简历内容是否能在 profile 中找到证据(关键!) |
| **结构与可读性** | 15% | 章节顺序、bullet 长度、动词领先 |
| **量化丰富度** | 10% | 数字、百分比、规模描述出现频率 |
| **语言专业性** | 10% | 中文表达自然、无翻译腔、术语规范 |
| **长度合规** | 5% | 字数在 800-1200 之间 |

总分 0-100。Judge prompt 使用 chain-of-thought + 输出 JSON。

### 7.4 硬性断言

- 必出现关键词覆盖率 ≥ 95%(规则匹配,**不**走 Judge)
- markdown lint 通过(无未闭合标题、列表层级正确)
- Reviewer 通过率(`review_passed=true`)≥ 0.90

### 7.5 阈值

| 指标 | 初始 | GA |
|------|------|-----|
| Judge 综合分均值 | ≥ 75 | ≥ 82 |
| Judge 综合分 P10(最差 10%) | ≥ 60 | ≥ 70 |
| 事实一致性维度均值 | ≥ 85 | ≥ 92 |
| Reviewer 通过率 | ≥ 0.85 | ≥ 0.90 |
| 单次成本 | ≤ ¥0.50 | ≤ ¥0.40 |
| 端到端延迟 P95 | ≤ 60s | ≤ 45s |

事实一致性的 P10 阈值是关键守门员:**任何一次回归出现 high severity 幻觉,直接 fail**。

---

## 8. Agent Suite:`resume_review`(对抗性)

### 8.1 数据集构造方法

20 条草稿,故意注入下列幻觉:

| 类型 | 注入数量 | 示例 |
|------|---------|------|
| `fabrication` | 8 | "主导设计千万 DAU 系统"(原档案没有) |
| `exaggeration` | 6 | 原"参与" → 写"独立负责" |
| `unsupported_number` | 4 | 凭空写"提升 35%" |
| `clean`(无问题对照组) | 2 | 验证不会误报 |

### 8.2 指标

| 指标 | 阈值 |
|------|------|
| 幻觉检出 precision | ≥ 0.90(误报会让用户失去信任) |
| 幻觉检出 recall | ≥ 0.85 |
| `fabrication` 子类 recall | ≥ 0.95(最严重,不能漏) |
| 严重度判定准确率 | ≥ 0.80 |

### 8.3 注意

Reviewer 是反幻觉最后防线,**precision 优先于 recall**。误报多 → 用户烦 → 关掉 review → 系统失去保护。

---

## 9. Agent Suite:`interview_ask` / `interview_eval`

### 9.1 `interview_ask`

15 条样本 = 5 种岗位 × 3 种 persona(主管 / 资深工程师 / 同行)。

每条由 Judge 评:

| 维度 | 权重 |
|------|------|
| 题目相关性(对应候选人简历或 JD) | 30% |
| 题目难度合适(匹配 persona) | 25% |
| 题目清晰(无歧义) | 20% |
| 题目深度(非泛泛)| 15% |
| 中文表达 | 10% |

阈值:Judge 综合分 ≥ 78。

### 9.2 `interview_eval`

10 条 `(题目, 高质量答案, 低质量答案)` 三元组。

指标:**排序一致性**(评分员给高质量答案的分必须 > 低质量答案的分),阈值 1.0(即 10/10 全对)。

附加:评分绝对值方差 ≤ 8(同一答案多次评分稳定性)。

---

## 10. CI 集成

### 10.1 触发时机

| 触发 | 跑哪些 suite | 预算 |
|------|------------|------|
| Prompt 文件改动 PR | 改动涉及的 Agent suite | ≤ ¥2 |
| `apps/api/agents/**` 改动 | 全部 suite(慢) | ≤ ¥3 |
| 主分支 nightly | 全部 + 历史回归对比 | ≤ ¥3 |
| 手动 `workflow_dispatch` | 选定 suite | 不限 |

PR 中 LLM 抽象层 (`apps/api/llm/**`) 的改动同样触发全部。

### 10.2 GitHub Actions 工作流

```yaml
# .github/workflows/eval.yml
name: Eval Regression
on:
  pull_request:
    paths: ['apps/api/agents/**', 'apps/api/llm/**', 'evals/**']
  schedule:
    - cron: '0 18 * * *'    # nightly UTC 18:00
jobs:
  eval:
    runs-on: ubuntu-latest
    timeout-minutes: 12
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
      - run: pnpm i
      - name: Detect affected suites
        id: detect
        run: pnpm tsx evals/scripts/detect-affected.ts
      - name: Run suites
        env:
          DASHSCOPE_API_KEY: ${{ secrets.DASHSCOPE_API_KEY_EVAL }}
        run: pnpm promptfoo eval --suite ${{ steps.detect.outputs.suites }}
      - name: Compare vs main
        run: pnpm tsx evals/scripts/compare.ts
      - name: Comment on PR
        uses: actions/github-script@v7
        with:
          script: file://./evals/scripts/pr-comment.cjs
      - uses: actions/upload-artifact@v4
        with:
          name: eval-report
          path: evals/reports/
```

### 10.3 PR 评论格式

```markdown
## 📊 Eval Regression

| Suite | Status | Δ vs main |
|-------|--------|----------|
| jd_extract | ✅ pass | +0.012 |
| match_analysis | ⚠️  pass | -0.003 |
| resume_generate | ❌ fail | -0.05 (P10 below) |

Top failures:
1. `resume_generate_011`: fact_consistency=62 (was 88)
2. `resume_generate_004`: judge_total=58 (was 79)

[Full report](artifact-link)
```

### 10.4 不退化策略

- 所有指标 Δ ≤ -2pp 视为退化(单维度),fail
- 综合分 Δ ≤ -1pp 也 fail
- 想故意降阈值需要在 PR 描述加 `EVAL_BASELINE_BUMP` 标签 + 在 reviews 中说明理由(罕见情况)

### 10.5 评测专用 API Key

`DASHSCOPE_API_KEY_EVAL` 与生产 Key 分开,便于:

1. 单独跟踪 CI LLM 成本
2. 触发限流不会影响开发
3. ¥15 阿里云额度耗尽前优先保护这个 Key,因为评测是工程纪律的根

---

## 11. 离线索引与确定性

### 11.1 评测专用数据库

`evals/fixtures/` 提供一份预先 ETL 好的 Postgres dump(包含 30 个测试 profile 的 chunks + embeddings)。

CI 启动:`docker compose -f docker-compose.eval.yml up -d` 后 `psql -f fixtures/eval.sql`。

**评测时绝对不连真实用户数据库**。

### 11.2 Embedding 缓存

百炼 `text-embedding-v3` 输出确定(同输入同 vector,API 文档保证)。CI 缓存策略:

- 第一次运行写 `evals/cache/embeddings.parquet`
- 后续 CI 读缓存,不再调 API
- 评测样本变更时,新 query 才打 API(增量)

预估:全套评测 embedding 调用 < 200 次 / nightly。

### 11.3 LLM 输出非确定

LLM 调用走 `temperature=0`(抽取类)与 `temperature=0.2`(创作类)。即使如此,sampling 仍有抖动。处理:

- **每条样本跑 3 次**,取中位数指标
- promptfoo 配置 `numTests: 3` + 自写 reducer
- nightly 跑 5 次,方差超阈值告警

---

## 12. Bad Case 闭环

### 12.1 Bad Case 来源

| 来源 | 进入路径 |
|------|---------|
| 用户 👎 反馈(API_SPEC §6.10 留扩展位) | 写入 `bad_cases` 表 |
| 简历定制 review_failed 状态 | 自动写入 |
| LLM 抛 schema 异常 | 自动写入 |
| CI 跑出的失败样本 | 写入 PR artifact,作者人工决定是否入池 |
| 自己使用过程中觉得"答非所问" | 手动 `pnpm tsx scripts/add-bad-case.ts` |

### 12.2 `bad_cases` 表(待补到 3-DATA_MODEL §3.20)

```sql
CREATE TABLE bad_cases (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    suite VARCHAR(50) NOT NULL,
    input JSONB NOT NULL,
    actual JSONB,
    expected JSONB,
    severity VARCHAR(20) NOT NULL,    -- 'high' | 'medium' | 'low'
    triage_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    -- pending | promoted_to_eval | wontfix | duplicate
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    promoted_at TIMESTAMPTZ
);
CREATE INDEX idx_bad_cases_triage ON bad_cases(triage_status, created_at DESC);
```

### 12.3 月度 Triage

每月第 1 周:

1. 列出过去 30 天 `triage_status=pending` 的 bad cases
2. 人工分类:
   - **高价值**(代表新场景)→ 改 `expected`,promote 到 `evals/suites/<suite>/dataset.jsonl`
   - **重复**(已有相似样本)→ duplicate,关掉
   - **wontfix**(模型固有限制)→ 记入 `evals/known_limitations.md`
3. 提交 PR,PR 描述列出新增样本与目的
4. 回归 CI 跑全套,允许阈值小幅下降(因为难度变高);更新 baseline

### 12.4 数据隐私

- 用户上传的 bad case 必须经过脱敏(姓名 / 公司 / 邮箱替换)才能进入评测仓库
- 用户在 settings 可以关掉自动 bad case 收集(默认关,需主动 opt-in)
- 云端 Demo 默认不收集

---

## 13. 评测自身的复审

### 13.1 评测集老化

每季度抽样 30 条评测样本人工复核 ground truth 是否仍然合理(技术栈变迁、JD 表述演化)。过时样本 deprecate 不删。

### 13.2 Judge 一致性

每季度跑一次 Judge 与 3 位人工评估的 Cohen's kappa,要求 ≥ 0.7。<0.7 表示 Judge prompt 已经偏移,触发 Judge prompt 改版 + 全部历史 Judge 结果重跑(重跑成本预计 ¥10 量级)。

### 13.3 阈值复审

- M3 GA 前所有阈值用"初始"列
- M3 GA 后切到 GA 列
- 后续每个里程碑结束时,如果某指标连续 4 周稳定高于阈值 5pp,可以提阈值

---

## 14. 不在本文档范围

| 主题 | 文档 |
|------|------|
| Agent 内部 prompt / 节点 | 5-AGENT_DESIGN |
| 数据库 schema(`bad_cases` 表正式版) | 3-DATA_MODEL §3.20(待补) |
| API 端点(/v1/admin/eval/*) | 4-API_SPEC §6.12 |
| 用户调研、产品 NSM 测量(线上 A/B) | 1-PRD §6 + 7-ROADMAP |
| 模型本身的预训练评测(Helm/MMLU)| 不做,信任百炼 |

## 15. 待决问题

- **Q-EVAL-01**:promptfoo 与自写 Python runner 之间的边界。当前默认 promptfoo 主导 + Python 断言桥接,如果实测 promptfoo 对中文嵌入支持有问题,M2 切自写 runner(预算 1 周)
- **Q-EVAL-02**:Judge 用 `qwen3.6-plus` 还是切其他模型避免"评委即被评者"偏差?M2 跑 Judge 一致性测试时再决定;若发现自评偏高 ≥ 5pp,Judge 切到 `deepseek-v4-pro`(走 BYOK)
- **Q-EVAL-03**:评测中是否要录像(record + replay)用户真实使用流?暂不做,等线上 trace + bad case 闭环跑通再考虑
