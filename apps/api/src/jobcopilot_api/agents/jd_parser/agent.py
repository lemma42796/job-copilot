"""JDParserAgent — text → LLMClient → JDStructured. ADR-0006.

Pure function (M1 §设计原则 4): no DB, no file I/O, no global state. The
service layer handles raw_text persistence (D7), PDF extraction (D10),
title-empty validation (D8), and DB updates.

This module is stateless; it returns whatever `LLMClient.complete`
returns. On success, `result.parsed` is a `JDStructured` instance; on
schema-invalid retries that exhaust budget, the client raises
`LLMSchemaInvalidError` (mapped to 422 `JD_PARSE_FAILED` upstream).
"""

from __future__ import annotations

from jobcopilot_api.infra.prompts import LoadedPrompt, render_user
from jobcopilot_api.llm.client import LLMClient, LLMResult
from jobcopilot_api.llm.tiers import Tier
from jobcopilot_api.schemas.jds import JDStructured

FEATURE = "jd_parse"
RELATED_ENTITY = "jd"


async def parse_jd(
    *,
    text: str,
    prompt: LoadedPrompt,
    llm: LLMClient,
    user_id: int | None = None,
    trace_id: str | None = None,
    related_id: int | None = None,
    timeout_s: float | None = None,
) -> LLMResult:
    """Run the JD parser against `text` using the given prompt template.

    Returns the raw `LLMResult`; service layer extracts `result.parsed`
    (`JDStructured`) and maps it to DB columns. `related_id` should be
    the `jds.id` already inserted with `status='parsing'` so the
    `llm_calls` row links back (ADR-0004 → ADR-0006 D6 chain).
    """
    user_text = render_user(prompt.user_template, jd_text=text)
    return await llm.complete(
        feature=FEATURE,
        tier=Tier.CHEAP,
        system=prompt.system,
        user=user_text,
        response_schema=JDStructured,
        cache_system=True,
        timeout_s=timeout_s,
        user_id=user_id,
        trace_id=trace_id,
        related_entity=RELATED_ENTITY,
        related_id=related_id,
        prompt_version_id=prompt.id,
    )
