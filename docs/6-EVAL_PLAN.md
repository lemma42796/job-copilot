---
title: EVAL PLAN - JobCopilot v2(评测套件 + Cohen's kappa 守门)
owner: lemma42796
last_updated: 2026-05-12
purpose: 锁评测套件结构、dataset 标注规范、kappa 算法、跑法、不达标处理流程
---

# 1. 一句话总览

六套评测,每套对应一个 DoD:

| Suite | M? DoD | 守什么 | 阈值 |
|-------|--------|-------|------|
| `hybrid_search` | M2 补测 | RAG 召回 + final context 干净度 + chunk 语义完整性 | final_context_recall ≥ 0.95 / final_context_precision ≥ 0.70 |
| `quiz_generator` | M2 | 出题结构合规 + type_mix 决策合理 | 合规率 ≥ 0.95 / type_mix 一致率 ≥ 0.7 |
| `answer_judge` | M2 | 三层 label 跟人工标注一致性 | Cohen's `κ ≥ 0.7`(三层独立) |
| `interview_coach` | M2.1 | Agent 状态机是否走到人工期望分支 | branch accuracy ≥ 0.8 / recovery case 全过 |
| `jd_aggregator` | M2.5 | 同义合并准确 + 频次重算正确 | F1 ≥ 0.85 / freq MAE ≤ 0.03 |
| `resume_advisor` | M3 | anchored ratio + 锚点正确率 + 永不替写文案 | anchored ≥ 0.7 / forbidden 触发率 ≤ 0.05 |

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
│   ├── jd_aggregator/
│   │   ├── dataset.jsonl
│   │   └── README.md
│   └── resume_advisor/
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
├── eval_jd_aggregator.py
└── eval_resume_advisor.py
```

## 2.2 dataset.jsonl 通用约定

- 一行一个 JSON,UTF-8 无 BOM,中文不转义(`ensure_ascii=False`)
- 每条样本必带:`id`(`q001` / `j001` / `s001` 风格)、`source`(标注来源,如 `dogfood_2026_05`)、`bug_ref`(可选,关联 bug 样本就填 issue 号)
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
- 简历单条记录(份数)

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

# 5. `jd_aggregator` suite(M2.5 DoD)

## 5.1 评什么

测 JdAggregator 多 JD 一键分析的 **同义合并准确率** + **频次重算正确性**。不算 kappa(canonical 不是 categorical label,是文本去重),改用集合 / 序列指标。

| 指标 | 定义 | 阈值 |
|------|------|----|
| **同义合并准确率** | Judge 输出的 canonical 集合 vs 人工标 canonical 集合的 F1 score(集合元素相等性比对)| ≥ 0.85 |
| **频次重算误差** | Python 重算 frequency vs 人工 ground truth frequency 的 MAE | ≤ 0.03 |
| **结构合规率** | aggregated_requirements 全部字段非空 + supporting_jd_ids 非空 | ≥ 0.98 |

## 5.2 dataset.jsonl schema

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

## 5.3 数据集容量

**MVP 起步 30 条**,组合多种场景:

| # | 场景 | 期望测什么 |
|---|------|---------|
| 1-10 | 简单同义(< 10 条 JD,清晰 1:1 同义)| 基础合并 |
| 11-15 | 跨 batch 同义(50+ 条 JD,需 hierarchical reduce 跨 batch 合并)| 二次 reduce 守门 |
| 16-20 | 频次边界(同一 canonical 在某条 JD 出现 ≥ 1 次但只算 1 次)| Python 重算 dedup 守门 |
| 21-25 | 噪声词(BOSS 平台标签 / 学历词混入 hard_skills)| 不算 canonical 进单独 group |
| 26-30 | 极端规模(200 条 JD 上限场景)| reduce 拓扑跑通 + P95 ≤ 60s |

## 5.4 跑法 + 阈值

```
python -m jobcopilot_api.scripts.eval_jd_aggregator \
    --suite evals/suites/jd_aggregator \
    --prompt-version v1.0 \
    --output evals/reports/2026-05-08_jd_agg_v1.0.json
```

**不达标处理**:类似 §3.5,先看 per_fixture trace(Langfuse 按 fixture_id 过滤);改 prompt 重跑;调 Stage 1 / Stage 2 拆 batch 大小。

# 6. `resume_advisor` suite(M3 DoD)

## 6.1 评什么

简历诊断难有"绝对正确的标准答案"(每条建议是否合理是主观的),所以**不强求 kappa**,只测**结构指标**:

| 指标 | 定义 | 阈值 |
|------|------|----|
| **anchored ratio** | `anchored_count / (anchored_count + unanchored_count)` | ≥ 0.7 |
| **forbidden_pattern 触发率** | service 层检测到 LLM 输出"建议改写为 X"等违规句式的比例 | ≤ 0.05 |
| **resume_position 锚点正确率** | LLM 给的 resume_position vs 人工标(按段落 §N 比对一致) | ≥ 0.8 |
| **诊断陈述合理度**(主观) | 作者人工审 30 条 fixture 的 diagnosis 文本,合理打 1 不合理打 0 | ≥ 0.8 |

## 6.2 dataset.jsonl schema

```json
{
  "id": "ra001",
  "source": "dogfood_2026_06",
  "input": {
    "requirements": [...]   // 来自一份真实 jd_analyses 报告(脱敏后)
    "resume_chunks": [...]  // 真实简历段落(作者本人或脱敏样本)
  },
  "ground_truth": {
    "anchored_expected_ratio": 0.75,    // 这份输入预期 anchored 比例 ~75%
    "key_anchors": [
      {"req_id": "req_1", "expected_resume_position": "§3"},
      {"req_id": "req_5", "expected_resume_position": "§4 项目 A"}
    ],
    "forbidden_must_not_appear": true   // 这条 fixture 不应触发任何 forbidden_pattern
  },
  "notes": "测高频要求锚到正确简历段落;反证 LLM 不替写文案"
}
```

## 6.3 数据集容量

**MVP 起步 15 条**(简历样本难收集,人工审每条工作量大):

| # | 场景 |
|---|------|
| 1-5 | 简历跟 JD 强匹配(预期 anchored ratio > 0.8)|
| 6-10 | 简历跟 JD 弱匹配(预期 anchored ratio 0.4-0.6;unanchored 主要是 missing)|
| 11-13 | 故意省略简历段落(测 LLM 不要乱挂位置)|
| 14-15 | 红队样本:用 prompt injection 试图让 LLM 输出 "建议改写为 'XXX'"(测 forbidden_pattern 拦截)|

## 6.4 跑法 + 阈值

```
python -m jobcopilot_api.scripts.eval_resume_advisor \
    --suite evals/suites/resume_advisor \
    --prompt-version v1.0 \
    --output evals/reports/2026-05-08_resume_advisor_v1.0.json
```

**dogfood 自查硬约束**:跑出任意一条触发 forbidden_pattern 的 fixture → **prompt 漏洞,M3 DoD 不通过**(必须修 prompt + 加 forbidden_pattern + 重跑直到 0 触发)。

# 7. `hybrid_search` suite(M2 补测 / M2.1 前置)

## 7.1 评什么

测 **用户主题 query → query rewrite → hybrid 召回 → reranker → parent-doc 扩展 → 最终 chunks** 这条完整 RAG 链路是否满足三件事:

1. **召回不漏**:该出现的 evidence 能进入候选、重排后靠前,最终上下文覆盖完整。
2. **上下文不脏**:最终喂给 QuizGenerator / AnswerJudge 的 chunks 以直接证据和必要上下文为主,避免靠堆无关 chunk 换 recall。
3. **切片不断义**:最终喂给 QuizGenerator / AnswerJudge 的 chunks 不把关键前提、否定、数值、表格、代码块、列表结构切坏。

本 suite 是 M2 已完成功能链路后的质量补测,也是 M2.1 `InterviewCoachAgent.retrieve_context` 的前置守门。它不改变产品边界:当前只评程序员面试笔记,不声明医学 / 法律等高风险领域可用。

## 7.2 分阶段指标

| 指标 | 定义 | 阈值 |
|------|------|------|
| `candidate_recall@50` | expected evidence 进入 query rewrite + hybrid + RRF 后候选 Top-50 的样本比例 | ≥ 0.98 |
| `rerank_recall@10` | expected evidence 进入 reranker Top-10 的样本比例 | ≥ 0.90 |
| `mrr@10` | reranker Top-10 中首个 expected evidence 的 mean reciprocal rank | ≥ 0.60 |
| `final_context_recall` | parent-doc 扩展后最终 chunks 覆盖 expected evidence 的样本比例 | ≥ 0.95 |
| `final_context_precision` | 非 0 命中样本中,final context 内由 Codex 判为直接证据 / 必要上下文的 chunk 数 / final context chunk 总数 | ≥ 0.70 |
| `zero_hit_precision` | 0 命中 / 近邻干扰样本没有被错误拿去出题的比例 | ≥ 0.90 |
| `unsafe_boundary_rate` | 人工审查中出现"关键前提 / 否定 / 数值 / 表格 / 代码块被切断且 final context 未补回"的比例 | ≤ 0.05 |

解释:

- `candidate_recall@50` 守"别漏":reranker 只能重排候选,不能找回没召回的 chunk。
- `rerank_recall@10` / `mrr@10` 守"排准":QuizGenerator 不应吃大量边缘候选。
- `final_context_recall` 守"最终可用":真正决定出题 / 评分质量的是 parent-doc 后的上下文,不是中间候选。
- `final_context_precision` 守"最终干净":防止 parent-doc / top_k 扩太宽,把无关 chunk 塞给 LLM。
- `unsafe_boundary_rate` 是 chunker 质量指标,发现问题优先调 chunker / parent-doc,不是换模型。

## 7.3 Ablation 矩阵

每条样本都跑以下路径,报告里并排输出:

| 路径 | 目的 |
|------|------|
| `vector_only` | 看 dense embedding 语义召回单路上限 |
| `lexical_only` | 看 char_ngram / 英文 token 对术语、拼写、缩写的守门能力 |
| `hybrid_no_rewrite` | 看 RRF 融合本身收益 |
| `hybrid_with_rewrite` | 看 query rewrite 是否真实提召回,以及是否引入跑题 |
| `hybrid_with_rewrite_rerank` | 看 reranker 是否把正确 evidence 推到 Top-10 |
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
    "expected_chunk_ids": [12, 15],
    "expected_heading_paths": [["Java", "并发", "synchronized"]],
    "evidence_anchors": ["偏向锁", "轻量级锁", "重量级锁"],
    "expected_zero_hit": false,
    "risk_tags": ["cross_heading", "ordered_steps"]
  }
}
```

字段约定:

- `expected_chunk_ids`:固定 fixture 库导入后的稳定 chunk id;用于精确算 recall / mrr。
- `expected_heading_paths`:chunker 调参导致 chunk_id 漂移时的人工兜底定位。
- `evidence_anchors`:短原文锚点,用于人工审查 final context 是否真包含关键证据;不要贴长段原文。
- `expected_zero_hit`:无该主题 / 近邻干扰样本置 `true`,用于测 0 命中守门。
- `risk_tags`:标注该样本主要测什么,取值建议:`exact_term` / `synonym` / `typo` / `mixed_cn_en` / `cross_heading` / `numeric` / `negation` / `table` / `code` / `ordered_steps` / `zero_hit`。

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
    "candidate_recall@50": 0.98,
    "rerank_recall@10": 0.92,
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
      "rerank_top_ids": [12, 15],
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

`failure_reason` 只能取固定枚举,便于统计:

| failure_reason | 含义 | 优先排查 |
|----------------|------|----------|
| `rewrite_drift` | rewrite 扩太宽,候选跑题 | query_rewriter prompt / expanded query 数量 |
| `vector_miss` | dense 路漏召回 | embedding 模型 / chunk 内容粒度 |
| `lexical_miss` | lexical 路漏召回 | char_ngram / tokenization / tsvector |
| `rerank_drop` | hybrid 命中但 reranker 掉出 Top-10 | reranker top_k / instruct / 候选噪声 |
| `parent_context_missing` | Top-10 命中但 final context 缺前提 | parent-doc 策略 |
| `final_context_noise` | final context 召回到了证据,但混入过多无关 chunk | parent-doc 扩展范围 / final context top_k / 去重与 source diversity |
| `chunk_boundary_unsafe` | 切片打断关键语义 | chunker / overlap / 结构化 markdown 保护 |
| `zero_hit_false_positive` | 无主题样本仍召回并出题 | 0 命中阈值 / rerank score threshold |

`unsafe_boundary_rate` 评审职责:

- 由 Codex 逐条审查 `risk_tags` 包含 `cross_heading` / `numeric` / `negation` / `table` / `code` / `ordered_steps` 的样本,对 final context 标 `boundary_safe=true/false`。
- 用户不做全量人工审查,只抽查 Codex 标为 `boundary_safe=false` 或 `needs_review=true` 的样本。
- 判 `boundary_safe=false` 时必须给出 `failure_reason` + 最小原文证据,说明是前提、否定、数值、表格、代码块还是步骤被切断。

## 7.7 跑法

```
python -m jobcopilot_api.scripts.eval_hybrid_search \
    --suite evals/suites/hybrid_search \
    --output evals/reports/2026-05-12_hybrid_search_baseline.json
```

本项目测试 / 自动化验证由用户手动跑。实现脚本后,Codex 只说明跑法、预期字段和判定标准;除非用户明确说"跑评测",否则不主动执行。

## 7.8 不达标处理顺序

1. 先看 per-case `failure_reason`,不要只看总均值。
2. `candidate_recall@50` 不达标:优先调 query rewrite / hybrid top_k / tokenization / embedding,不要调 reranker。
3. `rerank_recall@10` 不达标:优先调 reranker `top_k` / instruct / 候选去噪。
4. `final_context_recall` 或 `unsafe_boundary_rate` 不达标:优先调 parent-doc / chunker / overlap / markdown 结构保护。
5. `final_context_precision` 不达标:优先收紧 parent-doc 扩展范围 / final context top_k / 去重与 source diversity,不要靠减少 expected evidence 标注来抬分。
6. `zero_hit_precision` 不达标:增加 rerank score / source diversity / expected evidence 阈值,不要让 LLM 兜底编。
7. 调参后重跑全 suite,报告进 git history。

# 8. 防回归约束

跨各 suite 通用规则(沿用 v1 LESSONS §8.2):

1. **每发现一类新 bug 必须加 1 条 fixture**:dogfood 跑出"Judge 把常识标 fabricated"这类 bug → answer_judge dataset 加 1 条对应样本,标 `bug_ref` 记 issue 号
2. **prompt 改版必须重跑全套对应 suite**:`quiz_generator` v1.0 → v1.1 之前,`eval_quiz_generator.py --prompt-version v1.1` 必须先跑通,verdict=pass 才允许在生产用 v1.1
3. **dataset 永不删除**:历史 fixture 即使过时也保留(可标 `deprecated: true`,但不删行)— 删了就失去回归保障
4. **report 落在 git history**:每次跑出的 `evals/reports/*.json` commit 进 git(不是 .gitignored — 修正 §2.1 那条),给后续 ablation 看

修正:`evals/reports/` **要进 git**,不 ignore。理由是 ablation / 对比模型 / prompt 演化必须有时间线。

# 9. dataset 标注协议

为了 kappa 算得准,标注规范要锁死(沿用 v1 LESSONS §8.2 "Prompt 是产品代码"延伸 — dataset 也是)。

## 9.1 单人标注 vs 双人标注

MVP **单人主标注 + 抽样复核**,不上双人 inter-rater agreement。理由:
- hybrid_search 的 ground_truth / unsafe boundary 由 Codex 主标注,用户抽样复核与争议裁决,降低人工负担
- quiz_generator / answer_judge 等主观语义标注仍以作者 dogfood 复核为准,标注语义边界跟产品方向高度对齐
- 30-50 条样本 × 2 人 = 60-100 人时,投入产出比低
- M3 SaaS 化后再考虑双人交叉(M4+)

风险:单人 / Codex 标注偏差进 dataset。**对冲手段**:每条 fixture 必须填 `notes` 字段说"这条想测什么";Codex 低置信样本标 `needs_review: true`;用户抽查失败样本与争议样本。

## 9.2 Coverage label 边界

| label | 标注准则 |
|-------|--------|
| `hit` | 用户答完整覆盖 reference_point.text 的核心信息;允许同义改写 / 中英混合 / 顺序不同 |
| `partial` | 用户答提到 point 主题但缺关键细节(缺至少一个具体步骤 / 命名 / 数值)|
| `miss` | 用户答完全没提 point 主题(不是"提了但讲错"— 讲错走 partial)|

## 9.3 Fidelity label 边界

| label | 标注准则 |
|-------|--------|
| `supported` | 声明在 chunks 里能直接 / 间接找到对应文本支撑(同义视为支撑)|
| `inferred` | chunks 没明说,但属于该领域专业常识(如"Java 编译器叫 javac"chunks 没提也算 inferred)|
| `fabricated` | 跟 chunks 矛盾,或既非 chunks 内容又超出该领域专业常识(如把 ConcurrentHashMap 实现说成红黑树+CAS,chunks 没提且非常识)|

**关键判断**:`inferred` 和 `fabricated` 的边界是"这条声明,如果一个该领域稍有经验的从业者看到,会不会皱眉"。会皱眉 → fabricated;不会 → inferred。

## 9.4 Depth label 边界

| 维度 | covered=true 条件 |
|------|------------------|
| `tradeoff` | 用户答里出现至少一处对比 / 优劣讨论 / 替代方案讨论(关键词:"相比"、"优于"、"代价"、"另一种做法")|
| `why` | 用户答解释了"为什么这样设计"或"动机是什么"(关键词:"为了"、"因为"、"目的是")|
| `boundary` | 用户答提了适用 / 不适用场景,或边界条件(关键词:"在...时不适用"、"局限"、"前提是")|

# 10. 已锁定的关键决策

| 项 | 决策 | 备注 |
|----|------|------|
| 核心 suite | hybrid_search(M2 补测)/ quiz_generator(M2)/ answer_judge(M2) | hybrid_search 先补 RAG 召回 + chunk 完整性 |
| Cohen's kappa 阈值 | Coverage / Fidelity 各 ≥ 0.7;Depth 用 accuracy ≥ 0.75 | Depth 二值 + 三维度,kappa 抖动大 |
| 不达标处理 | 改 prompt(bump version)+ 重跑;**不切模型** | 沿用 5-AGENT_DESIGN §2.1 |
| dataset 容量 | hybrid_search 50 条起;quiz_generator / answer_judge 各 30 条起;新 bug 进 dataset(永不删)| hybrid_search 要覆盖 chunk 边界 / 0 命中 / ablation,样本更宽 |
| 标注模式 | MVP 单人标注(作者自己),notes 字段守"测点清晰" | M4+ 双人交叉 |
| LLM cache | 评测路径**不禁** cache | 测 prompt 输出质量,不测速度 |
| 报告归档 | `evals/reports/*.json` 进 git | ablation / 时间线对比必需 |
| Fidelity claim 匹配 | 语义最近邻(embedding 余弦,阈值 0.6)| Judge claim 跟人工 claim 不可能逐字对齐 |
| Tool use 评测路径 | **不禁** + 跑 baseline(tool=off)对比 | 没 baseline 不知道工具有没有真实价值 |
| Trace 集成 | 评测 LLM 调用全进 Langfuse,tag `eval_run_id` + `fixture_id` | 不达标 5 分钟定位,vs 翻日志半小时 |
| jd_aggregator 不算 kappa | 用 F1(集合)+ MAE(频次)指标 | canonical 不是 categorical label,kappa 不适用 |
| resume_advisor 不强求 kappa | anchored ratio + forbidden 触发率 + 主观合理度 | 简历建议主观度高,结构指标 + 自查更稳 |
| forbidden_pattern 触发 | M3 DoD 硬卡(0 容忍)| 触发即 prompt 漏洞;修 prompt + 加 pattern + 重跑直到 0 触发 |
| dogfood 笔记 fixture | hybrid_search suite 强依赖固定笔记库(notes_fixture/ 或 notes_fixture.zip)| chunk_id 稳定才能比;chunker 改动后用 heading_path + anchors 复核 |
| 跑评测 CLI | `eval_<suite>.py --suite <dir> --prompt-version <v> --output <path>` | 各 suite 统一 |

# 11. 上次会话遗留的开放问题

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
