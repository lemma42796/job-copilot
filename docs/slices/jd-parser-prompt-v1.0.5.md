---
title: JDParser prompt v1.0.5 + schema 改造 — 26 类 bug 修复(P0+P1+P2+P3 全做)
owner: lemma42796
date: 2026-05-05
purpose: 落实 [slices/jd-parser-bugs-2026-05.md](jd-parser-bugs-2026-05.md) 调研出的 26 类 bug。M2 待办累积 #1 的 prompt + schema 部分。
status: done(代码已落,但 dataset 扩 + evals 达阈仍 pending,M2 #1 整体未关)
---

# 范围

S18 准备期 dogfood 暴露的 26 类 JDParser bug,按 prompt v1.0.1 → v1.0.5 五版叠加修(中间 v1.0.2 / v1.0.3 / v1.0.4 三版是迭代过程的 ghost,见踩坑区)。同时落 schema 改造与前端预处理。

| 优先级 | 修法层 | 涉及 bug |
|---|---|---|
| **P0** | prompt 强约束(段落归属 / OR / 应届 / 黑名单 / 拒整句) | B1 / B2 / B3 / B6 / B7 / B9 / B10 / B11 / B12 / B13 / B16 |
| **P1** | prompt 强约束(标题清理 / bonus⊥hard / description 一致) | B5 / B14 / B17 |
| **P2** | schema 改造(`JDSkill.or_group_id` / education 中文枚举扩 / source_url+source_publisher / confidence 校准 prompt) | B23 / B24 / B25 / B26 |
| **P3** | 前端粘贴预处理(BOSS 标签剥离) | B4 |

未做:**B25 前端 URL 输入框**(后端字段已就绪,但当前无下游消费方,M5+ 反爬时再加 UI)。

# 产出文件

## 后端
- `apps/api/src/jobcopilot_api/prompts/jd_parser/v1.0.5.j2` — 新版 prompt(8 条任务规则 + 8 节抽取强约束 A-H)
- `apps/api/src/jobcopilot_api/schemas/jds.py` — `JDSkill.or_group_id` / `JDStructured.education` 枚举扩(全中文:专科/本科/硕士/博士/不限/本科及以上/任一档)/ `JDParseInput` 加 source_url+source_publisher / `JDDetail` 加同两字段
- `apps/api/src/jobcopilot_api/models/jd.py` — `Jd.source_url` (Text) + `Jd.source_publisher` (varchar(50))
- `apps/api/alembic/versions/0013_jds_source_url_publisher.py` — DDL ADD COLUMN
- `apps/api/src/jobcopilot_api/services/jd_service.py` — `create_pending_jd` / `create_and_parse` 接受 source_url/publisher 透传
- `apps/api/src/jobcopilot_api/routers/jds.py` — `PROMPT_KEY` v1.0.1 → v1.0.5;`/parse` body 透传 source_url/publisher;`_detail` 出回两字段

## 前端
- `apps/web/src/lib/jd-paste-clean.ts` — `stripBossTagsBlock(raw)` 启发式剥离顶部连续 ≥ 2 行的孤立短 token 块
- `apps/web/src/app/jds/new/page.tsx` — 提交前调用预处理 + 浅色 hint 行展示「已剥离顶部 N 行平台标签」

## 自动生成
- `packages/schemas/src/api.ts` — codegen 重生(覆盖 source_url/publisher/or_group_id/education 三个新中文枚举)

# 设计决策

## 1. prompt v1.0.5 = 5 节强约束 + 8 条任务规则的合并叠加,而非另起一篇

26 类 bug 涉及 8 个完全不同的修法面(段落归属 / OR / 黑名单 / 拒整句 / 标题清理 / 互斥 / description / 学历 enum / confidence 公式),每条单独写一个新版 prompt 太碎。最终结构:

- **任务规则 1-8**:复用 v1.0.1,但规则 7(学历) / 规则 8(confidence)被显著改写
- **新增「抽取强约束」A-H 节**:把 P0/P1 七大类规则按面分节(A 段落归属 / B OR+并列 / C 应届归 0 / D 黑名单 / E 拒整句 / F 标题清理 / G bonus⊥hard / H description 一致)

这种结构 LLM 能逐节遵守,且未来加新约束直接叠下一节,不会与旧规则冲突。

## 2. OR 关系建模选 `JSKill.or_group_id` 而非独立 `OrGroup` 列表

文档建议二选一,选简单的:

- ✅ **`JDSkill.or_group_id: int | None`**:同组共享一个 id(从 1 递增),null = 纯并列
- ❌ `JDStructured.or_groups: list[OrGroup]`:多一层嵌套,匹配 retrieval 读起来要 join

`JSONB` 字段加可选属性,旧数据(or_group_id 不存在)默认 None,完全向后兼容,匹配/简历定制不感知。

OR 组内 `weight` 平均分配(2 项各 0.5,3 项各 0.33),`required` 全 true,语义=组内任一即满足。

## 3. education 枚举值统一中文(P2 第二刀决策)

P2 第一稿用了中英混搭(旧 4 个中文 + 新 3 个英文 `unspecified/bachelor_or_higher/flexible`)。dogfood 发现 LLM 对「本科及以上」原文,优先匹配中文 `本科` 而非英文 `bachelor_or_higher` —— **字面 token 重叠权重压过语义匹配**。

这其实暴露了一个永久教训(见永久约束 #4):**enum 值应与原文同语言**。第二刀全统一为中文:`不限 / 本科及以上 / 任一档`,prompt 规则 7 不需要再加「优先级冲突」单条规则,enum 设计本身就引导 LLM 选对。

## 4. source_url / source_publisher 不进 `JDStructured`,只进 `JDParseInput` + DB 列

LLM 看不到源链接(它只看正文),抽不到也不应该抽。这两字段是用户粘贴时填(或前端识别 URL host 自动填),走 `JDParseInput` 直接落库,绕开 LLM。当前**没有下游消费方**(匹配/简历定制都不读),M5+ 反爬时启用。

## 5. confidence 校准选 prompt 公式,不删字段

文档列了两条修法:① 删字段 ② prompt 公式。删字段会破坏 list/detail/前端展示,且历史 JD 已存了 confidence 数据。改 prompt 公式更轻:

```
起始 0.40
+ 0.15 if title 抽到
+ 0.10 if company 抽到
+ 0.10 if salary 抽到
+ 0.10 if hard_skills ≥ 3
+ 0.10 if responsibilities ≥ 3
+ 0.05 if description 非空
```

字段全齐 ≈ 0.95,严重残缺 0.4-0.6。dogfood 验证:dense 测试 JD(title/salary/hard/desc 齐 + company 空)拿到 **85%**(不再固定 0.95)。

## 6. 前端 BOSS 标签剥离的启发式

孤立 token 块判定:
- 文本最开头(跳空行后)
- 连续 ≥ 2 行,每行 trim 后长度 ≤ 8
- 不含中英标点 / 数字 / 横线(避开 "3-5年")
- 块后必须接空行(BOSS 标签与正文之间有视觉间隔)

满足全部才剥;否则原文返回。剥离后给 hint 行展示给用户,保持透明可见性(避免"前端悄悄改 JD")。

## 7. dogfood 验证(JD #22 dense 测试样本)

故意构造覆盖全部 26 类 bug 的 JD 文本粘进前端,逐项对照:

- ✅ B4 BOSS 标签剥离生效(标题不是 `Java`)
- ✅ B5 标题清理:`AI Agent研发(MJ004075) (26届)` → `AI Agent研发 (26届)`
- ✅ B3 应届 → years_required=0
- ✅ B1+B16+B26 OR 关系:java/python/go(or_group_id=1)+ mysql/postgresql/oracle(or_group_id=2)— DB 字段验证存在
- ✅ B2 段落归属:LoRA/Q-LoRA/TensorRT/MCP/Multi-Agent 全在 bonus
- ✅ B6/B7/B9/B10/B11 黑名单全部生效
- ✅ B12 整句拒入 soft(soft_skills 空)
- ✅ B13 bonus 行为名拒入(无 GitHub/Hackathon)
- ✅ B14 bonus⊥hard(无 fastapi 重复)
- ✅ B17 description 准确抽【职位介绍】段
- ✅ B23 中文枚举 v1.0.5 后期望 `本科及以上`
- ✅ B24 confidence 85%(非默认 0.95)

# 踩坑

## 1. uvicorn `--reload` 写 prompt registry ghost 行

`prompt_versions` 表把首次见到的 prompt 文件 hash 入库,后续不允许同版本号内容变更(版本不可变约定)。但 uvicorn `--reload` 监听文件变化,**改了 prompt 文件 → reload → lifespan 把新内容入库**。后续如果继续编辑同版本号,hash 又对不上,就报 `PromptVersionMismatchError` 启动失败。

实际后果:本次 prompt 经历了 v1.0.2 → v1.0.3 → v1.0.4 → v1.0.5 四次 bump,**v1.0.2/v1.0.3/v1.0.4 都成了 DB 里的 ghost 行**(对应中间编辑的 hash,已无文件)。最后落定 v1.0.5。

**预防**:改 prompt 文件前先停 uvicorn,改完再起。或者改完直接 bump 版本号。这条已升永久约束。

## 2. 中英混搭 enum 引导 LLM 选错(根因比想象深)

P2 第一稿 education 加了三个英文值 `unspecified/bachelor_or_higher/flexible` 与原 4 个中文值并列。dogfood 发现 LLM 对「本科及以上」原文优先选了 `本科` 而非 `bachelor_or_higher`。

我先误以为是 prompt 不够强,加「任职要求段原文优先于元信息行」单条规则(v1.0.3)— 仍然没用。后才意识到根因是**字面 token 匹配权重 > 语义匹配权重**。第二刀全改中文 enum(v1.0.4 → v1.0.5),enum 自身字面就引导 LLM 选对,prompt 不需要叠床架屋。

教训已升永久约束 #5。

## 3. codegen 不需要 API 运行,直接用 `create_app().openapi()` dump

`packages/schemas/scripts/generate.mjs` 默认从 `http://localhost:8000/v1/openapi.json` 拉,但支持 `OPENAPI_FILE` 环境变量。当本地 API 未运行(或不想为 codegen 起服务),用:

```bash
cd apps/api && uv run python -c "import json; from jobcopilot_api.main import create_app; print(json.dumps(create_app().openapi(), ensure_ascii=False))" > /tmp/openapi.json
OPENAPI_FILE=/tmp/openapi.json pnpm gen:api
```

`create_app()` 不走 lifespan,所以不连 DB / 不加载 prompt registry,纯净 dump。后续 prompt 频繁迭代或 schema 调整时复用。

## 4. RAG 在职责段被抽进 hard(边缘行为,不算 bug)

dogfood JD 原文「推动 RAG 检索链路优化」出现在【岗位职责】段,prompt 规则 A 写了「加分项段」的归属约束,但没说「职责段」的术语该不该归 hard。LLM 把 RAG 当 hard 抽。语义上 RAG 确实是技术栈,边缘可接受。如果未来要严抠,prompt A 节可以扩展「只有任职要求段的术语进 hard」规则。

## 5. fastapi/django/flask 共享 or_group_id=3(意外但合理)

dogfood JD 原文「FastAPI / Django / Flask **等** Web 框架使用经验」prompt B 节的 OR 信号词清单(中至少一门 / 任一 / 之一 / 或)未明确包含「斜杠 + 等」组合,但 LLM 把它当 OR 处理了,语义上确实是「任一即可」。可争议但不算错,prompt B 节后续可以把「等」加进信号词清单更明确。

# 不在本文档范围

- M2 待办 #1 剩余:dataset 扩 50 条 + evals 达阈(`hardSkillF1` ≥ 0.80 / `titleExact` ≥ 0.85),需另刀做
- B25 前端 URL 输入框 — M5+ 反爬时落
- 学历的 `bachelor_or_higher` 是否同时在 description 注明门槛档(规则 7 提到了「同理硕士及以上 ... 在 description 注明」)— dogfood 暂未验证此分支
