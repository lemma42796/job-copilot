"""字符 n-gram 分词 — Python 端实现,与 SQL 函数 `public.char_ngrams(s)` 严格一致。

S21 子任务 4-A(Hybrid Search)第 2 步。

文档侧分词在 PG 端跑(GENERATED 列 `profile_chunks.content_tsv = to_tsvector('simple',
public.char_ngrams(content))`,见 alembic 0014)。Query 侧分词在 Python 跑,
两端必须切法严格一致 —— 否则 query 走不到文档已索引的 lexeme,召回率被卡到 0。

切法(SQL `char_ngrams` 镜像):
1. lower(s)
2. 把所有非 [a-z0-9 中文(U+4E00-U+9FFF)] 字符替换为单空格 + trim
3. 抽取所有 ASCII 单词作为 unigram(`Python` / `LangGraph` 整体匹配)
4. 字符滑窗 bigram,跳过含空格的 bigram + 长度必须 = 2(末尾边界)

一致性由 `tests/integration/test_tokenize_consistency.py` 守护,SQL 任何
改动需双端同步。
"""

from __future__ import annotations

import re

# CJK Unified Ideographs (U+4E00-U+9FFF) — 同 SQL 端 `[一-鿿]` 范围
_CN_RANGE = "一-鿿"
_NON_TOKEN_CHAR = re.compile(rf"[^a-z0-9{_CN_RANGE}]+")
_ASCII_WORD = re.compile(r"[a-z0-9]+")


def _normalize(text: str) -> str:
    """与 SQL `trim(regexp_replace(lower(coalesce(s, '')), '[^a-z0-9一-鿿]+', ' ', 'g'))` 严格一致。"""
    if not text:
        return ""
    s = text.lower()
    s = _NON_TOKEN_CHAR.sub(" ", s)
    return s.strip()


def tokenize_ngram(text: str | None) -> list[str]:
    """字符 bigram + ASCII unigram。

    返回 ASCII 单词在前、bigram 在后的扁平 token list,token 顺序与 SQL 端
    `char_ngrams(s)` 输出 split 后的顺序严格相同(测试守护)。
    """
    if text is None:
        return []
    s = _normalize(text)
    if not s:
        return []

    tokens: list[str] = list(_ASCII_WORD.findall(s))

    n = len(s)
    for i in range(n - 1):
        bg = s[i : i + 2]
        if " " in bg:
            continue
        if len(bg) != 2:  # 边界保护:理论上 i+2 ≤ n 时 len 恒为 2,留兜底
            continue
        tokens.append(bg)

    return tokens


def to_tsquery_string(text: str | None) -> str:
    """把 query 文本切成 token,用 ' | ' 拼成 to_tsquery 的 OR 表达式。

    OR 语义:任一 token 命中即召回,Postgres `ts_rank` 自然按命中数 / 密度
    排序 — bigram 命中越多分越高,等价于 BM25-lite 的 OR 召回。AND 太严格
    (短 query 必匹配全部 bigram 才召回,reviewer Top-K 漏召回的根因之一)。

    Tokens 去重(set 化但保序),空 query 返回空字符串(caller 应跳过 lexical 路径)。
    """
    tokens = tokenize_ngram(text)
    if not tokens:
        return ""
    seen: dict[str, None] = {}
    for t in tokens:
        seen.setdefault(t, None)
    return " | ".join(seen.keys())
