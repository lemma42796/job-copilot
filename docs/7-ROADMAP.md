---
title: JobCopilot 16 周里程碑路线图
owner: lemma42796
status: Draft
version: 0.1.0
created: 2026-05-01
last_updated: 2026-05-01
related:
  - 1-PRD.md
  - 2-TECH_DESIGN.md
  - 4-API_SPEC.md
  - 5-AGENT_DESIGN.md
  - 6-EVAL_PLAN.md
  - 8-ENGINEERING.md
---

## 修订记录

| 版本 | 日期 | 作者 | 变更说明 |
|------|------|------|---------|
| 0.1.0 | 2026-05-01 | lemma42796 | 初版 |

---

## 1. 时间轴一览

```
Week:    1    2    3    4    5    6    7    8    9   10   11   12   13   14   15   16
        │M0 │  M1   │       M2          │            M3            │     M4     │ M5  │ M6 │
        │骨架│ 入库  │ 匹配分析+评测     │  简历定制 + GA            │面试+投递  │扩展 │发布│
        │   │       │                   │                          │           │MCP  │    │
```

| 里程碑 | 周次 | 主题 | 关键交付 |
|-------|------|------|---------|
| **M0** | W1 | 仓库骨架 | monorepo + Postgres + Hello FastAPI + Hello Next.js + Docker Compose |
| **M1** | W2-3 | 数据入口贯通 | JD 入库(文本/PDF/图片)+ 个人档案解析 + chunks |
| **M2** | W4-6 | 匹配分析 + 评测体系 | MatchAnalyst 状态机 + `jd_extract`/`profile_extract`/`match_analysis` 评测套件 + CI 回归 |
| **M3** | W7-10 | 简历定制 GA | LangGraph 简历定制状态机 + Reviewer + PDF 导出 + 内测 5 用户 + v0.5 公开测试 |
| **M4** | W11-13 | 面试模拟 + 投递追踪 | 面试模拟状态机 + 投递看板 + NSM 输入指标可统计 |
| **M5** | W14-15 | 浏览器扩展 + MCP Server | Chrome 扩展智能粘贴 + MCP server(5 tool + 1 resource) |
| **M6** | W16 | 公开发布 | VitePress 文档站 + 云端 Demo + GitHub Release v1.0 + 推广 |

---

## 2. 跨里程碑约束

### 2.1 节奏纪律

- **每周五下班前必有合并到 main 的代码**(M0-M2 是骨架与基础设施,可以例外)
- **每个里程碑结束当天**:更新 `STATUS.md`、写一篇里程碑回顾(博客/Twitter)、整理 bad case
- **不允许累积技术债跨里程碑**,Reviewer 在每周五合并前必须见 0 issue

### 2.2 评测先行

- 任何"可被评测"的 Agent **必须在功能 PR 前先有 evals**
- `evals/suites/<agent>/dataset.jsonl` ≥ 10 条 + GitHub Actions 跑通 = 可以开 Agent 实现 PR
- 这是为了避免出现"功能 done 但质量无法量化"的死局

### 2.3 用户验证节奏

| 阶段 | 验证形式 | 用户数 | 反馈渠道 |
|------|---------|-------|---------|
| W2-3(M1 后) | 自己 dogfood + 1 位志愿者 | 1+1 | 微信文档收集 |
| W7(M3 中) | 内测 5 位志愿者(已确认 3 位) | 5 | 飞书表单 + 录屏 |
| W10(M3 末)| 公开 v0.5 招募(50 人内测) | 30-50 | GitHub Discussions |
| W13(M4 末)| 公开 v0.8 + NSM 自报回填 | 100 | 飞书表单 + 用户访谈 |
| W16(M6) | v1.0 发布 + 持续运营 | 不限 | GitHub issue + 邮件 |

### 2.4 内容产出节奏(求职可见性)

| 频率 | 形式 | 平台 |
|------|------|------|
| 每周 1 次 | 工程笔记(本周做了什么 / 踩了什么坑) | Twitter / V2EX / 即刻 |
| 每个里程碑 1 次 | 长文复盘(技术抉择、ADR 解读) | 个人博客 / 知乎 / 掘金 |
| W6、W10、W13、W16 | Demo 视频 60s | 视频号 / B 站 |
| W16 | 中英文 README + 1 篇英文 launch post | GitHub / Hacker News |

---

## 3. M0 — 仓库骨架(Week 1)

### 3.1 入口前提

- 所有 8 份核心文档已写完(本份是其中之一,M0 在文档收尾后立即开工)
- 已有阿里云百炼 API Key 与 ¥15 余额(ADR-0003)

### 3.2 目标

把"约定"落到代码上。**不实现任何 LLM 业务**,只把工程基座打通。

### 3.3 任务清单

| 任务 | 估时 | 备注 |
|------|------|------|
| `git init` + 初始 commit + push GitHub | 0.5h | repo `lemma42796/job-copilot`,public,MIT |
| monorepo 骨架(`apps/api` / `apps/web` / `packages/schemas` / `evals` / `docs`) | 2h | 见 8-ENGINEERING |
| `uv` Python 工程 + `pnpm` workspace + `pre-commit` | 2h | ruff / mypy / biome |
| `docker-compose.yml`:postgres(pgvector/pg16) + api + web + caddy | 3h | 健康检查、volume |
| FastAPI Hello + `/v1/health` 实现 + OpenAPI 自动导出 | 2h | sse-starlette 跑通示例 |
| Next.js 15 Hello + 调用 `/v1/health` 显示 status | 2h | App Router |
| `packages/schemas` 自动生成 TS 类型 + CI 卡口 | 2h | datamodel-code-generator |
| GitHub Actions:lint + type-check + 启动验证 | 2h | 不跑评测 |
| `.env.example` + README 启动说明初稿 | 1h | 强调 BYOK |

### 3.4 完成定义(DoD)

```bash
git clone https://github.com/lemma42796/job-copilot.git
cd job-copilot
cp .env.example .env  # 用户填 DASHSCOPE_API_KEY
docker compose up -d
curl http://localhost:3000           # ← Next.js 显示 "API: ok"
curl http://localhost:8000/v1/health # ← {"status":"ok",...}
```

### 3.5 退出标准

- ✅ `docker compose up` ≤ 3 分钟启动完成(冷启动)
- ✅ CI 全绿
- ✅ `apps/web` 能从 `apps/api` 拿到 `/v1/health` 数据
- ✅ Postgres 中 pgvector / pgmq extension 已 enable

### 3.6 不在范围

任何 LLM 调用、Agent、业务表 schema(M1 起做)。

---

## 4. M1 — 数据入口贯通(Week 2-3)

### 4.1 目标

把"用户能把 JD 与简历喂进系统"完整跑通,数据已结构化 + 已 chunked + 已 embedded。

### 4.2 关键交付

#### Week 2

| 任务 | 估时 |
|------|------|
| 业务表 schema:`users` / `jds` / `profiles` + 子表(见 3-DATA_MODEL §3.1-3.7) | 4h |
| Alembic 迁移 + 种子数据脚本 | 2h |
| `LLMClient` 抽象层 + DashScope provider 实现(Tier 路由 / Cache 控制位 / 重试) | 6h |
| `JDParserAgent` 实现(文本/PDF/图片三入口)+ `/v1/jds/parse` 端点(SSE) | 6h |
| 前端:JD 粘贴/上传页 + 解析结果可视化 + 手动编辑 | 6h |
| `evals/suites/jd_extract` 数据集 50 条 + promptfoo CI | 4h |

**Week 2 末DoD**:用户可粘贴 JD → 看到结构化字段 → 编辑 → 保存。`jd_extract` CI 全绿,`title_exact ≥ 0.92`。

#### Week 3

| 任务 | 估时 |
|------|------|
| `profile_chunks` 表 + 多粒度 chunk 策略实现 | 4h |
| `ProfileParserAgent` + `/v1/profiles/parse` SSE | 6h |
| Embedding 接入(`text-embedding-v4`)+ `pgvector` HNSW 索引 | 3h |
| 前端:简历上传 + 解析结果表单 + chunks 可视化(调试用) | 6h |
| `evals/suites/profile_extract` 数据集 30 条 + 端到端 chunk 召回断言 | 6h |
| 与 1 位志愿者 dogfood,收集第一波 bad cases | 2h |

**Week 3 末 DoD**:用户上传简历 → 看到结构化档案 → 编辑 → 保存 + 自动 chunk + embed。`profile_extract` CI 全绿,`skill_f1 ≥ 0.85`。

### 4.3 退出标准(M1 整体)

- ✅ 1 位志愿者完成"导入 1 份 JD + 上传 1 份简历"全流程,无需作者协助
- ✅ 已积累 ≥ 5 条 bad case,已对其中 high severity 修复
- ✅ 日均 LLM 成本 < ¥1(开发期不算 dogfood)
- ✅ 评测集 80 条全部跑通,各 suite 主指标达初始阈值

### 4.4 风险与回退

| 风险 | 触发 | 回退 |
|------|------|------|
| qwen3.6-flash 多模态 OCR 中文抽取效果差 | `jd_extract` 图片子集 < 70% | 前置 PaddleOCR 抽文本后再走 flash 文本输入 |
| `text-embedding-v4` 中文召回不稳 | `profile_extract` chunk_retrieval < 80% | 切回 BGE-M3(SiliconFlow,加 1 个外部依赖)或回退 v3 |
| 阿里云 ¥15 在 W3 末耗尽 | 触发 ADR-0003 复审条件 1 | 半天内切到 DeepSeek(等价 Tier 路由) |

---

## 5. M2 — 匹配分析 + 评测体系(Week 4-6)

### 5.1 目标

实现 `MatchAnalystAgent` + 把评测体系从"骨架"升级为"工程纪律"。

### 5.2 关键交付

#### Week 4 — 匹配分析

| 任务 | 估时 |
|------|------|
| `matches` 表 + ENUM | 1h |
| `QueryRewriterAgent`(单步) | 4h |
| Hybrid Search(pgvector + tsvector + RRF)+ Reranker(`gte-rerank-v2`) | 6h |
| `MatchAnalystAgent` 状态机(retrieve + analyze 两节点) | 6h |
| `/v1/matches` SSE 端点 + 引用追溯 | 4h |
| 前端:匹配结果页(评分 / 命中 / 缺失 / 差距分析 + 引用 hover) | 6h |
| `evals/suites/query_rewrite` + `match_analysis`(各 20-30 条) | 6h |

#### Week 5 — 评测扎根

| 任务 | 估时 |
|------|------|
| LLM-as-Judge(qwen3.6-plus,见 6-EVAL_PLAN §6.3) | 4h |
| 离线评测固件 dump(30 个 profile + chunks + embeddings) | 4h |
| Embedding 缓存 + numTests=3 | 3h |
| PR 评论自动汇总(top 3 失败 tag) | 3h |
| `bad_cases` 表 + API + 月度 triage 脚本 | 4h |
| Langfuse 自托管接入 + 前端 trace 链接 | 4h |
| `costs/summary` API + 前端 settings 成本面板 | 4h |

#### Week 6 — 体内自检 + 减脂

| 任务 | 估时 |
|------|------|
| 端到端 e2e Playwright(JD 入库 → 档案 → 匹配)| 6h |
| 性能 budget 验证(P95 延迟与成本指标核查 PRD §5)| 4h |
| 把所有 Prompt 版本化到 `prompt_versions` 表 + 版本切换 UI | 4h |
| 内部 dogfood:作者用真实简历跑 5 个真实 JD,收集体验问题 | 4h |
| 修 bug + 删冗余抽象 | 6h |

### 5.3 退出标准(M2 整体)

- ✅ 评测 4 个 suite(`jd_extract` / `profile_extract` / `query_rewrite` / `match_analysis`)CI 全绿,主指标达初始阈值
- ✅ 单次匹配分析端到端 P95 ≤ 15s,成本 ≤ ¥0.20
- ✅ Langfuse 已记录全部 LLM 调用,可按 feature 拆解成本
- ✅ 至少 1 个非作者用户(志愿者)反馈"匹配分析对我有用"
- ✅ 累计 LLM 总成本 < ¥10

### 5.4 风险与回退

| 风险 | 触发 | 回退 |
|------|------|------|
| Reranker 在百炼上效果不稳 | nDCG@10 提升 < 5pp | 关 reranker,只用 RRF |
| LLM-as-Judge 与人工 kappa < 0.7 | Judge 验证失败 | 切回纯规则 + BERTScore,长期再调 Judge |
| `match_analysis` MAE > 12 | 阈值不达标 | 强制升 PREMIUM tier 重跑,如仍不达,改简化评分 rubric |

---

## 6. M3 — 简历定制 GA(Week 7-10)

### 6.1 目标

完成 P0 最关键的功能:**端到端定制简历**。这是产品价值的核心,也是公开测试的入口。

### 6.2 关键交付

#### Week 7 — 状态机搭建

| 任务 | 估时 |
|------|------|
| `resumes` / `resume_versions` 表 | 2h |
| LangGraph 引入 + Postgres checkpointer 接入 | 4h |
| 简历定制状态机(retrieve / plan / draft / review / revise)5 节点 | 8h |
| `ResumePlannerAgent` + `ResumeDrafterAgent` + `ResumeReviewerAgent` Prompt v1 | 8h |
| `/v1/resumes/generate` SSE | 4h |
| 前端:简历生成页(进度条 / 流式 markdown 预览) | 6h |

**Week 7 末 DoD**:能跑出第一版 markdown 简历(可能粗糙),review 通过率 ≥ 50%,无 high severity 幻觉。

#### Week 8 — 反幻觉 + 可编辑

| 任务 | 估时 |
|------|------|
| `evals/suites/resume_review`(对抗性,20 条注入幻觉) | 6h |
| 对抗集合上 fabrication recall ≥ 0.95 调试 | 6h |
| markdown 编辑器(monaco + live preview) | 6h |
| version 管理 UI(diff / 切换 active) | 4h |
| Reviewer 失败时的"高亮 + 一键采纳建议"交互 | 4h |
| `evals/suites/resume_generate` 端到端 25 条 + LLM-as-Judge 综合分 | 6h |

**Week 8 末 DoD**:Reviewer fabrication recall ≥ 0.95,resume_generate Judge 综合分 ≥ 75。

#### Week 9 — 渲染与导出

| 任务 | 估时 |
|------|------|
| LaTeX `awesome-cv` 中文化模板调通(中文字体、间距) | 8h |
| markdown → LaTeX 转换器(章节 / bullet / 强调) | 6h |
| `/v1/resumes/{id}/export?format=pdf|docx|md` | 4h |
| 前端:导出按钮 + PDF 预览 | 4h |
| 字体 license 合规 + 模板版权说明写入 README | 2h |

#### Week 10 — 内测发布 v0.5

| 任务 | 估时 |
|------|------|
| 招募 30-50 内测用户(GitHub Discussions + Twitter) | 4h |
| 飞书反馈表单 + bad case 自动入库 | 2h |
| 性能稳定性收尾(超时 / 重试 / 缓存命中率) | 4h |
| 第一篇里程碑长文复盘 + Demo 视频 | 8h |
| GitHub Release v0.5(预发布)| 2h |

### 6.3 退出标准(M3 整体)

- ✅ 5 位内测志愿者每人完成 ≥ 3 份定制简历,无人遇到阻塞
- ✅ resume_generate 端到端 Judge 综合分 ≥ 75,Reviewer 通过率 ≥ 0.85
- ✅ 单次简历定制 P95 ≤ 60s,成本 ≤ ¥0.50
- ✅ GitHub Star ≥ 50,公开 issue ≥ 10
- ✅ 已修订 ≥ 1 次 prompt 版本(说明评测体系真的在用)

### 6.4 风险与回退

| 风险 | 触发 | 回退 |
|------|------|------|
| LaTeX 中文渲染坑爆(字体 / 间距 / 时间)| W9 末 PDF 不可用 | 切到 typst 模板(中文支持更稳)+ 推迟 docx |
| Reviewer recall 上不去 | 对抗集 < 0.85 | 增加规则前置过滤(数字、时间、公司名)+ Judge 二次复核 |
| 内测用户太少 | W10 招到 < 10 人 | 推迟 v0.5 公开 1 周,主动联系 5 个开发者群 |

---

## 7. M4 — 面试模拟 + 投递追踪(Week 11-13)

### 7.1 目标

完成 P1 功能,使产品形成"分析 → 简历 → 投递 → 面试"闭环。开始量化 NSM。

### 7.2 关键交付

#### Week 11 — 投递追踪(先做,简单且形成闭环)

| 任务 | 估时 |
|------|------|
| `applications` 表 + 状态机(applied → screening → interview → offer/rejected/ghosted) | 3h |
| `/v1/applications` 全 CRUD + `/stats`(NSM 输入)| 6h |
| 前端:投递看板(列表视图 + Kanban)| 8h |
| "标记已投递"在简历页一键完成 | 2h |
| NSM 自报基线问卷(用户首次进入投递页)| 4h |

#### Week 12-13 — 面试模拟

| 任务 | 估时 |
|------|------|
| `interview_sessions` / `interview_turns` 表 | 2h |
| 面试模拟状态机(plan_next / ask / judge_clarity / follow_up / evaluate / final_summary) | 10h |
| `InterviewPlannerAgent` / `InterviewerAgent` / `InterviewEvaluatorAgent` Prompt v1 | 8h |
| `/v1/interviews/{id}/turns` SSE 双向交互 | 6h |
| 前端:面试聊天界面(打字机效果 / 计时器 / 暂停)| 10h |
| 评分 + reference answer + 改进建议 UI | 6h |
| `evals/suites/interview_ask` + `interview_eval` | 8h |

### 7.3 退出标准(M4 整体)

- ✅ 投递追踪 + NSM 输入指标(`interview_invited_rate`)在 `/v1/applications/stats` 可查,且至少 5 位用户回填
- ✅ 面试模拟跑通 ≥ 7 轮 / 单场,无回答模糊未追问的样本
- ✅ `interview_ask` Judge ≥ 78,`interview_eval` 排序一致性 = 1.0
- ✅ 公开 v0.8,招募至 100 用户,日活 ≥ 10
- ✅ 累计 LLM 总成本 < ¥80

### 7.4 风险与回退

| 风险 | 触发 | 回退 |
|------|------|------|
| 面试 SSE 双向交互前端复杂(EventSource 不支持 POST 流)| W12 中前端阻塞 | 改用 fetch streaming reader(标准 ReadableStream)|
| 面试题质量 Judge < 70 | W13 评测不达标 | 增加 few-shot 题目示例;强制 PREMIUM tier |
| 用户不回填 NSM | W13 末 < 3 条样本 | 改投递状态变更时强制弹问卷,加退出按钮 |

---

## 8. M5 — 浏览器扩展 + MCP Server(Week 14-15)

### 8.1 目标

让 JobCopilot 不止于"网站",还能嵌入用户已有的工作流(浏览器 / Claude Desktop)。这是关键的差异化与作品集亮点。

### 8.2 关键交付

#### Week 14 — 浏览器扩展(智能粘贴)

| 任务 | 估时 |
|------|------|
| Chrome MV3 扩展骨架(content + background + popup) | 4h |
| 选中文本一键导入 → 调用本地 `/v1/jds/parse` | 4h |
| 自动检测页面是 JD(URL 模式 + 启发式) | 4h |
| 弹层显示解析结果 + 跳转到 JobCopilot 详情 | 4h |
| 不抓站,只在用户主动点击时工作(明确写入说明)| 2h |
| Chrome Web Store 上架(待审,留 buffer)| 2h |

#### Week 15 — MCP Server

| 任务 | 估时 |
|------|------|
| MCP Python SDK 接入,server 骨架 | 4h |
| 5 个工具:`get_match_analysis`、`generate_resume`、`list_jds`、`add_jd`、`mock_interview_round` | 8h |
| 1 个 resource:`profile://current`(只读返回 ProfileStructured 摘要) | 2h |
| Claude Desktop 接入说明 + 演示视频 | 4h |
| 安全边界:本地 socket only,不监听网络口 | 2h |
| 写一篇 "JobCopilot × Claude Desktop" 博客 | 4h |

### 8.3 退出标准(M5 整体)

- ✅ 扩展安装 ≥ 20 真实用户,智能粘贴成功率 ≥ 90%
- ✅ MCP server 在 Claude Desktop 中完成"连接 → 调用 generate_resume → 返回 markdown"完整路径
- ✅ 公开 demo 视频 ≥ 1 条 60s,带英文字幕
- ✅ 累计 LLM 总成本 < ¥120

### 8.4 风险与回退

| 风险 | 触发 | 回退 |
|------|------|------|
| Chrome Web Store 审核拖延 | W15 仍未上架 | 提供手动加载说明,推迟到 v1.0 后 |
| MCP 协议 2026 版本变动 | SDK 不兼容 | 锁定一个稳定版本,文档明确 |

---

## 9. M6 — 公开发布(Week 16)

### 9.1 目标

GitHub Release v1.0 + 中英文 launch + 长期运营机制。

### 9.2 关键交付

| 任务 | 估时 |
|------|------|
| VitePress 文档站(将所有 docs/ 编译为可搜索站点)| 8h |
| 云端 Demo 站部署(阿里云轻量 + Caddy + BYOK)| 6h |
| 中英文 README 终稿 + 一图概览 + 快速开始 | 4h |
| Hacker News / Twitter / V2EX / GitHub Trending 推广文案 | 4h |
| 写"为什么我做了 JobCopilot"求职故事长文 | 6h |
| 60s launch 视频 | 6h |
| GitHub Release v1.0 + CHANGELOG + 致谢 | 2h |
| 把 16 周的全部博客整理成"做 LLM 应用工程化的 16 周笔记"系列 | 4h |

### 9.3 退出标准

- ✅ GitHub Release v1.0 已发,CI 全绿
- ✅ 文档站可访问,搜索可用
- ✅ Demo 站 BYOK 模式可用,数据 30min TTL
- ✅ 至少 1 篇 launch 文章触达 ≥ 1000 阅读
- ✅ 作者本人在求职期内通过该项目获得面试邀约 ≥ 3 个(KR4 中期检查)

### 9.4 后续运营(超出 16 周但需提前安排)

- 每周 1 次 issue triage(每周日 1h)
- 每月 bad case 月度 triage + eval baseline 更新
- 每季度大版本(v1.x)规划 PR
- Q3 启动 v2:应届生画像 / 多语言 / 团队协作

---

## 10. 横向跟踪指标

每周更新到 `docs/STATUS.md`(本文档作为节奏参照,不在每周更新):

| 指标 | M0 | M1 | M2 | M3 | M4 | M5 | M6 |
|------|----|----|----|----|----|----|-----|
| 累计 LLM 成本(¥)| 0 | < 5 | < 10 | < 50 | < 80 | < 120 | < 200 |
| GitHub Star | - | - | - | ≥ 50 | ≥ 200 | ≥ 350 | ≥ 500 |
| 真实用户(非作者) | - | 1 | 1 | 5 | 30 | 50 | 100+ |
| Bad case 池 | - | 5 | 20 | 50 | 80 | 100 | 130 |
| Eval suite 数 | - | 1 | 4 | 6 | 8 | 8 | 8 |
| Prompt 版本累计 | - | 2 | 4 | 8 | 12 | 12 | 14 |
| 面试邀约(作者本人)| - | - | - | - | ≥ 1 | ≥ 2 | ≥ 3 |

---

## 11. 关键开放问题(里程碑相关)

- **Q-RM-01**:M3 v0.5 公开测试时,是否同步开放云端 Demo?默认延后到 M6,理由是 BYOK 体验差;若 W10 反馈"本地部署门槛高"超过 50% → 提前 Demo
- **Q-RM-02**:M4 末若 NSM(`interview_invited_rate`)样本数 < 5,是否延后 M5 ?默认不延后,但 M6 launch 文案不强调 NSM,改强调"工程作品集"叙事
- **Q-RM-03**:¥15 阿里云余额耗尽前是否预先把回切 DeepSeek 的代码通路打通?默认 W6(M2 末)做这件事,半天 buffer

---

## 12. 不在本文档范围

| 主题 | 文档 |
|------|------|
| 各 Agent 实现细节 | 5-AGENT_DESIGN |
| 各端点详细约定 | 4-API_SPEC |
| 评测样本与阈值 | 6-EVAL_PLAN |
| 仓库目录结构与 CI/CD | 8-ENGINEERING |
| 长期产品愿景(v2 / v3) | 1-PRD §7 / 单独 vision doc |
