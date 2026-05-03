"""ChunkBuilder — 5 表 ORM → ChunkInput,5-AGENT_DESIGN.md §4.7。

纯函数:不读 DB、不写 DB、不做 I/O。service 层把已加载的 ORM 实例
塞进来,拿到 list[ChunkInput] 后交给 Embedder + INSERT 到
`profile_chunks`(C4)。

`metadata.chunker_version="v1"` 是 S8 锁定的版本标。规则升级时 bump v2,
旧 chunk 的 metadata 仍保留 v1 标识方便回填。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from jobcopilot_api.models.profile import (
    Profile,
    ProfileExperience,
    ProfileProject,
    ProfileSkill,
)
from jobcopilot_api.schemas.profiles import ChunkGranularity

CHUNKER_VERSION = "v1"


@dataclass(frozen=True)
class ChunkInput:
    granularity: ChunkGranularity
    source_table: str
    source_id: int
    content: str
    metadata: dict[str, Any] = field(default_factory=lambda: {"chunker_version": CHUNKER_VERSION})


def _date_range(start: date | None, end: date | None) -> str | None:
    if start is None and end is None:
        return None
    start_s = start.isoformat() if start else "?"
    end_s = end.isoformat() if end else "至今"
    return f"{start_s} - {end_s}"


def _join_lines(parts: list[str | None]) -> str:
    return "\n".join(p for p in parts if p)


def _str_list(items: list[Any]) -> list[str]:
    return [str(x) for x in items if x is not None and str(x).strip()]


def build_summary_chunk(profile: Profile) -> ChunkInput | None:
    summary = (profile.summary or "").strip()
    if not summary:
        return None
    return ChunkInput(
        granularity="summary",
        source_table="profiles",
        source_id=profile.id,
        content=f"个人简介:{summary}",
    )


def build_experience_chunk(exp: ProfileExperience) -> ChunkInput:
    parts: list[str | None] = [
        f"公司:{exp.company}",
        f"职位:{exp.title}",
        f"地点:{exp.location}" if exp.location else None,
    ]
    rng = _date_range(exp.start_date, exp.end_date)
    if rng:
        parts.append(f"时间:{rng}")
    tech = _str_list(exp.tech_stack)
    if tech:
        parts.append(f"技术栈:{', '.join(tech)}")
    parts.append(f"描述:{exp.description}")
    bullets = _str_list(exp.bullets)
    if bullets:
        parts.append("亮点:" + " | ".join(bullets))
    achievements = _str_list(exp.achievements)
    if achievements:
        parts.append("成就:" + " | ".join(achievements))
    return ChunkInput(
        granularity="experience",
        source_table="profile_experiences",
        source_id=exp.id,
        content=_join_lines(parts),
    )


def build_project_chunk(proj: ProfileProject) -> ChunkInput:
    parts: list[str | None] = [
        f"项目名:{proj.name}",
        f"角色:{proj.role}" if proj.role else None,
    ]
    rng = _date_range(proj.start_date, proj.end_date)
    if rng:
        parts.append(f"时间:{rng}")
    tech = _str_list(proj.tech_stack)
    if tech:
        parts.append(f"技术栈:{', '.join(tech)}")
    parts.append(f"描述:{proj.description}")
    bullets = _str_list(proj.bullets)
    if bullets:
        parts.append("亮点:" + " | ".join(bullets))
    achievements = _str_list(proj.achievements)
    if achievements:
        parts.append("成就:" + " | ".join(achievements))
    return ChunkInput(
        granularity="project",
        source_table="profile_projects",
        source_id=proj.id,
        content=_join_lines(parts),
    )


def build_skill_chunk(skill: ProfileSkill) -> ChunkInput:
    parts: list[str | None] = [f"技能:{skill.name}"]
    if skill.category:
        parts.append(f"分类:{skill.category}")
    if skill.level:
        parts.append(f"水平:{skill.level}")
    if skill.years is not None:
        parts.append(f"年限:{skill.years} 年")
    return ChunkInput(
        granularity="skill",
        source_table="profile_skills",
        source_id=skill.id,
        content=_join_lines(parts),
    )


def build_chunks(
    *,
    profile: Profile,
    experiences: list[ProfileExperience],
    projects: list[ProfileProject],
    skills: list[ProfileSkill],
) -> list[ChunkInput]:
    """5 表 ORM → 全部 chunks。summary 为空时跳过该 chunk。

    顺序:summary → experiences → projects → skills(便于 service 层
    log/调试时按章节分组)。
    """
    chunks: list[ChunkInput] = []
    if (s := build_summary_chunk(profile)) is not None:
        chunks.append(s)
    chunks.extend(build_experience_chunk(e) for e in experiences)
    chunks.extend(build_project_chunk(p) for p in projects)
    chunks.extend(build_skill_chunk(sk) for sk in skills)
    return chunks
