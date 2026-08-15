"""后台异步任务 — 不走 router 路径,启动钩子挂在 main.py lifespan。

模块职责边界(docs/TECH_DESIGN.md):
- workers/ 只跑后台异步队列(embedding 等),不暴露 HTTP

当前 worker:
- embed_worker:轮询 note_chunks where embedding IS NULL 批量算(M1)
"""
