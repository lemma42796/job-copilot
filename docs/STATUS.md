---
title: JobCopilot 项目当前进度(单一可信源)
owner: lemma42796
last_updated: 2026-05-09
purpose: 跨会话续作的状态快照。任何新会话从这里开始读。
---

# 当前阶段

**M2 启动:聊天框 query → 全库 RAG → 出题 + Judge 三层评分 + Judge tool use + Trace 完整化。**

M1 已收口(tag `v0.3-m1-end`);hybrid search recall@5 评测挂账 M2 真消费场景(主题类 query → quiz 剪枝 + Judge tool use)。

**M2 启动前产品方向调整(2026-05-09)**:出题入口由"笔记面板点节点"改为"**聊天框 query**",笔记面板降级为查看 / 编辑 / 导航树,不再触发出题。RAG 由 M1 阶段的"节点内可选筛"升级为"**全库必做**",pipeline 四件:`query_rewriter → hybrid + RRF → reranker → parent-doc 扩展` + 0 命中守门(笔记里没这主题 → 直接报错不兜底)。query 三类形态:**M2 主题类 query("考考我多线程")** / **M3 岗位类("模拟一面 Java 后端" — 笔记 + 那一份简历 + 用户选定 JD 子集 三源融合)** / **M3 空 query("来模拟面试吧" — SR 系统自选)**。简历是单条记录(全库一行),**不做"简历库 / 多份切换"**(一个人就一份简历)。详情同步在 7-ROADMAP / 1-PRD / 2-TECH_DESIGN / 3-DATA_MODEL / 4-API_SPEC / 5-AGENT_DESIGN 七份文档。

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| M0 | 仓库改造 + 文档重写 + v2 schema + 模块骨架 | ✅ tag `v0.2-m0-end` |
| M1 | 笔记入库 + chunker + 树形导航 + Langfuse 起步 | ✅ tag `v0.3-m1-end` |
| M2 | 出题 + 答题 + Judge 三层评分 + Judge tool use + Trace 完整化 | ⏳ |
| M2.5 | JD 累积上传 + 一键分析 + 学习路径(独立有价值) | ⏳ |
| M3 | 弱点跟踪 + SR + 多轮追问 + 简历诊断(两方锚点严格) | ⏳ |

# M0 子任务进度

- ✅ README.md 重写
- ✅ docs/1-PRD.md 重写(扩展 JD 分析 + 简历诊断;目标用户全开)
- ✅ docs/2-TECH_DESIGN.md 重写(SDK / 模块 / 数据流)
- ✅ docs/3-DATA_MODEL.md 重写(+ jds / jd_analyses / resumes / resume_analyses 4 张新表)
- ✅ docs/4-API_SPEC.md 重写(+ JD 分析 + 简历诊断端点)
- ✅ docs/5-AGENT_DESIGN.md 重写(+ JdParser / JdAggregator / ResumeAdvisor + thinking 矩阵)
- ✅ docs/6-EVAL_PLAN.md 重写(+ jd_aggregator / resume_advisor 两个新 suite)
- ✅ docs/7-ROADMAP.md 重写(+ M2.5 + 重写 M3,砍语雀)
- ✅ docs/STATUS.md 重置(本文件)
- ✅ CLAUDE.md 文件导航更新
- ✅ docs/8-ENGINEERING.md 重写(仓库结构 / 工具链 / CI / 迁移 / 本地开发 / 部署 / Langfuse 实操)
- ✅ 砍旧代码 v1(110 文件 / -20.7K LoC):apps/api/agents 整目录 + services v1(7 个) + routers v1 + _deps + schemas 整目录 + prompts 整目录(6 个 v1 子目录) + models v1(7 个) + infra/{upload,pdf}.py + 配套 17 个 v1 单测 + 9 个 v1 集成测试 + apps/web/{jds,matches,profiles,resumes}/ + components/list/;沿用层留:llm/(全)/ infra/{db,embedder,llm,logging,prompts,request_id} / models/{base,llm_call,llm_response_cache,prompt_version} / services/tokenize / routers/health / web/{shell,ui,lib,layout,globals.css,page.tsx 文案改 v2}
- ✅ 新建 v2 模块骨架(详见 2-TECH §4.1):
  - ✅ models/{note, note_chunk, question, quiz_session, session_answer, knowledge_gap, jd, jd_analysis, resume, resume_analysis} 10 张 ORM 落地;__init__.py 更新 export(alembic env.py 通过 Base.metadata 看到全部)
  - ✅ agents/{quiz_generator, answer_judge, jd_parser, jd_aggregator, resume_advisor, embedder, followup_orchestrator} — 每个子目录 __init__ + agent.py(stub raise NotImplementedError)+ prompts.py(SYSTEM 占位);answer_judge 加 scoring.py(Python 算分 SSoT,三层权重 + fabricated 锁顶 50 抄入);jd_aggregator 加 frequency.py;resume_advisor 加 forbidden_patterns.py(具体正则落地)
  - ✅ services/{notes_service, chunk_service, search_service, quiz_service, answer_service, jd_service, resume_service, knowledge_gap_service} — 函数签名 + docstring + raise NotImplementedError("M{n}");tokenize.py 沿用 v1
  - ✅ schemas/{notes, quiz, jd, resume, dashboard, sse} + schemas/agents/{quiz_generator, answer_judge, jd_parser, jd_aggregator, resume_advisor, followup_orchestrator}(Pydantic Input/Output 按 5-AGENT + 3-DATA_MODEL §6 抄入字段)
  - ✅ workers/embed_worker — asyncio.Event 退出信号 + 单批失败不打挂 + 队列空退避主循环骨架
  - ✅ main.py lifespan 挂 embed_worker — startup `asyncio.create_task(run_forever)`,shutdown `stop_event.set()` + `wait_for(10s)` 超时 cancel
- ✅ LLM SDK 切换:OpenAI Python SDK(走百炼 base_url)+ Langfuse OpenAI wrapper(`from langfuse.openai import AsyncOpenAI` 对 chat/completions/responses 自动 instrument;**embeddings.create 不在 patch 范围**,M1 第 9 步显式 `Langfuse().generation()` 包了一层);settings 加 langfuse 三件套字段;main.py 启动镜像 LANGFUSE_*(env mirror 必须早于 routers / agents / llm 的 import,见永久约束)
- ✅ 新建 alembic 0016 migration(DROP v1 表 / ENUM + CREATE v2 ENUM × 3 + v2 表 × 10 + 8 个 updated_at 触发器);test_migrations.py EXPECTED_TABLES 同步换 v2;downgrade 不实做(NotImplementedError,DATA_MODEL §10)
- ✅ docker-compose.yaml 加 langfuse + langfuse-db(image tag 锁 v2 — v3 拆 redis/clickhouse/minio 会把 compose 服务数从 6 撑到 9+;langfuse 端口 3001:3000,langfuse-db 端口 5433:5432;api 服务 environment 加 JOBCOPILOT_LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY,public_key 留空走 SDK noop)
- ✅ tag `v0.1-jobcopilot-v1` 锁 v1 末态(`390efe9` HEAD,含 v2 全套设计文档)
- ✅ M0 sanity check:百炼 OpenAI 兼容接口三件验证通过(thinking 4096 reasoning_tokens / tool_calls.function.name / 图像识别 prompt_tokens 2522)

# 当前 working tree

**M1 收口 commit 含 STATUS / 7-ROADMAP 同步 + recall@5 挂账 M2**(`0bd166f` HEAD,tag `v0.3-m1-end`)。**M2 启动前产品方向调整(2026-05-09)** 已落 7 份文档:`docs/{1-PRD, 2-TECH_DESIGN, 3-DATA_MODEL, 4-API_SPEC, 5-AGENT_DESIGN, 7-ROADMAP, STATUS}.md`(详情见上文"当前阶段"段)。配套工程规范 / 评测规范 / 教训沉淀同步在 `docs/{8-ENGINEERING, 6-EVAL_PLAN, 9-LESSONS}.md`(memory 项目信息搬运)。

dogfood DB 现状(查 PG `2026-05-09`):30 篇笔记 / 258 chunks / 100% embedding(0 pending)/ 总字数 ~15.7 万字,过 DoD 10 万字门槛(永久约束按字数衡量负载)。

近期 commit:
- `233bd10` M1 第 9 步 Langfuse trace 进库三处修(SDK 锁 <3.0 / main.py import 顺序 / embedders 显式 generation)
- `76257d9` M1 第 8 步 — 笔记直读本地目录 + 前端 notes 页 + ENUM 反映射修
- `f57ea67` M1 后端 7 步落地 STATUS 同步 + uv.lock 同步
- `5dd4e66` M1 第 5/6/7 — embedder agent + embed_worker + hybrid_search 三件齐
- `8b5946e` M1 routers/notes.py 7 端点 + main.py 挂 /api 前缀

# 已锁定的关键决策(v2 起,完整版见各文档)

| 项 | 决策 | 备注 |
|----|------|------|
| 产品形态 | 学计算机的人的"找方向 + 笔记练习 + 简历诊断"全闭环 | 见 PRD §1-4 |
| 目标用户 | 学计算机的人(本科 / 研究生 / 1-3 年 / 5+ 年都包含,只排除非开发岗)| 见 PRD §2 |
| 出题入口 | **聊天框 query**(M2 主题 / M3 岗位 + 空);笔记面板不再触发出题 | 笔记面板降级查看/编辑/上传/导航;PRD §6 "明确不做"锁定 |
| RAG pipeline | **query_rewriter → hybrid + RRF → reranker → parent-doc 扩展** 四件 + 0 命中守门 | M2 主战场;每段独立可观测进 Langfuse |
| Reranker 模型 | 百炼 **`qwen3-rerank`**(`/compatible-api/v1/reranks`,¥0.0005/k token);本地 bge-reranker-v2-m3 fallback 不实施 | memory `reference_aliyun_dashscope_rerank.md` 校对 2026-05-09;接口路径跟其他 rerank 不通用 / langfuse 不自动 instrument |
| Parent-doc 扩展 | 自适应(命中段 < 200 字扩同 H2,≥ 200 字不扩) | 阈值 M2 实施时按命中分布调 |
| 0 命中阈值 | 起步 < 3 chunks → 报"笔记里没这主题" | dogfood 跑一段看真实分布再调 |
| query 三态 | M2 仅 `topic`;M3 加 `job`(三源融合)+ `auto`(SR 自选) | quiz_session_mode ENUM;详见 7-ROADMAP M3 |
| 简历存储 | **单条记录**(全库 1 行,`uq_resumes_singleton` partial unique);**永不做"简历库 / 多份切换"** | 一个人就一份简历;岗位类拼"这一份 + 选定 JD 子集"已够 |
| 笔记输入源 | M1: 本地目录 / 文件直读(File System Access API)+ Web 编辑器 | **不接 zip 上传**(笔记本来在本地,免打包);不做 Notion / 飞书 / Obsidian / **语雀** sync |
| JD 输入源 | 文本粘贴 + 截图(Qwen 多模态)| 累积型,陆续上传;立即解析 |
| JD 单次分析上限 | 200 条 / hierarchical reduce | 见 PRD §9 + 7-ROADMAP M2.5 |
| 简历诊断 | 两方锚点严格(JD req + 简历位置);**永不输出改写文案** | 见 PRD §9 + 5-AGENT §7 |
| 题型 | 开放式 + 八股 两类 | 不做代码 / 系统设计 / 选择题 |
| 评分 | LLM-as-Judge 三层(Coverage / Fidelity / Depth)| 权重 SSoT 在 Python |
| LLM 模型 | qwen3.6-flash(多模态 + 文本一把抓);thinking 按 agent | 见 5-AGENT §2.1 |
| LLM SDK | OpenAI Python SDK 走百炼兼容接口;Langfuse OpenAI wrapper(chat 自动 instrument,embedding 手动包 generation);langfuse SDK 锁 <3.0(server v2 不支持 OTLP) | 见 reference memory + M1 第 9 步沉淀 |
| LLM Provider | 阿里云百炼 | 沿用 v1 ADR |
| 数据存储 | Postgres 16(pgvector + tsvector)| 沿用 v1 |
| Tracing | Langfuse 自部署(docker compose 6 服务) | 见 2-TECH §6 |
| Tool use | AnswerJudge `lookup_in_notes_global` 反假阳性 | 见 5-AGENT §4.7 |
| UI 风格 | macOS 风,Tailwind 自己写 | 不引组件库 |
| Agent 编排 | MVP 单 Agent;M3 才用 LangGraph 多轮追问 | |
| 部署 | 本地 docker compose 6 服务(postgres / api / web / caddy / langfuse / langfuse-db);不做 SaaS(M4+) | |

风格规矩(中文为主 / 不估工时 / 不加 Co-Author / 测试由用户手动跑 / 大白话回答)见 `CLAUDE.md`。

# 永久约束累积

(M0 启动新内核,v1 永久约束已归档到 git history;v2 新约束在此区累积)

- **[来自 M1] 笔记不接 zip 上传** — 笔记本来在用户本地,前端走 File System Access API(showDirectoryPicker / showOpenFilePicker)直接读;后端只接 application/json 批量导入端点,不接 multipart。仅支持 Chromium 系浏览器(Safari 仅单文件 / Firefox 不支持时给提示)。后续里程碑(M2.5 JD / M3 简历)如有"读本地文件"需求,沿用同模式。
- **[来自 M1] JobCopilot 项目所有开发任务一律不写测试代码** — 用户已多次声明所有测试 / 自动化校验由用户手动跑;在此基础上明确不只是"不主动跑",而是不写 unit / integration / e2e 测试文件。已写好的测试不删,新切片不再产出。STATUS.md 列出的测试 TODO 不主动开工,问用户后再做。
- **[来自 M1] 笔记 / 文档类负载用总字数衡量,不用篇数** — DoD、评测样本规模、压力测试目标全部按总字数(或 token 数),不按篇数。理由:50 篇 × 100 字与 50 篇 × 2000 字对 chunker / embedding / hybrid search 的压力差一个数量级,篇数是虚假指标。例外:面试题数 / quiz session 题数等"功能型计数"仍用篇数(产品规格不是负载指标)。
- **[来自 M1] langfuse Python SDK 锁 `<3.0`** — Langfuse server 锁 v2(8-ENGINEERING §13 服务数 6 锁定)。SDK 3.x 默认走 OpenTelemetry exporter(`/api/public/otel/v1/traces`),v2 server 没这个端点 → 404。锁 2.60.x 系列走 `/api/public/ingestion`。后续若升 v3 server,SDK 同步升,**不能单独升 SDK**。
- **[来自 M1] LANGFUSE_* env mirror 必须早于 routers / agents / llm 的 import** — main.py 里 `os.environ.setdefault("LANGFUSE_PUBLIC_KEY", ...)` 块要放在 `from jobcopilot_api.routers import ...` **之前**;放后面会让 `langfuse.openai` import 时读不到 key 进 noop 模式。代价是触发 ruff E402,加 `# noqa: E402`。新模块如果 import 触发 langfuse,沿用同模式。
- **[来自 M1] langfuse 2.x 的 `langfuse.openai` 不 patch `embeddings.create`** — auto-instrument 只覆盖 `ChatCompletion / Completions / Responses` 共 11 个方法。embedder / 任何走 embeddings 端点的调用都要**手动**用 `Langfuse().generation(name=..., model=..., input=..., metadata=...)` 显式建 trace,成功路径 `.end(output, usage, metadata=cost)` + 失败路径 `.end(level="ERROR", status_message=...)`。M2/M3 加新 LLM 调用类型(rerank、tts、image gen)前先确认 langfuse 是否支持自动 instrument,不支持就手动包。

- **[来自 M1] 评测指标(recall / kappa / accuracy 等)必须挂在真正用到该能力的里程碑 DoD,不挂在"实现该能力"的里程碑** — M1 原 DoD 写 "hybrid search recall@5 ≥ 0.85",但 M1 阶段 hybrid search 只是 service 层就绪(`hybrid_search_in_node` / `global_hybrid_search`),没接入任何用户操作:出题剪枝(M2 quiz_generator)+ Judge 防假阳性(M2 lookup_in_notes_global tool)才是真正消费方,query 也来自这俩场景。M1 阶段做评测就是凭空造 query 测纯契约,数字过了证明不了产品 ready,数据集到 M2 还得重做。规矩:**评测指标排到该能力首次接入产品功能的那个里程碑**;实现里程碑只验"service 函数对外契约不崩"(烟测,不上指标卡)。后续 M2 / M2.5 / M3 设计 DoD 时同样原则:Judge kappa 排在 M2(M2 才真用 Judge),resume_advisor forbidden_pattern 排在 M3(M3 才真出简历诊断输出)等。

- **[M2 设计 2026-05-09] 出题入口走聊天框 query,不走笔记面板节点点击** — 笔记面板降级为查看 / 编辑 / 上传 / 导航树,**永不复用节点点击触发出题**。RAG 由"节点内可选筛"升级为"全库必做",pipeline 四件锁定(`query_rewriter → hybrid + RRF → reranker → parent-doc 扩展`)+ 0 命中守门(笔记里没这主题 → 直接报错不兜底)。query 三类形态 + 各自里程碑:M2 主题类("考考我多线程")/ M3 岗位类("模拟一面 Java 后端" — 三源融合,见下条)/ M3 空 query("来模拟面试吧" — SR 系统自选)。后续切片若想恢复"节点点击触发出题"需先回查这条约束(常见于"产品体验更直观"的诱惑)。

- **[M2 设计 2026-05-09] 简历是单条记录,不做"简历库 / 多份切换"** — 一个人就一份简历,不按岗位定制多份。`resumes` 表加 `uq_resumes_singleton` partial unique(WHERE deleted_at IS NULL),全库至多 1 行未删除记录;新上传 = UPDATE 现有行(覆盖 content_md / parsed_chunks),老版本用户自己 git 留档。**M3 岗位类 query 拼"那一份简历 + 用户选定 JD 子集"已足够**;不再设计"用户选哪份简历"的 UX 或端点。这条不可被 M3+ 推翻(产品复杂度无价值,直接撞 v1 失败模式之一)。

- **[M2 设计 2026-05-09] M3 岗位类 query 是三源融合检索而非单源** — 笔记 RAG(query_rewriter → hybrid → rerank → parent-doc)+ 那一份简历全文(直喂,不进 hybrid 索引)+ 用户选定 JD 子集职责/要求聚合,合并喂 quiz_generator。**重点考"简历写了 JD 也要"交集 + "JD 强要 简历没写"缺口**(直击"自己不会的也往简历上写,问到答不出"问题 + "只看 JD 要求不看职责,职责上的东西没复习就挂了"问题)。M3 切片设计 retrieval_pipeline 时多走两路并入,不要把岗位类降级为主题类近似处理。

# 文档清单

| 文件 | 用途 |
|------|------|
| `1-PRD.md` ✅ | 产品需求:三阶段闭环 / 目标用户 / 用户故事 / NSM |
| `2-TECH_DESIGN.md` ✅ | 技术架构 / 模块分层 / 数据流 / 错误分层 / Langfuse |
| `3-DATA_MODEL.md` ✅ | 表 schema(notes / chunks / questions / sessions / answers / gaps / **jds / jd_analyses / resumes / resume_analyses**) |
| `4-API_SPEC.md` ✅ | REST + SSE 端点 + JD 分析 + 简历诊断 |
| `5-AGENT_DESIGN.md` ✅ | 7 个 agent prompt + thinking 矩阵 + tool use |
| `6-EVAL_PLAN.md` ✅ | 5 个 suite + Cohen's kappa + jd_aggregator + resume_advisor |
| `7-ROADMAP.md` ✅ | M0 / M1 / M2 / M2.5 / M3 节奏 + DoD |
| `8-ENGINEERING.md` ✅ | 仓库结构 / 工具链 / 代码规范 / Git / CI 6 条 / Alembic / 本地开发 / Docker / 测试 / Langfuse |
| `9-LESSONS.md` | 工程踩坑录(v1 沉淀 + v2 设计阶段沉淀,持续追加) |
| `STATUS.md` ✅ | 进度快照(本文件) |
| `runbook/` | 部署期写,目前空 |

# 下一步建议

**M1 收口,DoD 全部 ✅**(recall@5 那条挂账 M2,见永久约束"评测指标必须挂在真正用到该能力的里程碑"):
- ≥10 万字入库 ✅(实际 15.7 万 / 30 篇 / 258 chunks / 100% embedding)
- Web 编辑器 3 秒出现树形 ✅
- rechunk(老删新建)✅
- Langfuse UI 看 embedder trace + token + cost ✅
- 闸门(alembic / ruff / mypy / typecheck / next build)✅(用户手动验)
- ~~hybrid search recall@5 ≥ 0.85~~ → **挂账 M2**(M1 service 就绪未接产品)

**开 M2:聊天框主题类 query → 全库 RAG → 出题 + Judge 三层评分 + Judge tool use + Trace 完整化**(范围/DoD 详见 7-ROADMAP §M2;产品方向调整见上文"当前阶段")。粗子任务清单(开工前再细拆):

1. **alembic 0017 schema 调整**:加 `quiz_session_mode` ENUM;`quiz_sessions` 删 `node_folder_path` / `node_heading_path`,加 `query` / `mode` / `jd_ids` / `trigger` / `gap_*` / `expanded_queries` / `retrieved_chunk_ids`;`questions` 删 `node_*`,加 `originated_query` / `originated_mode`;索引同步;`resumes` 加 `uq_resumes_singleton` partial unique(M3 表 schema 在 M2 一并预建,避免 M3 切片再动 quiz 表)
2. **retrieval pipeline 三件齐**:`services/query_rewriter.py`(LLM 改写,失败回退原 query 不阻塞)+ `services/reranker.py`(cross-encoder 选型见 PRD Q-07)+ `services/retrieval_pipeline.py`(query_rewrite → hybrid → rerank → parent-doc 编排 + 0 命中守门)
3. **agents/quiz_generator 实现**:输入 query + retrieved_chunks(含 heading_path / note_title 元数据);prompt 见 5-AGENT §3.3/3.4;输出 N 道开放/八股题 + source_chunk_ids 反幻觉
4. **services/quiz_service + routers/quiz** SSE 端点:入参 `{query, mode, question_count, jd_ids?}`(M2 仅 mode=topic;`mode=job`/`auto` 返 422 mode_not_implemented);SSE 5 段独立 phase(query_rewriting / hybrid / rerank / parent_doc / generating);0 命中报 `no_chunks_for_query` 不出题
5. **agents/answer_judge 实现 + Coverage / Fidelity / Depth 三层** — Python 算分 SSoT(`scoring.py` 已抄入)
6. **AnswerJudge tool use(`lookup_in_notes_global`)** 走百炼 function_call API,Judge 标 fabricated 前必调,直接消费 `global_hybrid_search`(M1 service 就绪)
7. **answer_service + session 沉淀**(自动写 `notes/_recall/{session_id}.md`)
8. **前端 quiz session UI**:聊天框入口(主页边栏笔记面板 + 中央聊天框)→ retrieval 进度条 → 出题 → 答(笔记面板答题阶段隐藏,active recall 强约束)→ 评分恢复笔记面板
9. **Langfuse trace 完整化**:agent / service 层装 `@observe`,SSE session 维度 root trace,retrieval pipeline 5 段独立可观测
10. **M2 评测套件**(三件,DoD 卡死):
    - `evals/suites/hybrid_search/`(从 M1 挂账继承)— 真 dogfood 库 + 30 条 (query, expected_chunk),query 来源**全部为主题类真实场景**(例 "考考我多线程" / "缓存一致性" 等),不再用"节点路径拼接"假 query;ablation(vector / lex / hybrid)recall@5 ≥ 0.85
    - `evals/suites/quiz_generator/` — 结构合规率 ≥ 0.95(Pydantic 校验 + reference_chunk_ids ⊆ source_chunk_ids)
    - `evals/suites/answer_judge/` — 30 条人工标注 + Cohen's kappa ≥ 0.7(Coverage / Fidelity)+ Depth accuracy ≥ 0.75
11. **M2 DoD 验证 + 闸门**

M2 DoD 全部达成 → tag `v0.4-m2-end` → 开 M2.5(JD 累积上传 + 一键分析)→ M3(岗位类三源融合 + SR 自选 + 简历诊断)。

# v1 历史

JobCopilot v1(M0-M3 W8)做的是 "AI 改简历 + 投递追踪"。
W8 真实评测发现产品价值假设站不住:JD 同质化导致定制简历价值低 + retrieval 错放在不增长的 profile。
v2 在同 repo 重定位:目标用户从 1-3 年开发者扩展到"学计算机的人"全谱;产品定位从"AI 改简历"转向"找方向(JD 分析)+ 笔记练习(出题 SR)+ 简历诊断(两方锚点)"三阶段闭环;工程能力(笔记 RAG / Cohen's kappa / hierarchical map-reduce / tool use 反幻觉 / Langfuse 可观测)真正落到对的对象上。

git tag `v0.1-jobcopilot-v1` 将打在 v1 末态(M0 末批量改造前最后一个 commit)。
v1 设计文档 / 切片归档 / ADR / 评测套件全部从仓库删除(git history 永远在,不影响档案)。
v1 工程踩坑沉淀保留在 `docs/9-LESSONS.md`(对作品集叙事价值大)。
