"""chunker 单测 — split_markdown / estimate_tokens 纯函数路径。

不连 DB,只验切分逻辑。rechunk_note / get_chunks_for_node 走集成测(testcontainers)。

测试用 monkeypatch 把 MAX_CHUNK_TOKENS 改小,避免每个 case 写 1000 token markdown。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from jobcopilot_api.services import chunk_service
from jobcopilot_api.services.chunk_service import (
    ParsedChunk,
    estimate_tokens,
    split_markdown,
)


# --------------------- estimate_tokens ---------------------


def test_estimate_tokens_empty() -> None:
    assert estimate_tokens("") == 0


def test_estimate_tokens_cjk_one_to_one() -> None:
    # 10 个中文字符 → 10 token
    assert estimate_tokens("锁升级机制偏向轻量重量") == 11  # 11 个汉字


def test_estimate_tokens_ascii_quarter() -> None:
    # "synchronized" 12 字符 → ceil(12/4)=3 token
    assert estimate_tokens("synchronized") == 3


def test_estimate_tokens_mixed() -> None:
    # 5 个汉字 + 8 个 ASCII = 5 + 2 = 7
    assert estimate_tokens("锁升级机制 abcdefgh") == 5 + (9 + 3) // 4


# --------------------- split_markdown happy path ---------------------


def test_empty_markdown() -> None:
    assert split_markdown(["Java"], "") == []
    assert split_markdown(["Java"], "   \n\n  ") == []


def test_single_h2_short() -> None:
    md = "## 锁升级\n\n无锁 → 偏向 → 轻量 → 重量。"
    chunks = split_markdown(["Java", "并发"], md)
    assert len(chunks) == 1
    c = chunks[0]
    assert c.folder_path == ["Java", "并发"]
    assert c.heading_path == ["锁升级"]
    assert c.heading_level == 2
    assert c.chunk_index == 0
    assert "无锁" in c.content
    assert c.content.startswith("## 锁升级")  # heading 行保留在 content 里


def test_h1_then_h2_path_chains() -> None:
    md = "# JVM\n\n概述。\n\n## 锁升级\n\n升级链。\n\n## 偏向锁\n\n撤销条件。"
    chunks = split_markdown(["Java"], md)
    # H1 单独成一个 chunk + 两个 H2 各一个
    assert len(chunks) == 3
    paths = [c.heading_path for c in chunks]
    levels = [c.heading_level for c in chunks]
    assert paths == [["JVM"], ["JVM", "锁升级"], ["JVM", "偏向锁"]]
    assert levels == [1, 2, 2]
    assert [c.chunk_index for c in chunks] == [0, 1, 2]


def test_h3_merged_into_h2_when_short() -> None:
    md = (
        "## 锁升级\n\n升级链概述。\n\n"
        "### 偏向锁\n\n偏向锁简介。\n\n"
        "### 轻量级锁\n\n轻量级锁简介。\n"
    )
    chunks = split_markdown(["Java"], md)
    # 总长很短 → 不拆 H3,合并成一个 H2 chunk
    assert len(chunks) == 1
    assert chunks[0].heading_path == ["锁升级"]
    assert chunks[0].heading_level == 2
    assert "### 偏向锁" in chunks[0].content
    assert "### 轻量级锁" in chunks[0].content


def test_preamble_no_heading() -> None:
    md = "无 heading 的纯段落,直接是正文。\n\n第二段。"
    chunks = split_markdown(["杂记"], md)
    assert len(chunks) == 1
    assert chunks[0].heading_path == []
    assert chunks[0].heading_level == 0


def test_preamble_then_h2() -> None:
    md = "导言。\n\n## 第一节\n\n内容。"
    chunks = split_markdown(["X"], md)
    assert len(chunks) == 2
    assert chunks[0].heading_path == []
    assert chunks[0].heading_level == 0
    assert chunks[1].heading_path == ["第一节"]
    assert chunks[1].heading_level == 2


# --------------------- fenced code block ---------------------


def test_hash_inside_code_fence_not_heading() -> None:
    md = (
        "## 注释规则\n\n"
        "Python 注释:\n\n"
        "```python\n"
        "# 这是注释\n"
        "## 这也是注释,不是 H2\n"
        "x = 1\n"
        "```\n\n"
        "JS 也类似。\n"
    )
    chunks = split_markdown(["Lang"], md)
    assert len(chunks) == 1
    assert chunks[0].heading_path == ["注释规则"]
    # code fence 内的 ## 没把 chunk 切开
    assert "x = 1" in chunks[0].content
    assert "## 这也是注释" in chunks[0].content


def test_tilde_fence_also_recognized() -> None:
    md = "## A\n\n~~~\n## fake h2\n~~~\n\n## B\n\n真正 H2。"
    chunks = split_markdown(["X"], md)
    assert len(chunks) == 2
    assert chunks[0].heading_path == ["A"]
    assert chunks[1].heading_path == ["B"]


# --------------------- 长度兜底拆分 ---------------------


def test_long_h2_splits_to_h3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chunk_service, "MAX_CHUNK_TOKENS", 30)
    body = "升级链描述。" * 5  # 约 30 个汉字 = ~30 token,触发拆分
    md = (
        f"## 锁升级\n\n{body}\n\n"
        f"### 偏向锁\n\n{body}\n\n"
        f"### 轻量级锁\n\n{body}\n"
    )
    chunks = split_markdown(["Java"], md)
    # H2 整体超阈值 → 拆 H3:H2 自己一段 + 两个 H3 各一段
    assert len(chunks) >= 2
    # 至少包含两个 H3 path
    h3_paths = [c.heading_path for c in chunks if c.heading_level == 3]
    assert ["锁升级", "偏向锁"] in h3_paths
    assert ["锁升级", "轻量级锁"] in h3_paths


def test_long_h3_paragraph_fallback_with_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chunk_service, "MAX_CHUNK_TOKENS", 20)
    monkeypatch.setattr(chunk_service, "OVERLAP_TOKENS", 5)
    p1 = "段落甲" * 10  # 30 token
    p2 = "段落乙" * 10
    p3 = "段落丙" * 10
    md = f"### 单 H3\n\n{p1}\n\n{p2}\n\n{p3}\n"
    chunks = split_markdown(["X"], md)
    # H3 超长 → 段落兜底拆,至少 2 个 chunk + 第二个起 prepend "...(承上)"
    assert len(chunks) >= 2
    assert all(c.heading_path == ["单 H3"] for c in chunks)
    # 第 2 chunk 起带 overlap 标记
    assert any("(承上)" in c.content for c in chunks[1:])


# --------------------- chunk_index 单调递增 ---------------------


def test_chunk_index_monotonic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chunk_service, "MAX_CHUNK_TOKENS", 30)
    md = (
        "## A\n\n"
        + "正文甲" * 10
        + "\n\n## B\n\n"
        + "正文乙" * 5
        + "\n\n### B1\n\n"
        + "正文 B1" * 10
        + "\n"
    )
    chunks = split_markdown(["X"], md)
    indexes = [c.chunk_index for c in chunks]
    assert indexes == list(range(len(chunks)))


# --------------------- ParsedChunk frozen ---------------------


def test_parsed_chunk_frozen() -> None:
    c = ParsedChunk(
        folder_path=["X"],
        heading_path=["A"],
        heading_level=2,
        chunk_index=0,
        content="x",
    )
    with pytest.raises(FrozenInstanceError):
        c.chunk_index = 1  # type: ignore[misc]
