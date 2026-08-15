"""Agent IO Pydantic schema — 每个 agent 一个文件(docs/TECH_DESIGN.md)。

引用关系:
- services/ 通过 agents/<name>/agent.py.run(input) 调用
- agents/<name>/agent.py 引用本目录 schema 做 Pydantic 校验 + retry
- service 层不直接 import LLM 客户端,只靠 agent 返回的 schema 落库
"""
