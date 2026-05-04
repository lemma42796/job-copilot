---
title: S12 JD 列表页 + 全局导航 + UX 顺手刀 — 切片归档
status: ✅ 完成已 push
date: 2026-05-04
purpose: M2 第一刀 — 兑现 S5 起"列表延后",为后续匹配场景"选哪份 JD"做基础;同时收口 M1 dogfood 暴露的两条 UX 小修(parse_failed 一键删 / 草稿暂存)
---

# 产出

```
apps/web/src/
├── lib/
│   ├── format.ts              # formatSalary(min,max,currency,months) + formatRelative(iso)
│   └── use-session-draft.ts   # sessionStorage hook(key + 1h TTL,提交后 clear)
├── components/list/
│   ├── jd-card.tsx            # JD 列表卡片(单列,hover 出删除)
│   └── profile-card.tsx       # 简历列表卡片(同结构,字段不同)
└── app/
    ├── jds/
    │   ├── page.tsx           # SSR 拿第一页 → JdsClient
    │   └── jds-client.tsx     # cursor 加载更多 + 行内删除
    └── profiles/
        ├── page.tsx
        └── profiles-client.tsx
```

改动:
- `lib/api.ts` — 加 `listJds / deleteJd / listProfiles` + 4 个类型 alias(`JDListItem` / `JDListResponse` / `ProfileListItem` / `ProfileListResponse`)
- `components/shell/sidebar.tsx` — 加"全部 JD / 全部简历"+ ListIcon + active 匹配重写(`exact / prefix` + 兄弟项让位)
- `app/jds/[id]/page.tsx` + `app/profiles/[id]/page.tsx` — "返回首页"改"返回列表"对齐 Sidebar 选中态
- `app/jds/[id]/jd-edit-form.tsx` + `app/profiles/[id]/profile-edit-form.tsx` — `parse_failed` 时顶部红条幅 + "删除并重传"按钮(profile 端复用现有删除流,加可选 redirect 参数)
- `app/jds/new/page.tsx` + `app/profiles/new/page.tsx` — `useSessionDraft` 接入,提交成功后 `clearDraft()`;"返回首页"改"返回列表"

后端 **0 改动 / 0 迁移**(`GET /jds` `GET /profiles` `DELETE` 全部 M1 已就位)。

# 设计决策(实现细节)

- **列表页骨架 = SSR 第一页 + CSR 加载更多**:`page.tsx` async server component 调 `listJds({ limit: 20 })` 注水;`xx-client.tsx` 拿 `initialItems / initialCursor` 接管。首屏快、翻页可控、不抢滚动。后续 matches / 投递列表复用此模板(见永久约束)。
- **卡片点击区域 = Link absolute overlay + 内容 `pointer-events-none` + 删除按钮 `pointer-events-auto`**:避开 button-in-a 的 W3C invalid 嵌套(biome `useValidAnchor`),同时保证整张卡片可点。代价:卡片文字不可选(链接卡片本就不该让用户选文字)。
- **删除交互 = hover 出按钮 + native `window.confirm` + 本地 splice 不重 fetch**:不引入 dialog 组件,删除后不抖动第一页。
- **Sidebar active = exact / prefix + 兄弟让位**:`isActive(item, pathname, siblings)`。`exact` 项严格相等;`prefix` 项除非有兄弟项是更长前缀(`/jds/new` 是 `/jds` 的子路径,所以 `/jds/new` 时 `/jds` 让位)。
- **草稿暂存 = sessionStorage + 1h TTL + 提交后 clear**:写入 `{v: string, t: timestamp}`,读取时 `Date.now() - t < ttlMs` 校验过期;session 关闭自动清,无服务端持久化。`/jds/new` 用 key `jobcopilot.draft.jd.text`,`/profiles/new` 同模式。
- **parse_failed UX = 顶部独立红条幅 + "删除并重传"按钮**:不嵌入编辑表单(异常态与正常编辑流分开)。按钮直接 `confirm` → `DELETE` → `router.push('/<list>/new')`。Profile 端 `onDelete(redirect)` 加可选参数:默认 `/`,parse_failed 路径传 `/profiles/new`。
- **"返回 X 列表"取代"返回首页"**:详情页 / 新建页面包屑链接对齐 Sidebar 选中态。
- **薪资单位 = 元**:DB 存原始数(`salary_min == 30000`),`formatSalary` / 1000 显示 "k";整千 → `15-30k`,非整千 → 一位小数 `15.5k`;currency != 'CNY' 才前置代码,默认隐藏。
- **相对时间自实现**:不引入 `date-fns` / `dayjs`;30 行内分段(刚刚 / N 分钟前 / N 小时前 / N 天前 / `MM-DD` / `YYYY-MM-DD`)。

# 期间踩到的小坑

1. **卡片整体点击只有窄边能点**:原结构 `<Card><Link absolute inset-0 /><div relative>...</div></Card>`,内容 div 的 `relative` 把 Link 遮住,只有 padding 边缘没被内容覆盖处可点。修复:内容 div 加 `pointer-events-none`,删除按钮加 `pointer-events-auto` 拉回事件。
2. **Sidebar 在 `/jds/new` 时"全部 JD" 也高亮**:原 active 匹配 `pathname === item.href || pathname.startsWith(${item.href}/)` 让 `/jds/new` 也命中 `/jds` 前缀。重写 `isActive` 加 sibling 让位逻辑。
3. **`onDelete` 加 redirect 参数后,`onClick={onDelete}` 把 MouseEvent 当 redirect 传入**:必须改成 `onClick={() => onDelete()}`。原代码无参函数直接传引用是 OK 的,加签名后必须包一层。
4. **造 `parse_failed` 测试数据无法走 API**:`POST /jds/parse` 失败时 HTTP 层 raise 422/502 不留行(ADR-0006 D4);`PATCH /jds/{id}` schema 里 `status` 只允许 `parsing | parsed`,不能写 `parse_failed`。只能 `docker exec ... psql -c "UPDATE jds SET status='parse_failed' WHERE id=X;"` 直改 DB,测完恢复。
5. **`router.push(redirect)` typedRoutes 类型**:redirect 必须是 Route literal union(`'/' | '/profiles/new'`),不能是 plain `string`。两个都是有效路由字面量,TS 推断接受。

# 闸门

- 后端:0 改动,M1 数字不动(`pytest -q` 321 passed,alembic → 0010)
- 前端:typecheck / lint / build 用户未本地跑(切片末端选择跳过自动化校验,改完 commit 直推)
- 浏览器手测覆盖:列表卡片 / 跳详情 / 删除 / 草稿暂存 / parse_failed 一键删 / 返回链接 — 全部 ✅

> 加载更多按钮 / 空态 CTA:数据 < 20 条 + 已有数据,无法触发,留待数据增长后顺测。
