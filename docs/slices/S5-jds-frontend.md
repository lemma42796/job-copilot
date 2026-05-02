---
title: S5 前端 JD 粘贴页 + 结构化结果可视化 + 编辑保存 — 切片归档
status: ✅ 完成
date: 2026-05-02
purpose: M1 数据入口贯通的前端入口闭环;ADR-0006 D4 同步路径(SSE / 列表延后)
---

# 产出

```
apps/web/
├── postcss.config.mjs                # 新建,Tailwind v4 PostCSS 集成
├── package.json                      # +tailwindcss@^4 +@tailwindcss/postcss@^4
│                                     # +class-variance-authority +clsx +tailwind-merge
│                                     # +lucide-react +@radix-ui/react-slot +@radix-ui/react-label
└── src/
    ├── app/
    │   ├── globals.css               # 改写,@import "tailwindcss" + @theme 注册主题色
    │   ├── page.tsx                  # 改:加导航 Button → /jds/new,改用 Tailwind utility
    │   └── jds/
    │       ├── new/page.tsx          # 新:client component,粘贴 + parseJd → router.push
    │       └── [id]/
    │           ├── page.tsx          # 新:server component,getJd 后 → JdEditForm
    │           └── jd-edit-form.tsx  # 新:client component,核心 6 字段 inline 编辑 + skills/职责只读
    ├── components/ui/                # 新:shadcn 最小子集
    │   ├── button.tsx                # cva variants(default/outline/ghost/destructive)+ asChild
    │   ├── input.tsx
    │   ├── textarea.tsx
    │   ├── card.tsx                  # Card / Header / Title / Description / Content / Footer
    │   └── label.tsx                 # @radix-ui/react-label 包装
    └── lib/
        ├── utils.ts                  # 新:`cn(...)` = twMerge(clsx(...))
        └── api.ts                    # 改写,加 ApiError 类 + jsonFetch helper(自动注入 X-User-Id)
                                      # + parseJd / getJd / patchJd

apps/api/src/jobcopilot_api/routers/
└── jds.py                            # 1 行改:@router.post 加 `responses={201: {"model": JDParseResponse}}`
                                      # 让 OpenAPI 暴露 201 schema,否则前端 unknown(见永久约束 6)

packages/schemas/src/
└── api.ts                            # 自动 regen(含 JDParseResponse / JDStructured / JDDetail / JDPatchInput / 各 enum)
```

# 设计决策(实现细节)

- **UI 工具链选型**:Tailwind v4(`@import "tailwindcss"` + CSS 内 `@theme`)+ shadcn 最小子集(button/input/textarea/card/label,5 个 ui/* 组件)。**无 `tailwind.config.ts`**(v4 默认全文件扫描)。理由:行业默认,后续 S9 简历表单复用,不装就只能继续 inline `style={...}` 写复杂表单。
- **路由结构**:`/jds/new`(client + form)+ `/jds/[id]`(server fetch + 编辑 client form)。SSE / 列表页 STATUS 切片表未列,延后到 S5+ 或 M2(避免下次重做)。
- **`X-User-Id` header**:M1 单用户,前端 `NEXT_PUBLIC_USER_ID` env 读默认 '1';所有前端 API 调用走统一 `jsonFetch` helper 自动注入。M5+ 替换成 JWT,前端 `jsonFetch` 内部一处改,router 不变。
- **PATCH structured 重建**:`JDDetail` 是扁平字段(`title / company / ...` 直接在顶层),`JDStructured` 是 nested + 部分 required(`title / salary_period / description / confidence`)。编辑表单只暴露核心 6 字段(`company / title / location / salary_min / salary_max / description`),其他字段(`hard_skills / soft_skills / responsibilities / job_level / years_required / education`)保留原值传回。`buildStructured(jd, edits)` helper 负责重建。
- **enum 字段类型**:`openapi-typescript --enum` 生成 TS enum 而非 string literal union,前端必须 `import { JDParseInputSource, JDStructuredSalary_period } from '@jobcopilot/schemas'`(不能 `'text_paste'` 字符串字面量)。
- **typed routes**:`router.push(`/jds/${id}` as Route)` —— Next 15 `experimental.typedRoutes` 启用后字符串模板需要 `as Route` cast。
- **OpenAPI 漏洞补丁**:`POST /v1/jds/parse` 之前 `response_model=None`(因为 union 返回 `JDParseResponse | EventSourceResponse`)导致 OpenAPI dump 里 201 response 是 `unknown`,前端拿不到自动类型。修法:`responses={201: {"model": JDParseResponse}}`。S7 ProfileParserAgent 同结构同样要写(已加到永久约束 6)。
- **Tailwind v4 主题色**:`@theme { --color-background / --color-foreground / --color-muted / --color-accent / --color-border / --color-input / --color-danger }` —— Tailwind 自动生成对应 utility(`bg-background`、`text-muted`、`border-border` 等),不需要手写 `bg-[var(--color-*)]`(只有 danger 因 reserved word 仍走 arbitrary value)。
- **Form state 设计**:用 `useState<EditableState>`(纯字符串字段,空字符串 vs null 用 helper 转),不是直接持有 `JDStructured`。理由:input 控件天然 string,number/null 转换在 submit 时一次性做,避免 controlled input 的 `''` ↔ `null` 跳变。
- **保存反馈**:`savedAt` 显示 `已保存 HH:MM:SS`,任何字段改动后清掉(避免误以为最新值已保存)。
- **server fetch 错误处理**:`ApiError` 在 server component 里捕获 404 → `notFound()`,其他错继续 throw 走 Next 错误页。

# 期间踩到的小坑

1. **OpenAPI dump 里 `/v1/jds/parse` 的 response 是 `unknown`** —— 因 union 返回 + `response_model=None`。修:`responses={201: {"model": JDParseResponse}}`(永久约束 6)。
2. **enum 字面量赋值报错** —— `JDParseInputSource` 不接 string literal,改 `import { JDParseInputSource } ...; source: JDParseInputSource.text_paste`(永久约束 8)。
3. **biome `noShadowRestrictedNames`** —— 局部 `parseInt` 跟 global parseInt 重名,改名 `toInt`。
4. **typed routes `router.push`** —— `/jds/${id}` 字符串模板被推断为 `string`,不匹配 `Route` 类型,需要 `as Route`(永久约束 7)。
5. **离线 OpenAPI dump 不要走 server 启动** —— `from jobcopilot_api.main import app; get_openapi(routes=app.routes)` 不触发 lifespan(只触发 ASGI startup 时才跑),省去启 docker postgres + uvicorn,直接 dump 给 `openapi-typescript --enum` 吃。脚本:`packages/schemas/scripts/generate.mjs` 已支持 `OPENAPI_FILE` env。

# 不做的(留 S5+)

- SSE 流式调用(同步 200 几秒回够用,SSE 是优化)
- JD 列表页(STATUS 切片表未列,M2 匹配阶段一起做)
- skills / responsibilities 编辑(第一刀只读;评测后看用户真需要再加)
- 真实 LLM dogfood:运行 `pnpm --filter @jobcopilot/web dev` + 后端 uvicorn,粘贴真 JD 测试(消耗 ¥)
