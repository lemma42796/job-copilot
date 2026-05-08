"""Pydantic IO 校验层 — REST 入参 / 出参 + SSE 事件 + agent IO。

模块拆分(2-TECH_DESIGN §4.1):
- notes.py / quiz.py / jd.py / resume.py / dashboard.py:REST 端点 IO
- sse.py:SSE 事件统一 schema(4-API_SPEC §2.3)
- agents/<name>.py:每个 agent 的 Input / Output(对应 5-AGENT_DESIGN §3-§8)

骨架阶段(M0):字段先按文档抄入,具体 field_validator / model_validator
等具体 milestone 实现时再补。
"""
