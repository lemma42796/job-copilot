# JobCopilot 项目指令

## 文件导航

- 项目最新状态 → `docs/STATUS.md`(**只在用户问进度或续作时读**,不主动读)
- 当前与未完成任务 → `docs/TASKS.md`
- 技术与架构设计 → `docs/TECH_DESIGN.md`
- 评测通用规范 → `evals/EVAL_GUIDE.md`
- 组件评测 → `evals/suites/<component>/EVAL.md`

## 行为约束

- **等待用户指示再开工**,不要自动起新切片
- 用户问"进度到哪"→ 读 STATUS.md;若同时问下一步再读 TASKS.md → 简短汇报 → 等指示
- 用户没说改文档,就只改代码
- Bash 输出尽量收紧:`pytest -q`(不是 `-v`)、`git log --oneline -5`、长输出加 `| head` 或 `| wc -l`
- **所有测试 / 自动化验证由用户手动跑**——改完代码后**不要主动**启动 `pytest` / `mypy` / `ruff` / `pnpm typecheck` / `pnpm lint` / `pnpm build` / `playwright` / `puppeteer` / 截图工具 / `curl localhost:*` 抓 HTML 等任何自动化校验。只口头描述期望(URL / 操作步骤 / 期望看到的字段或数字),让用户在浏览器或终端自己验。**例外**:用户明确说"跑闸门"/"跑测试"/"跑 typecheck"等指令时再跑。

## 文档职责

**目的**:这些文档会被自动加载或按导航读入模型上下文,每一行都占 token。文档一旦变成流水账——追加历史、复制别处已有的事实、堆积过期快照——上下文里就塞满了无效信息,稀释模型对当前任务的注意力,既费钱又降低准确率。因此每份文档只保留最新状态,职责单一,不重复。

| 文档 | 只写 | 不写 |
|------|------|------|
| `docs/STATUS.md` | 此刻为真的事实、当前分支、当前边界 | 历史条目、评测指标数字 |
| `docs/TASKS.md` | 未做 / 在做 / 计划做 | 已完成项、工时估算 |
| `docs/TECH_DESIGN.md` | 稳定架构、跨里程碑约束、决策理由与否决项 | 实现细节、逐步调用流程 |
| `evals/EVAL_GUIDE.md` | 评测共同规范与 suite 索引 | 具体运行结果 |
| `evals/suites/*/EVAL.md` | 方法、dataset schema、指标、阈值、证据边界 | 某次运行的数字 |
| `evals/reports/*.md` | 单次运行的完整证据 | — |

- **只保留最新状态**:更新时先删掉被取代的旧内容,再写新内容,不得追加。
- 任何文档不得设「变更历史 / 更新日志」章节,历史由 git commit / tag 承担。
- **一个事实只允许存在于一处**,其他位置只放指针。
- 代码 / migration / OpenAPI 是事实来源,文档不复制它们能表达的内容。
- 每次评测或实验新建一份 `evals/reports/<name>-<timestamp>.md`;有结论价值的用 `git add -f` 入库,其余保持 gitignore。
- 保持每份文档单一职责;发现内容超出本表职责,移到对应文档或删除,而不是让文档膨胀。
- `README.md` 与子包 README 面向人类读者,不进 agent 导航;**不写任何项目进展**(里程碑、状态、路线图),进展只在 `docs/STATUS.md`。

## 里程碑完成更新流程

用户说 "MX 完成了" 时,自动按以下步:

1. **更新 `docs/STATUS.md`**:只记录 MX 已实现 / 已验证的最新事实与当前 working tree
2. **更新 `docs/TASKS.md`**:移除已完成任务,只保留当前和仍计划执行的任务
3. **跨里程碑永久约束**(影响后续设计):写入 `docs/TECH_DESIGN.md`;真正难以撤销的架构决策才另立 ADR
4. 问用户是否 commit & push + 是否打 tag(`v0.X-MX-end`)

## 风格规矩

- **中文为主**,代码示例与 schema 标识符为英文
- **不估工时**(不写小时数 / 天数 / Week 汇总)
- **不加 Co-Author**(git commit / PR body 一律省略任何 `Co-Authored-By` 与 "Generated with ..." AI 工具注脚)
- 文档元数据头格式见已完成文档,严格遵循
- 主文档保持单一职责:`STATUS` 只写现状,`TASKS` 只写未完成任务,`TECH_DESIGN` 写稳定技术与架构
- 每个组件的评测说明写在 `evals/suites/<component>/EVAL.md`,不要恢复单份巨型总评测文档
- 只有真正跨里程碑且难以撤销的架构决策才另立 ADR(下一个编号 0007,v2 起)
- 不要重新讨论 `TECH_DESIGN.md` 已锁定且仍有效的架构边界;若实现已改变,先以代码 / migration / OpenAPI 为准核对后再修订文档
