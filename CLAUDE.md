# JobCopilot 项目指令

## 文件导航

- 项目进度 → `docs/STATUS.md`(**只在用户问进度或续作时读**,不主动读)
- 已完成切片细节 → `docs/slices/`(**只在用户查历史细节时读**)
- 设计文档 → `docs/{1-PRD,2-TECH_DESIGN,3-DATA_MODEL,4-API_SPEC,5-AGENT_DESIGN,6-EVAL_PLAN,7-ROADMAP,8-ENGINEERING}.md`(**只在写对应代码时读相关章节**)
- 架构决策 → `docs/adr/`

## 行为约束

- **等待用户指示再开工**,不要自动起新切片
- 用户问"进度到哪"→ 读 STATUS.md → 简短汇报 → 等指示
- 用户没说改文档,就只改代码
- Bash 输出尽量收紧:`pytest -q`(不是 `-v`)、`git log --oneline -5`、长输出加 `| head` 或 `| wc -l`
- **所有测试 / 自动化验证由用户手动跑**——改完代码后**不要主动**启动 `pytest` / `mypy` / `ruff` / `pnpm typecheck` / `pnpm lint` / `pnpm build` / `playwright` / `puppeteer` / 截图工具 / `curl localhost:*` 抓 HTML 等任何自动化校验。只口头描述期望(URL / 操作步骤 / 期望看到的字段或数字),让用户在浏览器或终端自己验。**例外**:用户明确说"跑闸门"/"跑测试"/"跑 typecheck"等指令时再跑。

## 切片完成更新流程

用户说 "SX 完成了" 时,自动按以下 4 步:

1. **更新 `docs/STATUS.md`**:`last_updated` / 切片表 SX → ✅ / 当前闸门数字 / 切片归档列表加一行
2. **新建 `docs/slices/SX-xxx.md`** 归档卡:产出文件清单 + 设计决策(实现细节)+ 期间踩到的小坑;模板参考 `slices/S2-llm-client.md`
3. **跨切片永久约束**(影响后续切片设计):加到 STATUS.md "永久约束累积" 区,每条标 `[来自 SX]`;否则只放归档卡
4. 问用户是否 commit & push

## 里程碑收官流程

某里程碑最后一个切片完成时(如 S11 = M1 末),多做一步:

1. 写 `docs/slices/MX-summary.md`:整体经验 + 内部约束归档
2. STATUS.md 把该 MX 的切片表 / 永久约束 / 归档列表**全折叠成一行链接**:`M1 完成 → [slices/M1-summary.md]`
3. 开下一个 MX 的切片表

目的:STATUS.md 始终是"**当前里程碑视图**",不跨里程碑累积,长期稳定 ~150-200 行。

## 风格规矩

- **必须用大白话回答**:解释概念 / 设计 / 问题排查时,先把核心结论用一两句日常语言讲清楚,再展开技术细节。避免上来就堆术语 / schema / 缩写。读者每多走一步抽象就少一个能跟上的人。
- **中文为主**,代码示例与 schema 标识符为英文
- **不估工时**(不写小时数 / 天数 / Week 汇总)
- **不加 Co-Author**(git commit / PR body 一律省略 `Co-Authored-By: Claude` 与 "Generated with Claude Code" 注脚)
- 文档元数据头格式见已完成文档,严格遵循
- 每份文档末尾写"不在本文档范围"指向相关文档
- 切片规划写在 STATUS.md 的"下一刀"区(不再每切片一份 ADR);只有真正跨切片的架构决策才另立 ADR(下一个编号 0007)
- 不要重新讨论已锁定的决策(见 STATUS.md "已经锁定的关键决策"表)
