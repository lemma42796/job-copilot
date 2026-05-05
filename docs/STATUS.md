---
title: JobCopilot 项目当前进度(单一可信源)
owner: lemma42796
last_updated: 2026-05-05 — S18 简历定制 dogfood 第一轮跑完:6+1 条样本(JD #9-#13 + #24 × profile #15)→ review 通过率 17%(1/6)未达 ≥ 50% 目标;match_analyst v1.1.2 + reviewer v1.0.2 落地(三处判定标准收窄);**重大 audit 发现 30-40% bug 诊断是"没读 profile 反推 chunks"的方法论错觉**;drafter v1.0.1 真 bug 5 类暴露但留下次修([slices/S18-prompt-iterations-2026-05.md](slices/S18-prompt-iterations-2026-05.md))。S18 主线**进行中**(未完成)。
purpose: 跨会话续作的状态快照。任何新会话从这里开始读。
---

# 当前阶段

**M2 匹配 + 简历定制 — 进行中**

| 切片 | 内容 | 状态 |
|------|------|------|
| S12        | JD 列表页 + 全局导航 + parse_failed 一键删 + 草稿暂存 → [slices/S12-jd-list-and-nav.md](slices/S12-jd-list-and-nav.md) | ✅ |
| S13-S15    | 匹配 MVP 端到端(检索骨架 + LLM 评分 + SSE 路由 + 前端结果页/列表页/触发按钮)→ [slices/S13-S15-match-mvp.md](slices/S13-S15-match-mvp.md) | ✅ |
| S16        | 简历定制 MVP 后端骨架(0012 migration + drafter/reviewer agent + retrieve→draft→review 串调 + SSE 路由 4 endpoint)→ [slices/S16-resume-mvp-backend.md](slices/S16-resume-mvp-backend.md) | ✅ |
| S17        | 简历定制前端(match 详情页触发按钮 + /resumes 列表 + /resumes/[id] 详情 + mini-markdown 渲染 + Blob 下载)→ [slices/S17-resume-mvp-frontend.md](slices/S17-resume-mvp-frontend.md) | ✅ |
| S18        | 简历定制 dogfood — 第一轮 6+1 样本(JD #9-#13 + #24 × profile #15)+ match_analyst v1.1.2 + reviewer v1.0.2 + audit 教训 → [slices/S18-prompt-iterations-2026-05.md](slices/S18-prompt-iterations-2026-05.md) | 进行中(drafter 真 bug 5 类待修) |

**当前 working tree**:S18 第一轮产出待 commit & push。续作前检查:`git status --short && git log origin/main..main --oneline | wc -l`。

**当前生效 prompt**:
- `match_analyst` = **v1.1.2**(4 条规则简化版,消费 `or_group_id`)
- `resume_drafter` = **v1.0.1**(4 条强约束 — 真 bug 5 类待修,见 S18 归档)
- `resume_reviewer` = **v1.0.2**(M2/M4/M5 判定收窄 + granularity 字段说明)
- `jd_parser` = v1.0.5 / `profile_parser` = v1.0.1(M2 待办 #11 部分修)

**当前闸门**:后端 `pytest -q` **321 passed**(未跑,无新测试) + ruff / mypy 全过 + `alembic upgrade head` → **0012**;前端 **typecheck / biome / next build 全过**(无前端改动);evals 数字未动;**S18 dogfood 第一轮**:6+1 样本(JD #9-#13 + #24 × profile #15)→ **review 通过率 17%(1/6)**(目标 ≥ 50%)❌ / **高 finding 平均 3.0**(目标 ≤ 1)❌ / cost ¥0.013 中位 ✅ / latency 8.7s 中位 ✅。详见 [slices/S18-prompt-iterations-2026-05.md](slices/S18-prompt-iterations-2026-05.md)。

**M1 完成**:[slices/M1-summary.md](slices/M1-summary.md) — 整体经验 + 25 条永久约束 + 业务/工程 DoD 检查 + 给 M2 的数据底座。各切片归档卡:`slices/{S0.5,S1..S11}-*.md`。

> 2026-05-01 LLM Provider 由 DeepSeek V4 切换到阿里云百炼 Qwen3.6,见 ADR-0003。ADR-0001 复审条件 1(余额 < ¥1)触发时回切。

## 下一刀:M2 后续(待对齐)

**M2 主线**(见 `7-ROADMAP.md`):JD ↔ profile 匹配 + 简历定制 + 投递追踪占位。

**剩余切片候选**(从下面挑下一刀):
1. **S18 续 — drafter v1.0.2 修真 bug 5 类**(M2 主线下一刀)— 见 [slices/S18-prompt-iterations-2026-05.md](slices/S18-prompt-iterations-2026-05.md) 末"drafter v1.0.1 真 bug 5 类":① 求职意向硬抄 JD title(应读 candidate `target_titles` 字段)② 强约束 D 在"X/Y: 描述"形式下失守(Flask 凭空)③ 副词跨 skill 错位("精通"是 Python skill.level=expert,被错位到 Go advanced)④ 侧项目泛化为通用能力 ⑤ chunks 弱措辞被强化("关注" → "擅长落地")。修完跑 6 条样本对比 review 通过率。
2. **匹配 v1.1 提质**(MVP 后置债)— Hybrid Search(pgvector + tsvector RRF)+ Reranker(`gte-rerank-v2`)+ QueryRewriterAgent + tier 升 STANDARD(thinking)再调 timeout;chunk content evidence hover 联动;详情页 footer 调试 metadata 收折叠。**附 match_analyst 已废规则待清理**(详见 S18 归档 audit 段):规则 3.1 撤回 chunks 自用副词的禁令(精通 / 全栈作者 / 较深积累);规则 4.2 拔高角色判定精修(chunks 已有的角色不禁);规则 1.2 strength 校准表精修(skill.level 字段映射优先)。
3. **prompt v1.0.2 + dataset 扩 + 评测达阈兜底**(原 M2 #1-9)— 把 jd_extract baseline 2/13 推到 ≥80%;同时建 `match_analysis` + `resume_review` evals suite(用 dogfood 真实三元组,LLM-as-Judge)。S18 dogfood 拿到的 6 条 review 输出是 `resume_review` evals 的天然标注样本。
4. **多刷 dogfood 累积 P95 / cost 样本**— 至少 20 条匹配 + 5 条简历跑出来才能算 P95;测试不同 JD × 同简历的 score 区分度 + reviewer 通过率分布。

## drafter v1.0.1 已落 + 真 bug 5 类待修(留 S18 续刀 v1.0.2)

drafter v1.0.1 4 条强约束已落,但 S18 dogfood 第一轮(6 条样本)+ audit 后发现:**A 单 chunk 80% 修住 / B 副词白名单部分基于反推错觉应回退 / C 业余项目标签 80% 修住 / D skill 硬规则错位最严重**。详见 [slices/S18-prompt-iterations-2026-05.md](slices/S18-prompt-iterations-2026-05.md) 的 audit 段 + 真 bug 5 类清单。下一刀 v1.0.2 修这 5 类。

---

# M2 待办累积(从 M1 沉淀)

## 评测 / Prompt(M1 ≥ 80 条评测达阈 DoD 推后)

1. ~~**JDParser prompt v1.0.2** 修 baseline + 26 类 bug~~ — **prompt + schema 部分已完成**,落到 v1.0.5 + schema 改造(`or_group_id` / 中文学历枚举扩 / source_url+publisher 字段 / confidence 公式)+ 前端 BOSS 标签剥离;详见 [slices/jd-parser-prompt-v1.0.5.md](slices/jd-parser-prompt-v1.0.5.md)。**剩余**:dataset 扩 50 条(下面 #2)+ evals 重跑达阈(`hardSkillF1` ≥ 0.80 / `titleExact` ≥ 0.85)。
2. **JDParser dataset 扩 50 条**(剩 37:OCR 7 / 邮件 8 / 极短 3 / 薪资模糊 2 / 标准中文 17)。
3. **4 新 metric**:`level_acc` / `confidence_calibration` / `latency_p95` / `cost_per_call_cny`。
4. **bad case 表 + promote 脚本 + 月度 triage**(EVAL_PLAN §12)。
5. **跑 3 次取中位数**(EVAL_PLAN §11.3)。
6. **不退化策略**:Δ ≤ -2pp 比对 main baseline。
7. **PR comment 脚本**。
8. **`salaryMonthsAcc` 改自定义聚合**(去掉 want=null 拉高分母的水分)。
9. **`.github/workflows/eval.yml` 启用 push/PR trigger**(取消注释 + 配 GitHub Secret `DASHSCOPE_API_KEY_EVAL`,见 EVAL_PLAN §10.5)。
10. **profile_extract dataset 扩 30+**(从 S11 dogfood 真实简历沉淀)。
11. **profile_parser prompt 升级链** — **v1.0.1 已落**(2026-05-05 一轮 dogfood,12 边界测试 PDF):① description 反幻觉(禁止从 bullets 改写/复述,包括半角→全角逗号);② 日期 end_date=null 仅限明示"至今",单年 YYYY 默认 end=YYYY-12;③ 中文等级映射表(熟练=advanced 等);详见 [slices/profile-parser-bugs-2026-05.md](slices/profile-parser-bugs-2026-05.md)。**v1.0.2 待修**(S11 dogfood 旧账):① 技能切分一致性(`/`、`+` 拆得不规律);② partial-year project end_date 兜底("2022" 现兜底成 `2022-01-01` 让 start=end 显示成持续 1 月);③ tech_stack 抽取剔除空泛词(jdk / 后端 等);④ 证书章节(AWS Solutions Architect / 阿里云 ACA)— schema 加 `certifications` 字段否则 LLM 直接扔。

## 数据 / 后端

12. **embedding `DataInspectionFailed` 观察**(S8 规划期暴露)— 跑 30+ profile dataset 时若有命中,需对 chunker 加内容脱敏或 retry-skip 策略。
13. **embedding 写 `llm_calls` 表统一**(S8 规划期暴露)— S8 阶段只 structlog 打印 embedding token + cost;M2 把 schema 通用化(加 `kind` 枚举或拆表)。

## 前端 / UX

~~14. JD 列表页 + 全局导航 + parse_failed 一键删并重传~~ — S12 已完成。
~~15. 草稿暂存(`/jds/new` + `/profiles/new` sessionStorage)~~ — S12 已完成。

---

# 永久约束累积(影响后续 M2 切片设计)

> M1 沉淀的 25 条已全部归档到 [slices/M1-summary.md](slices/M1-summary.md);M2 起新约束在此区累积。

1. **列表页统一模板** `[来自 S12]` — SSR 第一页(`page.tsx` async server component)+ client 接管(`xx-client.tsx` 拿 cursor 翻页)+ 行内 native `confirm` 删除(本地 splice 不重 fetch)+ 卡片整体可点击(Link absolute overlay + 内容 `pointer-events-none` + 删除按钮 `pointer-events-auto`)。后续 matches / 投递列表复用此结构,不再每个列表重新发明。

2. **长文 markdown / 自由文本输出的 LLM agent 不走 `response_schema`** `[来自 S16]` — 简历正文 / 面试自由问答 / 简历 reviewer 的 explanation 等"~1000 字以上 markdown / 自由文本"输出场景,**不要**用 `response_schema=Pydantic_model { content: str }` 包装。原因:LLM 把整段长文转义到一个 JSON 字符串字段时,`\n` / 代码块 / 引号 / 中文标点都易出错,fix-bad-json 重试也救不回来。直接 `response_schema=None`,把 LLM `result.content` 当 markdown 收;cache_system / 错误处理走 LLMClient 已有路径,不受影响。**仍走 schema 的场景**:结构化拿来落库的(评分 / findings 列表 / 选项枚举 / 状态布尔等)。drafter 不走 + reviewer 走的拆分是 S16 的标准模式,后续 resume_planner(M3 复活) / interview_grader(M5)等长文 agent 沿用。

3. **前端 dev / build 不混用同一 `.next` 目录** `[来自 S15+S17 第二次撞]` — `next build` 写 production 资源到 `.next/`,与 `next dev` 共用同目录会导致 SSR 渲染的 `<link href=...?v=N>` 命中失效 css,layout.css 不加载,sidebar `w-[220px]` 失效,SVG 图标铺满屏幕。**修法**:build 后必须 `rm -rf apps/web/.next` 再起 dev;反之同理。**触发频率**:每次"跑 build 验证 → 切回 dev 继续开发"的节点都会撞;S15 撞过一次未升约束,S17 第二次撞了。**预防**:build 仅在 commit 前作为闸门一次性跑,跑完不再回 dev(直接 commit/push);若 dev 还要继续,先 `rm -rf .next`。

4. **改 prompt 文件前必须先停 uvicorn** `[来自 jd-parser-prompt-v1.0.5]` — `prompt_versions` 表强制版本不可变(同 `name+version` 的内容 hash 必须与 DB 一致,否则启动 `PromptVersionMismatchError`)。但 uvicorn `--reload` 监听文件变化,改 prompt 文件 → reload → lifespan 把新内容入库;后续若继续编辑同版本号,hash 又对不上,启动失败,且 DB 留下 ghost 行。**修法**:迭代 prompt 时,先 `TaskStop` 当前 uvicorn,改完文件,起新 uvicorn;或者改完直接 bump 版本号(rename 到下一版,改 router `PROMPT_KEY` 常量)。本次落定 v1.0.5,中间 v1.0.2/v1.0.3/v1.0.4 都成了 DB ghost 行。

5. **新增 enum 值与既有值保持语言一致** `[来自 jd-parser-prompt-v1.0.5]` — Pydantic Literal enum 加新值时,新值必须与既有值同语言(全中文 or 全英文),不要中英混搭。原因:LLM 选 enum 时 **字面 token 重叠权重压过语义匹配权重**;中英混搭时,中文原文(如「本科及以上」)会优先选中文 enum 值(如 `本科`)而非语义更准的英文值(如 `bachelor_or_higher`),哪怕 prompt 明确指引也救不回。本次 education 枚举从混搭 `unspecified/bachelor_or_higher/flexible` 改成中文 `不限/本科及以上/任一档`(prompt 也精简一条规则),问题消失。后续 schema 加新 enum(职级 / 公司类型 / 任何 LLM 抽的 enum 字段)沿用此约束。

6. **prompt 调整前必须先读真实数据** `[来自 S18 第一轮 audit]` — 调任何 prompt(JDParser / ProfileParser / match_analyst / drafter / reviewer)前,**必须**先 curl 出 profile chunks + JD raw 数据 + LLM 输出,**人工对照**确认 LLM 哪里真错、哪里只是输出形式与你预期不同。**不可凭"reviewer / evaluator 抓了 X finding" 反推 chunks 内容**。S18 第一轮整个对话前半段(诊断 7 类 match bug + 制定 v1.1.0/v1.1.1/v1.1.2 三轮调整 + drafter v1.0.1 设计)都建立在反推错觉上,30-40% 误判:把 chunks summary 原文措辞("较深积累")当 evaluator 自加;把 chunks `granularity=skill` 字段(候选人自报权威清单)当"栈背景";把 chunks role 字段值("全栈作者")当 drafter 编造拔高;把 chunks 数字原文("P99 < 1.2s" / "Token 43%")当 LLM 凭空捏造。**根因**:profile_chunks 有 4 类 granularity(`summary / experience / project / skill`),prompt 不告诉 LLM "skill 字段是权威自报清单",LLM 凭"动作证据"标准判读,大量误判;chunks 有的措辞 / 数字 / 角色字段被错认为 LLM 自加。**预防**:① 每次 dogfood 前先 `curl /v1/profiles/{id}` 看完整 raw_text + structured + chunks 列表 ② 每次诊断 LLM 输出"是错"前,先在 chunks 里 grep 该字眼 / 数字 / 措辞 ③ prompt 中显式告诉 LLM "chunks 的 granularity 字段类型语义"。

---

# 已锁定的关键决策(不要再讨论)

| 项 | 决策 |
|----|------|
| 目标用户 | 1-3 年跳槽开发者(应届生 v2 再说) |
| 北极星 NSM | 投递前后面试邀约率提升;短期 proxy = 端到端完成率(粘 JD → 下载定制简历) |
| MVP 边界 | JD 入库 + 个人档案 + 匹配 + 简历定制 + 本地部署;面试模拟 P1(Phase 5) |
| 部署 / 仓库 | 本地优先 `docker compose up`;monorepo `apps/api` + `apps/web` + `packages/schemas` |
| LLM Provider | 仅阿里云百炼 Qwen3.6(Flash + Plus,ADR-0003;ADR-0001 已 Superseded) |
| 数据存储 | Postgres 16 一把梭(pgvector + tsvector + pgmq + bytea,ADR-0002) |
| Agent 编排 | LangGraph 仅用于简历定制 + 面试模拟,其他场景单 Agent |

风格规矩(中文为主 / 不估工时 / 不加 Co-Author / 测试由用户手动跑)见 `CLAUDE.md`。

---

# 文档清单

| 文件 | 用途 |
|------|------|
| `1-PRD.md` / `2-TECH_DESIGN.md` / `3-DATA_MODEL.md` / `4-API_SPEC.md` / `5-AGENT_DESIGN.md` / `6-EVAL_PLAN.md` / `7-ROADMAP.md` / `8-ENGINEERING.md` | 设计文档,**只在写对应代码时按需读相关章节** |
| `slices/M1-summary.md` | M1 收官总结(整体经验 + 25 条永久约束 + DoD 检查) |
| `slices/{S0.5,S1..S11}-*.md` | M1 各切片归档(产出 / 设计决策 / 踩坑) |
| `slices/{S12-jd-list-and-nav,S13-S15-match-mvp,S16-resume-mvp-backend,S17-resume-mvp-frontend}.md` | M2 各切片归档(产出 / 设计决策 / 踩坑) |
| `slices/{jd-parser-bugs-2026-05,jd-parser-prompt-v1.0.5}.md` | M2 待办 #1 的调研 + 修复(26 类 JDParser bug → prompt v1.0.5 + schema 改造) |
| `slices/profile-parser-bugs-2026-05.md` | M2 待办 #11 的部分修复(ProfileParser 6 类 bug → prompt v1.0.1 + 前端 UI 三处补渲染) |
| `slices/S18-prompt-iterations-2026-05.md` | S18 第一轮 dogfood + match v1.1.0/v1.1.1/v1.1.2 三轮迭代 + reviewer v1.0.2 + audit 教训 |
| `adr/0001-only-deepseek` (Superseded by 0003) / `0002-postgres-as-vector-db` / `0003-switch-to-qwen` / `0004-llm-client-contract` / `0005-files-upload-contract` / `0006-jd-parse-contract` | 架构决策;下一个编号 0007 |
| `runbook/` | 部署期再写,目前空 |

---

# 上次会话遗留的开放问题(PRD §9)

- **Q-01** 简历 PDF 模板(默认:LaTeX `awesome-cv` 中文化)— M3 启动前决策
- **Q-02** 投递追踪日历提醒(默认:不做)— M4 启动前决策
- **Q-03** MCP Server 工具粒度(默认:5 tool + 1 resource)— M5 启动前决策
- **Q-04** Web demo BYOK 在线试用(默认:做)— M6 启动前决策
