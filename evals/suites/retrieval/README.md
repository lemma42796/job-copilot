# Retrieval Ablation 评测(S21 子任务 4-A)

跑 v0(纯向量 top-K)vs v1(hybrid 双路 + RRF)在真实 profile chunks 上的
**Recall@10 / NDCG@10**,验证字符 n-gram lexical 路径是否真的补上了纯向量
召回的洞。

## 为什么不用 promptfoo

promptfoo 评 LLM prompt(input → LLM → output),retrieval 评的是
"input → retrieval 函数 → top-K chunks",不调 LLM,不进 promptfoo 体系。
用纯 Python 直接调 `apps/api/.../retrieval_service.py` 跑。

## 数据来源(无真实用户阶段)

需要"真实 profile + 真实 JD + 人工标注的 expected chunk_ids"。两条路:

| 来源 | 适合时机 |
|------|----------|
| **dogfood DB**(用户自己跑过的 match)| 现在,5-10 条 |
| **multi-persona synthetic fixture**(8-10 personas × 公开脱敏 JD)| 4-D 阶段,扩到 20+ 条 |

4-A 阶段先用 dogfood 数据,4-D 跟 `match_analysis` / `resume_generate` 评测集
一起扩到 20+ 条 multi-persona synthetic。

## 标注流程(dogfood 来源)

1. 选一个用户已经跑过 match 分析的 (profile_id, jd_id) 二元组
2. 从浏览器或后端日志里看 `build_match_query(jd)` 输出的 query string
3. 人工读 profile 全部 chunks,**标注**这条 query 应该召回的 chunk_ids 子集
   (评判标准:JD 字面提及的技能 / 经历应被召回;边缘相关的算可选)
4. 写到 `dataset.jsonl`(见 `dataset.example.jsonl`)

## 跑法

```bash
# 从项目根目录
uv run python apps/api/scripts/retrieval_eval.py \
    --dataset evals/suites/retrieval/dataset.jsonl \
    --report  evals/reports/retrieval-ablation-$(date +%Y-%m-%d).md
```

环境:连真实 dev DB(默认 `JOBCOPILOT_DATABASE_URL`)+ 真实 embedder
(默认 `JOBCOPILOT_LLM_PROVIDER=dashscope`,会消耗少量 token)。

## 指标

- **Recall@10** = `|relevant ∩ retrieved[:10]| / |relevant|`
- **NDCG@10** = `DCG@10 / IDCG@10`,DCG 项 = `rel_i / log2(i+1)`,二值 rel
- v0 vs v1 各算一遍,跨数据集求平均

## DoD

跑出报告里:
- v1 Recall@10 ≥ v0 Recall@10 + 0.10(绝对差 ≥ 10pp)
- v1 NDCG@10 ≥ v0 NDCG@10 + 0.05
- 至少 1 条样本是"v0 漏召、v1 命中" 的 case 留作回归 anchor

未达阈值 = lexical 路径要么切法不对、要么 query 端 ts_query 拼接不对、要么
数据本就纯英文 / 全在向量路命中 — 看 per-case ablation 调。
