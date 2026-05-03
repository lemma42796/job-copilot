---
title: S9 前端简历闭环 + PartialDate + stats.chunks 修 — 切片归档
status: ✅ 完成
date: 2026-05-03
purpose: profile 全栈贯通 — 上传 / 解析 / 展示 / patch / rechunk / chunks 调试 / delete;附带兼容简历缺位日期
---

# 产出

```
apps/web/src/
├── lib/
│   ├── sse.ts                              # C2: 通用 streamSse<TFrame> = fetch + ReadableStream + TextDecoder
│   └── api.ts                              # C1: profile/chunk types + uploadFile(multipart) + parseProfile/rechunkProfile(SSE) + getProfile/patchProfile/deleteProfile/listProfileChunks
└── app/
    ├── page.tsx                            # C6: 首页加「上传简历」入口
    └── profiles/
        ├── new/page.tsx                    # C3: 文本/PDF 双 tab + 4 阶段进度条 + 409 PROFILE_EXISTS 引导
        └── [id]/
            ├── page.tsx                    # C4: server fetch + notFound;组合 profile-edit-form + chunks-debug
            ├── profile-edit-form.tsx       # C4: 顶层 PATCH 表单 + 4 列表只读 + delete 二次确认 + fmtMonth(YYYY-MM)
            └── chunks-debug.tsx            # C5: <details> 折叠 + GET /chunks 列表 + rechunk SSE 进度

apps/api/src/jobcopilot_api/
├── schemas/profiles.py                     # +PartialDate type alias(BeforeValidator 兜底 YYYY / YYYY-MM)
└── routers/profiles.py                     # _detail() 加 chunks_count 查询(修 stats.chunks 永远 0 的预先 bug)

apps/api/tests/
├── unit/test_profile_schemas.py            # +5 PartialDate 单测
└── integration/test_profiles_router.py     # rechunk_sse_emits + GET stats.chunks==3 联动断言
```

# 设计决策(实现细节)

- **前端 SSE 走 fetch + ReadableStream,不用 EventSource**:`X-User-Id` header 必须能塞,EventSource 不支持。`lib/sse.ts:streamSse<TFrame>()` 30 行手解 SSE 帧:`body.getReader()` + `TextDecoder` + buffer split on `\n\n`。失败时 `ApiError` 走 RFC 7807 解析对齐 `jsonFetch`,异常路径完全统一。
- **PDF 上传两步独立**:`uploadFile(File, FilePurpose.profile_pdf)` → `parseProfile({source:'pdf_upload', file_id})`。upload 走 multipart 不走 jsonFetch(让浏览器自动加 boundary,共享 `X-User-Id` + `ApiError` 解析即可);parse 走 SSE。一边失败一边可独立重试,不浪费 sha256 去重命中。
- **4 阶段进度条**(`uploading 10% → started 30% → chunking_embedding 60% → result 90% → done 100%`):映射 SSE event 名 + 一个 PDF 上传前缀阶段。`chunking_embedding{ok:false}` 渲染为 warning 而非 error,UI 文案「自动生成 chunks 失败,简历已保存,可在详情页手动重建」(对齐 S8 永久约束 15 best-effort 语义)。
- **409 PROFILE_EXISTS 引导**:detail 字符串 `user X already has profile Y; ...` 用 `/profile (\d+)/` 正则提 id,UI 弹「已有简历(#Y)」+ 「查看 / 删除现有简历」按钮直跳 `/profiles/Y`。后端 detail 字符串成了非正式契约,改时要兼容(短期可接受,长期 M2 给 ConflictError 加结构化 detail 字段)。
- **detail 页 4 列表只读**:S9 第一刀 PATCH 只支持顶层字段(full_name / phone / email / location / summary)。`buildStructured(current, top)` 把 children 列表原样回传(DELETE-then-INSERT 等价于不动)。后续切片再加 children 编辑。
- **chunks 调试用 `<details><summary>`**:shadcn 没装 collapsible,原生 `<details>` + Tailwind `border / hover` 直接出来,不引入新组件依赖。对应永久约束没单独立(`<details>` 是平台原语,不是项目约束)。
- **删除二次确认就地切换**:不开 modal,卡片右上角 `[删除]` → 切换成 `[确认删除？] [确认] [取消]` 行内布局。最少代码 / 不引 dialog 组件。
- **`_pad_partial_date` 容忍简历日期**:LLM 抽简历时返回原文格式(`"2020-01"` / `"2016"`),Pydantic `date` 只收 `YYYY-MM-DD`。`Annotated[date | None, BeforeValidator(...)]` 在解析前补到 `01` 落库;前端 `fmtMonth()` 渲染时 `^(\d{4})-(\d{2})/` 截到 `YYYY-MM`。day 永远是占位,但简历语义本来就模糊到月,UI 显式不暴露 day。**适用面**:任何 LLM 抽出来的 date 字段(JD start/end / 面试 schedule / 简历定制时间线 ...),后续切片直接用 `PartialDate`(永久约束 17)。
- **修 `stats.chunks` 永远 0**:`_detail()` 之前不查 `profile_chunks` 表,导致 ProfileStats 默认 0 对外暴露。补 `sa.select(sa.func.count()).select_from(ProfileChunk).where(profile_id == ...)` 一句 SQL 解决。集成测试在 rechunk happy path 后加 `GET /profiles/{id}` 联动断言。

# 期间踩到的小坑

1. **LLM 一次性 schema_invalid 全是 partial date**:三次失败合计 ¥0.018,根因不是 prompt 不好,是 `educations[0].start_date="2016"` / `experiences[0].start_date="2020-01"` 这种简历常态格式 Pydantic 不收。debug 路径:写 `/tmp/debug_profile_parse.py` 直接调 DashscopeProvider + 打印 raw content + 手跑 `model_validate_json` 看 ValidationError 列表 → 一眼看到 `date_from_datetime_parsing input is too short`。**教训**:LLM schema_invalid 别先怪 prompt 怪 model,先看 ValidationError 具体哪个字段哪种 type — 多数是输入空间和 schema 边界不匹配。
2. **`_detail()` 漏算 chunks 是 S8 留的尾巴**:S8 写 ProfileChunk ORM + rechunk 路径,但 GET 详情 stats 还沿用 S7 的 `experiences/projects/skills/educations` 四件套,`chunks=0` 用了 schema 默认值。S9 测试时才发现「rechunk 完了但 stats.chunks 还显示 0」。M2 加任何「stats」字段都要回过来检查 GET 是否填值。
3. **biome formatter 偏好单行,Edit 工具后总要二次 format**:多次写完代码 `pnpm lint` 报 format 不一致,`pnpm exec biome format --write <files>` 一把过。下次写 React 组件直接事先单行格式化常见 short call(`<Input id={..} value={..} ... />`),减少回合。
4. **Next.js 15 `<Link href={...} as Route>`**:`router.push(\`/profiles/${id}\` as Route)` 必须显式断言,沿用 S5 永久约束 7。**漏一处全栈类型崩**。
5. **`Button` 没有 `variant="secondary"`**:只有 `default / outline / ghost / destructive`。typecheck 一报错就改 outline。
6. **服务端组件 vs 客户端组件**:`/profiles/[id]/page.tsx` 是 server(`async function` + `await getProfile`),`profile-edit-form.tsx` + `chunks-debug.tsx` 都 `'use client'`。Server 组件传 plain object props 给客户端,enum / Date 都要 JSON-serializable —— ProfileDetail 已经满足(`date` 序列化成 string)。
7. **`fetch` 在 server side 拿不到 cookie 但 X-User-Id 是 env 来的**:`api.ts` 的 `USER_ID = process.env.NEXT_PUBLIC_USER_ID ?? '1'` 在 server / client 两端都拿到值,统一注入 header。M5+ 切 JWT 时这一段重写但前端调用面不变。
8. **Static rendering vs Dynamic**:Next 默认尝试静态化 `/profiles/new`(无 fetch);`/profiles/[id]` 动态(因为 fetch with `cache: 'no-store'`)。build log 显示 ƒ(动态) vs ○(静态)是预期。
