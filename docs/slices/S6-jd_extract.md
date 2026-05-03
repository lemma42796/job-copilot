---
title: S6 evals/suites/jd_extract MVP + JDParser prompt v1.0.1 + 全栈 salary_months — 切片归档
status: ✅ 完成
date: 2026-05-03
purpose: M1 数据入口的评测基线;EVAL_PLAN §3 promptfoo 链路 + ADR-0006 prompt promote
---

# 产出

```
evals/                                    # 新 workspace
├── .gitignore                            # raw/ + reports/ + tmp/ + .promptfoo/
├── package.json                          # promptfoo@0.121.9 + openai@4.77 + tsx@4.19
├── scripts/
│   ├── from-screenshot.ts                # 截图 → JSONL 候选(VL OCR + Label,均走 qwen3.6-flash)
│   └── synth-seed.ts                     # 合成种子(S6 完成时已被 13 条真实 boss 替换)
├── suites/jd_extract/
│   ├── promptfooconfig.yaml              # provider=qwen3.6-flash + 4 断言 + passthrough.enable_thinking=false
│   ├── prompt.ts                         # 加载 v1.0.1.j2,parseTemplate + jd_text 渲染 + 拼 schema
│   ├── jd_structured.schema.json         # 含 salary_months 字段
│   ├── assertions.ts                     # titleExact / hardSkillF1 / salaryMatch / salaryMonthsAcc
│   └── dataset.jsonl                     # 13 条真实 boss(jd_extract_001_boss ~ 013_boss)
└── raw/boss/                             # 13 张截图(.gitignore,版权敏感)

.github/workflows/eval.yml                # 新建,workflow_dispatch only(M2 启用 push/PR + 配 secret)

apps/api/
├── alembic/versions/0008_jds_salary_months.py    # ALTER TABLE jds ADD COLUMN salary_months SMALLINT NULL
├── src/jobcopilot_api/
│   ├── prompts/jd_parser/v1.0.1.j2       # 新版本(v1.0.0 保留 history)
│   ├── models/jd.py                      # Jd ORM 加 salary_months: SmallInteger
│   ├── schemas/jds.py                    # JDStructured + JDDetail 加字段
│   ├── services/jd_service.py            # _apply_structured 映射
│   └── routers/jds.py                    # PROMPT_KEY → v1.0.1 + reconstruct/detail 加字段
└── tests/integration/test_jds_router.py  # fixture v1.0.0 → v1.0.1

apps/web/src/app/jds/[id]/jd-edit-form.tsx    # grid-cols-2 → cols-3,加"X 薪"输入框

packages/schemas/src/api.ts                   # 自动 regen(salary_months 渗透到所有 JD 类型)
```

# 设计决策(实现细节)

- **全栈 vs eval-only 加 salary_months**:用户选全栈(eval schema + 生产 pydantic + DB migration + prompt v1.0.1 + 前端 + 评测断言全部同步)。理由:ground truth 完整性 vs future TODO,用户偏好"要做就做完",拒绝挂技术债。
- **Qwen3.6 系列原生多模态**:`from-screenshot.ts` 两阶段(VL OCR + Label)都用 `qwen3.6-flash` 一个模型。Qwen3 时代的独立 `qwen3-vl-flash` 在 Qwen3.6 已合并到主模型,不再需要独立 VL 档。
- **JDParser prompt v1.0.0 → v1.0.1 promote 流程**:
  1. 写 `prompts/jd_parser/v1.0.1.j2`(SYSTEM/USER 双段)
  2. 改 `routers/jds.py:PROMPT_KEY` = `("jd_parser", "v1.0.1")`
  3. 同步 `tests/integration/test_jds_router.py` 的 fixture 版本号(STATUS 永久约束 §2)
  4. 启动 lifespan 自动 upsert v1.0.1 到 prompt_versions 表;v1.0.0 保留(history)
- **v1.0.1 加的两条规则**:`·14 薪 / 16 薪` → `salary_months=14/16`;在校 / 应届 / 实习 → `job_level=junior`(`intern` 仅明确"实习生"岗位)。"hard_skills 不抽厂商名/概念名"留给 v1.0.2(M2)。
- **评测断言 `salaryMonthsAcc` conditional 策略**:`want=null` 给 score=1(中性,代价是分母被 null 拉高,数值偏乐观);`want!=null` 严比。MVP 不设硬阈值,只观察。M2 改自定义聚合只算"有标"样本的精确 acc。
- **promptfoo `enable_thinking: false` 走 passthrough**:qwen3.6-flash 默认开深度思考,DashScope OpenAI 兼容模式把 reasoning 拼进 content,加 max_tokens=2048 不够 thinking + JSON 总输出 → schema_invalid。生产 JDParser 走 CHEAP tier(thinking_mode=False),评测对齐。`promptfoo` openai provider 不识别字段不透传,必须放 `config.passthrough` 下。
- **eval.yml 触发器**:S6 阶段只 `workflow_dispatch`(手动从 GitHub UI 触发);push/PR trigger 注释保留,M2 数据集扩到 50 条 + Δ ≤ -2pp 比对脚本就位时取消注释 + 配 secret 即恢复。
- **dataset 13 条而非 STATUS 原计划 15 条**:用户截图 13 张,不补满 15 条。M2 直接扩到 50 条,无需 +2 中转。
- **离线 OpenAPI dump**:`uv run python -c "from jobcopilot_api.main import create_app; print(json.dumps(create_app().openapi()))"` 直出 OpenAPI 给 `openapi-typescript` 吃,不用启 uvicorn(沿用 S5 流程)。
- **dataset 写入策略**:用 `> dataset.jsonl` 重置后 `for n in 01..13; do jq -c '.' tmp/edit/${n}.json >> dataset.jsonl`,保证 13 条干净 JSONL,不残留 2 条合成种子。

# Baseline(13 条 boss,qwen3.6-flash + v1.0.1)

| Metric | Mean | Pass / Total | 阈值 | 状态 |
|---|---|---|---|---|
| titleExact | 0.769 | 10/13 | 0.92 | ❌ — title 拼了"在校/应届"等行尾 metadata 或 OCR 截断 |
| hardSkillF1 | 0.67 | 3/13 | 0.85 | ❌ — STATUS 已记的"prompt 抽厂商名/概念名" |
| salaryMatch | 1.00 | 13/13 | 0.85 | ✅ |
| salaryMonthsAcc | 1.00 | 13/13 | 观察 | ✅(want=null 给 1 分含水分) |
| **case-level pass** | | **2/13 (15.38%)** | | — |

S6 DoD = "基线能跑通"(0 errors / promptfoo 链路 OK / 4 metric 真实数字),已达成。Metric 阈值不达留 M2。

# 期间踩到的小坑

1. **模型 ID 错误连环爆**:`from-screenshot.ts` 早期默认 `qwen-vl-max-latest` / `qwen-plus`(通用名,不在项目锁定);文档里 `qwen3.6-vl-flash` 是规划名,百炼实际清单不存在(实际是 `qwen3-vl-flash`,且 Qwen3.6 已合并 VL);`promptfooconfig.yaml` 写的 `qwen-flash` 也不在清单。Prep 重跑 4 次,49 张 LLM 调用,浪费 36 张 ~¥1.4。**教训记进 memory `feedback_llm_batch_dry_run.md`**(批量 LLM 调用前 dry-run + 校验三件事)。
2. **Schema drift**:LABEL 阶段偶尔输出 `hard_skills: ["langchain"]`(字符串数组)而非 `[{name: "langchain"}]`,需要后处理 normalize:`jq '.vars.expected.hard_skills |= map(if type=="string" then {name:.} else . end)'`。13 条里命中 1 次。
3. **promptfoo 默认走 cache**:debug 时改了 config 重跑,结果 `(cached) 0s` 没真发请求。加 `--no-cache` 才会真跑。
4. **promptfoo OpenAI provider 不透传不识别字段**:直接 `config.enable_thinking: false` 不发出去,必须 `config.passthrough.enable_thinking: false`。
5. **`pnpm run xxx > out.jsonl` 头几行 banner**:pnpm 自带 `> @scope/pkg@... run`/banner 写到 stdout,污染 JSONL 输出。`pnpm --silent run xxx` 才干净。
6. **zsh 数组从 1 起步**:bash `${arr[0]}` 第一个,zsh `${arr[1]}` 第一个。生成 review.md 时初值 i=0 全部偏一格;改 i=1 起 / `${srcs[$idx]}` zsh 风格。
7. **alembic 0008 应用**:`alembic current` 显示 dev DB 在 0005(STATUS 写"未 down"),`alembic upgrade head` 一气推到 0008(顺带跑了 0006/0007/0008)。

# 永久约束累积(影响后续切片)

- **添加 JDStructured 字段的全栈协同清单**(11 处):eval schema + JDStructured pydantic + JDDetail + JDListItem(如选择透出)+ Jd ORM + alembic migration + JDParser prompt vX.Y.Z + service `_apply_structured` + router `_structured_from_jd` / `_detail` + 前端 form / detail UI + 评测 assertions / promptfooconfig — 漏一处启动报错或 baseline 数据丢失。S7 ProfileStructured 加字段沿用此清单。
- **JDParser prompt 升级 promote 4 步**:① 写 `prompts/jd_parser/vX.Y.Z.j2`(SYSTEM/USER 双段);② `routers/jds.py:PROMPT_KEY` 改新版本;③ `tests/integration/test_jds_router.py` fixture 版本号同步;④ 启动 lifespan 自动 upsert,旧版本保留 history。S7 ProfileParser prompt 升级同流程。
- **DashScope 评测 provider 必须显式关 thinking**:promptfooconfig 加 `config.passthrough.enable_thinking: false`,否则 qwen3.6-flash 默认深度思考拼 reasoning 进 content + 截 max_tokens → schema_invalid。S10 profile_extract suite 同样加。

# 不做的(留 M2)

- dataset 扩到 50 条(剩 37 条:OCR 7 / 邮件 8 / 极短 3 / 薪资模糊 2 / 标准中文 17)
- 4 个新 metric:`level_acc` / `confidence_calibration` / `latency_p95` / `cost_per_call_cny`
- bad case 表 + promote 脚本 + 月度 triage(EVAL_PLAN §12)
- 跑 3 次取中位数(EVAL_PLAN §11.3)
- 不退化策略:Δ ≤ -2pp 比对 main baseline
- PR comment 脚本
- `salaryMonthsAcc` 改自定义聚合(只算 want!=null 样本的精确 acc,去掉 null 拉高分母的水分)
- `eval.yml` 取消 workflow_dispatch only,恢复 push/PR trigger + 配 GitHub Secret `DASHSCOPE_API_KEY_EVAL`
- 生产 LLMClient 加 `max_tokens` 参数(评测侧已在 promptfooconfig 用 2048 绕过)
- JDParser prompt v1.0.2:加"hard_skills 不抽厂商名/概念名"规则修 hardSkillF1=0.67;加"title 抽到第一行末"规则修 titleExact=0.769
