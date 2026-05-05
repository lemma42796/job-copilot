"""Seed S18 dogfood JDs from evals/suites/jd_extract/dataset.jsonl.

Selection (option B, 测岗位方向差异):
  001 AI 应用 / 006 AI Agent / 008 AI 算法 / 009 AI 校招 / 011 算法+infra+Agent

Idempotent: skips JDs whose raw_text already exists for user_id (live rows).
Re-runnable safely.

Usage:
  uv run python apps/api/scripts/seed_jds_from_evals.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import sqlalchemy as sa

# Add src/ to path so this script works without installation.
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "apps" / "api" / "src"))

from jobcopilot_api.infra.db import get_sessionmaker  # noqa: E402
from jobcopilot_api.infra.llm import get_llm_client  # noqa: E402
from jobcopilot_api.infra.prompts import load_prompt_versions  # noqa: E402
from jobcopilot_api.models import Jd  # noqa: E402
from jobcopilot_api.services.jd_service import create_and_parse  # noqa: E402

DATASET = _REPO_ROOT / "evals" / "suites" / "jd_extract" / "dataset.jsonl"
USER_ID = 1
SELECTED = [
    "jd_extract_001_boss",
    "jd_extract_006_boss",
    "jd_extract_008_boss",
    "jd_extract_009_boss",
    "jd_extract_011_boss",
]
PROMPT_KEY = ("jd_parser", "v1.0.1")


def _load_selected() -> list[tuple[str, str]]:
    wanted = set(SELECTED)
    found: dict[str, str] = {}
    with DATASET.open() as f:
        for line in f:
            d = json.loads(line)
            if d["description"] in wanted:
                found[d["description"]] = d["vars"]["jd_text"]
    missing = wanted - found.keys()
    if missing:
        raise SystemExit(f"dataset 缺少:{sorted(missing)}")
    return [(name, found[name]) for name in SELECTED]


async def _already_exists(sessionmaker, *, raw_text: str) -> int | None:
    async with sessionmaker() as session:
        return await session.scalar(
            sa.select(Jd.id).where(
                Jd.user_id == USER_ID,
                Jd.raw_text == raw_text,
                Jd.deleted_at.is_(None),
            )
        )


async def main() -> None:
    sessionmaker = get_sessionmaker()
    llm = get_llm_client()
    prompts = await load_prompt_versions(sessionmaker)
    if PROMPT_KEY not in prompts:
        raise SystemExit(f"prompt 未加载:{PROMPT_KEY}")
    prompt = prompts[PROMPT_KEY]

    items = _load_selected()
    print(f"目标:{len(items)} 条 JD,user_id={USER_ID}")
    print("=" * 60)

    inserted: list[tuple[str, int, str]] = []
    skipped: list[tuple[str, int]] = []
    failed: list[tuple[str, str]] = []

    for name, text in items:
        existing = await _already_exists(sessionmaker, raw_text=text)
        if existing is not None:
            print(f"⏭️  {name} → 已存在 jd#{existing},跳过")
            skipped.append((name, existing))
            continue
        try:
            jd, result = await create_and_parse(
                sessionmaker,
                user_id=USER_ID,
                source="text_paste",
                text=text,
                file_id=None,
                llm=llm,
                prompt=prompt,
            )
        except Exception as exc:  # noqa: BLE001 — log and continue
            print(f"❌ {name} → 失败:{type(exc).__name__}: {exc}")
            failed.append((name, str(exc)))
            continue
        title = (jd.title or "")[:30]
        cost = float(jd.parse_cost_cny or 0)
        print(f"✅ {name} → jd#{jd.id} '{title}' / {result.tokens_in + result.tokens_out}t / ¥{cost:.4f}")
        inserted.append((name, jd.id, title))

    print("=" * 60)
    print(f"汇总:{len(inserted)} 新建 / {len(skipped)} 跳过 / {len(failed)} 失败")
    if inserted:
        ids = ", ".join(f"#{jd_id}" for _, jd_id, _ in inserted)
        print(f"新 JD ID:{ids}")


if __name__ == "__main__":
    asyncio.run(main())
