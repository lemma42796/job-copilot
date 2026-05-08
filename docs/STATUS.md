---
title: JobCopilot 项目当前进度(单一可信源)
owner: lemma42796
last_updated: 2026-05-09
purpose: 跨会话续作的状态快照。任何新会话从这里开始读。
---

# 当前阶段

**M1 第 1-9 步全过(后端 7 + 前端本地目录直读 + Langfuse trace 进库);剩第 10 步 DoD 闸门**

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| M0 | 仓库改造 + 文档重写 + v2 schema + 模块骨架 | ✅ tag `v0.2-m0-end` |
| M1 | 笔记入库 + chunker + 树形导航 + Langfuse 起步 | 🔄 第 1-9 步过,剩第 10 步 DoD 浏览器手动验 + 闸门 |
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

**clean**,已与 `origin/main` 同步,M0 末态 tag `v0.2-m0-end`(M1 进行中,DoD 完成后才打 `v0.3-m1-end`)。

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

**M1 后端全部落地**(7-ROADMAP §M1),剩前端 + Langfuse 验证 + DoD:

✅ 已完成:
1. ✅ alembic 索引核对 — 0016 hybrid 索引齐全(folder_path GIN / embedding HNSW / content_tsv GIN),无需补 revision
2. ✅ chunk_service heading-aware chunker — H2 默认 + 超 MAX 拆 H3 + 段落兜底 + overlap;15 unit test(commit `04a7620`)
3. ✅ notes_service 7 函数 — CRUD + 批量导入(原 zip 上传已改为前端 File System Access API + 后端 batch_import)+ 树形导航
4. ✅ routers/notes.py 7 端点 + main.py 挂 /api 前缀(commit `8b5946e`)
5. ✅ agents/embedder.embed_batch — 走 langfuse.openai wrapper(commit `5dd4e66`);第 9 步发现 embeddings.create 不在 auto-patch 范围,显式 `Langfuse().generation()` 包了一层(commit `233bd10`)
6. ✅ workers/embed_worker.process_batch — BATCH_SIZE=10 跟百炼 EMBED_BATCH_LIMIT 对齐
7. ✅ search_service.hybrid_search_in_node + global_hybrid_search — 双路并发 + RRF + lex 短 query 降级

⏳ 剩余:

8. ✅ **前端 Web 编辑器 + 树形导航 + 本地目录直读导入页**(从原 4 页面缩到 2 页面):`notes/page.tsx` 树+Monaco 双栏 / `notes/import/page.tsx` 选目录(showDirectoryPicker) + 选单篇/多篇(showOpenFilePicker),Safari/Firefox 显示提示;Tailwind 自己写,不引组件库(8-ENG 锁定)
9. ✅ **Langfuse 起步验证**(commit `233bd10`):docker compose 起 langfuse + langfuse-db,浏览器注册 + 建 project + 拿 pk/sk 进 .env,api 重启后 POST 新笔记 → embed_worker 跑 → trace 进库,UI 能看 47 input tokens / 0.51s latency。**踩了三个坑**:① langfuse Python SDK 4.x 默认走 OTLP 端点,server v2 不支持 → 锁 `<3.0`;② main.py 里 LANGFUSE_* env mirror 原本在 routers import 之后,改成 settings + env 先;③ langfuse.openai 不 patch embeddings,要在 `_call` 里手动建 generation。三件已落代码 + 永久约束,后续不会再踩。
10. **M1 DoD 验证**(7-ROADMAP §M1):
    - 选总字数 ≥ 10 万字的笔记目录 + 全部入库,chunk 数符合预期(字数衡量负载,不按篇数)
    - Web 编辑器写新笔记 + 选目标 folder + 保存,3 秒内出现在树形导航
    - 编辑老笔记 → 老 chunks 删除 + 新 chunks 入库
    - hybrid search recall@5 ≥ 0.85(评测套件 6-EVAL §5)
    - Langfuse UI 能看到每条 embedder 调用的 trace + token + cost
    - alembic 全过 + ruff / mypy / typecheck / next build 全过

集成测(testcontainers 真 PG)与 M1 后端代码同步缺失:rechunk_note / get_chunks_for_node / hybrid_search / notes_service 全 7 函数 / routers 7 端点 — 都是 unit 不连 DB,要靠集成测兜底。建议在第 8 步前后挑一个时间点写一组 `tests/integration/test_notes_*.py`。

M1 DoD 全部达成 → tag `v0.3-m1-end` → 开 M2(出题 + 评分)。

# v1 历史

JobCopilot v1(M0-M3 W8)做的是 "AI 改简历 + 投递追踪"。
W8 真实评测发现产品价值假设站不住:JD 同质化导致定制简历价值低 + retrieval 错放在不增长的 profile。
v2 在同 repo 重定位:目标用户从 1-3 年开发者扩展到"学计算机的人"全谱;产品定位从"AI 改简历"转向"找方向(JD 分析)+ 笔记练习(出题 SR)+ 简历诊断(两方锚点)"三阶段闭环;工程能力(笔记 RAG / Cohen's kappa / hierarchical map-reduce / tool use 反幻觉 / Langfuse 可观测)真正落到对的对象上。

git tag `v0.1-jobcopilot-v1` 将打在 v1 末态(M0 末批量改造前最后一个 commit)。
v1 设计文档 / 切片归档 / ADR / 评测套件全部从仓库删除(git history 永远在,不影响档案)。
v1 工程踩坑沉淀保留在 `docs/9-LESSONS.md`(对作品集叙事价值大)。
