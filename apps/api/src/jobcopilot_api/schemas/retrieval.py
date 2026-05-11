"""Retrieval pipeline 输出 schema(M2)。

5-AGENT_DESIGN §3.1 RetrievedChunk + §2.7 PipelineResult — pipeline 把
query_rewrite → multi-query hybrid → RRF → rerank → parent-doc 编排成的
最终 chunk 集合(每个 chunk 含 heading_path / note_title / rerank_score
元数据,直接喂 quiz_generator USER 段)。

NoteChunk ORM 实体放进 RetrievedChunk.chunk(不复制字段),quiz_service
渲染 USER 段时拿 chunk.id / chunk.content;heading_path 反规范化字段从
NoteChunk 取也行,但 retrieval_pipeline 落 RetrievedChunk 时显式复制一份
方便下游 schema 校验(且 note_title 是 join 出来的非 NoteChunk 自有字段)。
"""

from __future__ import annotations

from dataclasses import dataclass

from jobcopilot_api.models.note_chunk import NoteChunk


@dataclass(frozen=True)
class RetrievedChunk:
    """retrieval_pipeline 喂给 quiz_generator 的单 chunk。"""

    chunk: NoteChunk
    folder_path: list[str]
    heading_path: list[str]
    note_title: str
    rerank_score: float


@dataclass(frozen=True)
class PipelineResult:
    """retrieval_pipeline.run 的整次产出。

    expanded_queries / retrieved_chunks 由 quiz_service 一并落 quiz_sessions
    的 expanded_queries / retrieved_chunk_ids 字段(DATA_MODEL §5.4 audit)。
    """

    expanded_queries: list[str]
    retrieved_chunks: list[RetrievedChunk]
