"""Prompt template loader. ADR-0006 D6.

Scans `apps/api/src/jobcopilot_api/prompts/<agent_name>/v*.j2`, parses
each into a SYSTEM block (constant) and a USER block (Jinja2 template),
and upserts a row into `prompt_versions`. Returns a `(agent_name, version)`
→ `LoadedPrompt` cache that the FastAPI app pins onto `app.state` for
agents to look up by `(name, version)`.

Hash semantics
--------------

`template_hash = sha256(file_bytes)`. The hash is the file-content fingerprint;
we don't bother with a separate `model` field in the hash because the
production rule is "model change ⇒ new version directory entry, not a
silent edit." If a row for `(agent, version)` exists with a different
hash, startup fails with `PromptVersionMismatchError` — bump the version
file (e.g. `v1.0.0.j2` → `v1.0.1.j2`) instead of silently editing.

Template format
---------------

A `.j2` file under `prompts/<agent>/v*.j2` looks like::

    ## SYSTEM
    <static system prompt text>

    ## USER
    <user-side template, may contain Jinja2 vars like {{ jd_text }}>

The SYSTEM block never gets variable substitution; it's the
`cache_system=True` payload that benefits from prefix-stable caching
(ADR-0004 D2). The USER block is rendered per call by `render_user`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobcopilot_api.models.prompt_version import PromptVersion

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

_SYSTEM_HEADER = re.compile(r"^##\s*SYSTEM\s*$", re.MULTILINE)
_USER_HEADER = re.compile(r"^##\s*USER\s*$", re.MULTILINE)
_VERSION_FILE = re.compile(r"^v[\w.-]+$")


class PromptVersionMismatchError(RuntimeError):
    """Raised at startup when a prompt file's content drifts from the row
    stored in `prompt_versions` under the same `(agent_name, version)`.

    Resolution: bump the version (rename `v1.0.0.j2` → `v1.0.1.j2`). The
    DB keeps history; never silently rewrite a published prompt.
    """


@dataclass(frozen=True)
class LoadedPrompt:
    """An entry in the runtime prompt cache.

    `id` is the `prompt_versions.id` to thread through `LLMClient.complete`
    as `prompt_version_id`, so `llm_calls.prompt_version_id` is always set
    and ADR-0004's cost/quality attribution closes the loop.
    """

    id: int
    agent_name: str
    version: str
    system: str
    user_template: str
    template_hash: str


def parse_template(content: str) -> tuple[str, str]:
    """Split a prompt file into (system, user_template) by `## SYSTEM` /
    `## USER` headers.

    Order is enforced: SYSTEM must precede USER. Anything before the
    first SYSTEM header is ignored (so templates can carry an optional
    front-matter block in the future without breaking the parser).
    """
    sys_m = _SYSTEM_HEADER.search(content)
    user_m = _USER_HEADER.search(content)
    if sys_m is None:
        raise ValueError("template missing `## SYSTEM` header")
    if user_m is None:
        raise ValueError("template missing `## USER` header")
    if sys_m.start() >= user_m.start():
        raise ValueError("`## SYSTEM` must appear before `## USER`")
    system = content[sys_m.end() : user_m.start()].strip()
    user = content[user_m.end() :].strip()
    if not system:
        raise ValueError("`## SYSTEM` block is empty")
    if not user:
        raise ValueError("`## USER` block is empty")
    return system, user


def compute_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def walk_templates(root: Path) -> Iterator[tuple[str, str, Path]]:
    """Yield `(agent_name, version, path)` for each `<root>/<agent>/v*.j2`.

    Hidden directories (`_*`) and non-versioned filenames are skipped.
    Iteration order is deterministic (sorted) so startup logs are stable.
    """
    if not root.is_dir():
        return
    for agent_dir in sorted(root.iterdir()):
        if not agent_dir.is_dir() or agent_dir.name.startswith("_"):
            continue
        for path in sorted(agent_dir.glob("v*.j2")):
            if _VERSION_FILE.match(path.stem):
                yield agent_dir.name, path.stem, path


_jinja_env = Environment(
    # Prompts are LLM input, not HTML. Autoescape would corrupt the
    # variable values (e.g. `<jd>` becomes `&lt;jd&gt;`); the LLM would
    # see escaped markup and parse poorly.
    autoescape=False,  # noqa: S701
    undefined=StrictUndefined,
    keep_trailing_newline=False,
)


def render_user(template: str, /, **variables: Any) -> str:
    """Render a USER-side Jinja2 template. SYSTEM blocks are never rendered."""
    return _jinja_env.from_string(template).render(**variables)


def _bump_hint(version: str) -> str:
    m = re.match(r"^v(\d+)\.(\d+)\.(\d+)$", version)
    if m:
        major, minor, patch = (int(x) for x in m.groups())
        return f"v{major}.{minor}.{patch + 1}"
    return f"{version}-next"


async def load_prompt_versions(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    prompts_dir: Path | None = None,
) -> dict[tuple[str, str], LoadedPrompt]:
    """Scan `prompts_dir`, upsert rows, return the in-memory cache.

    Idempotent: re-running with unchanged files is a no-op (existing rows
    re-used by id). Mismatched content raises immediately (see class
    docstring).

    Uses one session for the whole batch so partial inserts roll back
    cleanly on mismatch.
    """
    root = prompts_dir if prompts_dir is not None else PROMPTS_DIR
    out: dict[tuple[str, str], LoadedPrompt] = {}

    async with sessionmaker() as session, session.begin():
        for agent_name, version, path in walk_templates(root):
            content = path.read_text(encoding="utf-8")
            system, user_template = parse_template(content)
            template_hash = compute_hash(content)

            existing = (
                await session.execute(
                    select(PromptVersion).where(
                        PromptVersion.agent_name == agent_name,
                        PromptVersion.version == version,
                    )
                )
            ).scalar_one_or_none()

            if existing is not None:
                existing_hash = compute_hash(existing.template)
                if existing_hash != template_hash:
                    raise PromptVersionMismatchError(
                        f"prompt {agent_name}/{version} content differs from "
                        f"the row in DB. Bump the version "
                        f"(e.g. {version} → {_bump_hint(version)}) "
                        f"if you intentionally changed the template."
                    )
                pv_id = existing.id
            else:
                row = PromptVersion(
                    agent_name=agent_name,
                    version=version,
                    template=content,
                )
                session.add(row)
                await session.flush()
                pv_id = row.id

            out[(agent_name, version)] = LoadedPrompt(
                id=pv_id,
                agent_name=agent_name,
                version=version,
                system=system,
                user_template=user_template,
                template_hash=template_hash,
            )

    return out
