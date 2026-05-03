"""Integration tests for `services.profile_service`.

Covers the parse pipeline (Phase 1 / Phase 2 split with side-channel
failure commit), per-user uniqueness (409), child-table DELETE+INSERT,
skill dedupe, and the list / get / patch / soft_delete CRUD path.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from tenacity.wait import wait_none
from testcontainers.postgres import PostgresContainer

from jobcopilot_api.infra.prompts import LoadedPrompt
from jobcopilot_api.llm.client import BaseLLMClient, MemoryCallLogger
from jobcopilot_api.llm.errors import LLMUpstreamError
from jobcopilot_api.llm.providers.dummy import DummyProvider
from jobcopilot_api.models import (
    Profile,
    ProfileEducation,
    ProfileExperience,
    ProfileProject,
    ProfileSkill,
    User,
)
from jobcopilot_api.schemas.profiles import ProfilePatchInput, ProfileStructured
from jobcopilot_api.services.profile_service import (
    ProfileExistsError,
    ProfileParseFailedError,
    create_and_parse,
    create_pending_profile,
    get_profile,
    list_profiles,
    patch_profile,
    run_parse,
    soft_delete_profile,
)

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


GOLDEN_PARSED = json.dumps(
    {
        "full_name": "张三",
        "phone": "13800001234",
        "email": "zhangsan@example.com",
        "location": "上海",
        "summary": "5 年后端工程师",
        "target_titles": ["高级后端"],
        "experiences": [
            {
                "company": "ACME",
                "title": "高级后端工程师",
                "location": "上海",
                "start_date": "2022-03-01",
                "end_date": None,
                "is_current": True,
                "description": "主导平台架构",
                "bullets": ["重构订单系统,QPS 5x"],
                "tech_stack": ["python", "postgres"],
                "achievements": ["QPS 5x"],
            }
        ],
        "projects": [
            {
                "name": "调度系统",
                "role": "主程",
                "start_date": "2023-01-01",
                "end_date": "2023-06-01",
                "description": "celery 平台",
                "bullets": ["100w/日"],
                "tech_stack": ["celery"],
                "achievements": ["100w 调度量"],
                "repo_url": None,
                "demo_url": None,
            }
        ],
        "skills": [
            {
                "name": "python",
                "name_raw": "Python",
                "category": "language",
                "level": "expert",
                "years": 5.0,
            },
            {
                "name": "postgres",
                "name_raw": "PostgreSQL",
                "category": "database",
                "level": "advanced",
                "years": 4.0,
            },
        ],
        "educations": [
            {
                "school": "上海交通大学",
                "degree": "硕士",
                "major": "CS",
                "start_date": "2018-09-01",
                "end_date": "2021-06-30",
                "gpa": 3.7,
                "honors": ["优秀毕业生"],
            }
        ],
    }
)


def _long_resume() -> str:
    """Resume-like text comfortably above the 100-char floor."""
    return (
        "张三 高级后端工程师 5 年经验\n"
        "邮箱: zhangsan@example.com 电话: 13800001234\n"
        "ACME 公司 高级后端工程师 2022.03 - 至今\n"
        "  - 重构订单系统,QPS 从 2k 提升到 10k\n"
        "教育: 上海交通大学 硕士 计算机科学 2018-2021\n"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _container() -> Iterator[str]:
    container = (
        PostgresContainer(image="pgvector/pgvector:pg16")
        .with_env("POSTGRES_USER", "jobcopilot")
        .with_env("POSTGRES_PASSWORD", "jobcopilot")
        .with_env("POSTGRES_DB", "jobcopilot")
    )
    container.start()
    try:
        sync_url = container.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
            "postgresql://", "postgresql+asyncpg://"
        )
        cfg = Config(str(ALEMBIC_INI))
        cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
        cfg.set_main_option("sqlalchemy.url", async_url)
        command.upgrade(cfg, "head")
        yield async_url
    finally:
        container.stop()


@pytest.fixture
async def engine(_container: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(_container, future=True)
    yield eng
    await eng.dispose()


@pytest.fixture
def sessionmaker_(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _truncate(engine: AsyncEngine) -> AsyncIterator[None]:
    yield
    async with engine.begin() as conn:
        await conn.execute(sa.text("TRUNCATE TABLE users RESTART IDENTITY CASCADE"))


@pytest.fixture
async def user_id(sessionmaker_: async_sessionmaker[AsyncSession]) -> int:
    async with sessionmaker_() as session, session.begin():
        u = User(email="t@example.com", name="t")
        session.add(u)
        await session.flush()
        return u.id


@pytest.fixture
async def other_user_id(sessionmaker_: async_sessionmaker[AsyncSession]) -> int:
    async with sessionmaker_() as session, session.begin():
        u = User(email="other@example.com", name="other")
        session.add(u)
        await session.flush()
        return u.id


def _prompt() -> LoadedPrompt:
    return LoadedPrompt(
        id=1,
        agent_name="profile_parser",
        version="v1.0.0",
        system="you are a resume parser",
        user_template="parse: {{ resume_text }}",
        template_hash="x" * 64,
    )


def _client_with_response(content: str) -> BaseLLMClient:
    dummy = DummyProvider()
    dummy.queue(content=content, tokens_in=1200, tokens_out=420)
    return BaseLLMClient(provider=dummy, logger=MemoryCallLogger(), retry_wait=wait_none())


def _client_with_error(*errs: BaseException) -> BaseLLMClient:
    dummy = DummyProvider()
    for e in errs:
        dummy.queue_error(e)
    return BaseLLMClient(provider=dummy, logger=MemoryCallLogger(), retry_wait=wait_none())


# ---------------------------------------------------------------------------
# create_and_parse: text_paste happy path
# ---------------------------------------------------------------------------


async def test_create_and_parse_text_writes_parent_and_children(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    profile, _ = await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_resume(),
        file_id=None,
        llm=_client_with_response(GOLDEN_PARSED),
        prompt=_prompt(),
    )
    assert profile.status == "parsed"
    assert profile.full_name == "张三"
    assert profile.email == "zhangsan@example.com"
    assert profile.parse_tokens == 1620

    async with sessionmaker_() as session:
        exps = (
            (
                await session.execute(
                    sa.select(ProfileExperience).where(ProfileExperience.profile_id == profile.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(exps) == 1
        assert exps[0].company == "ACME"

        projs = (
            (
                await session.execute(
                    sa.select(ProfileProject).where(ProfileProject.profile_id == profile.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(projs) == 1
        assert projs[0].name == "调度系统"

        skills = (
            (
                await session.execute(
                    sa.select(ProfileSkill).where(ProfileSkill.profile_id == profile.id)
                )
            )
            .scalars()
            .all()
        )
        assert {s.name for s in skills} == {"python", "postgres"}

        edus = (
            (
                await session.execute(
                    sa.select(ProfileEducation).where(ProfileEducation.profile_id == profile.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(edus) == 1
        assert edus[0].school == "上海交通大学"


# ---------------------------------------------------------------------------
# 409: duplicate user
# ---------------------------------------------------------------------------


async def test_create_pending_profile_rejects_duplicate_user_with_409(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_resume(),
        file_id=None,
        llm=_client_with_response(GOLDEN_PARSED),
        prompt=_prompt(),
    )
    # Second call: must raise BEFORE inserting a second row.
    with pytest.raises(ProfileExistsError):
        await create_pending_profile(
            sessionmaker_,
            user_id=user_id,
            source="text_paste",
            text=_long_resume(),
            file_id=None,
        )

    async with sessionmaker_() as session:
        count = (
            await session.execute(
                sa.select(sa.func.count()).select_from(Profile).where(Profile.user_id == user_id)
            )
        ).scalar_one()
        assert count == 1


async def test_create_after_soft_delete_succeeds(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """Soft-deleted rows shouldn't block re-parse (uq is partial via
    `deleted_at IS NULL` check in the service-layer guard)."""
    p1, _ = await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_resume(),
        file_id=None,
        llm=_client_with_response(GOLDEN_PARSED),
        prompt=_prompt(),
    )
    async with sessionmaker_() as session, session.begin():
        await soft_delete_profile(session, user_id=user_id, profile_id=p1.id)

    p2, _ = await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_resume(),
        file_id=None,
        llm=_client_with_response(GOLDEN_PARSED),
        prompt=_prompt(),
    )
    assert p2.id != p1.id


# ---------------------------------------------------------------------------
# Pre-insert failures: NO row created
# ---------------------------------------------------------------------------


async def test_create_pending_text_too_short_inserts_nothing(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    with pytest.raises(ProfileParseFailedError):
        await create_pending_profile(
            sessionmaker_,
            user_id=user_id,
            source="text_paste",
            text="too short",
            file_id=None,
        )
    async with sessionmaker_() as session:
        rows = (await session.execute(sa.select(Profile))).scalars().all()
        assert rows == []


# ---------------------------------------------------------------------------
# LLM failures mark parse_failed (side-channel commit) and propagate
# ---------------------------------------------------------------------------


async def test_run_parse_upstream_error_marks_parse_failed(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    pid, raw = await create_pending_profile(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_resume(),
        file_id=None,
    )
    bad_client = _client_with_error(
        LLMUpstreamError("503", status_code=503),
        LLMUpstreamError("503", status_code=503),
        LLMUpstreamError("503", status_code=503),
    )
    with pytest.raises(LLMUpstreamError):
        await run_parse(
            sessionmaker_,
            profile_id=pid,
            user_id=user_id,
            raw_text=raw,
            llm=bad_client,
            prompt=_prompt(),
        )

    async with sessionmaker_() as session:
        prof = await get_profile(session, user_id=user_id, profile_id=pid)
        assert prof.status == "parse_failed"
        assert prof.raw_text is not None  # diagnostic state survived


async def test_run_parse_schema_invalid_raises_profile_parse_failed(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    pid, raw = await create_pending_profile(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_resume(),
        file_id=None,
    )
    dummy = DummyProvider()
    dummy.queue(content="not json", tokens_in=10, tokens_out=2)
    dummy.queue(content="still not json", tokens_in=10, tokens_out=2)
    bad = BaseLLMClient(provider=dummy, logger=MemoryCallLogger(), retry_wait=wait_none())

    with pytest.raises(ProfileParseFailedError):
        await run_parse(
            sessionmaker_,
            profile_id=pid,
            user_id=user_id,
            raw_text=raw,
            llm=bad,
            prompt=_prompt(),
        )

    async with sessionmaker_() as session:
        prof = await get_profile(session, user_id=user_id, profile_id=pid)
        assert prof.status == "parse_failed"


# ---------------------------------------------------------------------------
# patch: rebuild children, status / target_salary overrides
# ---------------------------------------------------------------------------


async def test_patch_with_structured_rebuilds_children_and_dedupes_skills(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    profile, _ = await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_resume(),
        file_id=None,
        llm=_client_with_response(GOLDEN_PARSED),
        prompt=_prompt(),
    )
    new_structured = ProfileStructured.model_validate(
        {
            "full_name": "李四",
            "skills": [
                {"name": "python", "name_raw": "Python"},
                {"name": "python", "name_raw": "PYTHON"},  # dup
                {"name": "go", "name_raw": "Go"},
            ],
        }
    )
    async with sessionmaker_() as session, session.begin():
        updated = await patch_profile(
            session,
            user_id=user_id,
            profile_id=profile.id,
            patch=ProfilePatchInput(structured=new_structured),
        )
        assert updated.full_name == "李四"

    async with sessionmaker_() as session:
        skills = (
            (
                await session.execute(
                    sa.select(ProfileSkill).where(ProfileSkill.profile_id == profile.id)
                )
            )
            .scalars()
            .all()
        )
        assert {s.name for s in skills} == {"python", "go"}

        # Old experiences/projects/educations should be wiped.
        exp_count = (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(ProfileExperience)
                .where(ProfileExperience.profile_id == profile.id)
            )
        ).scalar_one()
        assert exp_count == 0


async def test_patch_status_only_does_not_touch_children(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    profile, _ = await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_resume(),
        file_id=None,
        llm=_client_with_response(GOLDEN_PARSED),
        prompt=_prompt(),
    )
    async with sessionmaker_() as session, session.begin():
        updated = await patch_profile(
            session,
            user_id=user_id,
            profile_id=profile.id,
            patch=ProfilePatchInput(status="parsing", target_salary_min=30000),
        )
    assert updated.status == "parsing"
    assert updated.target_salary_min == 30000

    async with sessionmaker_() as session:
        skill_count = (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(ProfileSkill)
                .where(ProfileSkill.profile_id == profile.id)
            )
        ).scalar_one()
        assert skill_count == 2  # children survive a status-only patch


# ---------------------------------------------------------------------------
# get / list ownership
# ---------------------------------------------------------------------------


async def test_get_profile_404_for_other_user(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int, other_user_id: int
) -> None:
    profile, _ = await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_resume(),
        file_id=None,
        llm=_client_with_response(GOLDEN_PARSED),
        prompt=_prompt(),
    )
    async with sessionmaker_() as session:
        from jobcopilot_api.errors import NotFoundError

        with pytest.raises(NotFoundError):
            await get_profile(session, user_id=other_user_id, profile_id=profile.id)


async def test_list_profiles_returns_user_rows_only(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int, other_user_id: int
) -> None:
    await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_resume(),
        file_id=None,
        llm=_client_with_response(GOLDEN_PARSED),
        prompt=_prompt(),
    )
    await create_and_parse(
        sessionmaker_,
        user_id=other_user_id,
        source="text_paste",
        text=_long_resume(),
        file_id=None,
        llm=_client_with_response(GOLDEN_PARSED),
        prompt=_prompt(),
    )
    async with sessionmaker_() as session:
        rows, has_more = await list_profiles(session, user_id=user_id)
        assert len(rows) == 1
        assert has_more is False


async def test_soft_delete_then_get_returns_not_found(
    sessionmaker_: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    profile, _ = await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source="text_paste",
        text=_long_resume(),
        file_id=None,
        llm=_client_with_response(GOLDEN_PARSED),
        prompt=_prompt(),
    )
    async with sessionmaker_() as session, session.begin():
        await soft_delete_profile(session, user_id=user_id, profile_id=profile.id)

    async with sessionmaker_() as session:
        from jobcopilot_api.errors import NotFoundError

        with pytest.raises(NotFoundError):
            await get_profile(session, user_id=user_id, profile_id=profile.id)


# Mark every test in this file as integration so they run in the same
# pass as `test_jds_router.py` etc.
pytestmark = pytest.mark.integration
