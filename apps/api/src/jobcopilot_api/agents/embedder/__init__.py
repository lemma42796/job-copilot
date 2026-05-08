"""Embedder — text-embedding-v4 批量调用 wrapper(M1,沿用 v1)。

底层走 infra/embedder.py;本目录是 agent 层的批量 / 错误处理 wrapper,
被 workers/embed_worker.py 周期性调用。
"""
