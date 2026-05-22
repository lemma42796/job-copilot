---
title: EVAL PLAN - JobCopilot v2(评测套件 + Cohen's kappa 守门)
owner: lemma42796
last_updated: 2026-05-22
purpose: 锁评测套件结构、dataset 标注规范、kappa 算法、跑法、不达标处理流程,以及 JD-to-Knowledge 覆盖指标口径
---

# 1. 一句话总览

当前主动维护四套正式评测 + 一个 M2.5 最小覆盖指标脚本。M2.5 不恢复 `jd_aggregator` 大套件,但保留 `jd_coverage` 用来量化 JD-to-Knowledge 覆盖质量:

| Suite | M? DoD | 守什么 | 阈值 |
|-------|--------|-------|------|
| `hybrid_search` | M2 补测 | RAG 召回 + final context 干净度 + chunk 语义完整性 | final_context_recall ≥ 0.95 / final_context_precision ≥ 0.70 |
| `quiz_generator` | M2 | 出题结构合规 + type_mix 决策合理 | 合规率 ≥ 0.95 / type_mix 一致率 ≥ 0.7 |
| `answer_judge` | M2 | 三层 label 跟人工标注一致性 | Cohen's `κ ≥ 0.7`(三层独立) |
| `interview_coach` | M2.1 | Agent 状态机是否走到人工期望分支 + 多轮纠偏是否正确退出 | branch accuracy ≥ 0.8 / recovery + context + hallucination case 全过 |
| `jd_coverage` | M2.5 最小指标 | JD 要求对用户知识库的覆盖分类 + 证据排序 | 手动小样本报告 |
| `jd_aggregator` | M2.5 暂缓 | 不作为当前 DoD;不恢复大而全的同义合并 eval runner | 手动 dogfood |

不达标 → 改 prompt(bump version) / 分支阈值 / 状态机逻辑 + 重跑全套,**不切模型**(沿用 5-AGENT_DESIGN §2.1)。

# 2. 通用约定

## 2.1 目录结构

```
evals/
├── suites/
│   ├── hybrid_search/
│   │   ├── dataset.jsonl          # 标注好的样本
│   │   └── README.md               # 该 suite 评什么 + 标注规范
│   ├── quiz_generator/
│   │   ├── dataset.jsonl
│   │   └── README.md
│   ├── answer_judge/
│   │   ├── dataset.jsonl
│   │   └── README.md
│   ├── interview_coach/
│   │   ├── dataset.jsonl
│   │   └── README.md
│   ├── jd_coverage/
│   │   ├── dataset.jsonl           # 本地手工标签;默认不提交真实样本
│   │   └── README.md
│   └── jd_aggregator/              # 暂缓,不作为 M2.5 当前 DoD
│       ├── dataset.jsonl
│       └── README.md
├── reports/                       # 跑评测产物(.gitignored)
│   ├── 2026-05-08_quiz_v1.0.json
│   ├── 2026-05-08_judge_v1.0.json
│   └── 2026-05-08_search_baseline.json
└── README.md                       # 总入口 + 怎么跑
```

scripts/ 在 `apps/api/scripts/`(沿用 v1):

```
apps/api/scripts/
├── eval_hybrid_search.py
├── eval_quiz_generator.py
├── eval_answer_judge.py
├── eval_interview_coach.py
├── eval_jd_coverage.py             # M2.5 最小覆盖指标脚本
└── eval_jd_aggregator.py           # 暂缓,不作为 M2.5 当前 DoD
```

## 2.2 dataset.jsonl 通用约定

- 一行一个 JSON,UTF-8 无 BOM,中文不转义(`ensure_ascii=False`)
- 每条样本必带:`id`(`q001` / `j001` / `s001` 风格)、`source`(标注来源,如 `dogfood_2026_05`)、`bug_ref`(可选,关联 bug 样本就填 issue 号)
- 例外:`jd_coverage` 是 M2.5 最小读库指标脚本,只强制 `id / analysis_id / req_id / expected_status / expected_evidence_chunk_ids`。
- **bug 进 dataset 规则**(沿用 v1 LESSONS §8.2):每发现一类新 bug,加 1 条 fixture 进对应 suite,**永不删除**(防回归)
- dataset 修改必须 PR,不接受 commit 直接改

## 2.3 跑评测的入口约定

每个 `eval_*.py` 脚本统一 CLI:

```
python -m jobcopilot_api.scripts.eval_answer_judge \
    --suite evals/suites/answer_judge \
    --prompt-version v1.0 \
    --output evals/reports/2026-05-08_judge_v1.0.json \
    --limit 30          # 调试用,不传则跑全集
```

输出 JSON 报告固定 schema(详见各 suite §X.4)。报告里必带 `prompt_version` / `model_id` / `git_sha` / `total_cost_cny` / `cache_hit_rate` 五项 — 跑出问题时三件套定位(prompt 版本变没变 / 模型变没变 / 代码变没变)。

## 2.4 LLM cache 不在评测路径上禁

评测重跑同 dataset 时 cache 命中率高,**不禁** — 评测不是测 LLM 速度,是测 prompt 输出质量。报告里报 `cache_hit_rate` 仅供成本审计用。

## 2.5 Tool use 不禁,加 baseline 对比

AnswerJudge 在评测中**保留** `lookup_in_notes_global` 工具(5-AGENT §4.7)— 评测就是要测真实行为,禁工具就是测了一个不存在的产物。

但 M2 DoD 要求**额外跑一次 baseline**(同 dataset / 同 prompt / 关掉工具),报告对比两组:

| 指标 | tool=on(主路) | tool=off(baseline) | 期望差异 |
|------|---------------|---------------------|---------|
| Fidelity kappa | ≥ 0.7(守门)| 仅参考 | tool=on 应当显著高 |
| dataset 11-15(用户讲常识)子集 Fidelity 准确率 | ≥ 0.85 | 仅参考 | tool=on 应当 +20pp 以上 |
| 单 session 平均 Judge cost | 监控 | 基线 | tool=on 预期增 30-60% |

Baseline 跟主路对照是 harness engineering 标准动作 — 没对照就不知道这个工具有没有真实价值。

## 2.6 Trace 跟评测的集成

每条评测产生的 LLM 调用都进 Langfuse trace(2-TECH §6),trace tag 加 `eval_run_id` + `fixture_id`。kappa 不达标时直接 Langfuse UI 按 fixture_id 过滤,看 Judge evidence 怎么生出来的、工具调了几次、tool 返回啥 — 排查时间从"翻日志半小时"降到"点几下五分钟"。

## 2.7 样本规模按字数 / token 衡量,不按篇数 ⭐

**永久约束(STATUS.md `[来自 M1]`)**:dataset / dogfood 库 / 压力测试目标的"样本规模"一律按**总字数(或 token)**,不按篇数。

| ✗ 不要这样 | ✓ 应该这样 |
|---|---|
| "dogfood 库 50 篇笔记" | "dogfood 库 ≥ 10 万字" |
| "评测样本 30 条 JD" | "评测样本 30 条 JD,平均字数 1.5k,总 ~4.5 万字" |

**理由**:50 篇 × 100 字 vs 50 篇 × 2000 字,对 chunker / embedding / hybrid search / retrieval pipeline 的真实压力**差一个数量级**。篇数是虚假指标 — DoD 写"50 篇过 chunker"可能挂在你笔记总长 8000 字的极端样本上。

**例外**:**功能型计数**仍用篇数(产品规格 ≠ 负载指标),例如:
- 一次面试 session 出 5 题(题数)
- 一键分析 ≤ 200 条 JD(条数)
- 一次 JD 分析 ≤ 200 条 JD(条数)

这类是产品契约,不是工程负载,继续用篇数 / 条数 / 题数表达。

**M1 dogfood 实例**:30 篇 / 258 chunks / 100% embedding / **15.7 万字**(过 10 万字 DoD 门槛)— 报告里"30 篇"只作为辅助信息,DoD 守的是字数。

# 3. `answer_judge` suite(核心 / M2 DoD)

## 3.1 评什么

测 Judge 输出的三层 label 跟人工标注的一致性。**核心是 Cohen's kappa**(沿用 `apps/api/src/jobcopilot_api/evals/kappa.py`),三层独立算 + 独立报告。

- **Coverage kappa**:Judge 给的每个 point label(`hit` / `partial` / `miss`)vs 人工标的 label
- **Fidelity kappa**:Judge 给的每条 claim label(`supported` / `inferred` / `fabricated`)vs 人工标的 label
- **Depth accuracy**(不算 kappa):Judge 给的三个维度 `covered` bool vs 人工标。**为什么 depth 不算 kappa**:三维度均权 + 二值,样本量 30 时类别分布不稳,kappa 抖动大;直接报 accuracy(每维度独立 + 三维度平均)更直观。**目标 ≥ 0.75**

阈值要求:**三个都过才算合格**

| 指标 | 阈值 |
|------|------|
| Coverage `κ` | ≥ 0.7 |
| Fidelity `κ` | ≥ 0.7 |
| Depth accuracy | ≥ 0.75 |

## 3.2 dataset.jsonl schema

```json
{
  "id": "j001",
  "source": "dogfood_2026_05",
  "bug_ref": null,
  "input": {
    "question": {
      "type": "open_ended",
      "prompt": "解释 synchronized 的锁升级过程",
      "reference_answer": "synchronized 在 JDK 1.6 后引入锁升级:无锁 → 偏向锁 → ...",
      "reference_points": [
        {"id": "p1", "text": "锁升级四阶段:无锁→偏向→轻量级→重量级", "weight": 0.4, "evidence_chunk_ids": [1]},
        {"id": "p2", "text": "偏向锁的撤销发生在多线程竞争时", "weight": 0.3, "evidence_chunk_ids": [2]},
        {"id": "p3", "text": "重量级锁通过 ObjectMonitor 实现", "weight": 0.3, "evidence_chunk_ids": [3]}
      ]
    },
    "chunks": [
      {"id": 1, "folder_path": ["Java","并发"], "heading_path": ["synchronized","锁升级"], "content": "..."},
      {"id": 2, "folder_path": [...], "heading_path": [...], "content": "..."},
      {"id": 3, "folder_path": [...], "heading_path": [...], "content": "..."}
    ],
    "user_answer": "锁升级有四个阶段,从无锁开始,然后偏向锁,然后是 CAS 加红黑树,最后重量级。"
  },
  "ground_truth": {
    "coverage": {
      "p1": "hit",
      "p2": "miss",
      "p3": "partial"
    },
    "fidelity": [
      {"claim": "锁升级有四个阶段",          "label": "supported"},
      {"claim": "从无锁开始",                 "label": "supported"},
      {"claim": "然后偏向锁",                 "label": "supported"},
      {"claim": "然后是 CAS 加红黑树",        "label": "fabricated"},
      {"claim": "最后重量级",                 "label": "supported"}
    ],
    "depth": {
      "tradeoff": false,
      "why":      false,
      "boundary": false
    }
  },
  "notes": "测 fabricated 识别 + miss 识别 + 全 depth=false 边界"
}
```

字段语义:

- `input` 字段直接喂给 `AnswerJudge` 的 USER 模板
- `ground_truth.fidelity` 是**人工拆好的 claim 列表 + label**;Judge 输出的 claims 不一定逐字对得上,评测脚本按"语义最近邻"匹配人工 claims 后再比 label(详见 §3.4 算法)
- `notes` 标这条 fixture 想测什么(给后续维护者看)

## 3.3 30 条覆盖矩阵

| # | 类型 | 期望测什么 |
|---|------|----------|
| 1-5 | 完美答(reference_answer 复述) | Coverage 全 hit / Fidelity 全 supported / Depth 高 |
| 6-10 | 答了一半(漏关键点) | Coverage mix(hit + miss);Judge 不应整体打高 |
| 11-15 | 用专业常识答(chunks 没明说) | Fidelity 应标 inferred 而非 fabricated(LESSONS §1.1 假阳性守门) |
| 16-20 | 故意编造(跟 chunks 矛盾) | Fidelity 应标 fabricated;触发 30% 锁顶 50 |
| 21-25 | 答了但偏题 / 答错题 | Coverage 多 partial / miss;Judge 不应硬给 hit |
| 26-30 | 边界 case | 空答 / 1 字答 / 全英文答 / 中英混合 / 答了 reference 没要求的内容(应不扣分) |

具体 30 条样本第一次由作者 dogfood 时手工标(每周 5 条,每次 session 评分后顺便标),M2 启动时 30 条齐全。

## 3.4 评测脚本算法(`eval_answer_judge.py`)

```python
# 伪代码
fixtures = load_jsonl('evals/suites/answer_judge/dataset.jsonl')
results = []

for fx in fixtures:
    judge_output = await answer_judge.run(fx.input, prompt_version='v1.0')

    # Coverage:逐 point id 比 label
    cov_pairs = [(judge_output.coverage_evidence.points[p.id].label,
                  fx.ground_truth.coverage[p.id])
                 for p in fx.input.question.reference_points]

    # Fidelity:Judge claims 跟人工 claims 按语义最近邻匹配,然后比 label
    fid_pairs = match_claims(judge_output.fidelity_evidence.claims,
                             fx.ground_truth.fidelity)

    # Depth:三维度逐个比 covered
    dep_pairs = [(judge_output.depth_evidence.dimensions[d].covered,
                  fx.ground_truth.depth[d])
                 for d in ['tradeoff', 'why', 'boundary']]

    results.append((fx.id, cov_pairs, fid_pairs, dep_pairs))

# 全 dataset 聚合
all_cov = flatten(r.cov_pairs for r in results)
all_fid = flatten(r.fid_pairs for r in results)
all_dep = flatten(r.dep_pairs for r in results)

cov_kappa = cohen_kappa([j for j, _ in all_cov], [g for _, g in all_cov])
fid_kappa = cohen_kappa([j for j, _ in all_fid], [g for _, g in all_fid])
dep_acc   = sum(j == g for j, g in all_dep) / len(all_dep)

# 报告
report = {
  'prompt_version': 'v1.0',
  'model_id': 'qwen3.6-flash',
  'git_sha': '...',
  'total_cost_cny': sum(r.cost for r in results),
  'cache_hit_rate': cached / len(fixtures),
  'metrics': {
    'coverage_kappa': cov_kappa,
    'fidelity_kappa': fid_kappa,
    'depth_accuracy': dep_acc,
  },
  'per_fixture': [...],   # 不达标时挑差的 fixture 看
  'verdict': 'pass' if all 三个都过 else 'fail'
}
```

`match_claims` 的语义最近邻:对 Judge 每条 claim,用 embedding 余弦相似度跟人工 claims 找最近的;阈值 0.6 以下视为"未匹配"(Judge 编了人工没列的 claim,该 claim 强制 label = `unmatched_extra`,算 fabricated 加分);人工 claim 没被任何 Judge claim 匹配上(Judge 漏 claim) → 视为 Judge 漏标,算 `unmatched_missing` (Judge 应当至少识别人工列的那些)。两类 unmatched 都计入 kappa 计算时算"额外的不一致 pair"。

## 3.5 不达标处理流程

任一指标不过 → **不切模型**(沿用 5-AGENT_DESIGN §2.1),按下列顺序排查:

1. **Langfuse 看 trace**:按 `eval_run_id` 过滤,挑 verdict=fail 的 5-10 条 fixture,直接看 Judge 的输入 / 输出 / 工具调了几次 / tool 返回内容,5 分钟看出问题在 prompt 还是 tool 还是 retrieval
2. **看 per_fixture report**:补充 trace 看不到的统计层信息(label 分布偏移 / tool 调用率)
3. **判类型分支**:
   - Judge 不调 tool 直接标 fabricated → 改 prompt 强化"必调"约束(v1.0 → v1.1)
   - Judge 调了 tool 但仍标错 → 是 tool 召回质量问题,看 hybrid search baseline 数据
   - label 定义理解偏 → 加更多反例 / 边界示例进 SYSTEM
4. **重跑全套** + 报告对比 v1.0 vs v1.1(主路 + baseline 各一份)
5. 仍不达标 → 找 ground_truth 标得是不是不一致(标注者偏差) — 重新标 30% 抽样,二次评估
6. 仍不达标 → 升级到 6-EVAL_PLAN 的"开放问题"区,触发设计调整(可能要拆 Judge 三层为三次独立 LLM 调用,降单次推理负载)

# 4. `quiz_generator` suite(M2 DoD)

## 4.1 评什么

QuizGenerator 输出**没有**单一"对错"标准(题没法精确比对),所以不算 kappa,只测**结构合规率**和**type_mix 决策一致率**。

| 指标 | 定义 | 阈值 |
|------|------|----|
| 结构合规率 | 通过 §3.5 service 层 5 项校验的题数 / 总题数 | ≥ 0.95 |
| type_mix 一致率 | LLM 决策的 (open_ended:definition) 跟人工预判一致的样本比例 | ≥ 0.7 |
| 反幻觉率 | 题干 / reference 里出现 chunks 没提的实体的样本数 | ≤ 0.05 |

## 4.2 dataset.jsonl schema

```json
{
  "id": "q001",
  "source": "synthetic_2026_05",
  "input": {
    "node_folder_path": ["Java","并发"],
    "node_heading_path": ["synchronized"],
    "chunks": [
      {"id": 1, "folder_path": [...], "heading_path": [...], "content": "..."},
      ...
    ],
    "question_count": 5
  },
  "ground_truth": {
    "expected_type_mix": {"open_ended": 3, "definition": 2},
    "type_mix_rationale": "chunks 多为机制描述(锁升级过程 / 实现),适合开放式偏多",
    "forbidden_entities": ["ConcurrentHashMap", "ReentrantLock"]   // chunks 没提的实体,题里出现就算幻觉
  },
  "notes": "..."
}
```

`forbidden_entities`:作者标 dataset 时审 chunks 后列出,通常是"同领域但 chunks 没提到的相关实体"。

## 4.3 30 条覆盖矩阵

| # | chunks 风格 | 期望 type_mix |
|---|-------------|--------------|
| 1-10 | 概念定义 / 八股偏多 | definition 多数(3:2 或 4:1)|
| 11-20 | 过程 / 原理 / trade-off 偏多 | open_ended 多数(3:2 或 4:1)|
| 21-25 | 中性混合 | 6:4 偏 open_ended(产品默认)|
| 26-30 | 边界:chunks 极少(5)/ chunks 多(30)/ 单 heading vs 多 heading | 看结构合规是否守住 |

## 4.4 跑法

```
python -m jobcopilot_api.scripts.eval_quiz_generator \
    --suite evals/suites/quiz_generator \
    --prompt-version v1.0 \
    --output evals/reports/2026-05-08_quiz_v1.0.json
```

输出报告 schema 类似 §3.4。**不达标时**:跟 §3.5 同流程,先看 per_fixture 再改 prompt。

# 5. M2.5 JD Intelligence suites

## 5.1 `jd_coverage` suite(最小指标脚本)

最新决策(2026-05-22):为简历里的 JD-to-Knowledge 覆盖分析补一条最小可复现指标链,但不恢复大而全的自动化测试。该脚本不调 LLM,只读人工 JSONL 标签和 DB 里已经生成的 `jd_analyses.note_match_summary`。

### 5.1.1 评什么

测"JD 聚合出的 canonical requirement → 用户知识库覆盖矩阵"这一步是否可信:

| 指标 | 定义 | 用途 |
|------|------|------|
| `coverage_macro_f1` | `covered / partial / missing / unknown` 四分类 macro F1 | 看覆盖分类整体是否准 |
| `missing_recall` | 人工标为 `missing` 的 requirement 被系统判成 `missing` 的比例 | 守"缺口别漏" |
| `false_covered_rate` | 人工非 covered 却被系统判成 covered 的比例 | 守"别假装已掌握" |
| `evidence_precision@k` | Top-k 证据 chunk 中人工认可证据的比例 | 看证据是否干净 |
| `evidence_recall@k` | 人工认可证据被 Top-k 找回的比例 | 看证据是否找全 |
| `evidence_mrr@k` | Top-k 中第一个人工认可证据的倒数排名均值 | 看好证据是否排前 |

`coverage_accuracy` 只作为诊断指标,不当 headline,避免类别不均衡时掩盖 missing 类问题。

### 5.1.2 dataset.jsonl schema

```json
{
  "id": "cov_001",
  "analysis_id": 12,
  "req_id": "req_3",
  "expected_status": "partial",
  "expected_evidence_chunk_ids": [9012, 9018],
  "notes": "Redis cluster note partially covers the requirement"
}
```

标注口径:

- `expected_status` 只能是 `covered / partial / missing / unknown`。
- `expected_evidence_chunk_ids` 只标真正能支持覆盖判断的 `note_chunks.id`;`missing / unknown` 可为空。
- 第一批最小 dogfood 只需 5-10 条,覆盖 covered / partial / missing 三类即可。

### 5.1.3 跑法

```
uv run python apps/api/scripts/eval_jd_coverage.py
```

默认读取 `evals/suites/jd_coverage/dataset.jsonl`,报告写入 `evals/reports/jd-coverage-<timestamp>.md`。可用 `--dataset / --report / --report-dir / --k` 覆盖默认值。

## 5.2 `jd_aggregator` suite(暂缓,不作为 M2.5 DoD)

最新决策(2026-05-18):M2.5 不再新增自动化测试 / eval runner。最多约 50 条同质 JD 的真实场景优先靠手动 dogfood 判断报告是否有生产力;以下设计仅作为将来重新需要自动化回归时的存档,不要把它当下一刀。

### 5.2.1 评什么

测 JdAggregator 多 JD 一键分析的 **同义合并准确率** + **频次重算正确性**。不算 kappa(canonical 不是 categorical label,是文本去重),改用集合 / 序列指标。

| 指标 | 定义 | 阈值 |
|------|------|----|
| **同义合并准确率** | Judge 输出的 canonical 集合 vs 人工标 canonical 集合的 F1 score(集合元素相等性比对)| ≥ 0.85 |
| **频次重算误差** | Python 重算 frequency vs 人工 ground truth frequency 的 MAE | ≤ 0.03 |
| **结构合规率** | aggregated_requirements 全部字段非空 + supporting_jd_ids 非空 | ≥ 0.98 |

### 5.2.2 dataset.jsonl schema

```json
{
  "id": "agg001",
  "source": "synthetic_2026_05",
  "input": {
    "parsed_jds": [
      {"jd_index": 1, "hard_skills": ["Java", "JVM 调优", "MySQL 索引"]},
      {"jd_index": 2, "hard_skills": ["Java 虚拟机", "MySQL 事务", "Redis 集群"]},
      {"jd_index": 3, "hard_skills": ["JVM", "MySQL", "Redis cluster"]}
    ]
  },
  "ground_truth": {
    "canonical_requirements": [
      {
        "canonical_text": "Java 虚拟机 / JVM",
        "raw_phrases_expected": ["Java", "JVM 调优", "Java 虚拟机", "JVM"],
        "supporting_jd_indexes": [1, 2, 3],
        "frequency": 1.0
      },
      {
        "canonical_text": "MySQL(索引 / 事务)",
        "raw_phrases_expected": ["MySQL 索引", "MySQL 事务", "MySQL"],
        "supporting_jd_indexes": [1, 2, 3],
        "frequency": 1.0
      },
      {
        "canonical_text": "Redis 集群",
        "raw_phrases_expected": ["Redis 集群", "Redis cluster"],
        "supporting_jd_indexes": [2, 3],
        "frequency": 0.667
      }
    ]
  },
  "notes": "测同义合并(JVM 三种说法 / Redis 中英)+ 频次跨 JD 计算"
}
```

### 5.2.3 数据集容量

若未来重启自动化 suite,可从 30 条起步,组合多种场景:

| # | 场景 | 期望测什么 |
|---|------|---------|
| 1-10 | 简单同义(< 10 条 JD,清晰 1:1 同义)| 基础合并 |
| 11-15 | 跨 batch 同义(50+ 条 JD,需 hierarchical reduce 跨 batch 合并)| 二次 reduce 守门 |
| 16-20 | 频次边界(同一 canonical 在某条 JD 出现 ≥ 1 次但只算 1 次)| Python 重算 dedup 守门 |
| 21-25 | 噪声词(BOSS 平台标签 / 学历词混入 hard_skills)| 不算 canonical 进单独 group |
| 26-30 | 极端规模(200 条 JD 上限场景)| reduce 拓扑跑通 + P95 ≤ 60s |

### 5.2.4 存档跑法 + 阈值

当前不执行。只有用户重新明确要求"跑评测"或"恢复 jd_aggregator suite"时再启用。

```
python -m jobcopilot_api.scripts.eval_jd_aggregator \
    --suite evals/suites/jd_aggregator \
    --prompt-version v1.0 \
    --output evals/reports/2026-05-08_jd_agg_v1.0.json
```

**不达标处理**:类似 §3.5,先看 per_fixture trace(Langfuse 按 fixture_id 过滤);改 prompt 重跑;调 Stage 1 / Stage 2 拆 batch 大小。

# 6. 已砍掉的 suite

`resume_advisor` suite 不再规划。后续不做简历上传 / 简历诊断 / 简历改写 / 简历参与出题,因此不再维护 anchored ratio、resume_position 锚点或 forbidden resume copy 指标。

这一类风险只保留为 v1 失败复盘经验:JDAnalysisAgent 只能产出岗位要求地图、学习路径和 quiz topic 候选,不能生成任何简历文案。

# 7. `hybrid_search` suite(M2 补测 / M2.1 前置)

## 7.1 评什么

测 **用户主题 query → query rewrite → hybrid 召回 → reranker → post-rerank governance/blend → parent-doc 扩展 → 最终 chunks** 这条完整 RAG 链路是否满足三件事:

1. **召回不漏**:该出现的 evidence 能进入候选、重排后靠前,最终上下文覆盖完整。
2. **上下文不脏**:最终喂给 QuizGenerator / AnswerJudge 的 chunks 以直接证据和必要上下文为主,避免靠堆无关 chunk 换 recall。
3. **切片不断义**:最终喂给 QuizGenerator / AnswerJudge 的 chunks 不把关键前提、否定、数值、表格、代码块、列表结构切坏。

本 suite 是 M2 已完成功能链路后的质量补测,也是 M2.1 `InterviewCoachAgent.retrieve_context` 的前置守门。它不改变产品边界:当前只评程序员面试笔记,不声明医学 / 法律等高风险领域可用。

## 7.2 分阶段指标

| 指标 | 定义 | 阈值 |
|------|------|------|
| `candidate_recall@15` | 每个非 0 命中样本中,direct evidence chunk 进入 query rewrite + hybrid + RRF 后候选 Top-15 的比例;再做 macro average | ≥ 0.70 |
| `selected_recall@10` | 每个非 0 命中样本中,direct evidence chunk 进入最终 selected Top-10 的比例;`provider_blend` 下指 post-rerank governance/blend 后的 Top-10,再做 macro average | ≥ 0.90 |
| `mrr@10` | selected Top-10 中首个 expected evidence 的 mean reciprocal rank | ≥ 0.60 |
| `final_context_recall` | 每个非 0 命中样本中,parent-doc 扩展后最终 chunks 覆盖 direct evidence chunk 的比例;再做 macro average | ≥ 0.95 |
| `final_context_precision` | 非 0 命中样本中,final context 内由 Codex 判为直接证据 / 必要上下文的 chunk 数 / final context chunk 总数 | ≥ 0.70 |
| `zero_hit_precision` | 0 命中 / 近邻干扰样本没有被错误拿去出题的比例 | ≥ 0.90 |
| `unsafe_boundary_rate` | 人工审查中出现"关键前提 / 否定 / 数值 / 表格 / 代码块被切断且 final context 未补回"的比例 | ≤ 0.05 |

解释:

- `candidate_recall@15` 守"粗排紧窗口是否够好":它不是 rerank input 上限;top50 继续保留为诊断窗口和 provider rerank input,用于发现排在 30+ 的长尾正样本。
- `selected_recall@10` / `mrr@10` 守"排准":QuizGenerator 不应吃大量边缘候选;provider rerank 只提供 challenger 排序,最终成员还要过 governance/blend。
- `final_context_recall` 守"最终可用":真正决定出题 / 评分质量的是 parent-doc 后的上下文,不是中间候选。
- `final_context_precision` 守"最终干净":防止 parent-doc / top_k 扩太宽,把无关 chunk 塞给 LLM。
- `unsafe_boundary_rate` 是 chunker 质量指标,发现问题优先调 chunker / parent-doc,不是换模型。

聚合口径:recall / mrr headline 默认是 **macro average**。`expected_zero_hit=true` 的样本没有 direct evidence,不参与 recall / mrr 均值;但非 0 命中样本如果被系统误判为 0 命中,`final_context_recall` 记 0,避免被 headline 漏算。需要看全局 chunk 粒度时,另看 report 里的 micro coverage 字段。

## 7.3 Ablation 矩阵

每条样本都跑以下路径,报告里并排输出:

| 路径 | 目的 |
|------|------|
| `vector_only` | 看 dense embedding 语义召回单路上限 |
| `lexical_only` | 看 char_ngram / 英文 token 对术语、拼写、缩写的守门能力 |
| `hybrid_no_rewrite` | 看 RRF 融合本身收益 |
| `hybrid_with_rewrite` | 看 query rewrite 是否真实提召回,以及是否引入跑题 |
| `hybrid_with_rewrite_rerank` | 看 provider rerank 是否把正确 evidence 推入 challenger pool |
| `post_rerank_governance_blend` | 看 source/type、contrast、route anchors 和 provider score blend 后,最终 selected Top-10 是否更干净 |
| `final_context_parent_doc` | 看 parent-doc 是否补回被切断的语义上下文 |

不接受只报最终均值。报告必须保留 per-case 的各阶段 chunk_ids / heading_path / score / failure_reason,否则无法知道该调 query rewrite、hybrid、reranker、chunker 还是 parent-doc。

## 7.4 dataset.jsonl schema

```json
{
  "id": "s001",
  "source": "dogfood_2026_05",
  "input": {
    "query": "synchronized 锁升级过程",
    "mode": "topic"
  },
  "ground_truth": {
    "direct_evidence_chunk_ids": [12, 15],
    "necessary_context_chunk_ids": [13],
    "expected_note_paths": ["Java/并发/synchronized.md"],
    "hard_negative_note_paths": ["Java/集合/HashMap.md"],
    "expected_heading_paths": [["Java", "并发", "synchronized"]],
    "evidence_anchors": ["偏向锁", "轻量级锁", "重量级锁"],
    "expected_zero_hit": false,
    "risk_tags": ["cross_heading", "ordered_steps"]
  }
}
```

字段约定:

- `direct_evidence_chunk_ids`:固定 fixture / dogfood 库导入后的稳定 chunk id;这些 chunk 直接回答 query,用于精确算 recall / mrr。
- `necessary_context_chunk_ids`:自身不一定直接回答 query,但用于补齐前提、边界、步骤或 parent-doc 上下文;计入 final context precision 的 relevant。
- `expected_note_paths`:note-level 粗守门;用于保留历史 smoke pass/fail 口径,避免 chunk_id 漂移时完全失去诊断信号。
- `hard_negative_note_paths`:近邻干扰笔记;若进入最终上下文,报告 `hard_negative_intrusion` 和首个 rank。
- `expected_heading_paths`:chunker 调参导致 chunk_id 漂移时的人工兜底定位。
- `evidence_anchors`:短原文锚点,用于人工审查 final context 是否真包含关键证据;不要贴长段原文。
- `expected_zero_hit`:无该主题 / 近邻干扰样本置 `true`,用于测 0 命中守门。
- `risk_tags`:标注该样本主要测什么,取值建议:`exact_term` / `synonym` / `typo` / `mixed_cn_en` / `cross_heading` / `numeric` / `negation` / `table` / `code` / `ordered_steps` / `zero_hit`。

标注口径:

- `direct_evidence_chunk_ids` 只放**单独拿出来也能回答 query 的核心证据**;不要把面试追问、anchor 汇总、评测诊断、泛背景说明放进 direct。
- `necessary_context_chunk_ids` 放**防止证据断义的上下文**:协议形态、代理配置、限流算法、通用重试规则、parent-doc 流程 / 风险等。
- `evidence_anchors` 是短文本锚点,用于确认 final context 是否包含关键原文事实;anchor 不等于 direct chunk,也不要求每个 direct chunk 都逐字命中。
- 发现 recall 低时先复核标签口径,避免用过宽 direct evidence 把 reranker / parent-doc 指标压低。

标注职责:

- `ground_truth` 初标由 Codex 完成:先读 fixture 笔记、定位 expected chunks / heading_path / anchors,生成 dataset 草稿。
- 用户只做抽样复核与争议裁决:Codex 对不确定样本必须标 `needs_review: true` 并写 `notes`,等待用户确认后再纳入正式阈值统计。
- Codex 不得用 LLM 自己输出的相关性判断当唯一依据;必须回到笔记原文 / chunk 内容定位 evidence。
- `final_context_precision` 由 Codex 逐条判定 final context chunk 相关性:`direct_evidence` / `necessary_context` 计为 relevant,`noise` 不计;`expected_zero_hit=true` 且 final context 为空的样本不参与该指标。

注:dataset 跟 dogfood 笔记库强绑,所以本 suite 跑前必须先 load fixture 笔记包(`evals/suites/hybrid_search/notes_fixture/` 或 `notes_fixture.zip`)。每次正式跑 suite 用干净 DB 重 import,保证 chunk_id 稳定;如果改 chunker 导致 chunk_id 全量漂移,必须用 `expected_heading_paths` + `evidence_anchors` 重新审核并更新 dataset。

## 7.5 样本覆盖矩阵

MVP 起步 50 条主题 query,总 fixture 笔记 ≥ 10 万字。

| # | 场景 | 主要风险 |
|---|------|----------|
| 1-10 | 完整术语,如 `synchronized 锁升级` | baseline 应稳定命中 |
| 11-20 | 同义改写,如 `Java 同步关键字 锁的等级` | lexical 失效时 dense / rewrite 要补上 |
| 21-30 | 中英混合 / 缩写 / 拼写错误,如 `synchroized monitor` | char_ngram + dense 互补 |
| 31-40 | 跨 heading / 列表 / 代码块 / 表格 / 数值 / 否定样本 | 测 chunk 是否切断关键语义 |
| 41-50 | 0 命中 / 近邻干扰,如笔记没有 React 却有前端泛论 | 不应强行出题 |

新增 bug 进入 dataset:dogfood 中出现"题目基于半截上下文"、"Judge 漏看 source chunk 外的全库证据"、"query rewrite 扩太宽导致跑题"等问题,必须新增至少 1 条 fixture。

## 7.6 报告 schema

`eval_hybrid_search.py` 输出 JSON 至少包含:

```json
{
  "suite": "hybrid_search",
  "git_sha": "...",
  "fixture_word_count": 157000,
  "summary": {
    "candidate_recall@15": 0.70,
    "selected_recall@10": 0.92,
    "mrr@10": 0.67,
    "final_context_recall": 0.96,
    "final_context_precision": 0.74,
    "zero_hit_precision": 0.90,
    "unsafe_boundary_rate": 0.04
  },
  "cases": [
    {
      "id": "s001",
      "expanded_queries": ["synchronized 锁升级过程", "..."],
      "vector_top_ids": [1, 2],
      "lexical_top_ids": [12, 15],
      "hybrid_top_ids": [12, 2, 15],
      "provider_top_ids": [12, 15],
      "selected_top_ids": [12, 15],
      "final_context_ids": [12, 13, 15],
      "final_context_relevance": [
        {"chunk_id": 12, "label": "direct_evidence", "counted_relevant": true},
        {"chunk_id": 13, "label": "necessary_context", "counted_relevant": true},
        {"chunk_id": 15, "label": "noise", "counted_relevant": false}
      ],
      "pass": true,
      "failure_reason": null
    }
  ]
}
```

当前 note/chunk smoke 额外保存 trace JSONL,用于把"跑 pipeline"和"按标签打分"拆开:

```json
{
  "case_id": "hs_note_001",
  "query": "为什么砍掉岗位类三源出题？",
  "predicted_zero_hit": false,
  "expanded_queries": ["为什么砍掉岗位类三源出题？", "..."],
  "candidate_chunk_ids": [2212, 2333, 434],
  "rerank_chunk_ids": [2212, 434],
  "rerank_movements": [
    {
      "chunk_id": 2212,
      "candidate_rank": 1,
      "rerank_rank": 2,
      "post_rank": 1,
      "final_score": 0.8123,
      "governance_score": 0.91,
      "governance_flags": ["anchors_covered", "coarse_floor_selected"]
    }
  ],
  "final_chunks": [
    {
      "chunk_id": 2212,
      "note_path": "项目/JobCopilot/M2 RAG 与出题链路.md",
      "heading_path": ["JobCopilot M2 RAG 与出题链路", "M2 的产品边界"],
      "rerank_score": 0.9859,
      "content": "..."
    }
  ],
  "rerank_tokens": 22550,
  "rerank_cost_cny": "0.011275",
  "error": ""
}
```

trace 保存的是**可按新标签重新求交集的阶段全集**,不是旧标签下的命中结果。后续只调整 `direct_evidence_chunk_ids` / `necessary_context_chunk_ids` / heading / anchor / hard-negative 标签时,必须复用 trace 离线重算,避免重复调用 query rewrite / rerank。只有改检索代码、prompt、reranker、DB 语料、embedding 或 chunker 时,才完整重跑 pipeline。

注意:`candidate_chunk_ids` 在 0 命中守门前就会保存;即使 `predicted_zero_hit=true`,也能检查系统是否其实召回了 1-2 个正确证据、只是没达到出题阈值。`rerank_chunk_ids` 和 `final_chunks` 只有通过 0 命中守门后才会出现。`post_rank` 只给真正进入 post-rerank selected context 的 chunk;被 governance 拒绝的候选只保留 `final_score / governance_score / governance_flags` 供诊断。

`failure_reason` 只能取固定枚举,便于统计:

| failure_reason | 含义 | 优先排查 |
|----------------|------|----------|
| `rewrite_drift` | rewrite 扩太宽,候选跑题 | query_rewriter prompt / expanded query 数量 |
| `vector_miss` | dense 路漏召回 | embedding 模型 / chunk 内容粒度 |
| `lexical_miss` | lexical 路漏召回 | char_ngram / tokenization / tsvector |
| `rerank_drop` | hybrid 命中但 provider reranker 没捞进 challenger pool | reranker top_k / instruct / 候选噪声 |
| `post_governance_drop` | provider 捞到但 post-rerank governance/blend 拒绝 | source/type / anchor coverage / contrast route / hard-negative clamp |
| `parent_context_missing` | Top-10 命中但 final context 缺前提 | parent-doc 策略 |
| `final_context_noise` | final context 召回到了证据,但混入过多无关 chunk | parent-doc 扩展范围 / final context top_k / 去重与 source diversity |
| `chunk_boundary_unsafe` | 切片打断关键语义 | chunker / overlap / 结构化 markdown 保护 |
| `zero_hit_false_positive` | 无主题样本仍召回并出题 | 0 命中阈值 / rerank score threshold |

`unsafe_boundary_rate` 评审职责:

- 由 Codex 逐条审查 `risk_tags` 包含 `cross_heading` / `numeric` / `negation` / `table` / `code` / `ordered_steps` 的样本,对 final context 标 `boundary_safe=true/false`。
- 用户不做全量人工审查,只抽查 Codex 标为 `boundary_safe=false` 或 `needs_review=true` 的样本。
- 判 `boundary_safe=false` 时必须给出 `failure_reason` + 最小原文证据,说明是前提、否定、数值、表格、代码块还是步骤被切断。

## 7.7 跑法

当前已落地的 smoke 子集:

```
uv run python apps/api/scripts/eval_hybrid_search_note_smoke.py
```

该脚本读取 `evals/suites/hybrid_search/dataset.note_smoke.jsonl`,只读当前 DB,写 markdown report 到 `evals/reports/`,并同时写同时间戳的 `.trace.jsonl`。报告包含 top notes、top chunks、heading/anchor coverage、hard-negative rank、`candidate_recall@15`、`selected_recall@10`、`mrr@10`、`final_context_recall`、`final_context_precision`、zero-hit 与成本。旧 note-level pass rule 暂时保留;chunk / heading / anchor 字段用于诊断。

当前生产对齐跑法:

```
uv run python apps/api/scripts/eval_hybrid_search_note_smoke.py \
    --rerank-mode provider_blend \
    --rerank-input-top-k 50 \
    --selected-top-k 10 \
    --query-embedding-cache-policy cache-only
```

`cache-only` 是 eval/smoke 默认策略:相同 expanded query 必须命中本地 `query_embedding` cache,cache miss 直接失败,避免重复实验静默请求 embedding provider。需要刻意补缓存时,由用户显式切 `--query-embedding-cache-policy live-on-miss`。

只改标签或打分口径时,不要完整重跑。复用上一次 trace 离线重算:

```
uv run python apps/api/scripts/eval_hybrid_search_note_smoke.py \
    --score-trace evals/reports/hybrid-search-note-smoke-xxx.trace.jsonl
```

离线重算只读 trace + 当前 dataset,不调用 query rewrite / search / rerank / LLM。它会生成 `hybrid-search-note-smoke-rescore-*.md`,其中成本字段来自原 trace,用于审计历史跑法,不是本次新增花费。

正式 suite 目标入口:

```
python -m jobcopilot_api.scripts.eval_hybrid_search \
    --suite evals/suites/hybrid_search \
    --output evals/reports/2026-05-12_hybrid_search_baseline.json
```

本项目测试 / 自动化验证由用户手动跑。实现脚本后,Codex 只说明跑法、预期字段和判定标准;除非用户明确说"跑评测",否则不主动执行。

## 7.8 不达标处理顺序

1. 先看 per-case `failure_reason`,不要只看总均值。
2. `candidate_recall@15` 不达标但 top50 诊断能看到正样本:优先调粗排排序 / query weights / governance,不要把 provider input 收窄。
3. top50 诊断也漏正样本:优先调 query rewrite / hybrid top_k / tokenization / embedding,不要调 reranker。
4. `selected_recall@10` 不达标:优先查 provider challenger 是否捞到、post-rerank governance 是否误伤、blend 权重是否过强;再看 reranker `top_k` / instruct / document format。
5. `final_context_recall` 或 `unsafe_boundary_rate` 不达标:优先调 parent-doc / chunker / overlap / markdown 结构保护。
6. `final_context_precision` 不达标:优先调 dynamic clean-context selection / parent-doc 扩展范围 / 去重与 source diversity,不要靠减少 expected evidence 标注来抬分。
7. `zero_hit_precision` 不达标:调整 0 命中守门的核心实体 / anchor / source diversity / score 阈值组合,不要让 LLM 兜底编。注意 reranker `relevance_score` 只适合本次请求内排序,不要未经标定就做跨请求绝对阈值。
8. 调参后重跑全 suite,报告进 git history。

## 7.9 RAG / reranker 调参沉淀(百炼文档对照)

百炼 RAG 优化文档和 qwen3-rerank 文档对本 suite 的可复用结论:

1. **先有基线再调参**:每次改 query rewrite / chunker / reranker instruct / document format / top_k,都必须对比同一批 case 的 trace/report。只看总均值不够,要定位 `candidate_recall@15`、top50 诊断、`selected_recall@10`、`final_context_recall` 分别在哪一层变化。
2. **`instruct` 是排序策略,不是装饰参数**:qwen3-rerank 明确支持用 instruct 区分"问答检索"和"语义相似度"。JobCopilot 的目标是找"能直接作为笔记证据回答 query 的 chunk",不是找标题 / 文件夹 / 主题最相似的 chunk。默认 web-search instruct 只能作为 baseline,不算最终锁定策略。
3. **metadata 拼进 document 后就是正文**:qwen3-rerank 的 `documents` 是字符串数组,没有结构化 metadata 字段。`folder_path` / `heading_path` 一旦被拼到正文前面,模型会把它们当强文本信号,可能把"题库 / 评测 / anchor 汇总 / hard negative"抬到 direct evidence 前面。metadata format 变更必须 A/B,并同时看 `selected_recall@10`、hard-negative intrusion、token cost。
4. **标签 / 元数据优先用于检索前过滤或降权**:百炼 RAG 文档里的标签过滤和元数据搜索语义是"先结构化筛选,再向量检索 / rerank",不是把标签粗暴拼进文本。JobCopilot 可考虑给 chunk 标轻量 `content_type`,例如 `project_fact` / `interview_question_bank` / `eval_case` / `anchor_summary` / `hard_negative` / `generic_background`;项目事实 query 优先事实笔记,题库和评测样本通常只能作为 necessary context 或噪声候选。
5. **top_k 分两类**:provider rerank input 可以保留 top50,用于给排在 30+ 的正样本二次机会;final selected top10 不能硬塞满,应由 governance/blend 动态选 3-10 个干净 chunk。提高 final context K 会增加噪声、成本和生成端截断风险。
6. **chunk 完整性单独排查**:如果 evidence 已进 top_k 但 final context 缺前提、否定、数值、步骤,优先查 chunker / parent-doc / markdown 结构保护,不要误归因为 reranker。
7. **本地降权先做诊断,不要默认进主路**:2026-05-14 试过 intent × chunk-type soft adjustment,能把 hard-negative intrusion 从 4/12 压到 3/12、提升 MRR,但 `selected_recall@10` 和 final recall 下降。后续若继续做,report/trace 必须打印 `query_intent`、`chunk_type`、provider rank、adjustment、adjusted_score,先离线 A/B penalty 表,再决定是否进产品路径。

# 8. `interview_coach` suite(M2.1 DoD)

## 8.1 评什么

本 suite 守的是 **Agent harness 行为**:同一题多轮答不好时,系统是否能提示具体缺口、引导补答、对累计答案重评,并在合理条件下继续或退出。它不重新评 Judge label 质量;Judge 本身仍由 `answer_judge` suite 守门。

重点风险:

1. **分支错误**:该纠偏时直接下一题,或答得好仍反复追问。
2. **纠偏无效**:系统只说泛泛建议,没有指出漏掉的 reference point / fabricated claim / depth 缺口。
3. **无限循环**:没有固定 1 轮上限后,必须靠达标、用户跳过、无明显提升、偏题、token budget 等条件退出。
4. **长上下文污染**:多轮后把全量聊天塞进 prompt,挤掉 source chunks / reference points / unresolved gaps。
5. **追问幻觉**:纠偏 prompt 引入 source chunks 之外的新标准答案来源。

## 8.2 dataset schema

当前 M2.1 已落第一批最小流程 fixture:`evals/suites/interview_coach/dataset.flow_smoke.jsonl`,并已接入离线 runner:`apps/api/scripts/eval_interview_coach.py`。它固定 harness 行为标签,不触发真实 LLM,也不重新评 Judge label 质量;需要分数变化的 fixture 使用样本内给定 score history / stubbed Judge result。自然语言 `turn_type=auto` 分类质量不放进本 suite,只验证分流进入状态机后的结构行为。后续再按需要沉淀稳定版 `dataset.jsonl`。

每行是一个流程型 fixture:

```json
{
  "fixture_id": "coach_coverage_001",
  "query": "考考我 Outbox 和 MQ 的区别",
  "question": {
    "text": "Outbox 和 MQ 的核心差异是什么?",
    "reference_point_ids": ["rp_1", "rp_2", "rp_3"],
    "source_chunk_ids": [101, 102]
  },
  "turns": [
    {"role": "user", "text": "MQ 就是发消息,Outbox 也是发消息。"},
    {"role": "expected_agent", "expected_action": "remediate", "triggered_by": "coverage", "missing_reference_point_ids": ["rp_2", "rp_3"]},
    {"role": "user", "text": "补充:Outbox 先和业务事务一起落库,再异步投递。"}
  ],
  "expected_final": {
    "action": "ask_next",
    "min_coverage_score": 80,
    "max_fabricated_ratio": 0.1
  },
  "context_expectation": {
    "must_include": ["question", "source_chunks", "reference_points", "cumulative_answer", "unresolved_gaps"],
    "must_not_include": ["full_raw_transcript_when_over_budget"],
    "must_emit_events": ["context_pack_built"]
  },
  "notes": "测 coverage 缺口 → 补答 → 累计答案重评"
}
```

## 8.3 指标

| 指标 | 定义 | 阈值 |
|------|------|------|
| `branch_accuracy` | 每个 decision node 是否走到人工期望 action(`remediate` / `ask_next` / `summarize` / `finish`) | ≥ 0.8 |
| `remediation_target_accuracy` | 纠偏 prompt 的 `triggered_by`、缺口 id、claim id、depth 维度是否命中人工标签 | ≥ 0.8 |
| `cumulative_rejudge_pass` | 补答后 Judge 输入是否为累计答案,不是只评最后一句 | 1.0 |
| `loop_exit_pass` | 达标 / 用户跳过 / 连续提升很小 / 偏题 / token budget 场景是否正确退出 | 1.0 |
| `context_pack_pass` | context pack 是否保留必需字段并压缩旧轮次 | 1.0 |
| `hallucination_guard_pass` | 纠偏 prompt 是否只围绕 source chunks / reference_points / Judge gaps | 1.0 |
| `recovery_pass` | 从 `wait_user_answer` / `judge_answer` 后恢复能继续同一节点 | 1.0 |

`finish_session` 暂不单列 headline 指标,而是并入 `branch_accuracy / context_pack_pass / recovery_pass` 检查:runner 要确认整场总结只读取最新 Judge 结果、`answer_turns`、Judge gaps 与 `remediation_state`,不重新调用 AnswerJudge,并产出 `summary_context_pack / final_summary`。

## 8.4 覆盖矩阵

当前 `dataset.flow_smoke.jsonl` 已按 10 条覆盖:

| 类别 | 数量 | 期望 |
|------|------|------|
| 答得好 | 2 | 不纠偏,直接下一题 / 总结 |
| coverage 缺口 | 2 | 指出漏掉 reference point,补答后重评 / 总结 |
| fabricated 高 | 2 | 追问依据来源,不直接下一题 / 总结 |
| depth 缺维度 | 1 | 明确追问 tradeoff / why / boundary |
| 多轮无明显提升 | 1 | 退出纠偏并总结缺口 |
| 中途恢复 | 1 | 从 `wait_user_answer` 恢复 |
| 长上下文压缩 | 1 | 旧轮次摘要化,必需证据不丢 |

当前 runner 输出:

- 每条 fixture 的 `pass/fail`、实际 action、失败原因
- 聚合 `branch_accuracy / remediation_target_accuracy / cumulative_rejudge_pass / loop_exit_pass / context_pack_pass / hallucination_guard_pass / recovery_pass`
- 检查 `judge_score_history` 无明显提升退出、`context_compacted / prior_turn_summary / token_budget_exhausted` 长上下文治理、`session_events` 回放和 finish summary 结构
- 不连接真实 AnswerJudge label 质量评估;需要分数变化的 fixture 使用样本内给定 score history 或 stubbed Judge result

最近一次结果(`2026-05-17`,report:`evals/reports/interview-coach-flow-smoke-20260517-132154.md`):

- 10/10 fixtures pass
- `branch_accuracy / remediation_target_accuracy / cumulative_rejudge_pass / loop_exit_pass / context_pack_pass / hallucination_guard_pass / recovery_pass` 均为 `1.000`
- 本 suite 不评自然语言 `turn_type=auto` 分类质量;它只验证进入状态机后的分支、context pack、`session_events`、finish summary 等结构行为

# 9. 防回归约束

跨各 suite 通用规则(沿用 v1 LESSONS §8.2):

1. **每发现一类新 bug 必须加 1 条 fixture**:dogfood 跑出"Judge 把常识标 fabricated"这类 bug → answer_judge dataset 加 1 条对应样本,标 `bug_ref` 记 issue 号
2. **prompt 改版必须重跑全套对应 suite**:`quiz_generator` v1.0 → v1.1 之前,`eval_quiz_generator.py --prompt-version v1.1` 必须先跑通,verdict=pass 才允许在生产用 v1.1
3. **dataset 永不删除**:历史 fixture 即使过时也保留(可标 `deprecated: true`,但不删行)— 删了就失去回归保障
4. **report 落在 git history**:每次跑出的 `evals/reports/*.json` commit 进 git(不是 .gitignored — 修正 §2.1 那条),给后续 ablation 看

修正:`evals/reports/` **要进 git**,不 ignore。理由是 ablation / 对比模型 / prompt 演化必须有时间线。

# 10. dataset 标注协议

为了 kappa 算得准,标注规范要锁死(沿用 v1 LESSONS §8.2 "Prompt 是产品代码"延伸 — dataset 也是)。

## 10.1 单人标注 vs 双人标注

MVP **单人主标注 + 抽样复核**,不上双人 inter-rater agreement。理由:
- hybrid_search 的 ground_truth / unsafe boundary 由 Codex 主标注,用户抽样复核与争议裁决,降低人工负担
- quiz_generator / answer_judge 等主观语义标注仍以作者 dogfood 复核为准,标注语义边界跟产品方向高度对齐
- 30-50 条样本 × 2 人 = 60-100 人时,投入产出比低
- 单用户 dogfood 稳定后再考虑是否需要双人交叉;当前不进入后续主线

风险:单人 / Codex 标注偏差进 dataset。**对冲手段**:每条 fixture 必须填 `notes` 字段说"这条想测什么";Codex 低置信样本标 `needs_review: true`;用户抽查失败样本与争议样本。

## 10.2 Coverage label 边界

| label | 标注准则 |
|-------|--------|
| `hit` | 用户答完整覆盖 reference_point.text 的核心信息;允许同义改写 / 中英混合 / 顺序不同 |
| `partial` | 用户答提到 point 主题但缺关键细节(缺至少一个具体步骤 / 命名 / 数值)|
| `miss` | 用户答完全没提 point 主题(不是"提了但讲错"— 讲错走 partial)|

## 10.3 Fidelity label 边界

| label | 标注准则 |
|-------|--------|
| `supported` | 声明在 chunks 里能直接 / 间接找到对应文本支撑(同义视为支撑)|
| `inferred` | chunks 没明说,但属于该领域专业常识(如"Java 编译器叫 javac"chunks 没提也算 inferred)|
| `fabricated` | 跟 chunks 矛盾,或既非 chunks 内容又超出该领域专业常识(如把 ConcurrentHashMap 实现说成红黑树+CAS,chunks 没提且非常识)|

**关键判断**:`inferred` 和 `fabricated` 的边界是"这条声明,如果一个该领域稍有经验的从业者看到,会不会皱眉"。会皱眉 → fabricated;不会 → inferred。

## 10.4 Depth label 边界

| 维度 | covered=true 条件 |
|------|------------------|
| `tradeoff` | 用户答里出现至少一处对比 / 优劣讨论 / 替代方案讨论(关键词:"相比"、"优于"、"代价"、"另一种做法")|
| `why` | 用户答解释了"为什么这样设计"或"动机是什么"(关键词:"为了"、"因为"、"目的是")|
| `boundary` | 用户答提了适用 / 不适用场景,或边界条件(关键词:"在...时不适用"、"局限"、"前提是")|

# 11. 已锁定的关键决策

| 项 | 决策 | 备注 |
|----|------|------|
| 核心 suite | hybrid_search(M2 补测)/ quiz_generator(M2)/ answer_judge(M2)/ interview_coach(M2.1) | M2.1 重点守状态机分支、多轮纠偏、恢复、长上下文与幻觉治理 |
| Cohen's kappa 阈值 | Coverage / Fidelity 各 ≥ 0.7;Depth 用 accuracy ≥ 0.75 | Depth 二值 + 三维度,kappa 抖动大 |
| 不达标处理 | 改 prompt(bump version)+ 重跑;**不切模型** | 沿用 5-AGENT_DESIGN §2.1 |
| dataset 容量 | hybrid_search 50 条起;quiz_generator / answer_judge 各 30 条起;新 bug 进 dataset(永不删)| hybrid_search 要覆盖 chunk 边界 / 0 命中 / ablation,样本更宽 |
| 标注模式 | MVP 单人标注(作者自己),notes 字段守"测点清晰" | M4+ 双人交叉 |
| LLM cache | 评测路径**不禁** cache | 测 prompt 输出质量,不测速度 |
| 报告归档 | `evals/reports/*.json` 进 git | ablation / 时间线对比必需 |
| Fidelity claim 匹配 | 语义最近邻(embedding 余弦,阈值 0.6)| Judge claim 跟人工 claim 不可能逐字对齐 |
| Tool use 评测路径 | **不禁** + 跑 baseline(tool=off)对比 | 没 baseline 不知道工具有没有真实价值 |
| Trace 集成 | 评测 LLM 调用全进 Langfuse,tag `eval_run_id` + `fixture_id` | 不达标 5 分钟定位,vs 翻日志半小时 |
| jd_coverage suite | M2.5 最小指标脚本 | 不调 LLM;用小样本人工标签评覆盖分类和证据 P/R/MRR@k |
| jd_aggregator suite | 暂缓,不作为 M2.5 当前 DoD | 不恢复大而全的同义合并 eval runner;若未来恢复,再用 F1(集合)+ MAE(频次) |
| 简历相关 suite | 全部砍掉 | 不上传、不诊断、不改写、不参与出题 |
| dogfood 笔记 fixture | hybrid_search suite 强依赖固定笔记库(notes_fixture/ 或 notes_fixture.zip)| chunk_id 稳定才能比;chunker 改动后用 heading_path + anchors 复核 |
| 跑评测 CLI | `eval_<suite>.py --suite <dir> --prompt-version <v> --output <path>` | 各 suite 统一 |

# 12. 上次会话遗留的开放问题

- **EQ-01** 第一批样本怎么齐?hybrid_search 50 条、quiz_generator / answer_judge 各 30 条,偏向 dogfood 边跑边标(标 = 顺便审产品)
- **EQ-02** dataset PR review 谁审?MVP 没团队,作者自审 + 1 周 cooldown 再 merge?
- **EQ-03** Depth 三维度的"关键词触发"标注规则会不会过死?dogfood 跑完看实际答题语料再调

---

# 不在本文档范围

- prompt 全文 / 算分公式 → `docs/5-AGENT_DESIGN.md`
- 表 schema(reference_points / evidence JSONB) → `docs/3-DATA_MODEL.md`
- API 端点 → `docs/4-API_SPEC.md`
- scripts 怎么集成进 CI → `docs/8-ENGINEERING.md`
- 里程碑节奏 → `docs/7-ROADMAP.md`
