"""Heading-aware markdown chunker(M1)。

切粒度规则(常量在模块顶部,dogfood 后调动作只改这两个数):

1. 默认按 H2 切片 — 用户在 markdown 里打的 H2 已经是语义边界
2. H2 chunk tokens > MAX_CHUNK_TOKENS 才拆 H3
3. H3 还超 → 按段落(空行)兜底拆,相邻段落 chunk 之间 prepend 上 chunk
   末尾 OVERLAP_TOKENS 估算字符的 overlap(防 LLM 看到的内容刚好被切边界打断)

token 估算不引外部 tokenizer 库:CJK char ≈ 1 token,ASCII char ≈ 1/4 token。
偏多估算(留余量),qwen 实际 tokenizer 跑出来一般略低,不影响"超阈值才拆"决策。

数据流(docs/TECH_DESIGN.md):chunker 只切 chunks + 落 content + content_tsv,
embedding 留 NULL;workers/embed_worker 异步补 embedding。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from jobcopilot_api.models.note import Note
from jobcopilot_api.models.note_chunk import NoteChunk

MAX_CHUNK_TOKENS = 1000
OVERLAP_TOKENS = 100

_HEADING_LINE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE_LINE = re.compile(r"^(```|~~~)")


@dataclass(frozen=True)
class ParsedChunk:
    folder_path: list[str]
    heading_path: list[str]
    heading_level: int
    chunk_index: int
    content: str


def estimate_tokens(s: str) -> int:
    """简化估算:CJK 1:1 + ASCII 4:1。偏多 — 留余量。"""
    if not s:
        return 0
    cjk = sum(1 for c in s if "一" <= c <= "鿿")
    other = len(s) - cjk
    return cjk + (other + 3) // 4


def _split_at_levels(
    content_md: str,
    max_level: int,
    prefix_path: list[str] | None = None,
) -> list[tuple[list[str], int, str]]:
    """切到 max_level 粒度(max_level=2:切 H1/H2;max_level=3:切 H1/H2/H3)。

    遇到 level <= max_level 的 heading 开新 block;level > max_level 的 heading
    不开新 block,heading 行连同正文留在当前 block 内。

    fenced code block(``` 或 ~~~)内的 # 不识别为 heading。

    prefix_path:递归 sub-split 时父 heading_path 前缀,拼到产出的 sub_path 前。
    """
    prefix = list(prefix_path or [])
    blocks: list[tuple[list[str], int, str]] = []
    heading_stack: list[tuple[int, str]] = []
    cur_lines: list[str] = []
    cur_path: list[str] = list(prefix)
    cur_level = 0
    in_fence = False
    fence_marker: str | None = None

    def flush() -> None:
        nonlocal cur_lines
        content = "\n".join(cur_lines).strip()
        if content:
            blocks.append((list(cur_path), cur_level, content))
        cur_lines = []

    for line in content_md.splitlines():
        m_fence = _FENCE_LINE.match(line)
        if m_fence:
            marker = m_fence.group(1)
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif fence_marker and line.startswith(fence_marker):
                in_fence = False
                fence_marker = None
            cur_lines.append(line)
            continue

        if in_fence:
            cur_lines.append(line)
            continue

        m_heading = _HEADING_LINE.match(line)
        if m_heading:
            new_level = len(m_heading.group(1))
            new_text = m_heading.group(2)

            while heading_stack and heading_stack[-1][0] >= new_level:
                heading_stack.pop()
            heading_stack.append((new_level, new_text))

            if new_level <= max_level:
                flush()
                cur_path = prefix + [t for _, t in heading_stack]
                cur_level = new_level
                cur_lines = [line]
            else:
                cur_lines.append(line)
            continue

        cur_lines.append(line)

    flush()
    return blocks


def _split_long_block(
    folder_path: list[str],
    heading_path: list[str],
    heading_level: int,
    content: str,
    chunk_index_start: int,
) -> list[ParsedChunk]:
    """H3 段落兜底:按空行切段落,贪心累积到 MAX_CHUNK_TOKENS,相邻 chunk 加 overlap。

    overlap 实现:取上一 chunk 末尾约 OVERLAP_TOKENS * 2 字符(token 估算上界),
    prepend 到新 chunk 开头,加 "...(承上)" 标记让 LLM 知道是上下文回顾不是主线内容。
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", content) if p.strip()]
    if not paragraphs:
        return []

    chunks: list[ParsedChunk] = []
    cur_paras: list[str] = []
    cur_tokens = 0
    next_idx = chunk_index_start

    def flush() -> None:
        nonlocal cur_paras, cur_tokens, next_idx
        if not cur_paras:
            return
        body = "\n\n".join(cur_paras)
        if chunks:
            tail = chunks[-1].content
            overlap_chars = OVERLAP_TOKENS * 2
            overlap = tail[-overlap_chars:].lstrip()
            if overlap:
                body = f"...(承上)\n\n{overlap}\n\n{body}"
        chunks.append(
            ParsedChunk(
                folder_path=folder_path,
                heading_path=heading_path,
                heading_level=heading_level,
                chunk_index=next_idx,
                content=body,
            )
        )
        cur_paras = []
        cur_tokens = 0
        next_idx += 1

    for p in paragraphs:
        p_tokens = estimate_tokens(p)
        if cur_paras and cur_tokens + p_tokens > MAX_CHUNK_TOKENS:
            flush()
        cur_paras.append(p)
        cur_tokens += p_tokens

    flush()
    return chunks


def split_markdown(
    folder_path: list[str], content_md: str
) -> list[ParsedChunk]:
    """Heading-aware markdown chunker — 模块 docstring 是 SSoT。"""
    h2_blocks = _split_at_levels(content_md, max_level=2)
    if not h2_blocks:
        return []

    chunks: list[ParsedChunk] = []
    next_idx = 0
    for path, level, content in h2_blocks:
        if estimate_tokens(content) <= MAX_CHUNK_TOKENS:
            chunks.append(
                ParsedChunk(
                    folder_path=folder_path,
                    heading_path=path,
                    heading_level=level,
                    chunk_index=next_idx,
                    content=content,
                )
            )
            next_idx += 1
            continue

        # H2 太长 → 拆 H3。父 heading_path = path[:-1](去掉 H2 自己),
        # _split_at_levels 内部 stack 会重新识别 H2 + H3,得到正确的 sub_path。
        h3_blocks = _split_at_levels(
            content, max_level=3, prefix_path=path[:-1] if path else None
        )
        for sub_path, sub_level, sub_content in h3_blocks:
            if estimate_tokens(sub_content) <= MAX_CHUNK_TOKENS:
                chunks.append(
                    ParsedChunk(
                        folder_path=folder_path,
                        heading_path=sub_path,
                        heading_level=sub_level,
                        chunk_index=next_idx,
                        content=sub_content,
                    )
                )
                next_idx += 1
            else:
                paragraph_chunks = _split_long_block(
                    folder_path,
                    sub_path,
                    sub_level,
                    sub_content,
                    next_idx,
                )
                chunks.extend(paragraph_chunks)
                next_idx += len(paragraph_chunks)

    return chunks


async def rechunk_note(
    session: AsyncSession, note_id: int, *, user_id: int
) -> int:
    """笔记内容更新 / 新建后重新切片。

    DELETE + INSERT 同事务:旧 chunks 全删,新 chunks 入库,embedding 留 NULL
    给 embed_worker 异步补。返回 chunk 数。

    note 不存在或不属于该用户 → ValueError(service 层包装成 note_not_found)。
    chunks 继承 note 的 `user_id`,让召回侧能只用 note_chunks 一张表做归属过滤。
    """
    note = (
        await session.execute(
            select(Note)
            .where(Note.id == note_id)
            .where(Note.user_id == user_id)
        )
    ).scalar_one_or_none()
    if note is None:
        raise ValueError(f"note {note_id} not found")

    await session.execute(
        delete(NoteChunk)
        .where(NoteChunk.note_id == note_id)
        .where(NoteChunk.user_id == user_id)
    )

    parsed = split_markdown(list(note.folder_path), note.content_md)
    if parsed:
        session.add_all(
            NoteChunk(
                user_id=user_id,
                note_id=note_id,
                folder_path=p.folder_path,
                heading_path=p.heading_path,
                heading_level=p.heading_level,
                chunk_index=p.chunk_index,
                content=p.content,
            )
            for p in parsed
        )

    await session.flush()
    return len(parsed)


async def get_chunks_for_node(
    session: AsyncSession,
    folder_path: list[str],
    heading_path: list[str] | None,
    limit: int = 30,
    *,
    user_id: int,
) -> list[NoteChunk]:
    """节点 prefix 命中 chunks(docs/TECH_DESIGN.md 出题前剪枝)。

    folder_path / heading_path 都按"前缀匹配":chunks 的 folder_path 必须以
    入参 folder_path 开头(数组切片对比);heading_path 同。

    limit:超出走 hybrid_search_in_node(M1 后段填实);本函数只做朴素 prefix
    过滤 + ORDER BY (note_id, chunk_index) LIMIT。

    M1 简化:不算 chunk count 是否 < 5(insufficient_chunks),由调用方
    quiz_service 自己判。
    """
    stmt = select(NoteChunk).where(
        NoteChunk.user_id == user_id,
        NoteChunk.folder_path[1 : len(folder_path)] == folder_path,
    )
    if heading_path:
        stmt = stmt.where(
            NoteChunk.heading_path[1 : len(heading_path)] == heading_path
        )
    stmt = stmt.order_by(NoteChunk.note_id, NoteChunk.chunk_index).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())
