"""Unit tests for `infra.prompts` pure helpers. ADR-0006 D6.

DB-side (load_prompt_versions / mismatch detection) lives in the
integration test module since it requires the live `prompt_versions`
table. Here we cover the parser, walker, hashing, and Jinja rendering.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobcopilot_api.infra.prompts import (
    PROMPTS_DIR,
    compute_hash,
    parse_template,
    render_user,
    walk_templates,
)

# ---- parse_template ----


def test_parse_template_extracts_two_blocks() -> None:
    content = "## SYSTEM\nyou are a foo.\n\n## USER\nhello {{ name }}\n"
    system, user = parse_template(content)
    assert system == "you are a foo."
    assert user == "hello {{ name }}"


def test_parse_template_ignores_leading_frontmatter() -> None:
    content = "# meta\nignored\n\n## SYSTEM\nsys\n\n## USER\nu\n"
    system, user = parse_template(content)
    assert system == "sys"
    assert user == "u"


def test_parse_template_requires_system_header() -> None:
    with pytest.raises(ValueError, match="missing `## SYSTEM`"):
        parse_template("## USER\nonly user\n")


def test_parse_template_requires_user_header() -> None:
    with pytest.raises(ValueError, match="missing `## USER`"):
        parse_template("## SYSTEM\nonly sys\n")


def test_parse_template_requires_order() -> None:
    content = "## USER\nu\n\n## SYSTEM\ns\n"
    with pytest.raises(ValueError, match="must appear before"):
        parse_template(content)


def test_parse_template_rejects_empty_system() -> None:
    with pytest.raises(ValueError, match="`## SYSTEM` block is empty"):
        parse_template("## SYSTEM\n\n## USER\nu\n")


def test_parse_template_rejects_empty_user() -> None:
    with pytest.raises(ValueError, match="`## USER` block is empty"):
        parse_template("## SYSTEM\ns\n\n## USER\n")


# ---- compute_hash: stable + content-sensitive ----


def test_compute_hash_deterministic() -> None:
    assert compute_hash("hello") == compute_hash("hello")


def test_compute_hash_changes_on_edit() -> None:
    assert compute_hash("hello") != compute_hash("hello.")


def test_compute_hash_returns_64_hex() -> None:
    h = compute_hash("x")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


# ---- render_user: Jinja2 with strict undefined ----


def test_render_user_substitutes_variables() -> None:
    out = render_user("hello {{ name }}", name="world")
    assert out == "hello world"


def test_render_user_strict_on_missing_variable() -> None:
    from jinja2.exceptions import UndefinedError

    with pytest.raises(UndefinedError):
        render_user("hi {{ missing_var }}")


def test_render_user_does_not_autoescape() -> None:
    # Prompts are not HTML; '<' must pass through verbatim.
    out = render_user("{{ jd_text }}", jd_text="<jd>raw</jd>")
    assert out == "<jd>raw</jd>"


# ---- walk_templates ----


def test_walk_templates_finds_jd_parser_v100() -> None:
    # Use the real PROMPTS_DIR shipped with the package.
    found = list(walk_templates(PROMPTS_DIR))
    assert ("jd_parser", "v1.0.0", PROMPTS_DIR / "jd_parser" / "v1.0.0.j2") in found


def test_walk_templates_skips_non_versioned_files(tmp_path: Path) -> None:
    (tmp_path / "agent_a").mkdir()
    (tmp_path / "agent_a" / "v1.0.0.j2").write_text("## SYSTEM\ns\n## USER\nu\n")
    (tmp_path / "agent_a" / "draft.j2").write_text("draft")
    (tmp_path / "agent_a" / "README.md").write_text("docs")
    found = list(walk_templates(tmp_path))
    assert len(found) == 1
    assert found[0][1] == "v1.0.0"


def test_walk_templates_skips_underscore_dirs(tmp_path: Path) -> None:
    (tmp_path / "_shared").mkdir()
    (tmp_path / "_shared" / "v1.0.0.j2").write_text("ignored")
    found = list(walk_templates(tmp_path))
    assert found == []


def test_walk_templates_returns_empty_for_missing_root(tmp_path: Path) -> None:
    nonexistent = tmp_path / "nope"
    assert list(walk_templates(nonexistent)) == []


def test_walk_templates_sorted_for_stable_logs(tmp_path: Path) -> None:
    (tmp_path / "b_agent").mkdir()
    (tmp_path / "a_agent").mkdir()
    (tmp_path / "a_agent" / "v2.0.0.j2").write_text("x")
    (tmp_path / "a_agent" / "v1.0.0.j2").write_text("x")
    (tmp_path / "b_agent" / "v1.0.0.j2").write_text("x")
    found = [(name, ver) for name, ver, _ in walk_templates(tmp_path)]
    assert found == [("a_agent", "v1.0.0"), ("a_agent", "v2.0.0"), ("b_agent", "v1.0.0")]


# ---- jd_parser/v1.0.0.j2 sanity ----


def test_jd_parser_v100_template_parses() -> None:
    """The shipped template must round-trip through parse_template."""
    path = PROMPTS_DIR / "jd_parser" / "v1.0.0.j2"
    content = path.read_text(encoding="utf-8")
    system, user = parse_template(content)
    assert "招聘信息分析师" in system
    assert "{{ jd_text }}" in user


def test_jd_parser_v100_user_renders_with_jd_text() -> None:
    path = PROMPTS_DIR / "jd_parser" / "v1.0.0.j2"
    _, user = parse_template(path.read_text(encoding="utf-8"))
    rendered = render_user(user, jd_text="Senior Backend @ ACME")
    assert "Senior Backend @ ACME" in rendered
    assert "{{ jd_text }}" not in rendered
