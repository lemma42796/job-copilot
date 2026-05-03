---
title: S7 ProfileParserAgent + /v1/profiles SSE + 5 表写入 — 切片归档
status: ✅ 完成已 push
purpose: 把 JD 链路同款流程复制到简历(parent + 4 children + 软删可重建 + 409 dup user)
---

# 产出

```
apps/api/
├── alembic/versions/0009_profiles_source_status.py  # source/status ENUM + 列;
│                                                    # uq_profiles_user_id 改 partial(WHERE deleted_at IS NULL)
├── src/jobcopilot_api/
│   ├── models/profile.py            # Profile + 4 子模型(无 ORM relationship,沿用 ADR-0005 D1)
│   ├── schemas/profiles.py          # ProfileSource/Status/Skill/Experience/Project/Education/Structured /
│   │                                # ParseInput / Detail(嵌 stats) / ListItem / PatchInput
│   ├── prompts/profile_parser/v1.0.0.j2   # SYSTEM/USER 双段
│   ├── agents/profile_parser/agent.py     # parse_profile 纯函数,Tier=CHEAP
│   ├── services/profile_service.py        # create_pending + run_parse + create_and_parse 包装 /
│   │                                       # _replace_children(DELETE+INSERT 4 表)/ skill 去重 /
│   │                                       # 409 ProfileExistsError / list / get / patch / soft_delete
│   ├── routers/profiles.py          # POST /parse(SSE-only)+ GET 列表/详情(嵌 structured + stats)+ PATCH + DELETE
│   └── main.py                       # include profiles router
└── tests/
    ├── unit/test_profile_parser_agent.py    # 5 个(golden / metadata / upstream / schema invalid / template render)
    ├── unit/test_profile_schemas.py         # 12 个(enum / mutex / extra-forbid / structured defaults)
    ├── integration/test_profile_service.py  # 11 个(text/dup user/软删恢复/upstream/schema/patch/skill 去重 等)
    └── integration/test_profiles_router.py  # 10 个(SSE 4 路 / 401 / list ownership / detail / patch / DELETE 重 parse)
fixtures/llm/profile_parser__golden.json    # DummyProvider 回放
```

闸门:`mypy --strict src` 50 files 0 issues / `pytest --cov` **238 passed,97.70%**(+38 测试 / +4.07pp 覆盖率)/ migration round-trip 通过 / dev DB 已到 0009。

# 设计决策

1. **profile_source / profile_status ENUM 与 jds 对称**(0009)——不是平添,是为了让"SSE `started` 必须带 resource_id"(永久约束 4)在 profile 上同样成立(必须先 INSERT pending row)。`source` 值集 `pdf_upload / text_paste / manual`(无 image_upload,简历 OCR 在 M3 才考虑)。
2. **uq_profiles_user_id 改 partial unique index**(0009)——原始 0004 是普通 `UNIQUE (user_id)`,软删后旧行还在,DELETE→重 parse 流程会被 IntegrityError 卡死。改成 `WHERE deleted_at IS NULL` 模式(沿用 S3 `uq_files_user_sha256`)。已记入永久约束 13。
3. **POST /parse 强制 SSE,不接 sync 模式**——简历比 JD 长,sync 模式响应时间 30s+ 体验差,且 S9 前端没有 sync 入口需求。`create_and_parse` 函数仍在 service 层留着,纯属 jd 对称,目前没有 caller(标注在 service docstring)。
4. **patch_profile structured 触发 DELETE+INSERT 全量子表**——不做 diff;profile 子表量小(单数十行),DELETE+INSERT 简单可靠。代价是子表 ID 不稳定(后续 S8 chunk 用 `evidence_project_ids` 引用 project id 时,patch 后会失效),目前接受;真要 ID 稳定要等 M3 简历定制。
5. **skill 名归一化去重在 service 层兜底**(`_replace_children` 用 `dict[str, ProfileSkillItem]`,后写覆盖)——`uq_ps_profile_name (profile_id, name)` 是硬约束,LLM prompt 里也写了"同一技能名只输出一次"做规则约束,但 service 不能信 LLM,必须再去一次重。
6. **每个子表独立 sort_order INTEGER**——LLM 输出顺序就是用户视觉顺序,service 直接按枚举 idx 写入。前端 S9 渲染按 `sort_order ASC` 展示。
7. **`PROFILE_PARSE_FAILED` 422 + `PROFILE_EXISTS` 409**——错误码全 PROFILE_ 前缀;ProfileExistsError 继承 ConflictError(已有 409 父类),覆盖 `code` / `title` 给业务语义。
8. **`MIN_TEXT_LENGTH = 100`**(JD 是 50)——简历比 JD 长很多,< 100 字几乎肯定不是合法简历;阈值翻倍,把"贴错内容"挡在前面。
9. **ProfileDetail 嵌 stats 而不是分接口**——AGENT_DESIGN §4.5 原本说要单独 `GET /chunks` 接口,但 S7 不做 chunks;detail 里嵌 `stats: {experiences, projects, skills, educations, chunks}` 给前端一次拿全。`chunks=0` 占位,S8 会填实数。
10. **target_titles 在 PatchInput 不允许编辑**——S9 前端表单里 target_titles 是数组列表 UI,改起来不直观;M1 单用户场景下用户极少改求职意向。要改就用 PATCH structured 整段重写。target_salary_min/max 可单独 patch。

# 期间踩坑

1. **migration 0009 改了之后 dev DB 卡半路**——第一版 0009 只加 source/status 列,把 dev DB upgrade 到 0009 后,我才意识到要补 partial unique index。downgrade 失败(因为旧 unique 是 constraint 不是 index)。处理:`docker exec` 进 PG 容器手动 `ALTER TABLE profiles DROP COLUMN source/status`、`DROP TYPE` 两个 enum、`ADD CONSTRAINT uq_profiles_user_id`、`UPDATE alembic_version SET version_num='0008'`,然后重跑 `alembic upgrade head`。**经验:在 dev DB 已经 upgrade 了一个 migration 的情况下,如果要修这个 migration 本身,必须先想好 downgrade 路径或者准备好手动清理脚本。**testcontainers 集成测试不受影响(每次都从 0000 起 upgrade)。
2. **`max_tokens` 不在 LLMClient.complete 签名里**——agent.py 第一版我写了 `max_tokens=4096`,mypy 没报错(因为 `complete` 是 Protocol),但运行会爆 TypeError。后来才想起 STATUS.md M2 待办 #1 就写了"生产 LLMClient 没设 max_tokens"。删了,简历长度风险跟 JD 同等承担,等 M2 一起修。
3. **ruff PT018 把 `assert len(x) == 1 and x[0].field == ...` 全报错**——一行写两个断言不行,要拆。无脑拆即可。
4. **测试 fixture 要同时 seed 两个 prompt key**——`make_app` 里 `app.state.prompt_versions = {("profile_parser", "v1.0.0"): ..., ("jd_parser", "v1.0.1"): ...}`,只 seed 一个会让另一个 router 的 `_resolve_prompt` 在测试外的健康检查时炸。
5. **`uq_ps_profile_name` 唯一约束触发 IntegrityError 之前看不见**——LLM 偶尔吐重复 skill name,如果不去重会在 `session.flush()` 时炸。`_replace_children` 用 `dict[str, ProfileSkillItem]` 后写覆盖兜底,顺手做了 patch 测试。

# 永久约束(影响后续切片)

只有 1 条记进 STATUS.md 永久约束 13(每用户单例资源用 partial unique index)。其他设计决策都是 profile 内部的实现细节,不跨切片。

# S8 接力点

- `profile_chunks` 表(0005 migration 已建)→ S8 实现 `services/profile_service.py:rechunk_profile()`(参考 `_replace_children` 的 DELETE+INSERT 模式)+ `routers/profiles.py:POST /{id}/rechunk` SSE
- `ProfileDetail.stats.chunks` 现在恒为 0,S8 改成实数
- patch_profile 现在不触发 rechunk(API_SPEC §6.4 明确"需显式调用"),S8 不要把 rechunk 偷偷塞进 patch
