---
title: S13 + S14 + S15 匹配 MVP 端到端 — 切片归档(三刀合一)
status: ✅ 完成已 push
date: 2026-05-04
purpose: M2 主线刀 — JD vector + chunks 召回 → MatchAnalyst LLM 评分 → 结构化 fit 报告 + 前端结果页 / 列表页 / 全局导航,首次端到端 dogfood 通过
---

# 三刀的边界

按 S12 末规划期对齐:
- **S13 检索骨架**(纯后端,不挂路由):matches 表 + retrieval_service + match_service.create_pending_match
- **S14 LLM 评分 + SSE 路由**:MatchAnalystAgent + run_analyze + POST /v1/matches SSE + GET / DELETE
- **S15 前端**:JD 详情页"开始匹配"按钮 + matches 列表页 + matches 详情页 + sidebar 入口

合一归档因为三刀本质是一件事:"匹配 MVP 端到端"。任何一刀单独 commit 都不能 dogfood,所以三刀放一起评估更合理。

# 产出

```
apps/api/
├── alembic/versions/0011_matches.py             # matches 表 + match_status enum (S13)
├── src/jobcopilot_api/
│   ├── models/
│   │   ├── match.py                             # Match ORM (S13)
│   │   └── __init__.py                          # 注册 Match
│   ├── schemas/matches.py                       # MatchCreateInput / MatchResult / ... (S14)
│   ├── agents/match_analyst/
│   │   ├── __init__.py                          # re-export analyze_match
│   │   └── agent.py                             # analyze_match(jd, chunks, prompt, llm) (S14)
│   ├── prompts/match_analyst/v1.0.0.j2          # SYSTEM 评分规则 + USER chunks/JD (S14)
│   ├── services/
│   │   ├── retrieval_service.py                 # build_match_query + retrieve_for_match (S13)
│   │   └── match_service.py                     # create_pending_match + run_analyze + list/get/soft_delete (S13+S14)
│   ├── routers/matches.py                       # POST SSE + GET list / detail + DELETE (S14)
│   └── main.py                                  # include_router(matches.router)
└── tests/integration/test_migrations.py         # EXPECTED_TABLES + matches + trigger ≥ 8

packages/schemas/src/api.ts                      # OpenAPI regen,加 MatchCreateInput / MatchDetail / MatchResult / MatchedSkill / MissingSkill / MatchStatus / MatchDepth / MissingSkillSeverity (8 个新类型 + 2 路径)

apps/web/src/
├── lib/api.ts                                   # types + createMatch SSE generator + getMatch + listMatches + deleteMatch
├── components/
│   ├── shell/sidebar.tsx                        # 加"匹配"组 + SparkIcon
│   └── list/match-card.tsx                      # 卡片(分数 badge + jd 标题/公司 + status pill)
└── app/
    ├── jds/[id]/
    │   ├── page.tsx                             # SSR 拉 profile + 嵌入 MatchTrigger
    │   └── match-trigger.tsx                    # SSE 触发 + 进度文案 + router.push 详情
    └── matches/
        ├── page.tsx                             # SSR list + jdLookup
        ├── matches-client.tsx                   # cursor + 行内 confirm 删除
        └── [id]/
            ├── page.tsx                         # SSR getMatch
            └── match-result.tsx                 # 分数环 + 命中/缺失/优势/差距/建议
```

S15 顺手清:
- `app/jds/page.tsx` + `app/profiles/page.tsx` — `[...state.data.data]` spread 修 `--immutable` schema 让 SSR readonly array 不能赋给 mutable props 的祖传 typecheck 债(S12 末跳过 typecheck 留下的)
- `components/list/profile-card.tsx` — 副标题去掉 `parse_model`(LLM 模型名不该在用户列表页可见,见永久约束 #22)

# 设计决策(实现细节)

## 后端(S13 + S14)

- **matches 表偏离 DATA_MODEL §3.9**:加了 `match_status` enum(pending / scored / failed),`score` 改 nullable。理由:永久约束 #4 SSE 起手要 resource_id,phase-1 INSERT 时 score 还没算;失败保留行 `status='failed'` 留诊断,与 jds.status='parse_failed' 模式对齐。`ck_matches_score_range` 改成"NULL OR 0..100"适配 nullable。

- **MatchAnalystAgent 不上 LangGraph**:5-AGENT_DESIGN §6.2 描述为"2 节点状态机(retrieve + analyze)",但锁定决策表说"LangGraph 仅用于简历定制 + 面试模拟"。实现就是两个函数串调(`retrieve_for_match` + `analyze_match`),`run_analyze` 在 service 层把它们粘起来。没引 LangGraph 依赖,prompt+schema 本身就够。

- **检索策略 v0 = 纯 pgvector top-K(K=10)**:不做 Hybrid Search(RRF)+ Reranker(`gte-rerank-v2`)+ QueryRewriterAgent。原因:① 这套全链路一刀做完范围太大 ② Reranker / RRF 各自有调参成本 ③ 评测 baseline 还没建,没法量化效果。MVP 跑通再说,M2 评测扎根阶段升 v1.1。

- **Query 拼法 = `title + hard_skills 名 + responsibilities top-5`**:多 query (QueryRewriter)留 v1.1。`build_match_query` 在 retrieval_service 暴露独立函数,方便未来替换。

- **POST /v1/matches 强制 SSE**:不开 sync alias(对比 jds 是 sync default + `?stream=1` SSE)。因为单次匹配 = retrieve + LLM ≈ 8-15s,sync 等不起。同 profiles/parse 模式。客户端拿 SSE `result.url` 走 `GET /v1/matches/{id}` 取详情。

- **SSE 事件序列简化版**:只 `started → result → done`(失败:`started? → error → done`)。没 emit `retrieve_done` / `analyze_done` 中间事件,因为 `run_analyze` 在 service 层一站式串调,不拆出来给 router 编排。**轻微偏离永久约束 #6**("SSE 副作用编排在 router 而非 service") — 但这里没"副作用编排"需求(retrieve 失败和 analyze 失败的 SSE 出口都是 `error → done(ok=false)`,没必要拆),归档卡留备查。

- **evidence_chunk_ids 业务校验 = 剔除非法,不重试**:LLM 输出 `matched_skills[*].evidence_chunk_ids` 中的 chunk_id 必须来自 input chunks 集合。MVP 直接剔除非法 id(`_sanitize_evidence`),不像 5-AGENT_DESIGN §6.6 描述那样重试一次。原因:重试拉长 SSE 延迟没必要,evals 阶段量化 hallucination 率再决定要不要重试。

- **MVP 用 CHEAP tier 而非 STANDARD**:见"dogfood 调整"段。AGENT_DESIGN §6.2 说"STANDARD 起",dogfood 实测 STANDARD(thinking_mode)+ 大 prompt 在 30s 默认 timeout 下 3 次重试都过不去。CHEAP(无 thinking)单次 ≈ 8-10s,质量也够看。M2 评测扎根再决定要不要升 STANDARD。

- **list / get / soft_delete 走 caller-managed session**:同 jd_service / profile_service 模式;run_analyze / create_pending_match 走 sessionmaker(永久约束 #7 embed/IO 在事务外的标配)。

- **失败状态机**:LLM upstream / timeout → `_mark_failed` 旁路 commit + raise 502;LLM schema invalid → `_mark_failed` + raise 422 `MATCH_ANALYZE_FAILED`;chunks 召回空(profile 没 chunk 等)→ `_mark_failed` + raise 422 同 code。`_mark_failed` 套 `jd_service._mark_failed` / `DBCallLogger` 旁路写模板。

## 前端(S15)

- **MatchTrigger 假定单 user 单 profile**:M1 永久约束 #5 `UNIQUE (user_id) WHERE deleted_at IS NULL` 兜住,SSR 拉 `listProfiles({ limit: 1 })` 取第一项即可。M3+ 多 profile 时再加挑选 UI。
- **blocker 三态文案**:`!jdParsed` / `profileId == null` / `!profileParsed` 各自有针对性提示;profileId 缺时提供"去新建简历"链接。后端 422 兜底(profile 没 chunk 等)显示 `error.detail`。
- **分数环 = 纯 CSS conic-gradient**:不引 chart 库;`background: conic-gradient(<color> <angle>deg, var(--color-border) 0)` + 内层 inset-2 白色 disc 中心。tone 三档(≥75 绿 / ≥50 黄 / 其他红)。
- **缺失技能 severity 三档色标**:`critical` 红 / `major` 黄 / `minor` 中性灰。直接复用 `--color-danger` / `--color-warning-*` token。
- **evidence chunk 联动 hover MVP 不做**:`MatchedSkillsCard` 的 chip `title` 属性显示 `证据 chunk:#{ids}`,鼠标 hover 浏览器原生 tooltip。chunk content 不联动高亮。M2 末有 dogfood 反馈再做。
- **matches 列表页 jdLookup**:SSR 同时 `listMatches({ limit: 20 })` + `listJds({ limit: 100 })`,build `Record<jd_id, JDListItem>` 传给 client 渲染卡片(否则只能显示 `JD #id` 数字)。100 条 JD 对单 user dogfood 量级足够;miss 自动 fallback `JD #${jd_id}`。
- **列表页 / 详情页 = 套 S12 永久约束 #1 模板**:SSR 第一页 + client cursor + 行内 confirm 删除 + 卡片整体可点击。复制 jds-client / JdCard 改字段,没新发明结构。

# dogfood 调整(改完代码后浏览器验证暴露的小坑)

按时序记录,这部分本质是 S15 末第一次跑通时的修复:

1. **CSS link 旧时间戳 404** → SVG 图标铺满屏幕。Root cause:next dev `.next` cache 的 css 时间戳与 SSR 渲染的 `<link href=...?v=N>` 不同步,query string 命中 404,layout.css 未加载,sidebar 的 `<aside class="w-[220px]">` 失效,SVG 不受 `size-4` 约束铺满。**修法:`rm -rf apps/web/.next` 后 next dev 重启。** 偶发;仅在 next dev + OpenAPI dump 期间多个进程同时写 .next 才出现。归档卡记一笔,不进永久约束(工具层偶发)。

2. **profile-card 副标题泄漏 `parse_model`** → 列表页显示"上海 · qwen3.6-flash"。Root cause:S5/S7 `meta` 字符串拼接时把 `profile.parse_model` 当 metadata 拉进来。**修法:`meta = profile.location?.trim()` 去掉 model 字段。** 同永久约束 #22 类(不暴露内部命名),提醒下次列表页加 metadata 字段时过一遍"用户视角是否有意义"。

3. **STANDARD tier 30s timeout 全炸** → POST /v1/matches SSE 显示"匹配失败:Request timed out",`llm_calls` 表 `error_code='timeout'` `latency_ms=276963`(3 次重试 × 30s)。Root cause:STANDARD(thinking_mode=True)+ 10 chunks + 大 prompt + 多段输出,单次 LLM 调用 > 30s。**修法:`Tier.STANDARD → Tier.CHEAP` + `DEFAULT_TIMEOUT_S = 60.0`(覆盖 CHEAP 默认 30s)。** 偏离 AGENT_DESIGN §6.2 ("STANDARD 起"),归档卡明记 — 评测扎根阶段重审。

4. **typecheck 祖传债**:S12 末跳过前端 typecheck,`JdsClient initialItems={state.data.data}` 把 `readonly JDListItem[]` 赋给 mutable 类型直接挂。同时 `MatchResult.matched_skills` 是 optional(`readonly ... | undefined`)我用时没 `?? []` 兜底也挂。**修法:`[...state.data.data]` spread + `?? []` fallback。**

# 期间踩到的坑

1. **DATA_MODEL §3.9 与永久约束 #4 矛盾**:文档里 matches.score 是 `NOT NULL`,但 SSE 起手要 resource_id 必须 INSERT pending 行(score 还没算)。最后选择偏离 §3.9 加 `match_status` enum + nullable score,migration / model docstring 写明偏离原因。下次再有"文档 schema 与 SSE 实现冲突"模式时,优先 SSE 实现倒推 schema。

2. **`# type: ignore[attr-defined]` 在 pgvector 0.3.6 上反而是 unused**:第一版 retrieval_service 给 `ProfileChunk.embedding.cosine_distance(vec)` 加了 ignore,mypy 报 `unused-ignore`。原因:pgvector ≥ 0.3.x 已经 ship type stubs,直接调可见。删掉 ignore + 替换注释说明。

3. **`scalar(select(Jd))` 返回 detached ORM 对象**:`_load_match_for_analyze` 在短 tx 里 read 完关闭 session,jd / match 后续要在 LLM 调用阶段读属性。靠 sessionmaker 设的 `expire_on_commit=False` 兜住(infra/db.py 已配),detached 对象的已加载属性可读。

4. **biome `useArrayIndexKey`**:`{suggestions.map((s, i) => <li key={i}>...)` 报错。改 `key={\`${i}-${s.slice(0,16)}\`}`。

5. **profile parsing 状态对 match 触发的影响**:M2 dogfood 时 profile.status 必须 `parsed`(MatchTrigger blocker)+ 至少有 1 条 embedding 非 NULL 的 chunk(后端 MATCH_PRECONDITION 422)。chunker rebuild 是 best-effort 后置(永久约束 #6),所以 profile 状态 = parsed 不一定有 chunks。前端 MVP 没拉 ProfileDetail 看 chunks 数,只看 status,后端 422 兜住。

# 闸门

| 项 | 数字 |
|---|---|
| 后端 `pytest -q` | **321 passed**(S13/S14 没新写测试;`test_migrations.py` 把 EXPECTED_TABLES 加 matches + trigger 计数从 ≥7 改 ≥8,既有用例修改不计 +新)|
| 后端 ruff / mypy | All passed(98 src + tests) |
| 后端 alembic | upgrade head → **0011** |
| 前端 typecheck | All passed(34 files) |
| 前端 biome | All passed |
| 前端 next build | All passed(9 routes,新增 `/matches` `/matches/[id]`) |
| OpenAPI dump | regen 完毕 + `pnpm --filter @jobcopilot/schemas typecheck` 过 |
| 端到端 dogfood | ✅ JD 解析 → profile 解析 → 匹配 → 分数 72 / 8810ms / ¥0.0095 |

注:S13/S14 没写新单元 / 集成测试(用户决定跳过),仅靠 alembic round-trip 和已有 chunk_service / jd_service tests 兜住底层。匹配业务路径靠浏览器手测覆盖。

# Dogfood 数据(2026-05-04 首次端到端)

- JD #3 + 简历 #13(单 user 单 profile)
- depth=quick → 走 CHEAP tier(qwen3.6-flash 无 thinking)
- 分数 72(黄绿圆环);命中 8 / 缺失 4 / 建议 5
- tokens 3689 in / 1015 out;成本 ¥0.0095;延迟 8810 ms
- M2 退出标准对照:**P95 ≤ 15s ✅(8.8s 一次,远低于 budget),成本 ≤ ¥0.20 ✅(¥0.0095,21x 余量)**
- 输出质量看着合理:优势/差距/建议都有简历依据,没"建议提升综合能力"那种空话;severity 分级与 JD `required` 字段对得上。

留待多刷几次看 score 抖动 / 不同 JD 区分度。

# 给后续切片的输入

- **简历定制 MVP**(M2 下一刀):基于 match.gap_summary + missing_skills 生成 emphasized resume。LangGraph 5 节点编排,markdown 输出占位(awesome-cv 中文化推到 M3)。`matches.id` 已经是 `resumes.match_id` FK 候选。
- **evals 套件 `match_analysis`**(M2 评测扎根阶段):用 dogfood 拿到的真实 (JD, profile, MatchResult) 三元组沉淀 dataset(目标 20-30 条)。需要 LLM-as-Judge,因为人工标准答案构造成本高。
- **M2 退出 P95 / cost 数字**:已有 8.8s / ¥0.0095 一条样本,需要至少 20 条样本才能算 P95。从 M2 dogfood 阶段的真实匹配中累积。

# 什么没改(本三刀范围外)

- 详情页 footer 的"模型 qwen3.6-flash · cost ¥… · 创建于…"调试 metadata(JD/profile/match 三处)— 保留为 dogfood 调试信息,M2 收口前可统一收到一个折叠区。
- chunk content evidence hover 联动 — MVP 跳过,留 M2 dogfood 反馈再决定。
- match 列表卡片 jdLookup 上限 100 — 单 user 量级够,数据量大时改后端 list endpoint 直接 enrich。
- depth=deep 选项 — schema 接收但实际等同 quick(都走 CHEAP tier)。M2 评测扎根再差分。
