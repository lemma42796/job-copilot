"""LLM 调用层 — 每个子目录一个 agent。

骨架阶段(M0):仅占位 stub,run() 抛 NotImplementedError。
具体编排在对应 milestone 落地:

- quiz_generator       M1
- answer_judge         M2
- embedder             M1(沿用 v1 infra/embedder.py 的批量 wrapper)
- jd_parser            M2.5
- jd_aggregator        M2.5
- resume_advisor       M3
- followup_orchestrator M3(LangGraph)

模块职责边界见 2-TECH_DESIGN §4.3:agent 渲染 prompt + 调 LLM + Pydantic 校验,
不写 DB(返回结构化结果给 service 层落)。
"""
