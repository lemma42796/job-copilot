"""ProfileParserAgent — text → LLMClient → ProfileStructured.

Pure function (M1 §设计原则 4): no DB, no file I/O, no global state. The
service layer handles raw_text persistence, PDF extraction, child-table
DELETE/INSERT, and DB updates.

Single-shot parse only — chunked parsing for >10-page resumes (and
`max_tokens` plumbing on LLMClient — STATUS.md M2 待办 #1) ship in M2.
"""

from __future__ import annotations

from jobcopilot_api.infra.prompts import LoadedPrompt, render_user
from jobcopilot_api.llm.client import LLMClient, LLMResult
from jobcopilot_api.llm.tiers import Tier
from jobcopilot_api.schemas.profiles import ProfileStructured

FEATURE = "profile_parse"
RELATED_ENTITY = "profile"


async def parse_profile(
    *,
    text: str,
    prompt: LoadedPrompt,
    llm: LLMClient,
    user_id: int | None = None,
    trace_id: str | None = None,
    related_id: int | None = None,
    timeout_s: float | None = None,
) -> LLMResult:
    """Run the resume parser against `text` using the given prompt template.

    Returns the raw `LLMResult`; service layer extracts `result.parsed`
    (`ProfileStructured`) and maps it to DB rows. `related_id` should be
    the `profiles.id` already inserted with `status='parsing'` so the
    `llm_calls` row links back.
    """
    user_text = render_user(prompt.user_template, resume_text=text)
    return await llm.complete(
        feature=FEATURE,
        tier=Tier.CHEAP,
        system=prompt.system,
        user=user_text,
        response_schema=ProfileStructured,
        cache_system=True,
        timeout_s=timeout_s,
        user_id=user_id,
        trace_id=trace_id,
        related_entity=RELATED_ENTITY,
        related_id=related_id,
        prompt_version_id=prompt.id,
    )
