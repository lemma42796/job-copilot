---
title: M2 匹配 + 简历定制 — 里程碑收官总结
status: ✅ 收官(部分指标未达阈,见 DoD 检查)
date: 2026-05-05
purpose: M2 整体经验沉淀 + 跨切片永久约束完整归档 + 给 M3 的输入
---

# 里程碑范围

**M2 = S12 + S13-S15 + S16 + S17 + S18**(共 5 个切片块,跨度约 1 周)

PRD 边界:JD ↔ profile 匹配 + 简历定制(LLM 改写 + 反幻觉 reviewer)+ 投递追踪占位。M2 实际交付前两块,投递追踪移到 M3。

# 切片清单

| 切片 | 内容 | 归档 |
|------|------|------|
| S12       | JD 列表页 + 全局导航 + parse_failed 一键删 + 草稿暂存(SSR + client cursor) | [S12-jd-list-and-nav.md](S12-jd-list-and-nav.md) |
| S13-S15   | 匹配 MVP 端到端(检索骨架 + LLM 评分 + SSE 路由 + 前端结果页/列表页/触发按钮) | [S13-S15-match-mvp.md](S13-S15-match-mvp.md) |
| S16       | 简历定制 MVP 后端骨架(0012 migration + drafter/reviewer agent + retrieve→draft→review 串调 + 4 个 SSE endpoint) | [S16-resume-mvp-backend.md](S16-resume-mvp-backend.md) |
| S17       | 简历定制前端(match 详情页触发按钮 + /resumes 列表 + /resumes/[id] 详情 + mini-markdown 渲染 + Blob 下载) | [S17-resume-mvp-frontend.md](S17-resume-mvp-frontend.md) |
| S18       | 简历定制 dogfood — 第一轮 6+1 样本 + match v1.1.2 + reviewer v1.0.2 + 重大 audit + drafter v1.0.2/v1.0.3 + JDParser v1.0.6(双线落地) | [S18-prompt-iterations-2026-05.md](S18-prompt-iterations-2026-05.md) |

期间 prompt 沉淀:
- [jd-parser-bugs-2026-05.md](jd-parser-bugs-2026-05.md) + [jd-parser-prompt-v1.0.5.md](jd-parser-prompt-v1.0.5.md):JDParser 26 类 bug → v1.0.5 + schema 改造
- [profile-parser-bugs-2026-05.md](profile-parser-bugs-2026-05.md):ProfileParser 6 类 bug → v1.0.1

# DoD 检查

## 业务 DoD

| 项 | 状态 | 说明 |
|---|---|---|
| 用户能粘 JD → 拿到匹配分 + gap 分析 | ✅ | 端到端跑通,SSE 路由稳定 |
| 用户能基于匹配生成定制简历 + 看 reviewer 反馈 + 下载 .md | ✅ | 端到端跑通,review_failed 也保留 markdown 展示 |
| 简历 reviewer 反幻觉准确率 | ⚠️ | 第一轮 dogfood reviewer 误判率 30-40%(audit 暴露);v1.0.2 三处判定收窄后改善但未完全验证;reviewer 漏抓"弱→强升级"两类 |
| dogfood review 通过率 ≥ 50% | ❌ | 第一轮 17%(1/6);drafter v1.0.3 写完未跑第二轮验证 |
| match 评测达阈 | ❌ | 未建 match_analysis evals suite |

## 工程 DoD

| 项 | 状态 |
|---|---|
| 后端 `pytest -q` 全过 | ✅(321 passed,M2 末) |
| 后端 ruff / mypy 全过 | ✅ |
| `alembic upgrade head` 一键到 0012 | ✅ |
| 前端 typecheck / biome / next build 全过 | ✅(S17 末) |
| OpenAPI dump + `pnpm gen` schema 同步 | ✅ |

## 不达阈的部分(M2 接受现状,不滚 M3 待办)

- review 通过率 17%、JD evals baseline 2/13、reviewer 漏抓两类 升级 → **M2 已结案,不进 M3 待办累积**
- 沉淀价值已通过永久约束 #6(prompt 调整前必须先读真实数据)+ S18 归档卡方法论教训保留
- drafter v1.0.3 + JDParser v1.0.6 代码已落但未跑第二轮 dogfood 验证 — 留作"未验证已发布"状态,M3 期间若有用户反馈再调

# 永久约束归档(6 条 — 对所有后续里程碑生效)

> M2 起新增 6 条;M1 的 25 条仍生效见 [M1-summary.md](M1-summary.md)。

1. **列表页统一模板** `[来自 S12]` — SSR 第一页(`page.tsx` async server component)+ client 接管(`xx-client.tsx` 拿 cursor 翻页)+ 行内 native `confirm` 删除(本地 splice 不重 fetch)+ 卡片整体可点击(Link absolute overlay + 内容 `pointer-events-none` + 删除按钮 `pointer-events-auto`)。后续 matches / 投递列表复用此结构。

2. **长文 markdown / 自由文本输出的 LLM agent 不走 `response_schema`** `[来自 S16]` — 简历正文 / 面试自由问答 / reviewer 的 explanation 等 ~1000 字以上 markdown 场景,**不要**用 `response_schema=Pydantic_model { content: str }` 包装。LLM 把整段长文转义到 JSON 字符串字段时 `\n` / 代码块 / 引号 / 中文标点都易出错。直接 `response_schema=None`,把 LLM `result.content` 当 markdown 收。**仍走 schema 的场景**:结构化拿来落库的(评分 / findings 列表 / 选项枚举 / 状态布尔等)。drafter 不走 + reviewer 走的拆分是 S16 标准模式。

3. **前端 dev / build 不混用同一 `.next` 目录** `[来自 S15+S17 第二次撞]` — `next build` 写 production 资源到 `.next/`,与 `next dev` 共用同目录会导致 SSR 渲染的 `<link href=...?v=N>` 命中失效 css。**修法**:build 后必须 `rm -rf apps/web/.next` 再起 dev。**预防**:build 仅在 commit 前作为闸门一次性跑,跑完不再回 dev;若 dev 还要继续,先 `rm -rf .next`。

4. **改 prompt 文件前必须先停 uvicorn** `[来自 jd-parser-prompt-v1.0.5]` — `prompt_versions` 表强制版本不可变(同 `name+version` 的内容 hash 必须与 DB 一致)。uvicorn `--reload` 监听文件变化 → 改 prompt 文件触发 reload → lifespan 把新内容入库;后续若继续编辑同版本号,hash 又对不上,启动失败,且 DB 留下 ghost 行。**修法**:迭代 prompt 时,先停 uvicorn,改完起新 uvicorn;或者改完直接 bump 版本号(rename 到下一版,改 router `PROMPT_KEY` 常量)。

5. **新增 enum 值与既有值保持语言一致** `[来自 jd-parser-prompt-v1.0.5]` — Pydantic Literal enum 加新值时,新值必须与既有值同语言(全中文 or 全英文),不要中英混搭。原因:LLM 选 enum 时**字面 token 重叠权重压过语义匹配权重**;中英混搭时,中文原文(如「本科及以上」)会优先选中文 enum 值(如 `本科`)而非语义更准的英文值(如 `bachelor_or_higher`),哪怕 prompt 明确指引也救不回。后续 schema 加新 enum(职级 / 公司类型 / 任何 LLM 抽的 enum 字段)沿用此约束。

6. **prompt 调整前必须先读真实数据** `[来自 S18 第一轮 audit]` — 调任何 prompt(JDParser / ProfileParser / match_analyst / drafter / reviewer)前,**必须**先 curl 出 profile chunks + JD raw 数据 + LLM 输出,**人工对照**确认 LLM 哪里真错、哪里只是输出形式与你预期不同。**不可凭"reviewer / evaluator 抓了 X finding" 反推 chunks 内容**。S18 第一轮整个对话前半段(诊断 7 类 match bug + 制定 v1.1.0/v1.1.1/v1.1.2 三轮调整 + drafter v1.0.1 设计)都建立在反推错觉上,30-40% 误判:把 chunks summary 原文措辞当 evaluator 自加;把 chunks `granularity=skill` 字段(候选人自报权威清单)当"栈背景";把 chunks role 字段值当 drafter 编造拔高;把 chunks 数字原文当 LLM 凭空捏造。**根因**:profile_chunks 有 4 类 granularity(`summary / experience / project / skill`),prompt 不告诉 LLM "skill 字段是权威自报清单",LLM 凭"动作证据"标准判读,大量误判。**预防**:① 每次 dogfood 前先 `curl /v1/profiles/{id}` 看完整 raw_text + structured + chunks 列表 ② 每次诊断 LLM 输出"是错"前,先在 chunks 里 grep 该字眼 / 数字 / 措辞 ③ prompt 中显式告诉 LLM "chunks 的 granularity 字段类型语义"。

# M2 内部经验(不升永久约束 — 仅 M2 自用,记账)

## 关于 prompt 迭代

- **prompt 工程边际收益递减很快**:match_analyst v1.1.0 → v1.1.1 大改善但有退步;v1.1.1 → v1.1.2 修退步但顽固问题(中间夸张词同义词替换 / 资历套话 / FC 顽固 / 在自家项目塞缺口)继续。同义词替换是无穷无尽游戏(突出 → 较强 → 深厚 → 显著优势 → 完美契合)。M3 起对单 prompt 迭代设上限 ≤ 3 轮,达阈即停;不达阈考虑改 retrieval / agent 编排而非继续磨 prompt。

- **deterministic 字段不该走 chunks 召回**:S18 第二轮发现基本信息(full_name/email/phone)/ 教育背景因 K=20 相似度召回不全/不存在,被简历输出成 `[待补充]` 占位符。drafter v1.0.3 修法是把 candidate dict 直接透传(绕过 chunks)。M3+ 任何"必须出现的 deterministic 字段"统一走 candidate 透传,不指望 retrieval。

- **LLM 输出形式不稳定**:同一 drafter prompt 跑两次,技能段格式可能从"堆 24 个栈名词"变"8 项 X/Y: 描述",输出形式不一致。结构化让 LLM 自由发挥的字段(技能段格式 / 业余项目识别 / 求职意向写法)需要 prompt 明确"格式建议",但仍有抖动。M3+ 关键格式字段(简历章节顺序 / 必填字段)考虑后处理校验或 schema 约束。

## 关于 dogfood

- **dogfood 必须基于真实数据,不能反推**:S18 audit 暴露的核心教训(永久约束 #6)。
- **dogfood 样本量不够无法算 P95**:6 条样本对 latency / cost 分布无统计意义。M3 累积 ≥ 20 条匹配 + 10 条简历后才能算 P95。
- **dogfood 过程中发现的 reviewer 漏抓不该改 reviewer 兜底,该让 drafter 不犯错**:reviewer 是事后审核,reviewer 加规则 = 在错误输出基础上贴补丁。优先改 drafter 不产生该错误;reviewer 仅作为最后一道防线。

## 关于架构

- **两阶段 pipeline 模式稳**:create_pending → run_generate(retrieve → LLM → persist)在 match_service 和 resume_service 复用,异常路径 `_mark_failed` 旁路 commit(永久约束 ADR-0004)。M3 投递追踪不需此模式(无 LLM 调用)。
- **caller-managed session 模式稳**:list / get / soft_delete 走 caller 的 session,run_generate 内部分多个短 tx + 慢 IO 段。M3 复用。
- **MVP 偏 chunks 在 user 段**:retrieval 每次 K=20 chunks 不固定,放 user 段比 system 段更直观;cache 命中目标退到 system 段(角色 + 风格 + 章节顺序)。

# 给 M3 的输入

## 数据底座状态

- `jds` / `matches` / `resumes` / `resume_versions` 表都已建,前端列表页 + 详情页全跑通
- profile_chunks 38 条/profile #15(M2 dogfood 用)
- prompt_versions 表稳定:jd_parser=v1.0.6 / profile_parser=v1.0.1 / match_analyst=v1.1.2 / resume_drafter=v1.0.3 / resume_reviewer=v1.0.2

## 未验证已发布(M3 期间使用前需注意)

- **drafter v1.0.3** 改写完没跑第二轮 dogfood 验证 — 第一轮第一条 audit 暴露的 4 类问题(基本信息占位 / 教育占位 / TypeScript 漏列 / 弱→强升级)在 v1.0.3 prompt 里有显式修复规则,但实际效果未验
- **JDParser v1.0.6** 加了 B.1 复合句式新规但未跑 dogfood 验证 — 期望"精通 X, 熟悉 Y/Z 任一" 句式中 X 的 `or_group_id=null, weight=1.0`

## M3 首要看 ROADMAP

M3 = 投递追踪(applications)+ 提醒系统占位(看 PRD §8 / ROADMAP §3)。无 LLM,纯 CRUD + 状态机 + 前端日历 / 列表视图。比 M2 简单。

## 关键决策仍生效(不重议)

参见 [STATUS.md](../STATUS.md) "已经锁定的关键决策" 表。M2 期间未推翻任何 M1 决策。
