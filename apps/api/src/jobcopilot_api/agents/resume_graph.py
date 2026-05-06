"""ResumeGraph — M3 W7 5 节点 LangGraph(retrieve → plan → draft → review → revise)。

封装简历定制的状态机,替代 S16 MVP 的"3 函数串调"形态。Service 层
(`resume_service.run_generate_stream`)调用 `build_resume_graph(...).astream`
迭代节点更新事件,转发给 router 的 SSE。

## 节点

1. **retrieve**:`hybrid_retrieve_for_match`(K=20,向量+lexical RRF 融合,S21 4-A)+ 加载 JD / candidate / hint
2. **plan**:`plan_resume` → ResumePlan(章节计划 + emphasis_skills)
3. **draft**:`draft_resume(plan=plan, prev_findings=None)` → markdown
4. **review**:`review_resume(draft_markdown, chunks)` → ResumeReview
5. **revise**(条件触发):`draft_resume(plan=plan, prev_findings=review.findings)`
   → markdown(替换 state["draft_markdown"]),`revision_count` += 1,回到 review

## 边

- start → retrieve → plan → draft → review
- review 条件分支:
  - `review.passed` 或 `revision_count >= max_revisions` → END
  - 否则 → revise → review

## Checkpointer

W7 **不带** checkpointer(`workflow.compile()` 默认即无)。原本想用
`MemorySaver`,实测 langgraph 0.2.x 所有 checkpointer(含 MemorySaver)都走
`JsonPlusSerializer` + ormsgpack 持久化 state — SQLAlchemy ORM 行 / LLMResult /
RetrieveResult / ResumePlan / ResumeReview 不在 ormsgpack 内置类型表里,
graph 第一次 `aput_writes` 就 raise `Type is not msgpack serializable`。
业务上也不需要:单 SSE 请求生命周期内不会跨进程恢复,W7 不开 interrupt /
不长时驻留。真正需要"中断恢复 / 长时任务"时再升,届时要么:
- 给 langgraph 注册自定义 serde(ORM 行 / dataclass pickle)
- 或重设计 state:只放 id 引用 + plain dict,nodes 内部按需重建

## State 字段说明

无 checkpointer = state 全程在内存里直接传引用,允许放 SQLAlchemy detached
ORM 行 + dataclass。这与 S16 MVP "phase-1 短 tx 拿 detached row,phase-2
复用" 模板保持一致。

## 失败语义(由 service 层处理 mark_failed)

graph 节点抛出的 `LLMUpstreamError / LLMTimeoutError / LLMSchemaInvalidError /
ResumeGenerationFailedError` 一路冒泡到 service.run_generate_stream,
service 在 except 块里调 `_mark_failed` 并 re-raise(同 S16 旁路 commit 模式)。
Graph 本身**不**捕获业务异常。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobcopilot_api.agents.resume_drafter import draft_resume
from jobcopilot_api.agents.resume_planner import plan_resume
from jobcopilot_api.agents.resume_reviewer import review_resume
from jobcopilot_api.infra.prompts import LoadedPrompt
from jobcopilot_api.llm.client import LLMClient, LLMResult, OnTokenCallback
from jobcopilot_api.llm.embedders import Embedder
from jobcopilot_api.models import Jd, ProfileChunk
from jobcopilot_api.schemas.resumes import ResumePlan, ResumeReview
from jobcopilot_api.services.retrieval_service import (
    RetrieveResult,
    build_match_query,
    hybrid_retrieve_for_match,
    load_all_profile_chunks,
)

DEFAULT_MAX_REVISIONS = 1
DEFAULT_RESUME_K = 20

DrafterTokenCallback = Callable[[str, str], Awaitable[None]]
"""(phase, delta) → awaitable. `phase` 是 `"draft"` 或 `"revise"`,让 SSE
消费方区分首轮起草与修订重写(前端遇 phase 切换重置预览缓冲)。"""


class ResumeGraphState(TypedDict, total=False):
    """5 节点共享的 state。`total=False` 让节点函数只返回部分字段更新。

    LangGraph 默认 reducer = override(后写覆盖前写),适合本场景:
    - retrieve / plan / review 各自只写自己产出的字段
    - draft / revise 都写 `draft_markdown`(revise 覆盖 draft)
    - revision_count 由 revise 节点自行 += 1 后整体写回
    """

    # 输入(由 service 层填好)
    resume_id: int
    user_id: int
    trace_id: str | None
    max_revisions: int

    # retrieve 节点产出
    jd: Jd
    hint: str | None
    candidate: dict[str, Any]
    chunks: list[ProfileChunk]  # JD-anchored Top-K — 给 planner / drafter 聚焦
    all_chunks: list[ProfileChunk]  # profile 完整 chunks — 给 reviewer 做全文事实核查
    retrieve_result: RetrieveResult

    # plan 节点产出
    plan: ResumePlan
    planner_result: LLMResult

    # draft / revise 节点产出
    draft_markdown: str
    drafter_result: LLMResult  # 最新一轮(revise 覆盖)
    drafter_results: list[LLMResult]  # 累积所有 draft + revise(便于聚合 token / cost)

    # review 节点产出
    review: ResumeReview
    reviewer_result: LLMResult  # 最新一轮(revise 后再 review 时覆盖)
    reviewer_results: list[LLMResult]  # 累积

    # revise 节点累计
    revision_count: int


@dataclass(frozen=True)
class ResumeGraphDeps:
    """运行时依赖打包。所有 node 闭包从这里读,不放 state(避免不必要的 state 字段)。

    `on_drafter_token`:可选;非 None 时 draft / revise 节点把 LLM 流式
    delta 通过该回调向上送(service 层用一个 asyncio.Queue 桥接到 SSE
    `drafter_token` 事件)。Planner / Reviewer 不开流式(JSON schema 输出
    流式没有渲染价值)。"""

    sessionmaker: async_sessionmaker[AsyncSession]
    llm: LLMClient
    embedder: Embedder
    drafter_prompt: LoadedPrompt
    reviewer_prompt: LoadedPrompt
    planner_prompt: LoadedPrompt
    k: int = DEFAULT_RESUME_K
    on_drafter_token: DrafterTokenCallback | None = None


def _phase_token_cb(
    cb: DrafterTokenCallback | None, phase: str
) -> OnTokenCallback | None:
    """Adapt a (phase, delta) deps callback into the (delta) shape that
    LLMClient.on_token wants. None in → None out(短路 streaming)。"""
    if cb is None:
        return None

    async def _wrapped(delta: str) -> None:
        await cb(phase, delta)

    return _wrapped


def build_resume_graph(
    deps: ResumeGraphDeps,
    *,
    pre_loaded: PreLoadedResumeContext | None = None,
) -> Any:
    """Compile a 5-node LangGraph(无 checkpointer,见模块 docstring "## Checkpointer")。

    Returns a `CompiledGraph` that supports `astream(initial_state, config)`。
    Caller 仍在 config 里传 `thread_id`,但没 checkpointer 时 langgraph 不
    分桶,只是兼容透传 — 未来加 checkpointer 时现有 call site 不用改。

    `pre_loaded`:Service 层在 phase-1 INSERT 后已经把 jd / hint / candidate
    从 DB 拿好(保留与 MVP 同款的预加载语义,避免 graph 内开短 tx)。retrieve
    节点直接用 pre_loaded 的字段,不再开 session。
    """

    async def retrieve_node(state: ResumeGraphState) -> dict[str, Any]:
        """Build query + run retrieval + 把 pre_loaded 的 jd / hint / candidate 写入 state。"""
        if pre_loaded is None:
            raise RuntimeError("pre_loaded resume context required for retrieve_node")
        query_text = build_match_query(pre_loaded.jd)
        retrieve = await hybrid_retrieve_for_match(
            deps.sessionmaker,
            profile_id=pre_loaded.profile_id,
            query_text=query_text,
            embedder=deps.embedder,
            k=deps.k,
        )
        if not retrieve.chunks:
            from jobcopilot_api.services.resume_service import ResumeGenerationFailedError

            raise ResumeGenerationFailedError(
                "Profile chunks 召回为空,无法生成简历(profile 是否已 chunk?embedding 是否完整?)"
            )
        # Reviewer 走全量 chunks(不是 JD-anchored Top-K)避免 [M4] 假阳性 —
        # JD-不相关的 education / language skill chunks 在 Top-K 里召回不到。
        all_chunks = await load_all_profile_chunks(
            deps.sessionmaker, profile_id=pre_loaded.profile_id
        )
        return {
            "jd": pre_loaded.jd,
            "hint": pre_loaded.hint,
            "candidate": pre_loaded.candidate,
            "chunks": retrieve.chunks,
            "all_chunks": all_chunks,
            "retrieve_result": retrieve,
        }

    async def plan_node(state: ResumeGraphState) -> dict[str, Any]:
        result = await plan_resume(
            jd=state["jd"],
            chunks=state["chunks"],
            hint=state.get("hint"),
            candidate=state.get("candidate"),
            prompt=deps.planner_prompt,
            llm=deps.llm,
            user_id=state["user_id"],
            trace_id=state.get("trace_id"),
            related_id=state["resume_id"],
        )
        plan = result.parsed
        if not isinstance(plan, ResumePlan):
            from jobcopilot_api.services.resume_service import ResumeGenerationFailedError

            raise ResumeGenerationFailedError("Planner 未返回有效的 ResumePlan")
        return {"plan": plan, "planner_result": result}

    async def draft_node(state: ResumeGraphState) -> dict[str, Any]:
        on_token = _phase_token_cb(deps.on_drafter_token, "draft")
        result = await draft_resume(
            jd=state["jd"],
            chunks=state["chunks"],
            hint=state.get("hint"),
            prompt=deps.drafter_prompt,
            llm=deps.llm,
            candidate=state.get("candidate"),
            plan=state.get("plan"),
            prev_findings=None,
            user_id=state["user_id"],
            trace_id=state.get("trace_id"),
            related_id=state["resume_id"],
            on_token=on_token,
        )
        markdown = (result.content or "").strip()
        if not markdown:
            from jobcopilot_api.services.resume_service import ResumeGenerationFailedError

            raise ResumeGenerationFailedError("Drafter 返回空 markdown")
        prev = state.get("drafter_results") or []
        return {
            "draft_markdown": markdown,
            "drafter_result": result,
            "drafter_results": [*prev, result],
        }

    async def review_node(state: ResumeGraphState) -> dict[str, Any]:
        # 用 all_chunks(profile 全量)+ candidate(profile 表上 deterministic 字段)
        # 做事实核查;chunks (JD-anchored Top-K) 漏的 education / language 等
        # 类别会在这里补回来。
        result = await review_resume(
            draft_markdown=state["draft_markdown"],
            chunks=state["all_chunks"],
            candidate=state.get("candidate"),
            prompt=deps.reviewer_prompt,
            llm=deps.llm,
            user_id=state["user_id"],
            trace_id=state.get("trace_id"),
            related_id=state["resume_id"],
        )
        review = result.parsed
        if not isinstance(review, ResumeReview):
            from jobcopilot_api.services.resume_service import ResumeGenerationFailedError

            raise ResumeGenerationFailedError("Reviewer 未返回有效的 ResumeReview")
        prev = state.get("reviewer_results") or []
        return {
            "review": review,
            "reviewer_result": result,
            "reviewer_results": [*prev, result],
        }

    async def revise_node(state: ResumeGraphState) -> dict[str, Any]:
        """Re-run drafter with prev_findings; bump revision_count."""
        on_token = _phase_token_cb(deps.on_drafter_token, "revise")
        result = await draft_resume(
            jd=state["jd"],
            chunks=state["chunks"],
            hint=state.get("hint"),
            prompt=deps.drafter_prompt,
            llm=deps.llm,
            candidate=state.get("candidate"),
            plan=state.get("plan"),
            prev_findings=state["review"].findings,
            user_id=state["user_id"],
            trace_id=state.get("trace_id"),
            related_id=state["resume_id"],
            on_token=on_token,
        )
        markdown = (result.content or "").strip()
        if not markdown:
            from jobcopilot_api.services.resume_service import ResumeGenerationFailedError

            raise ResumeGenerationFailedError("Drafter(revise)返回空 markdown")
        prev_drafts = state.get("drafter_results") or []
        return {
            "draft_markdown": markdown,
            "drafter_result": result,
            "drafter_results": [*prev_drafts, result],
            "revision_count": state.get("revision_count", 0) + 1,
        }

    def review_branch(state: ResumeGraphState) -> str:
        """Conditional edge: review → revise(if !passed && room) or END."""
        review = state.get("review")
        max_rev = state.get("max_revisions", DEFAULT_MAX_REVISIONS)
        rev_count = state.get("revision_count", 0)
        if review is not None and review.passed:
            return END
        if rev_count >= max_rev:
            return END
        return "revise"

    workflow: StateGraph = StateGraph(ResumeGraphState)
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("plan", plan_node)
    workflow.add_node("draft", draft_node)
    workflow.add_node("review", review_node)
    workflow.add_node("revise", revise_node)

    workflow.add_edge(START, "retrieve")
    workflow.add_edge("retrieve", "plan")
    workflow.add_edge("plan", "draft")
    workflow.add_edge("draft", "review")
    workflow.add_conditional_edges(
        "review", review_branch, {"revise": "revise", END: END}
    )
    workflow.add_edge("revise", "review")

    return workflow.compile()


@dataclass(frozen=True)
class PreLoadedResumeContext:
    """Service 层在 phase-1 INSERT 后从 DB 加载的 jd / hint / candidate /
    profile_id —— graph 直接用,不再开 session 读 DB(套 S16 _load_resume_for_generate
    的"短 tx,然后 detached 用"模板)。"""

    profile_id: int
    jd: Jd
    hint: str | None
    candidate: dict[str, Any]


__all__ = [
    "DEFAULT_MAX_REVISIONS",
    "DEFAULT_RESUME_K",
    "DrafterTokenCallback",
    "PreLoadedResumeContext",
    "ResumeGraphDeps",
    "ResumeGraphState",
    "build_resume_graph",
]


# ---------------------------------------------------------------------------
# Stream helper — service 层用这个把 graph 节点更新转成节点事件
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeEvent:
    """SSE 中 node_completed 事件的 payload。Router 转 SSE 时再扁平化为 dict。"""

    node: str
    revision_count: int


async def stream_resume_graph(
    graph: Any,
    initial_state: ResumeGraphState,
    *,
    thread_id: str,
) -> AsyncIterator[tuple[str, Any]]:
    """Iterate the graph, yielding `(kind, payload)` tuples:

    - `("node_completed", NodeEvent)` after each node finishes
    - `("final", ResumeGraphState)` when the graph reaches END

    Caller(`resume_service.run_generate_stream`)负责把这些事件再转成 SSE。"""
    config = {"configurable": {"thread_id": thread_id}}
    final_state: dict[str, Any] = {}
    async for step in graph.astream(initial_state, config=config, stream_mode="updates"):
        # `step` is `{node_name: state_delta}` per stream_mode="updates"
        for node_name, delta in step.items():
            if not isinstance(delta, dict):
                continue
            final_state.update(delta)
            yield (
                "node_completed",
                NodeEvent(
                    node=node_name,
                    revision_count=final_state.get("revision_count", 0),
                ),
            )
    yield ("final", final_state)
