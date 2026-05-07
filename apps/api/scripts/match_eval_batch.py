"""Batch run match_analyst on (jd_id, profile_id) pairs from a yaml spec.

Output: jsonl lines suitable to drop into evals/suites/match_analysis/dataset.jsonl
after a human evaluator (Opus 4.7 in Claude Code session) fills the human_* fields.

Spec format (yaml):
    samples:
      - jd_id: 24
        profile_id: 17
        persona_label: synthetic - 三年前端 + 一年 AI 应用转入
      - ...

Usage:
  uv run python apps/api/scripts/match_eval_batch.py \\
    --spec evals/specs/match_analysis-w8-bootstrap.yaml \\
    --output /tmp/match-pending.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import sqlalchemy as sa
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "apps" / "api" / "src"))

from jobcopilot_api.agents.match_analyst import analyze_match  # noqa: E402
from jobcopilot_api.infra.db import get_sessionmaker  # noqa: E402
from jobcopilot_api.infra.embedder import get_embedder  # noqa: E402
from jobcopilot_api.infra.llm import get_llm_client  # noqa: E402
from jobcopilot_api.infra.prompts import load_prompt_versions  # noqa: E402
from jobcopilot_api.models import Jd, Profile, ProfileChunk  # noqa: E402
from jobcopilot_api.services.retrieval_service import (  # noqa: E402
    build_match_query,
    hybrid_retrieve_for_match,
)

_GRANULARITY_TITLE_ZH = {
    "summary": "总结",
    "experience": "工作经历",
    "project": "项目",
    "education": "教育",
    "skill": "技能",
}
_GRANULARITY_ORDER = ["summary", "experience", "project", "education", "skill"]


async def _build_profile_summary(sessionmaker, *, profile_id: int) -> str:
    async with sessionmaker() as session:
        profile = await session.scalar(sa.select(Profile).where(Profile.id == profile_id))
        if profile is None:
            raise SystemExit(f"profile {profile_id} not found")
        chunks = (
            (
                await session.execute(
                    sa.select(ProfileChunk)
                    .where(ProfileChunk.profile_id == profile_id)
                    .order_by(ProfileChunk.granularity, ProfileChunk.id)
                )
            )
            .scalars()
            .all()
        )

    lines: list[str] = ["## Candidate(deterministic 字段,Profile 表)"]
    lines.append(f"姓名:{profile.full_name or '-'}")
    contact_bits = [profile.email or "-", profile.phone or "-", profile.location or "-"]
    lines.append("联系方式:" + " | ".join(contact_bits))
    lines.append("求职意向:" + (" / ".join(profile.target_titles or []) or "-"))
    lines.append("目标城市:" + (" / ".join(profile.target_locations or []) or "-"))
    lines.append("")
    lines.append("## Chunks(profile_chunks 表 全量)")

    by_gran: dict[str, list[ProfileChunk]] = {}
    for c in chunks:
        by_gran.setdefault(c.granularity, []).append(c)

    for gran in _GRANULARITY_ORDER:
        items = by_gran.get(gran) or []
        if not items:
            continue
        title = _GRANULARITY_TITLE_ZH.get(gran, gran)
        lines.append(f"### {title}")
        for c in items:
            lines.append(c.content.strip())
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


async def _run_one(
    *,
    sessionmaker,
    embedder,
    llm,
    prompt,
    jd_id: int,
    profile_id: int,
    persona_label: str,
    id_slug: str,
) -> dict:
    async with sessionmaker() as session:
        jd = await session.scalar(sa.select(Jd).where(Jd.id == jd_id))
    if jd is None:
        raise SystemExit(f"jd {jd_id} not found")

    query = build_match_query(jd)
    retrieve = await hybrid_retrieve_for_match(
        sessionmaker, profile_id=profile_id, query_text=query, embedder=embedder, k=20
    )

    result = await analyze_match(
        jd=jd, chunks=retrieve.chunks, prompt=prompt, llm=llm, related_id=None
    )
    parsed = result.parsed
    if parsed is None:
        raise SystemExit(
            f"match_analyst returned no parsed result; raw={result.content[:300]}"
        )

    profile_summary = await _build_profile_summary(sessionmaker, profile_id=profile_id)
    jd_text = (jd.title or "") + "\n\n" + (jd.raw_text or "")

    sample_id = f"ma-jd{jd_id}-p{profile_id}-{id_slug}"

    return {
        "id": sample_id,
        "jd_id": jd_id,
        "profile_id": profile_id,
        "persona_label": persona_label,
        "jd_text": jd_text,
        "profile_summary": profile_summary,
        "llm_output": parsed.model_dump(mode="json"),
        # human_* fields intentionally omitted — fill after manual review
        "_meta": {
            "model": result.model,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "cost_cny": str(result.cost_cny) if result.cost_cny is not None else None,
            "latency_ms": result.latency_ms,
            "cached": getattr(result, "cached", False),
            "retrieved_chunks": len(retrieve.chunks),
        },
    }


async def main(spec_path: Path, output_path: Path, limit: int | None) -> None:
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    samples = spec.get("samples") or []
    if not samples:
        raise SystemExit(f"no samples in {spec_path}")
    if limit is not None:
        samples = samples[:limit]

    sessionmaker = get_sessionmaker()
    embedder = get_embedder()
    llm = get_llm_client()
    prompts = await load_prompt_versions(sessionmaker)
    prompt_key = ("match_analyst", "v1.1.2")
    if prompt_key not in prompts:
        raise SystemExit(f"prompt 未加载:{prompt_key}")
    prompt = prompts[prompt_key]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    total_cost = 0.0
    cached_count = 0
    with output_path.open("w", encoding="utf-8") as f:
        for idx, sample in enumerate(samples, 1):
            jd_id = int(sample["jd_id"])
            profile_id = int(sample["profile_id"])
            persona_label = str(sample["persona_label"])
            id_slug = str(sample["id_slug"])
            print(
                f"[{idx}/{len(samples)}] jd={jd_id} profile={profile_id} slug={id_slug}",
                flush=True,
            )
            row = await _run_one(
                sessionmaker=sessionmaker,
                embedder=embedder,
                llm=llm,
                prompt=prompt,
                jd_id=jd_id,
                profile_id=profile_id,
                persona_label=persona_label,
                id_slug=id_slug,
            )
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            written += 1
            meta = row["_meta"]
            total_cost += float(meta["cost_cny"] or 0)
            if meta["cached"]:
                cached_count += 1
            print(
                f"    → score={row['llm_output'].get('score')} "
                f"cost={meta['cost_cny']} cached={meta['cached']} "
                f"chunks={meta['retrieved_chunks']}",
                flush=True,
            )
    print(
        f"\nDONE — wrote {written} rows to {output_path}, "
        f"total_cost_cny={total_cost:.4f}, cached={cached_count}/{written}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条(smoke test 用)")
    args = parser.parse_args()
    asyncio.run(main(args.spec.resolve(), args.output.resolve(), args.limit))
