# JobCopilot 评测规范

本目录保存评测数据、组件说明和历史报告。评测实现以 `apps/api/scripts/eval_*.py` 为准,本文只定义共同规则。

## 当前存在的评测

| 组件 | 数据集 | 组件文档 | 脚本 | 当前证据边界 |
|------|--------|----------|------|--------------|
| Hybrid Search | `suites/hybrid_search/dataset.note_smoke.jsonl` | `suites/hybrid_search/EVAL.md` | `eval_hybrid_search_note_smoke.py` | 12 条固定 smoke,不是任意 query 泛化证明 |
| Interview Coach | `suites/interview_coach/dataset.flow_smoke.jsonl` | `suites/interview_coach/EVAL.md` | `eval_interview_coach.py` | 离线 stubbed Judge,只验证 harness 行为 |
| JD Coverage | `suites/jd_coverage/dataset.jsonl` | `suites/jd_coverage/EVAL.md` | `eval_jd_coverage.py` | 10 条 analysis#6 人工标签 |

当前没有 `quiz_generator`、`answer_judge` 或 `jd_aggregator` 的独立 suite 目录。旧设计文档中的计划不算现有评测能力。

## 目录约定

```text
evals/
├── EVAL_GUIDE.md
├── suites/
│   └── <component>/
│       ├── EVAL.md
│       ├── dataset*.jsonl
│       └── notes_fixture/        # 仅需要固定语料时存在
└── reports/                      # 历史结果;新产物默认被 gitignore
```

- 一行一个 JSON,UTF-8 无 BOM。
- 每条 fixture 使用稳定 `id`,并记录来源和必要说明。
- 新 bug 只有在根因确认后才加入对应 dataset;已成为正式防回归样本的 fixture 不随意删除。
- dataset 字段由对应脚本和组件 `EVAL.md` 共同定义,脚本是运行事实。
- 报告必须注明数据集、运行模式、关键配置和生成时间;LLM / rerank 路径还要报告 tokens、成本或 cache 策略。

## suite 文档与运行报告的分工

- `suites/<component>/EVAL.md`:一个组件长期一份,写方法、dataset schema、指标定义、目标阈值和证据边界。稳定内容,不写某次运行的数字。
- `reports/<name>-<timestamp>.md`:每次评测或实验新建一份,写该次运行的完整证据。默认被 gitignore;有结论价值、被 `EVAL.md` 引用的报告用 `git add -f` 入库,其余留在本地。
- 需要评测的新方向(如后端并发压测)先建 `suites/<component>/EVAL.md`,结果照样落 `reports/`。
- `*.trace.jsonl` 等大体积原始产物一律不入库,只留在本机;`EVAL.md` 引用时标明未入库。

## 运行与缓存边界

- 所有评测都由用户明确指令后手动运行;AI 助手不主动执行。
- 评测默认测行为和质量,不是测冷启动延迟;允许使用已有 response cache。
- Hybrid Search smoke 默认 query embedding cache-only,cache miss 必须显式暴露,不能静默请求 provider。
- 涉及真实工具调用的评测不能为了方便关闭工具后仍宣称代表产品主路;需要关工具时必须标成 baseline。
- Langfuse / SDK 资源由 CLI 显式管理;无 key 时不要仅因 noop 模式就假设没有后台资源。

## 如何解释结果

- 固定 smoke 通过只说明这些 fixture 的已标注路径没有回归。
- 指标低于目标阈值时要保留失败层和 per-case 证据,不能只报总平均。
- 小样本结果不能外推成任意 query、任意知识库、线上并发或生产可靠性结论。
- 文档中的“最近结果”是带日期的历史快照;当前结果必须由新报告证明。

## 不在本文档范围

- 项目最新状态 → `../docs/STATUS.md`
- 当前与未完成任务 → `../docs/TASKS.md`
- 技术架构 → `../docs/TECH_DESIGN.md`
