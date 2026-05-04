---
title: JobCopilot 项目当前进度(单一可信源)
owner: lemma42796
last_updated: 2026-05-04 — M2 主线:S13-S15 ✅(匹配 MVP 端到端 dogfood 通过)
purpose: 跨会话续作的状态快照。任何新会话从这里开始读。
---

# 当前阶段

**M2 匹配 + 简历定制 — 进行中**

| 切片 | 内容 | 状态 |
|------|------|------|
| S12        | JD 列表页 + 全局导航 + parse_failed 一键删 + 草稿暂存 → [slices/S12-jd-list-and-nav.md](slices/S12-jd-list-and-nav.md) | ✅ |
| S13-S15    | 匹配 MVP 端到端(检索骨架 + LLM 评分 + SSE 路由 + 前端结果页/列表页/触发按钮)→ [slices/S13-S15-match-mvp.md](slices/S13-S15-match-mvp.md) | ✅ |
| -          | 下一刀待规划 — 见下方"下一刀"区 | pending |

**当前 working tree**:S13-S15 代码改动 + 归档卡待 commit & push(用户确认后一并执行)。续作前检查:`git status --short && git log origin/main..main --oneline | wc -l`。

**当前闸门**:后端 `pytest -q` **321 passed**(S13/S14 没写新测试;test_migrations 加 matches 表名是修改既有用例不计 +新)+ ruff / mypy / `alembic upgrade head` → **0011**;前端 **typecheck / biome / next build 全过**(34 files / 9 routes,S12 末跳过的祖传债顺手清完);evals 数字未动(`pnpm eval:jd` 2/13 / `pnpm eval:profile` 11/11);**端到端 dogfood**:JD#3 + 简历#13 一次匹配 → score=72 / 8810ms / ¥0.0095(M2 退出 P95 ≤ 15s & cost ≤ ¥0.20 都达标但仅 1 条样本,需累积)。

**M1 完成**:[slices/M1-summary.md](slices/M1-summary.md) — 整体经验 + 25 条永久约束 + 业务/工程 DoD 检查 + 给 M2 的数据底座。各切片归档卡:`slices/{S0.5,S1..S11}-*.md`。

> 2026-05-01 LLM Provider 由 DeepSeek V4 切换到阿里云百炼 Qwen3.6,见 ADR-0003。ADR-0001 复审条件 1(余额 < ¥1)触发时回切。

## 下一刀:M2 后续(待对齐)

**M2 主线**(见 `7-ROADMAP.md`):JD ↔ profile 匹配 + 简历定制 + 投递追踪占位。

**剩余切片候选**(S13-S15 匹配 MVP 已完成,从下面挑下一刀):
1. **简历定制 MVP**(LangGraph)— 基于 match 结果(`matches.gap_summary` + `missing_skills`)用 LangGraph 串 5 表 CRUD agent + render markdown。Q1(awesome-cv 中文化)在 M3 启动前决策,M2 用 plain text/markdown 占位。**M2 主线下一刀**。
2. **匹配 v1.1 提质**(MVP 后置债)— Hybrid Search(pgvector + tsvector RRF)+ Reranker(`gte-rerank-v2`)+ QueryRewriterAgent + tier 升 STANDARD(thinking)再调 timeout;chunk content evidence hover 联动;详情页 footer 调试 metadata 收折叠。
3. **prompt v1.0.2 + dataset 扩 + 评测达阈兜底**(原 M2 #1-9)— 把 jd_extract baseline 2/13 推到 ≥80%;同时建 `match_analysis` evals suite(用 dogfood 累积的真实三元组,LLM-as-Judge)。
4. **多刷 dogfood 累积 P95 / cost 样本**— 至少 20 条匹配跑出来才能算 P95;测试不同 JD × 同简历的 score 区分度。

下次开工讨论顺序与切粒度。

---

# M2 待办累积(从 M1 沉淀)

## 评测 / Prompt(M1 ≥ 80 条评测达阈 DoD 推后)

1. **JDParser prompt v1.0.2** 修 baseline 不达阈:① "hard_skills 不抽厂商名/概念名"(修 hardSkillF1=0.67);② "title 抽到第一行末"(修 titleExact=0.769)。
2. **JDParser dataset 扩 50 条**(剩 37:OCR 7 / 邮件 8 / 极短 3 / 薪资模糊 2 / 标准中文 17)。
3. **4 新 metric**:`level_acc` / `confidence_calibration` / `latency_p95` / `cost_per_call_cny`。
4. **bad case 表 + promote 脚本 + 月度 triage**(EVAL_PLAN §12)。
5. **跑 3 次取中位数**(EVAL_PLAN §11.3)。
6. **不退化策略**:Δ ≤ -2pp 比对 main baseline。
7. **PR comment 脚本**。
8. **`salaryMonthsAcc` 改自定义聚合**(去掉 want=null 拉高分母的水分)。
9. **`.github/workflows/eval.yml` 启用 push/PR trigger**(取消注释 + 配 GitHub Secret `DASHSCOPE_API_KEY_EVAL`,见 EVAL_PLAN §10.5)。
10. **profile_extract dataset 扩 30+**(从 S11 dogfood 真实简历沉淀)。
11. **profile_parser prompt v1.0.2**(S11 dogfood 暴露):① 技能切分一致性(`/`、`+` 拆得不规律);② partial-year project end_date 兜底("2022" 现兜底成 `2022-01-01` 让 start=end 显示成持续 1 月);③ tech_stack 抽取剔除空泛词(jdk / 后端 等);④ 证书章节(AWS Solutions Architect / 阿里云 ACA)— schema 加 `certifications` 字段否则 LLM 直接扔。

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
| `slices/S12-jd-list-and-nav.md` | M2 切片归档(产出 / 设计决策 / 踩坑) |
| `adr/0001-only-deepseek` (Superseded by 0003) / `0002-postgres-as-vector-db` / `0003-switch-to-qwen` / `0004-llm-client-contract` / `0005-files-upload-contract` / `0006-jd-parse-contract` | 架构决策;下一个编号 0007 |
| `runbook/` | 部署期再写,目前空 |

---

# 上次会话遗留的开放问题(PRD §9)

- **Q-01** 简历 PDF 模板(默认:LaTeX `awesome-cv` 中文化)— M3 启动前决策
- **Q-02** 投递追踪日历提醒(默认:不做)— M4 启动前决策
- **Q-03** MCP Server 工具粒度(默认:5 tool + 1 resource)— M5 启动前决策
- **Q-04** Web demo BYOK 在线试用(默认:做)— M6 启动前决策
