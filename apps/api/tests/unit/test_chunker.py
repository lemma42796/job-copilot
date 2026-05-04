"""Unit tests for `agents.chunker`.

纯函数,不碰 DB:直接构造 ORM 实例(SQLAlchemy 允许内存实例化即使
`id` 列是 Identity always=True)、断言 ChunkInput 文本与元数据。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from jobcopilot_api.agents.chunker import (
    CHUNKER_VERSION,
    ChunkInput,
    build_chunks,
    build_education_chunk,
    build_experience_chunk,
    build_project_chunk,
    build_skill_chunk,
    build_summary_chunk,
)
from jobcopilot_api.models.profile import (
    Profile,
    ProfileEducation,
    ProfileExperience,
    ProfileProject,
    ProfileSkill,
)


def _profile(**overrides: object) -> Profile:
    base: dict[str, object] = {
        "id": 1,
        "user_id": 7,
        "source": "text_paste",
        "status": "parsed",
        "summary": None,
    }
    base.update(overrides)
    return Profile(**base)


def _experience(**overrides: object) -> ProfileExperience:
    base: dict[str, object] = {
        "id": 10,
        "profile_id": 1,
        "company": "ACME",
        "title": "高级后端工程师",
        "location": "上海",
        "start_date": date(2022, 3, 1),
        "end_date": date(2024, 12, 1),
        "is_current": False,
        "description": "负责核心交易系统",
        "bullets": ["拆单服务从单体到微服务", "QPS 从 500 到 5000"],
        "tech_stack": ["Go", "Kafka", "Redis"],
        "achievements": ["P99 延迟下降 40%"],
        "sort_order": 0,
    }
    base.update(overrides)
    return ProfileExperience(**base)


def _project(**overrides: object) -> ProfileProject:
    base: dict[str, object] = {
        "id": 20,
        "profile_id": 1,
        "name": "订单中心重构",
        "role": "Tech Lead",
        "start_date": date(2023, 1, 1),
        "end_date": None,
        "description": "拆分单体,引入消息队列",
        "bullets": ["设计 schema", "推动 code review 流程"],
        "tech_stack": ["Go", "PostgreSQL"],
        "achievements": ["上线 3 个月零事故"],
        "sort_order": 0,
    }
    base.update(overrides)
    return ProfileProject(**base)


def _skill(**overrides: object) -> ProfileSkill:
    base: dict[str, object] = {
        "id": 30,
        "profile_id": 1,
        "name": "python",
        "name_raw": "Python",
        "category": "language",
        "level": "expert",
        "years": Decimal("6.0"),
        "evidence_project_ids": [],
        "evidence_experience_ids": [],
        "sort_order": 0,
    }
    base.update(overrides)
    return ProfileSkill(**base)


def _education(**overrides: object) -> ProfileEducation:
    base: dict[str, object] = {
        "id": 40,
        "profile_id": 1,
        "school": "上海交通大学",
        "degree": "本科",
        "major": "计算机科学与技术",
        "start_date": date(2018, 9, 1),
        "end_date": date(2022, 6, 30),
        "gpa": Decimal("3.7"),
        "honors": ["一等奖学金", "院级优秀毕业生"],
        "sort_order": 0,
    }
    base.update(overrides)
    return ProfileEducation(**base)


# ----- summary -----


def test_summary_chunk_full() -> None:
    chunk = build_summary_chunk(_profile(summary="6 年后端,深耕高并发交易系统"))
    assert chunk is not None
    assert chunk.granularity == "summary"
    assert chunk.source_table == "profiles"
    assert chunk.source_id == 1
    assert chunk.content == "个人简介:6 年后端,深耕高并发交易系统"
    assert chunk.metadata == {"chunker_version": CHUNKER_VERSION}


def test_summary_chunk_none_when_empty() -> None:
    assert build_summary_chunk(_profile(summary=None)) is None
    assert build_summary_chunk(_profile(summary="")) is None
    assert build_summary_chunk(_profile(summary="   \n  ")) is None


# ----- experience -----


def test_experience_chunk_full() -> None:
    chunk = build_experience_chunk(_experience())
    assert chunk.granularity == "experience"
    assert chunk.source_table == "profile_experiences"
    assert chunk.source_id == 10
    expected = "\n".join(
        [
            "公司:ACME",
            "职位:高级后端工程师",
            "地点:上海",
            "时间:2022-03-01 - 2024-12-01",
            "技术栈:Go, Kafka, Redis",
            "描述:负责核心交易系统",
            "亮点:拆单服务从单体到微服务 | QPS 从 500 到 5000",
            "成就:P99 延迟下降 40%",
        ]
    )
    assert chunk.content == expected


def test_experience_chunk_skips_optional_fields() -> None:
    chunk = build_experience_chunk(
        _experience(
            location=None,
            start_date=None,
            end_date=None,
            tech_stack=[],
            bullets=[],
            achievements=[],
        )
    )
    assert chunk.content == "公司:ACME\n职位:高级后端工程师\n描述:负责核心交易系统"


def test_experience_chunk_open_ended_date() -> None:
    chunk = build_experience_chunk(_experience(end_date=None, is_current=True))
    assert "时间:2022-03-01 - 至今" in chunk.content


def test_experience_chunk_only_start_date_missing() -> None:
    chunk = build_experience_chunk(_experience(start_date=None, end_date=date(2024, 6, 1)))
    assert "时间:? - 2024-06-01" in chunk.content


# ----- project -----


def test_project_chunk_full() -> None:
    chunk = build_project_chunk(_project())
    assert chunk.granularity == "project"
    assert chunk.source_table == "profile_projects"
    assert chunk.source_id == 20
    expected = "\n".join(
        [
            "项目名:订单中心重构",
            "角色:Tech Lead",
            "时间:2023-01-01 - 至今",
            "技术栈:Go, PostgreSQL",
            "描述:拆分单体,引入消息队列",
            "亮点:设计 schema | 推动 code review 流程",
            "成就:上线 3 个月零事故",
        ]
    )
    assert chunk.content == expected


def test_project_chunk_skips_optional_role_and_dates() -> None:
    chunk = build_project_chunk(
        _project(role=None, start_date=None, end_date=None, achievements=[], bullets=[])
    )
    expected = "\n".join(
        [
            "项目名:订单中心重构",
            "技术栈:Go, PostgreSQL",
            "描述:拆分单体,引入消息队列",
        ]
    )
    assert chunk.content == expected


# ----- skill -----


def test_skill_chunk_full() -> None:
    chunk = build_skill_chunk(_skill())
    assert chunk.granularity == "skill"
    assert chunk.source_table == "profile_skills"
    assert chunk.source_id == 30
    expected = "\n".join(
        [
            "技能:python",
            "分类:language",
            "水平:expert",
            "年限:6.0 年",
        ]
    )
    assert chunk.content == expected


def test_skill_chunk_minimal() -> None:
    chunk = build_skill_chunk(_skill(category=None, level=None, years=None))
    assert chunk.content == "技能:python"


def test_skill_chunk_zero_years_is_kept() -> None:
    chunk = build_skill_chunk(_skill(years=Decimal("0.0")))
    assert "年限:0.0 年" in chunk.content


# ----- education -----


def test_education_chunk_full() -> None:
    chunk = build_education_chunk(_education())
    assert chunk.granularity == "education"
    assert chunk.source_table == "profile_educations"
    assert chunk.source_id == 40
    expected = "\n".join(
        [
            "学校:上海交通大学",
            "学历:本科",
            "专业:计算机科学与技术",
            "时间:2018-09-01 - 2022-06-30",
            "GPA:3.7",
            "荣誉:一等奖学金 | 院级优秀毕业生",
        ]
    )
    assert chunk.content == expected


def test_education_chunk_skips_optional_fields() -> None:
    chunk = build_education_chunk(
        _education(degree=None, major=None, start_date=None, end_date=None, gpa=None, honors=[])
    )
    assert chunk.content == "学校:上海交通大学"


def test_education_chunk_open_ended_date() -> None:
    chunk = build_education_chunk(_education(end_date=None))
    assert "时间:2018-09-01 - 至今" in chunk.content


# ----- orchestrator -----


def test_build_chunks_orders_summary_exp_proj_skill_edu() -> None:
    profile = _profile(summary="资深后端")
    chunks = build_chunks(
        profile=profile,
        experiences=[_experience(id=11), _experience(id=12)],
        projects=[_project(id=21)],
        skills=[_skill(id=31), _skill(id=32, name="go")],
        educations=[_education(id=41), _education(id=42, school="清华大学")],
    )
    assert [c.granularity for c in chunks] == [
        "summary",
        "experience",
        "experience",
        "project",
        "skill",
        "skill",
        "education",
        "education",
    ]
    assert [c.source_id for c in chunks] == [1, 11, 12, 21, 31, 32, 41, 42]


def test_build_chunks_skips_summary_when_empty() -> None:
    chunks = build_chunks(
        profile=_profile(summary=None),
        experiences=[_experience()],
        projects=[],
        skills=[],
        educations=[],
    )
    assert [c.granularity for c in chunks] == ["experience"]


def test_build_chunks_empty_profile_returns_empty_list() -> None:
    assert (
        build_chunks(
            profile=_profile(summary=None),
            experiences=[],
            projects=[],
            skills=[],
            educations=[],
        )
        == []
    )


def test_chunk_input_metadata_default_is_independent() -> None:
    """Ensure default factory doesn't share dict between instances."""
    a = ChunkInput(granularity="summary", source_table="profiles", source_id=1, content="x")
    b = ChunkInput(granularity="summary", source_table="profiles", source_id=2, content="y")
    a.metadata["scribbled"] = True
    assert "scribbled" not in b.metadata


def test_chunker_version_constant() -> None:
    assert CHUNKER_VERSION == "v1"


# ----- 杂项防御 -----


def test_experience_chunk_filters_blank_list_entries() -> None:
    chunk = build_experience_chunk(
        _experience(tech_stack=["Go", "", None, "Kafka"], bullets=["", "  ", "真亮点"])
    )
    assert "技术栈:Go, Kafka" in chunk.content
    assert "亮点:真亮点" in chunk.content
