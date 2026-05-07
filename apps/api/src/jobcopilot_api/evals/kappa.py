"""Cohen's kappa(EVAL_PLAN §6.3 — Judge 自身可靠性指标)。

`κ = (po - pe) / (1 - pe)`

`po` = observed agreement = 两个 rater 给出相同标签的样本比例。
`pe` = expected agreement by chance = 假设两 rater 独立按各自的边缘分布采样
       时碰巧一致的概率 = `Σ_c P_a(c) · P_b(c)`。

直接用 `po`(accuracy)反映可靠性会高估 — 比如所有样本都是同一类别时,
"全猜该类"也能达到 100% accuracy 但 κ → 0。EVAL_PLAN 要求 κ ≥ 0.7。

实现支持任意 hashable label(二分类 / 多分类皆可);二分类只是 categories
集合大小 = 2 的特例。Numeric 维度评测(如 resume_generate 6 维 0-100 分)
应先按桶离散化(如 ≥80 优 / 60-79 良 / <60 差)再算 kappa,该判桶逻辑由
调用方决定,不嵌入此函数。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Sequence


def cohen_kappa(rater_a: Sequence[Hashable], rater_b: Sequence[Hashable]) -> float:
    """Cohen's kappa for two raters with categorical labels.

    Returns κ ∈ [-1, 1]. Convention:
    - 1.0  完全一致
    - 0.0  仅达到 chance 水平
    - <0   比 chance 还差(实践中应当成数据/标注问题排查)

    `1 - pe == 0`(双方都全押同一标签)是退化情形,po 必为 1.0,
    返回 1.0(否则 0/0)。

    Raises `ValueError` if the two rater lists differ in length or are empty.
    """
    if len(rater_a) != len(rater_b):
        raise ValueError(
            f"rater_a / rater_b length mismatch: {len(rater_a)} vs {len(rater_b)}"
        )
    n = len(rater_a)
    if n == 0:
        raise ValueError("cannot compute kappa on empty input")

    agree = sum(1 for a, b in zip(rater_a, rater_b, strict=True) if a == b)
    po = agree / n

    counts_a = Counter(rater_a)
    counts_b = Counter(rater_b)
    categories = set(counts_a) | set(counts_b)
    pe = sum((counts_a[c] / n) * (counts_b[c] / n) for c in categories)

    if 1.0 - pe == 0.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1.0 - pe)


def confusion_matrix(
    rater_a: Sequence[Hashable],
    rater_b: Sequence[Hashable],
) -> dict[tuple[Hashable, Hashable], int]:
    """`(label_a, label_b) → count`. Use to debug disagreements before
    re-tuning Judge prompt — kappa alone tells you it's broken, not where."""
    if len(rater_a) != len(rater_b):
        raise ValueError(
            f"rater_a / rater_b length mismatch: {len(rater_a)} vs {len(rater_b)}"
        )
    cm: dict[tuple[Hashable, Hashable], int] = {}
    for a, b in zip(rater_a, rater_b, strict=True):
        cm[(a, b)] = cm.get((a, b), 0) + 1
    return cm
