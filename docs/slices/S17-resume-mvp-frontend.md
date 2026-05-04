---
title: S17 简历定制 MVP 前端 — 切片归档
status: ✅ 完成,代码 + 文档 + push 一并提交
date: 2026-05-04
purpose: M2 主线刀 — S16 后端骨架对应的前端实现:match 详情页触发按钮 + /resumes 列表 + /resumes/[id] 详情 + sidebar 入口;首条 dogfood 通过(reviewer 精准识别 7 条 drafter 幻觉)
---

# 切片范围

S17 = 简历定制 MVP **前端**(对接 S16 已建好的 4 个 endpoint)。范围按 STATUS.md S16-S17 规划区 D9 + D10 收紧:

- 触发入口仅从 match 详情页(沿 D9)
- 4 个 endpoint 全接(generate SSE / list / detail / delete)
- 详情页只读 markdown,无编辑 / 版本切换 / regenerate(沿 D5 / D10)
- 下载 .md 走浏览器 Blob,无后端 export(D1 markdown only)

# 产出

```
apps/web/src/
├── lib/api.ts                              # 加 6 个 Resume 类型 alias + createResume SSE generator + getResume / listResumes / deleteResume
├── components/
│   ├── list/resume-card.tsx                # 列表卡(StatusBadge 4 状态 + StatusPill + meta 行)
│   └── shell/sidebar.tsx                   # 加"简历定制"组 + PenIcon
└── app/
    ├── matches/[id]/
    │   ├── resume-trigger.tsx              # SSE 触发组件(套 MatchTrigger 模式)
    │   └── match-result.tsx                # 末尾挂 <ResumeTrigger match={match} />
    └── resumes/
        ├── page.tsx                        # SSR 拉 listResumes + listJds (jdLookup) 同 matches 模式
        ├── resumes-client.tsx              # cursor + 行内 confirm 删除
        └── [id]/
            ├── page.tsx                    # SSR getResume,404 处理
            └── resume-detail.tsx           # mini-markdown 渲染器 + status banner + review_findings 警告条 + 下载 .md
```

`packages/schemas/src/api.ts` regen 同步:新增 6 个 Resume 命名空间类型(ResumeCreateInput / ResumeDetail / ResumeListItem / ResumeListResponse / ResumeStatus / ResumeTokens / ReviewFinding)+ 3 路径 4 endpoint。

# 设计决策(实现细节)

- **mini-markdown 渲染器(不引 react-markdown 依赖)**:drafter prompt 硬约束输出仅 H2 / bullet / 段落 / `**bold**`,本地写 4 个 block 类型(`{kind:'h2'} | {kind:'bullets'} | {kind:'p'}`)+ Inline `**bold**` 拆段够覆盖。代码块 / 链接 / 表格 / inline code 不渲染(简历正文不出现);drafter 罕见脱出约束时 fallback 段落分支显示 raw 文本(仍可读)。引依赖换更鲁棒的渲染留给 M3。

- **下载 .md 走浏览器 Blob**(无后端 endpoint):API_SPEC §6.6 的 `POST /resumes/{id}/export` MVP 不接,M3 加 PDF/DOCX 时再起。MVP markdown 用 `new Blob([md], {type:'text/markdown'})` + `URL.createObjectURL` 触发 anchor.click 直接下载,filename 走 `resume.title` 清洗 illegal chars + 80 字截断。

- **ResumeTrigger 套 MatchTrigger 完全同款**:phase 状态机 `idle → starting → generating → redirecting`;成功 emit `result {url}` 后 router.push;失败 `error → done(ok=false)` 显示 `error.detail`。区别仅文案(写"生成中"而非"分析中")和成本/延迟提示(¥0.04-0.06 / 30-90s)。

- **review_failed 视觉处理 = 大警告条 + ReviewFindingsList**:`status='review_failed'` 时挂醒目 Card(warning bg + 标题 ⚠ + 副标题强调"直接使用前请逐条核对" + 列出全部 findings);`status='ready'` 但仍有 medium/low findings(无 high)→ 末尾挂普通 ReviewFindingsCard 不阻断使用。`failed`(IO/schema 层错)→ 红色 banner 引导删除重试,不展示 markdown(因为 markdown 为空)。

- **列表页卡片 StatusBadge 4 状态**:ready ✓ 绿 / review_failed ! 黄 / failed × 红 / generating … 灰。配合 StatusPill 副标识"生成中 / 待人工核查 / 生成失败"。

- **debug footer 保留**:沿 match 详情页祖传(drafter / reviewer 模型 / tokens / cost / latency),dogfood 调试用。M2 末统一收折叠债。

- **mini-markdown parser 与 `noUncheckedIndexedAccess` 兼容**:tsconfig 启用了这个 strict 项,`lines[i]` 返 `string | undefined`。在 while-loop 头部加 `const line = lines[i] ?? ''` 把类型固化成 string,内部全部用 `line` 而不是再 index。同模式套到内层 bullet / 段落 collector。

- **`noArrayIndexKey` lint 规避**:Block / Inline 渲染里把 index 与值的稳定切片拼成 key(`${i}-${item.slice(0, 16)}` / `${block.kind}-${i}-${...}`)。同 S15 SuggestionsCard 的 `${i}-${s.slice(0,16)}` 模式。

- **typed routes 自动覆盖**:Next.js App Router 的 `Route` 类型基于实际页面文件自动推导;新建 `app/resumes/page.tsx` + `app/resumes/[id]/page.tsx` 后,`Route` 自动包含 `/resumes` 与 `/resumes/[id]`,所有 `as Route` cast 都过。无需手工注册。

- **listResumes 同 matches 列表模式**:并发拉 `listResumes({limit:20})` + `listJds({limit:100})` 拼 jdLookup;miss fallback `JD #${jd_id}`。100 条 JD 单 user 量级足够,数据量大时改后端 list endpoint 直接 enrich(同 S15 留的债)。

- **单 commit 包 S16 + S17**:本切片完成时 working tree 同时含 S16 后端骨架(待 commit)+ S17 前端;两刀本质是"简历定制 MVP 端到端"一件事,任何一刀单独 commit 都不能 dogfood,合一 commit 比拆两条 git history 更清晰。

# 期间踩到的坑

1. **CSS link 时间戳 404 / SVG 铺满屏幕(第二次撞同一坑)**:跑过 `pnpm build`(production)写了 `.next` 后,旧的 next dev 共用同目录,SSR 渲染的 `<link href=...?v=N>` 命中失效的 css 文件,layout.css 没加载,sidebar `w-[220px]` 失效,SVG 不受 `size-4` 约束铺满。**修法**:停 dev → `rm -rf apps/web/.next` → `pnpm dev` 重启。S13-S15 归档卡(#1)第一次记过,这次第二次撞 — **升级为永久约束 #3**:dev / build 不混用同一 `.next` 目录;build 后必须清 `.next` 再起 dev。

2. **`noUncheckedIndexedAccess` 把 mini-markdown parser 全部 ` lines[i]` 标 undefined(6 处)**:S5 / S15 都没碰到这种密集 index 访问。修法:在 while loop 头 `const line = lines[i] ?? ''` 提取局部 string-typed 变量,内部全部用 `line` 替代 index 访问。下次写循环 + array index 时直接用这个模式。

3. **`noArrayIndexKey` 4 处**:Block / Inline 里渲染 markdown 拆出来的子 fragment 时纯 `key={i}` 触发。补 stable 后缀模式套 `${i}-${item.slice(0, 16)}`(同 S15)。

4. **biome 自动 format 抹掉手写换行**:三个文件首版用了较窄的换行(贴近 80-char),biome 默认 100-char 全部重排了。`pnpm biome format --write` 直接接受,代码语义不变。

5. **ruff `RUF002` ambiguous unicode `×`**:drafter agent.py docstring 写"30s × 3"被识别为非 ASCII 数学乘号,ruff 挡。改"的 3 倍"。下次 docstring 别用 `×` / `÷` / `±` 等数学符号。

6. **biome glob 在 zsh 下要单引号包**:`pnpm biome format --write src/app/resumes/[id]/...` 直接写裸路径报 "no matches",必须 `'src/app/resumes/[id]/...'` 单引号包(zsh 默认 nomatch 严格)。

# Dogfood 数据(2026-05-04 第一次端到端)

- **入参**:JD #3「高级 Python 后端工程师(AIGC 方向)」+ 简历 #13 + match #2(score=72)→ resume #1
- **结果**:status='review_failed' / 7 findings(**3 high / 3 medium / 1 low**)
- **性能**:tokens 6567 in / 1670 out(drafter+reviewer 之和) / **¥0.016** / **12.4s**
  - 估算 ¥0.04-0.06 / 30-60s — 实测**双指标 60% 优于估算**;余量大,后续可考虑升 STANDARD/thinking
  - 1 条样本不够算 P95,留 S18 累积 5+ 条
- **对照 M2 退出 budget**:cost ≤ ¥0.20 ✅(13x 余量) / SSE ≤ 90s ✅(7x 余量)

## Reviewer 7 条 finding(业务质量验证)

| # | 严重度 | issue | 草稿原文 | 病灶 |
|---|---|---|---|---|
| 1 | high | fabrication | "主导 RAG + LangGraph PoC" | chunks 标 chunk 86 = 业余项目,drafter 当成职业主导 |
| 2 | high | unsupported_number | "曾处理日均 80w+ 订单及峰值 QPS 12w" | **跨公司数据合并** — 80w 是携程订单 / 12w 是字节直播,被合并成单段成果 |
| 3 | high | fabrication | "Java (JDK 17/21)" | chunks 仅提"Spring Boot+JDK 21 升级背景",drafter 错抽为"会 Java" |
| 4 | medium | exaggeration | "精通 FastAPI/Django/..." | chunks 写"掌握"/"熟悉",drafter 升级到"精通" |
| 5 | medium | fabrication | "TopPerformer 评级" | chunks 是"团队 TopPerformer",drafter 漏"团队"暗示个人绩效 |
| 6 | medium | unsupported_number | "多 Agent 协作流程编排" | chunks 仅"路由到不同 LLM agent",drafter 抽象拔高 |
| 7 | low | exaggeration | Mentor 主语漂移 | chunks 主语是新人,drafter 改写后变 candidate 助力 |

**MVP 设计目标达成**:reviewer 兜住 drafter 幻觉,前端展示 markdown + 警告条让用户自决。如果只看 markdown 不看 reviewer,候选人直接投递就有麻烦。

# 给 S18 / prompt v1.0.1 的优化方向(基于上面 7 条 findings)

drafter prompt 需要加 4 条强约束(在 v1.0.0 的"写作铁律"基础上扩):

1. **每条 bullet 仅引用单一 chunk 的事实**,禁止跨公司 / 跨项目数据合并(对应 #2 / 类似的合并模式)
2. **副词白名单**:仅允许"掌握 / 熟悉 / 参与 / 负责 / 主导";禁用"精通 / 优秀 / 卓越 / 资深"(对应 #4)
3. **业余项目独立成段并标注"侧项目 / 独立开发者"**,不可与职业经历同语调(对应 #1 / #6)
4. **技能列表硬规则**:只列 chunks 中明确出现"会 X / 用 X 做了 Y"的语言/框架;栈背景(JDK 版本号 / Docker 镜像 / 数据库版本)不算 skill 证据(对应 #3)

加约束的同时 reviewer prompt 要相应放宽 medium/low 误报阈值,避免好不容易压住的 high 全转 medium 充数。两个 prompt 一起调,跑 5+ 样本对比 v1.0.1 vs v1.0.0 的 review 通过率 / high finding 比例。

# 闸门

| 项 | 数字 |
|---|---|
| 后端 alembic | `0012 (head)` ✅(0011 → 0012 顺利) |
| 后端 `pytest -q` | **321 passed** in 59s ✅(数字与 S13-S15 一致;S16 没新写测试,test_migrations EXPECTED_TABLES 当前不阻塞额外表) |
| 后端 ruff | All passed ✅(顺手清掉 RUF002 ambiguous `×`) |
| 后端 mypy | 70 src files / 0 issues ✅ |
| API 路由活 | `/v1/resumes/generate` + `/v1/resumes` + `/v1/resumes/{id}` 3 paths / 4 endpoints ✅(`curl /v1/openapi.json` 验) |
| 前端 typecheck | All passed ✅(顺手修 noUncheckedIndexedAccess 在 mini-markdown parser 6 处 + biome 自动 format 3 文件 + 手动改 4 处 noArrayIndexKey) |
| 前端 biome | All passed(45 files)✅ |
| 前端 next build | All passed,11 routes,新增 `/resumes`(2.66 kB)+ `/resumes/[id]`(5 kB)✅ |
| `pnpm gen` | api.ts 22 处 Resume 引用 ✅ |
| 端到端 dogfood | ✅(见上方"Dogfood 数据"区) |

# 给后续切片的输入

- **S18 简历定制 dogfood + prompt v1.0.1**(M2 主线下一刀候选):跑 5+ 真实 (JD, profile),drafter prompt 升 v1.0.1(上方 4 条强约束),reviewer prompt 同步调阈值。评估:① review 通过率(目标 ≥ 50%,目前 0/1)② 高严重度 finding 平均数(目前 3,目标 ≤ 1)③ cost / latency 抖动 ④ 章节齐全度 / chunks 引用准确度。
- **`match_analysis` + `resume_review` evals suite**(M2 评测扎根阶段):用 dogfood 真实三元组沉淀 dataset,LLM-as-Judge。这次的 7 条 findings 已是天然标注样本(quoted_text 是草稿原文,explanation 是判定理由),进 dataset 直接复用。
- **typecheck `noUncheckedIndexedAccess` + 循环 array index 模式**:已沉淀,后续 S18+ 写 markdown / chunked text 处理时直接套 `const x = arr[i] ?? ''` 提取局部变量。
- **CSS link 时间戳 404 坑升级为永久约束 #3**:dev / build 不混用 `.next` 目录(详 STATUS.md)。

# 什么没改(本切片范围外)

- 简历正文编辑 / monaco / live preview / version diff(M3 编辑器)
- 多版本切换(M3 `/resumes/{id}/versions`)
- regenerate / export PDF/DOCX / patch 端点对接(M3)
- 触发入口扩展(JD 详情 / `/resumes/new`)— D9 收窄
- chunk evidence hover 联动(M2 v1.1 提质,同 match 列)
- 详情页 footer 调试 metadata 收折叠(M2 末统一)
- prompt v1.0.1(留 S18,有真实 dogfood 后再调,避免空想优化)
- evals suite(M2 评测扎根阶段)
