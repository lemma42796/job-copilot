"""Load synthetic persona YAML fixture into DB(供 match_analysis 评测用)。

Pipeline
--------

1. Read YAML at `evals/fixtures/personas/{file}.yaml`
2. INSERT `profiles` + `profile_educations` + `profile_experiences` +
   `profile_projects` + `profile_skills`(single transaction)
3. Call `chunk_service.rebuild_for_profile(...)` → builds chunks +
   runs embedder + writes `profile_chunks` rows

Idempotency:re-running with same `(full_name, source='manual')` 会**新建**
另一份 profile;loader 不去重 — 评测想换 persona 直接改 yaml 然后跑新 id。
真要去重就先 `UPDATE profiles SET deleted_at=now() WHERE id=...`。

Usage:
  uv run python apps/api/scripts/load_persona_fixture.py \\
      evals/fixtures/personas/persona-frontend-to-ai.yaml
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "apps" / "api" / "src"))

import sqlalchemy as sa  # noqa: E402

from jobcopilot_api.infra.db import get_sessionmaker  # noqa: E402
from jobcopilot_api.infra.embedder import get_embedder  # noqa: E402
from jobcopilot_api.models import (  # noqa: E402
    Profile,
    ProfileEducation,
    ProfileExperience,
    ProfileProject,
    ProfileSkill,
    User,
)
from jobcopilot_api.services.chunk_service import rebuild_for_profile  # noqa: E402


async def _ensure_eval_user(sessionmaker, *, fixture_stem: str) -> int:
    """Each synthetic persona lives under its own eval-only user, so the
    `profiles.uq_profiles_user_id WHERE deleted_at IS NULL` partial unique
    doesn't collide with the real user_id=1 active profile."""
    email = f"{fixture_stem}@evaluation.example.com"
    async with sessionmaker() as session, session.begin():
        existing = await session.scalar(sa.select(User.id).where(User.email == email))
        if existing is not None:
            return int(existing)
        user = User(email=email, name=f"eval-{fixture_stem}", locale="zh-CN", settings={})
        session.add(user)
        await session.flush()
        return int(user.id)


def _to_date(v: object) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


async def main(fixture_path: Path) -> None:
    data = yaml.safe_load(fixture_path.read_text(encoding="utf-8"))
    pdata = data["profile"]
    sessionmaker = get_sessionmaker()
    embedder = get_embedder()
    user_id = await _ensure_eval_user(sessionmaker, fixture_stem=fixture_path.stem)
    print(f"eval user: id={user_id}, email={fixture_path.stem}@evaluation.example.com")

    async with sessionmaker() as session, session.begin():
        phone_raw = pdata.get("phone")
        profile = Profile(
            user_id=user_id,
            full_name=pdata.get("full_name"),
            phone=str(phone_raw) if phone_raw is not None else None,
            email=pdata.get("email"),
            location=pdata.get("location"),
            summary=pdata.get("summary"),
            target_titles=pdata.get("target_titles") or [],
            target_locations=pdata.get("target_locations") or [],
            source=pdata.get("source", "manual"),
            status=pdata.get("status", "parsed"),
        )
        session.add(profile)
        await session.flush()
        profile_id = profile.id

        for sort_idx, edu in enumerate(data.get("educations") or []):
            session.add(
                ProfileEducation(
                    profile_id=profile_id,
                    school=edu["school"],
                    degree=edu.get("degree"),
                    major=edu.get("major"),
                    start_date=_to_date(edu.get("start_date")),
                    end_date=_to_date(edu.get("end_date")),
                    gpa=edu.get("gpa"),
                    honors=edu.get("honors") or [],
                    sort_order=sort_idx,
                )
            )

        for sort_idx, exp in enumerate(data.get("experiences") or []):
            session.add(
                ProfileExperience(
                    profile_id=profile_id,
                    company=exp["company"],
                    title=exp["title"],
                    location=exp.get("location"),
                    start_date=_to_date(exp.get("start_date")),
                    end_date=_to_date(exp.get("end_date")),
                    is_current=bool(exp.get("is_current", False)),
                    description=exp.get("description") or "",
                    bullets=exp.get("bullets") or [],
                    tech_stack=exp.get("tech_stack") or [],
                    achievements=exp.get("achievements") or [],
                    sort_order=sort_idx,
                )
            )

        for sort_idx, proj in enumerate(data.get("projects") or []):
            session.add(
                ProfileProject(
                    profile_id=profile_id,
                    name=proj["name"],
                    role=proj.get("role"),
                    start_date=_to_date(proj.get("start_date")),
                    end_date=_to_date(proj.get("end_date")),
                    description=proj.get("description") or "",
                    bullets=proj.get("bullets") or [],
                    tech_stack=proj.get("tech_stack") or [],
                    achievements=proj.get("achievements") or [],
                    repo_url=proj.get("repo_url"),
                    demo_url=proj.get("demo_url"),
                    sort_order=sort_idx,
                )
            )

        for sort_idx, sk in enumerate(data.get("skills") or []):
            session.add(
                ProfileSkill(
                    profile_id=profile_id,
                    name=sk["name"],
                    name_raw=sk.get("name_raw") or sk["name"],
                    category=sk.get("category"),
                    level=sk.get("level"),
                    years=sk.get("years"),
                    sort_order=sort_idx,
                )
            )

    print(f"profile inserted: id={profile_id}, full_name={pdata.get('full_name')!r}")
    print("rebuilding chunks...")
    result = await rebuild_for_profile(
        sessionmaker,
        user_id=user_id,
        profile_id=profile_id,
        embedder=embedder,
    )
    print(
        f"chunks_written={result.chunks_written}, "
        f"tokens_in={result.tokens_in}, "
        f"cost_cny={result.cost_cny}, "
        f"embed_model={result.embed_model}"
    )
    print(f"DONE — new profile_id = {profile_id}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: load_persona_fixture.py <fixture.yaml>")
    asyncio.run(main(Path(sys.argv[1]).resolve()))
