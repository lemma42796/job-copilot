---
title: S4 JDParserAgent + /v1/jds/parse SSE + 读改删 + prompt_versions — 切片归档
status: ✅ 完成已 push
purpose: 见 ADR-0006
---

# 产出

```
apps/api/src/jobcopilot_api/
├── models/jd.py               # JD ORM(无 ORM FK,延续 ADR-0005 D1)
├── schemas/jds.py             # JdSource / JdStatus / JDStructured / JDSkill /
│                              # JDParseInput(text/file_id 二选一)/ JDParseResponse /
│                              # JDListItem / JDListResponse / JDDetail / JDPatchInput
├── prompts/jd_parser/v1.0.0.j2  # SYSTEM/USER 双段 Jinja2 模板
├── agents/jd_parser/agent.py    # parse_jd 纯函数,prompt_version_id 透到 LLMResult
├── infra/
│   ├── prompts.py             # 扫描 + sha256 hash + upsert + lifespan 缓存 +
│   │                          # PromptVersionMismatchError 启动报错
│   └── pdf.py                 # pypdfium2 抽取,PdfExtractionError(422)
├── services/jd_service.py     # create_pending_jd + run_parse + create_and_parse 包装 /
│                              # list / get / patch / soft_delete / 失败 4 分支
└── routers/jds.py             # POST /parse(sync 201 + ?stream=1 SSE)/
                               # GET 列表(cursor)+ 详情 / PATCH / DELETE 204
```

依赖新增:`pypdfium2>=4.30.0`(S4-A)、`jinja2>=3.1.4`(S4-B)。`main.py` lifespan
接线 `app.state.prompt_versions = await load_prompt_versions(get_sessionmaker())`。
**S4 不需要新 migration**(0003 / 0006 已建 jds 表 + ENUM + prompt_versions / llm_calls)。

> S4 永久约束(影响后续切片决策的 4 条)留在 STATUS.md,不在此重复。
