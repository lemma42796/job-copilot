"""JdAggregator 编排(M2.5)— 三阶段流水线 + 学习路径生成。

流水线(docs/TECH_DESIGN.md,单次 ≤ 200 条 JD):
1. 分批 reduce(LLM,thinking off,temperature 0.3)
2. 二次 reduce / merge(LLM)
3. Python 重算频次(frequency.py)
4. 学习路径生成(LLM,temperature 0.5)
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

from jobcopilot_api.agents.jd_aggregator.frequency import recompute_frequency
from jobcopilot_api.agents.jd_aggregator.prompts import (
    PROMPT_NAME_BATCH,
    PROMPT_NAME_MERGE,
    PROMPT_NAME_PATH,
    SYSTEM_BATCH,
    SYSTEM_LEARNING_PATH,
    SYSTEM_MERGE,
    render_user_batch,
    render_user_learning_path,
    render_user_merge,
)
from jobcopilot_api.infra.llm import get_llm_client
from jobcopilot_api.llm.client import LLMClient, LLMResult
from jobcopilot_api.llm.tiers import Tier
from jobcopilot_api.schemas.agents.jd_aggregator import (
    JdAggregateInput,
    JdAggregateOutput,
    JdLearningPathOutput,
    JdRequirementReduceOutput,
    ParsedJdForAggregation,
    RawRequirementItem,
    Requirement,
    RequirementCategory,
    RequirementCandidate,
)

RAW_JD_BATCH_PROMPT_CHAR_BUDGET = 18_000
LEARNING_PATH_REQUIREMENT_LIMIT = 80
REDUCE_TEMPERATURE = 0.3
LEARNING_PATH_TEMPERATURE = 0.5
REDUCE_TIMEOUT_S = 120.0
LEARNING_PATH_TIMEOUT_S = 90.0
_SCHEMA_PROMPT_PREFIX = (
    "\n\nRespond with a single JSON object that matches this schema:\n"
)

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


class _CallStats:
    def __init__(self) -> None:
        self.tokens_in = 0
        self.tokens_out = 0
        self.cached_tokens = 0
        self.cost_cny = Decimal("0")

    def add(self, result: LLMResult) -> None:
        self.tokens_in += result.tokens_in
        self.tokens_out += result.tokens_out
        self.cached_tokens += result.cached_tokens
        self.cost_cny += result.cost_cny

    def cache_hit_rate(self) -> Decimal | None:
        if self.tokens_in <= 0:
            return None
        return (Decimal(self.cached_tokens) / Decimal(self.tokens_in)).quantize(
            Decimal("0.001")
        )


async def run(
    inp: JdAggregateInput,
    *,
    llm: LLMClient | None = None,
    on_progress: ProgressCallback | None = None,
) -> JdAggregateOutput:
    client = llm or get_llm_client()
    stats = _CallStats()
    jd_ids = {item.jd_id for item in inp.parsed_jds}
    raw_items = _extract_raw_items(inp.parsed_jds)
    if not raw_items:
        return JdAggregateOutput(
            aggregated_requirements=[],
            learning_path_md="## 你的学习路径\n\n本次 JD 没有可聚合的结构化要求。",
        )

    fallback_items = _exact_candidates(raw_items)
    batches = _batch_jds(inp.parsed_jds)
    partials: list[RequirementCandidate] = []
    for index, jds in enumerate(batches, start=1):
        if on_progress is not None:
            await on_progress(
                {"phase": "reducing_batch", "batch": index, "total": len(batches)}
            )
        result = await client.complete(
            feature=PROMPT_NAME_BATCH,
            tier=Tier.CHEAP,
            system=SYSTEM_BATCH,
            user=render_user_batch(
                batch_index=index,
                total_batches=len(batches),
                jds=jds,
            ),
            response_schema=JdRequirementReduceOutput,
            temperature=REDUCE_TEMPERATURE,
            timeout_s=REDUCE_TIMEOUT_S,
        )
        stats.add(result)
        reduce_output = _expect_parsed(result, JdRequirementReduceOutput)
        partials.extend(_normalize_candidates(reduce_output.requirements, jd_ids))

    if not partials:
        partials = fallback_items

    if len(batches) > 1 and len(partials) > 1:
        if on_progress is not None:
            await on_progress({"phase": "merging"})
        merge_result = await client.complete(
            feature=PROMPT_NAME_MERGE,
            tier=Tier.CHEAP,
            system=SYSTEM_MERGE,
            user=render_user_merge(partials),
            response_schema=JdRequirementReduceOutput,
            temperature=REDUCE_TEMPERATURE,
            timeout_s=REDUCE_TIMEOUT_S,
        )
        stats.add(merge_result)
        merge_output = _expect_parsed(merge_result, JdRequirementReduceOutput)
        merged = _normalize_candidates(merge_output.requirements, jd_ids)
        if merged:
            partials = merged

    if on_progress is not None:
        await on_progress({"phase": "frequency_recompute"})
    requirements = _finalize_requirements(partials, total_jds=len(inp.parsed_jds))

    if on_progress is not None:
        await on_progress({"phase": "learning_path_gen"})
    path_result = await client.complete(
        feature=PROMPT_NAME_PATH,
        tier=Tier.CHEAP,
        system=SYSTEM_LEARNING_PATH,
        user=render_user_learning_path(
            requirements=requirements[:LEARNING_PATH_REQUIREMENT_LIMIT],
            jd_count=len(inp.parsed_jds),
        ),
        response_schema=JdLearningPathOutput,
        temperature=LEARNING_PATH_TEMPERATURE,
        timeout_s=LEARNING_PATH_TIMEOUT_S,
    )
    stats.add(path_result)
    path_output = _expect_parsed(path_result, JdLearningPathOutput)
    learning_path_md = path_output.learning_path_md.strip() or _fallback_learning_path(
        requirements
    )

    return JdAggregateOutput(
        aggregated_requirements=requirements,
        learning_path_md=learning_path_md,
        total_tokens_in=stats.tokens_in,
        total_tokens_out=stats.tokens_out,
        total_cost_cny=stats.cost_cny,
        cache_hit_rate=stats.cache_hit_rate(),
    )


def _extract_raw_items(
    parsed_jds: list[ParsedJdForAggregation],
) -> list[RawRequirementItem]:
    items: list[RawRequirementItem] = []
    for jd in parsed_jds:
        seen: set[tuple[str, str]] = set()
        parsed = jd.parsed
        _extend_items(items, seen, jd.jd_id, "职责", parsed.responsibilities)
        _extend_items(items, seen, jd.jd_id, "硬技能", parsed.hard_skills)
        _extend_items(items, seen, jd.jd_id, "软技能", parsed.soft_skills)
        if parsed.experience_years:
            _extend_items(items, seen, jd.jd_id, "经验", [parsed.experience_years])
        if parsed.education:
            _extend_items(items, seen, jd.jd_id, "学历", [parsed.education])
    return items


def _extend_items(
    items: list[RawRequirementItem],
    seen: set[tuple[str, str]],
    jd_id: int,
    category: RequirementCategory,
    values: list[str],
) -> None:
    for value in values:
        text = " ".join(value.split())
        key = (category, text)
        if not text or key in seen:
            continue
        seen.add(key)
        items.append(
            RawRequirementItem(
                jd_id=jd_id,
                category=category,
                text=text,
            )
        )


def _batch_jds(
    jds: list[ParsedJdForAggregation],
) -> list[list[ParsedJdForAggregation]]:
    batches: list[list[ParsedJdForAggregation]] = []
    current: list[ParsedJdForAggregation] = []
    for jd in jds:
        candidate = [*current, jd]
        if current and _reduce_prompt_chars(candidate) > RAW_JD_BATCH_PROMPT_CHAR_BUDGET:
            batches.append(current)
            current = [jd]
            continue
        current = candidate
    if current:
        batches.append(current)
    return batches


def _reduce_prompt_chars(jds: list[ParsedJdForAggregation]) -> int:
    user = render_user_batch(batch_index=999, total_batches=999, jds=jds)
    schema = json.dumps(
        JdRequirementReduceOutput.model_json_schema(),
        ensure_ascii=False,
        indent=2,
    )
    return len(SYSTEM_BATCH) + len(user) + len(_SCHEMA_PROMPT_PREFIX) + len(schema)


def _expect_parsed(result: LLMResult, schema: type[Any]) -> Any:
    if isinstance(result.parsed, schema):
        return result.parsed
    raise ValueError(f"{result.feature} did not return {schema.__name__}")


def _normalize_candidates(
    candidates: list[RequirementCandidate],
    allowed_jd_ids: set[int],
) -> list[RequirementCandidate]:
    normalized: list[RequirementCandidate] = []
    for candidate in candidates:
        text = _clean_text(candidate.canonical_text)
        supporting_ids = sorted(
            {jd_id for jd_id in candidate.supporting_jd_ids if jd_id in allowed_jd_ids}
        )
        if not text or not supporting_ids:
            continue
        raw_phrases = _dedupe(
            [_clean_text(item) for item in [*candidate.raw_phrases, text]]
        )
        normalized.append(
            candidate.model_copy(
                update={
                    "canonical_text": text,
                    "raw_phrases": raw_phrases,
                    "supporting_jd_ids": supporting_ids,
                }
            )
        )
    return normalized


def _exact_candidates(items: list[RawRequirementItem]) -> list[RequirementCandidate]:
    grouped: dict[tuple[RequirementCategory, str], set[int]] = {}
    raw_phrases: dict[tuple[RequirementCategory, str], set[str]] = {}
    for item in items:
        text = _clean_text(item.text)
        if not text:
            continue
        key = (item.category, text.casefold())
        grouped.setdefault(key, set()).add(item.jd_id)
        raw_phrases.setdefault(key, set()).add(text)
    candidates: list[RequirementCandidate] = []
    for (category, normalized_text), jd_ids in grouped.items():
        phrases = raw_phrases[(category, normalized_text)]
        candidates.append(
            RequirementCandidate(
                canonical_text=sorted(phrases)[0],
                category=category,
                raw_phrases=sorted(phrases),
                supporting_jd_ids=sorted(jd_ids),
            )
        )
    return candidates


def _finalize_requirements(
    candidates: list[RequirementCandidate],
    *,
    total_jds: int,
) -> list[Requirement]:
    merged: dict[tuple[str, str], RequirementCandidate] = {}
    for candidate in candidates:
        key = (candidate.category, candidate.canonical_text.casefold())
        existing = merged.get(key)
        if existing is None:
            merged[key] = candidate
            continue
        merged[key] = existing.model_copy(
            update={
                "raw_phrases": _dedupe(
                    [*existing.raw_phrases, *candidate.raw_phrases]
                ),
                "supporting_jd_ids": sorted(
                    set(existing.supporting_jd_ids) | set(candidate.supporting_jd_ids)
                ),
            }
        )

    requirements = [
        Requirement(
            id=f"tmp_{index}",
            canonical_text=candidate.canonical_text,
            category=candidate.category,
            raw_phrases=candidate.raw_phrases,
            supporting_jd_ids=candidate.supporting_jd_ids,
            frequency=0,
        )
        for index, candidate in enumerate(merged.values(), start=1)
    ]
    recomputed = recompute_frequency(requirements, total_jds)
    ordered = sorted(
        recomputed,
        key=lambda req: (
            -req.frequency,
            _category_order(req.category),
            req.canonical_text.casefold(),
        ),
    )
    return [
        req.model_copy(update={"id": f"req_{index}"})
        for index, req in enumerate(ordered, start=1)
    ]


def _category_order(category: str) -> int:
    order = {"硬技能": 0, "职责": 1, "经验": 2, "软技能": 3, "学历": 4}
    return order.get(category, 99)


def _clean_text(value: str) -> str:
    return " ".join(value.split()).strip()


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _fallback_learning_path(requirements: list[Requirement]) -> str:
    lines = ["## 你的学习路径", ""]
    for req in requirements[:20]:
        pct = int(round(req.frequency * 100))
        lines.append(f"- {req.canonical_text}({pct}%)")
    return "\n".join(lines)
