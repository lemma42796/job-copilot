# JobCopilot 项目指令

## 文件导航

- 项目进度 → `docs/STATUS.md`(**只在用户问进度或续作时读**,不主动读)
- 产品需求 → `docs/1-PRD.md`(**只在写产品/UI 代码时读**)
- 技术设计 → `docs/2-TECH_DESIGN.md`
- 数据模型 → `docs/3-DATA_MODEL.md`
- API 规范 → `docs/4-API_SPEC.md`
- Agent 设计 → `docs/5-AGENT_DESIGN.md`
- 评测计划 → `docs/6-EVAL_PLAN.md`
- 路线图 → `docs/7-ROADMAP.md`
- 工程规范 → `docs/8-ENGINEERING.md`
- 工程踩坑录 → `docs/9-LESSONS.md`(v1 沉淀,持续追加)

## 行为约束

- **等待用户指示再开工**,不要自动起新切片
- 用户问"进度到哪"→ 读 STATUS.md → 简短汇报 → 等指示
- 用户没说改文档,就只改代码
- Bash 输出尽量收紧:`pytest -q`(不是 `-v`)、`git log --oneline -5`、长输出加 `| head` 或 `| wc -l`
- **所有测试 / 自动化验证由用户手动跑**——改完代码后**不要主动**启动 `pytest` / `mypy` / `ruff` / `pnpm typecheck` / `pnpm lint` / `pnpm build` / `playwright` / `puppeteer` / 截图工具 / `curl localhost:*` 抓 HTML 等任何自动化校验。只口头描述期望(URL / 操作步骤 / 期望看到的字段或数字),让用户在浏览器或终端自己验。**例外**:用户明确说"跑闸门"/"跑测试"/"跑 typecheck"等指令时再跑。

## 里程碑完成更新流程

用户说 "MX 完成了" 时,自动按以下步:

1. **更新 `docs/STATUS.md`**:`last_updated` / 里程碑表 MX → ✅ / 当前 working tree / 下一个 MX 子任务展开
2. **跨里程碑永久约束**(影响后续 MX 设计):加到 STATUS.md "永久约束累积" 区,每条标 `[来自 MX]`
3. 问用户是否 commit & push + 是否打 tag(`v0.X-MX-end`)

## 风格规矩

- **中文为主**,代码示例与 schema 标识符为英文
- **不估工时**(不写小时数 / 天数 / Week 汇总)
- **不加 Co-Author**(git commit / PR body 一律省略 `Co-Authored-By: Codex` 与 "Generated with Codex" 注脚)
- 文档元数据头格式见已完成文档,严格遵循
- 每份文档末尾写"不在本文档范围"指向相关文档
- 路线图写在 `docs/7-ROADMAP.md`(MX 粒度);只有真正跨里程碑的架构决策才另立 ADR(下一个编号 0007,v2 起)
- 不要重新讨论已锁定的决策(见 STATUS.md "已经锁定的关键决策"表)
