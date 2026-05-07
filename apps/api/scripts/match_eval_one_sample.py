"""Run match agent on a single (jd_id, profile_id) and dump LLM output as JSON.

Bypasses match_service / matches DB write entirely — just runs:
  hybrid_retrieve_for_match → analyze_match → print MatchResult JSON

Usage:
  uv run python apps/api/scripts/match_eval_one_sample.py <jd_id> <profile_id>
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import sqlalchemy as sa

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "apps" / "api" / "src"))

from jobcopilot_api.agents.match_analyst import analyze_match  # noqa: E402
from jobcopilot_api.infra.db import get_sessionmaker  # noqa: E402
from jobcopilot_api.infra.embedder import get_embedder  # noqa: E402
from jobcopilot_api.infra.llm import get_llm_client  # noqa: E402
from jobcopilot_api.infra.prompts import load_prompt_versions  # noqa: E402
from jobcopilot_api.models import Jd  # noqa: E402
from jobcopilot_api.services.retrieval_service import (  # noqa: E402
    build_match_query,
    hybrid_retrieve_for_match,
)


async def main(jd_id: int, profile_id: int) -> None:
    sessionmaker = get_sessionmaker()
    embedder = get_embedder()
    llm = get_llm_client()
    prompts = await load_prompt_versions(sessionmaker)
    prompt_key = ("match_analyst", "v1.1.2")
    if prompt_key not in prompts:
        raise SystemExit(f"prompt 未加载:{prompt_key}")
    prompt = prompts[prompt_key]

    async with sessionmaker() as session:
        jd = await session.scalar(sa.select(Jd).where(Jd.id == jd_id))
    if jd is None:
        raise SystemExit(f"jd {jd_id} not found")

    query = build_match_query(jd)
    retrieve = await hybrid_retrieve_for_match(
        sessionmaker, profile_id=profile_id, query_text=query, embedder=embedder, k=20
    )
    print(f"# retrieved {len(retrieve.chunks)} chunks for jd={jd_id} profile={profile_id}")
    for c in retrieve.chunks:
        print(f"  [chunk_id={c.id} {c.granularity}] {c.content[:80]}")

    result = await analyze_match(
        jd=jd, chunks=retrieve.chunks, prompt=prompt, llm=llm, related_id=None
    )
    parsed = result.parsed
    if parsed is None:
        raise SystemExit(f"match_analyst returned no parsed result; raw={result.content[:300]}")

    print("\n# match agent output")
    print(json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False, indent=2))
    print(
        f"\n# meta — model={result.model} tokens_in={result.tokens_in} "
        f"tokens_out={result.tokens_out} cost_cny={result.cost_cny} latency_ms={result.latency_ms}"
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: match_eval_one_sample.py <jd_id> <profile_id>")
    asyncio.run(main(int(sys.argv[1]), int(sys.argv[2])))
