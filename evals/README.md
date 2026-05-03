# JobCopilot Evals

Prompt 回归评测。每个 Agent 一套 suite,在 PR 跑闸门防退化。当前 MVP 只有 `jd_extract`(M1 S6)。

## 一句话用法

```bash
# 一次性
cp .env.example .env  # 填 DASHSCOPE_API_KEY_EVAL
pnpm install

# 跑评测
pnpm --filter @jobcopilot/evals run eval:jd
```

## 目录

```
evals/
├── suites/jd_extract/          # JD 抽取 suite(M1 S6 MVP)
│   ├── promptfooconfig.yaml    # 入口
│   ├── prompt.ts               # 加载 v1.0.0.j2 + 拆 SYSTEM/USER + 渲染
│   ├── assertions.ts           # title_exact / hard_skill_f1 / salary_match
│   └── dataset.jsonl           # 15 条样本(含 ground truth)
├── scripts/
│   ├── from-screenshot.ts      # 截图 → 文本 + ground truth 候选(qwen3.6-flash 两阶段)
│   └── synth-seed.ts           # 纯 LLM 合成 1-2 条临时种子(管道开发期用)
├── raw/                        # ★ gitignore — 你本地的截图
└── reports/                    # ★ gitignore — 跑评测的 artifact
```

## 数据脱敏(public repo 强约束)

`dataset.jsonl` 入仓库,所以**任何用户上传的真实 JD 必须脱敏**才能进:

| 字段 | 处理 |
|------|------|
| 公司名 | 一律替换 `[CompanyA]` / `[CompanyB]` / ...,JD 文本与 ground truth 同步 |
| HR 邮箱 / 手机号 / 微信号 | 一律 mask 成 `[email]` / `[phone]` |
| 真实人名(招聘负责人) | 替换成 `[Recruiter]` |
| 截图原图 | 永远不入仓库,只放 `evals/raw/` 本地 |

公司名脱敏导致 `company_exact` 指标失去意义,所以 MVP 不做 `company` 字段评测,只评 `title` / `hard_skills` / `salary`。

## 添加样本流程(15 条目标)

1. 截图 Boss 上的 JD(或复制邮件 / LinkedIn 文本)放到 `evals/raw/`
2. 跑 `pnpm --filter @jobcopilot/evals run prep:screenshot evals/raw/<file>.png`
3. 脚本输出一行候选 jsonl(`qwen3.6-flash` 两阶段:OCR 转的文本 + 标注 prompt 抽的 ground truth)
4. **人工核对**这一行(改公司名占位、改错的 ground truth 字段),确认后追加到 `suites/jd_extract/dataset.jsonl`
5. 本地跑 `pnpm run eval:jd` 验证仍通过

## 指标(MVP 3 个)

| 指标 | 阈值 | 文件 |
|------|------|------|
| `title_exact` | ≥ 0.92 | `assertions.ts:titleExact` |
| `hard_skill_f1` | ≥ 0.85 | `assertions.ts:hardSkillF1` |
| `salary_match` | ≥ 0.85 | `assertions.ts:salaryMatch` |

阈值取自 `docs/6-EVAL_PLAN.md` §3.2 "初始" 列。GA 阈值 / 其他 5 个指标(level_acc / confidence / latency / cost / company)推到 M2。

## 接口

走 DashScope OpenAI 兼容模式调 `qwen3.6-flash`(评测目标模型,Tier=CHEAP):

```yaml
provider: openai:chat:qwen3.6-flash
apiBaseUrl: https://dashscope.aliyuncs.com/compatible-mode/v1
temperature: 0
seed: 42
response_format: { type: json_object }
```

不走 `apps/api` 的 `LLMClient`,所以评测的是**纯 prompt 表现**,不会被 retry / cache_system / 重试逻辑污染。详见 `docs/6-EVAL_PLAN.md` §10.1。

## CI

`.github/workflows/eval.yml` 在以下路径变更时触发:
- `apps/api/src/jobcopilot_api/agents/**`
- `apps/api/src/jobcopilot_api/llm/**`
- `apps/api/src/jobcopilot_api/prompts/**`
- `evals/**`

需要在仓库 Settings → Secrets 加 `DASHSCOPE_API_KEY_EVAL`(与生产 Key 分开,见 `docs/6-EVAL_PLAN.md` §10.5)。
