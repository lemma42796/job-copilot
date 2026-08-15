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
