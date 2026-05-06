"""Unit tests for `services.tokenize` 字符 n-gram 切分(S21 子任务 4-A)。

集成测试 `test_tokenize_consistency.py` 守护 Python ↔ SQL 一致性;本文件
只测 Python 端的 fixed cases / 边界。
"""

from __future__ import annotations

import pytest

from jobcopilot_api.services.tokenize import (
    tokenize_ngram,
    to_tsquery_string,
)


class TestTokenizeNgram:
    def test_pure_chinese(self) -> None:
        # "我精通后端" → bigram only(无 ASCII 单词)
        toks = tokenize_ngram("我精通后端")
        assert toks == ["我精", "精通", "通后", "后端"]

    def test_pure_english(self) -> None:
        # "Hello World" → 2 unigram + 8 bigram(空格不跨)
        toks = tokenize_ngram("Hello World")
        assert toks == [
            "hello",
            "world",
            "he",
            "el",
            "ll",
            "lo",
            "wo",
            "or",
            "rl",
            "ld",
        ]

    def test_mixed_zh_en(self) -> None:
        # 中英相邻 — 跨边界 bigram(`通p` / `n后`)保留(query 端会切出同样 bigram)
        toks = tokenize_ngram("我精通Python后端开发")
        assert toks == [
            "python",
            "我精",
            "精通",
            "通p",
            "py",
            "yt",
            "th",
            "ho",
            "on",
            "n后",
            "后端",
            "端开",
            "开发",
        ]

    def test_punctuation_replaced_with_space(self) -> None:
        # 多个连续标点合成一个空格,bigram 不跨该空格
        toks = tokenize_ngram("hello, world!!!")
        # "hello world" → 2 unigram + 不跨空格的 bigram
        assert "hello" in toks
        assert "world" in toks
        assert all(" " not in t for t in toks)

    def test_empty_and_none(self) -> None:
        assert tokenize_ngram("") == []
        assert tokenize_ngram(None) == []
        assert tokenize_ngram("   ") == []
        assert tokenize_ngram(",.!?") == []

    def test_single_char(self) -> None:
        # 单 ASCII 字符 → unigram,无 bigram
        assert tokenize_ngram("a") == ["a"]
        # 单中文字符 → 无 unigram,无 bigram(滑窗需 ≥ 2)
        assert tokenize_ngram("我") == []

    def test_lowercase(self) -> None:
        # ASCII 全部小写化
        toks = tokenize_ngram("LangGraph")
        assert "langgraph" in toks
        assert "LangGraph" not in toks
        assert "Lang" not in toks

    def test_numeric_word(self) -> None:
        # 数字也算 ASCII 单词
        toks = tokenize_ngram("Python3.9")
        # "python3 9" → "python3" + "9" + bigram on "python3 9"(跳空格)
        assert "python3" in toks
        assert "9" in toks


class TestToTsqueryString:
    def test_empty_input(self) -> None:
        assert to_tsquery_string("") == ""
        assert to_tsquery_string(None) == ""
        assert to_tsquery_string("   ,.!") == ""

    def test_or_join(self) -> None:
        # query "Python" → "python" + bigrams,用 ' | ' 拼接
        q = to_tsquery_string("Python")
        # ASCII unigram + bigrams
        assert " | " in q
        assert "python" in q
        # 不应该用 ' & '(AND 太严格)
        assert " & " not in q

    def test_dedup_preserves_order(self) -> None:
        # "abab" → "abab" 单词 + bigram "ab"(出现 2 次)+ "ba"
        # to_tsquery_string 去重后只保留 1 个 "ab"
        q = to_tsquery_string("abab")
        tokens_in_q = q.split(" | ")
        assert len(tokens_in_q) == len(set(tokens_in_q))  # 去重
        assert "abab" in tokens_in_q
        assert "ab" in tokens_in_q
        assert "ba" in tokens_in_q


@pytest.mark.parametrize(
    "text,expected_subset",
    [
        # query 端典型 input 触发的 token,文档侧索引必须有这些 token 才能召回
        ("Python 后端", {"python", "后端"}),
        ("AI Agent 开发", {"ai", "agent", "开发"}),
        ("熟悉 PostgreSQL 和 Redis", {"postgresql", "redis"}),
    ],
)
def test_realistic_jd_query_substring(text: str, expected_subset: set[str]) -> None:
    """真实 JD query 切法 sanity — 关键技术名词必须以 ASCII unigram 形式出现。"""
    toks = set(tokenize_ngram(text))
    assert expected_subset.issubset(toks), (
        f"Expected subset {expected_subset} not in tokens: {toks}"
    )
