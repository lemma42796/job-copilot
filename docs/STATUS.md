---
title: JobCopilot 项目当前进度(单一可信源)
owner: lemma42796
last_updated: 2026-05-04 — M1 完成(S0.5/S1-S11 全部 ✅);M2 匹配 + 简历定制待规划
purpose: 跨会话续作的状态快照。任何新会话从这里开始读。
---

# 当前阶段

**M2 匹配 + 简历定制 — 待规划**

| 切片 | 内容 | 状态 |
|------|------|------|
| -    | 待规划 — 见 `7-ROADMAP.md` M2 段;首切片候选:JD 列表页 + 全局导航(为匹配场景做"选哪份 JD"基础) | pending |

**当前 working tree**:干净,M1 收官 + S11 commits 已 push 至 origin/main。续作前检查:`git status --short && git log origin/main..main --oneline | wc -l`。

**当前闸门**(M1 收官):后端 `pytest -q` **321 passed** + ruff / mypy / `alembic upgrade head` → 0010;前端 `pnpm --filter @jobcopilot/web typecheck` + `pnpm lint`(根 biome 扫 monorepo)+ `pnpm --filter @jobcopilot/web build` 全绿;evals `pnpm eval:jd` 13 条 case_pass=2/13(差 11 推 M2)/ `pnpm eval:profile` 11 条 case_pass=11/11(schemaValid=1.0 / experienceRecall=1.0 / skillF1=0.988 / chunkRecall=1.0)。

**M1 完成**:[slices/M1-summary.md](slices/M1-summary.md) — 整体经验 + 25 条永久约束 + 业务/工程 DoD 检查 + 给 M2 的数据底座。各切片归档卡:`slices/{S0.5,S1..S11}-*.md`。

> 2026-05-01 LLM Provider 由 DeepSeek V4 切换到阿里云百炼 Qwen3.6,见 ADR-0003。ADR-0001 复审条件 1(余额 < ¥1)触发时回切。

## 下一刀:M2 规划(待对齐)

**M2 主线**(见 `7-ROADMAP.md`):JD ↔ profile 匹配 + 简历定制 + 投递追踪占位。

**首切片候选**(待用户确认优先级):
1. **JD 列表页 + 全局导航**(原 M2 #12)— S5 起"列表延后"的兑现;调现成 `GET /jds`(cursor 分页已就位),提供卡片列表 / 进入详情 / 删除;同时补简历列表入口与首页导航。M2 起匹配场景需"选哪份 JD",列表才真正有产品意义。**最低风险开张刀**。
2. **匹配 MVP**(retrieval + scoring)— `match_service`:JD vector + chunks 召回 → `MatchAgent` LLM 评分 → 结构化 fit 报告。需要 M1 沉淀的 chunks(已就位)+ 新匹配表(`matches`)。
3. **简历定制 MVP**(LangGraph)— 基于 match 结果用 LangGraph 串 5 表 CRUD agent + render PDF。Q1(awesome-cv 中文化)在 M3 启动前决策,M2 用 plain text/markdown 占位。
4. **prompt v1.0.2 + dataset 扩 + 评测达阈兜底**(原 M2 #1-9)— 把 jd_extract baseline 2/13 推到 ≥80%。

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

14. **JD 列表页 + 全局导航**(S5 起"列表延后"的兑现)— 调现成 `GET /jds`(cursor 分页已就位),提供卡片列表 / 进入详情 / 删除;同时补简历列表入口与首页导航。M2 起匹配场景需"选哪份 JD",列表才真正有产品意义。**顺手做**:`/profiles/new` 错误页"一键删除并重传"按钮(parse_failed UX 绕路降级,S11 调研)。
15. **草稿暂存**:`/profiles/new` 文本粘贴后跳详情页删旧 profile,回来 textarea 内容丢失(S11 dogfood 体感)。sessionStorage 缓存粘贴内容即可。

---

# 永久约束累积(影响后续 M2 切片设计)

> M1 沉淀的 25 条已全部归档到 [slices/M1-summary.md](slices/M1-summary.md);M2 起新约束在此区累积。

(空 — M2 第一个切片落地时开始记)

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
| `slices/{S0.5,S1..S11}-*.md` | 各切片归档(产出 / 设计决策 / 踩坑) |
| `adr/0001-only-deepseek` (Superseded by 0003) / `0002-postgres-as-vector-db` / `0003-switch-to-qwen` / `0004-llm-client-contract` / `0005-files-upload-contract` / `0006-jd-parse-contract` | 架构决策;下一个编号 0007 |
| `runbook/` | 部署期再写,目前空 |

---

# 上次会话遗留的开放问题(PRD §9)

- **Q-01** 简历 PDF 模板(默认:LaTeX `awesome-cv` 中文化)— M3 启动前决策
- **Q-02** 投递追踪日历提醒(默认:不做)— M4 启动前决策
- **Q-03** MCP Server 工具粒度(默认:5 tool + 1 resource)— M5 启动前决策
- **Q-04** Web demo BYOK 在线试用(默认:做)— M6 启动前决策
