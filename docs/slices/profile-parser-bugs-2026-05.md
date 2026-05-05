---
title: ProfileParser dogfood + prompt v1.0.1 + 前端 UI 漏渲染补 — 6 个 bug 全修
owner: lemma42796
date: 2026-05-05
status: ✅ 完成
purpose: 自造一份覆盖 12 类边界的测试简历 PDF,dogfood 简历解析端到端,发现 6 个 bug 并一把全修;同时补一份可重复生成的 fixture 生成器
---

# 背景

S18 主线开动前顺手做的一轮 ProfileParser dogfood。流程:

1. 写 `apps/api/scripts/gen_test_resume.py`(reportlab + STHeiti,uv 临时拉,不污染依赖),刻意把 12 类边界压进同一份 PDF
2. 上传到前端 `/profiles/new` 走端到端 SSE 解析
3. 比对原 PDF / API 返回 / 前端渲染三处,定位是 prompt bug、UI bug 还是可接受设计

PDF 落 `fixtures/resumes/test-resume-bugs.pdf`(`fixtures/` 已 gitignore),后续可一键再生。

发现 6 个 bug + 5 个可接受设计选择。**全部一轮内修完**(重新生成 fixture → 重新 dogfood → 验证全过)。

---

# 产出

```
apps/api/scripts/
└── gen_test_resume.py                # 新 — reportlab 临时依赖,12 类边界单文件覆盖

apps/api/src/jobcopilot_api/prompts/profile_parser/
├── v1.0.0.j2                         # 保留(prompt_versions 表已 hash,不可变)
└── v1.0.1.j2                         # 新 — B1/B2/B3 三规则改造

apps/api/src/jobcopilot_api/routers/
└── profiles.py                       # PROMPT_KEY: ("profile_parser", "v1.0.0") → "v1.0.1"

apps/web/src/app/profiles/[id]/
└── profile-edit-form.tsx             # F1/F2/F3 三处补渲染

docs/STATUS.md                        # last_updated + #11 标 v1.0.1 已落 + 文档清单加本卡
docs/slices/profile-parser-bugs-2026-05.md  # 本卡
```

---

# Bug 清单(按修复位置分组)

## 🐞 后端 prompt(profile_parser v1.0.0 → v1.0.1)

### B1 — `description` 幻觉:从 `bullets[0]` 改写复述
- **症状**:6/6 段 experience+project 的 `description` 字段都是 `bullets[0]` 的"复制+轻改写"产物
- **特征**:`description` 用**全角逗号**,`bullets[0]` 是同句但**半角逗号** — 证明 LLM 在两处分别"创作",不是简单复制
- **触发条件**:简历正文里没有独立的 description 段落(只有 bullets 列表)— v1.0.0 prompt 描述「`description` 是该段经历的一句话总结」,LLM 拿 `bullets[0]` 当总结去填
- **后果**:UI 渲染同一句话上下两遍,且 description 半角变全角影响搜索/embedding
- **修法**:prompt 加硬约束 — `description` 仅当简历存在**独立段落式概述**(不是 bullets 列表本身)才填;无则**必须返回空字符串 `""`**;**禁止从 bullets 改写、摘抄、压缩、翻译或复述(包括把半角逗号改成全角逗号这种轻度改写)**
- **验证**:#15 重解后,3 段 experience + 3 段 project 的 description 全部为 `""`,UI 不再上下重复

### B2 — 日期 `end_date=null` 被过度使用
- **症状**:简历单写「2019」(无月份、无"至今")的项目被解析为 `start_date=2019-01-01, end_date=null`;UI 用 `end_date===null` 渲染成「至今」,把 5 年前结束的项目变成"在做"
- **复现**:JobCopilot 测试 PDF 的「分布式爬虫调度框架(2019)」/ 美团经历(2018 — 2020)
- **后果**:`is_current` 语义被破坏,匹配阶段的"近期经验"加权全错
- **修法**:prompt 加日期细则 — **明示**「至今 / Present / Now / 迄今 / —」时才允许 `end_date=null`;否则必须给具体值;单年 YYYY 默认 `start=YYYY-01, end=YYYY-12`(不是 `YYYY-01`)
- **验证**:#15 重解后,「分布式爬虫」`2019-01 ~ 2019-12`,美团 `2018-01 ~ 2020-12`(原本是 `2020-01`,新规则更准 — "2020" 通常意指做完这一年,不是年初离职)

### B3 — 中文等级词「熟练」错位到 `intermediate`
- **症状**:Go「熟练 4 年」/ TypeScript「熟练 3 年」被解析为 `level=intermediate`(应是 `advanced`)
- **复现**:语言 / 框架 / 任何带中文等级修饰的 skill 都中招
- **根因**:v1.0.0 prompt 只列了等级词列表,没给中→英映射表;LLM 自己判断时,「熟练」按字面靠近 "skilled / fluent" → 选了 intermediate
- **后果**:候选人技能等级整体被压低 1 档,匹配召回偏低
- **修法**:prompt 加严格映射表
  ```
  精通 / Expert / Proficient / Master       → expert
  熟练 / 擅长 / Advanced / Strong            → advanced
  掌握 / 良好 / 熟悉 / Intermediate / Familiar → intermediate
  了解 / 入门 / Beginner / Basic / Novice    → beginner
  ```
- **验证**:#15 重解后 Go / TypeScript 都是 `advanced`

## 🎨 前端 UI 漏渲染(`profile-edit-form.tsx`)

后端数据**全部正确**(curl `/v1/profiles/{id}` 验证过),三个字段单纯没在 UI 渲染,纯展示 bug。

### F1 — `target_titles` 无渲染区
- **现场**:API 返回 `["大模型应用工程师","LLM Application Engineer","AI 全栈工程师"]`,UI 只画了姓名/邮箱/电话/所在地/简介
- **修法**:`summary` 下方加一段 — 数组非空时渲染 chip 列表,空数组不显示

### F2 — `demo_url` 不渲染
- **现场**:`renderProject` 里只画了 `repo_url`,`demo_url` 字段直接忽略
- **修法**:在 `repo_url` 块后追加同样模式的 `demo_url` 块,顺便给两个块加「代码:」「Demo:」前缀

### F3 — 教育 `honors` 不渲染
- **现场**:`renderEducation` 只画了 GPA;chunks 里能看到 `荣誉:国家奖学金 | 校优秀毕业生`(后端入库正确)
- **修法**:GPA 行后追加 `荣誉:xxx · yyy`,空数组不显示

---

# 设计决策(实现细节)

## 1. 修 prompt 必须 bump 版本号(已知约束的应用)

`prompt_versions` 表强制 `(agent_name, version)` 唯一 + content hash 不可变,直接改 `v1.0.0.j2` 启动会撞 `PromptVersionMismatchError`(STATUS.md 永久约束 #4「改 prompt 文件前必须先停 uvicorn」)。本轮直接 `v1.0.0.j2` 复制为 `v1.0.1.j2` 改,router `PROMPT_KEY` 从 `v1.0.0` 切到 `v1.0.1`,uvicorn `--reload` 触发的 startup `load_prompt_versions` 自动 upsert 新行,无 ghost row。

## 2. description 反幻觉的关键措辞

第一版加约束写「`description` 不要复制 bullets」 — 不够,LLM 仍然轻度改写。终版加细到「**禁止改写、摘抄、压缩、翻译或复述,包括把半角逗号改成全角逗号这种轻度改写**」 — 把观察到的具体行为写进 prompt,LLM 这才停手。

教训:prompt 反幻觉约束要把"具体作弊路径"写出来,泛泛说"不要 X"不够。

## 3. 单年 YYYY 默认到年末而非年初

「2020」原 v1.0.0 默认拼成 `2020-01-01`(LLM 自己选起点),意味着「2018 — 2020」会被理解为"做了 2 年零 0 月"(start 2018-01, end 2020-01)。v1.0.1 改成 end=YYYY-12 后变成"做了将近 3 年"(start 2018-01, end 2020-12),更贴近中文简历"我做到 2020"的实际语义。

## 4. 测试 fixture 用 `uv run --with reportlab` 临时拉

reportlab 不进 `pyproject.toml`(只测试用,不该污染生产依赖)。脚本顶部写明跑法 `uv run --with reportlab python apps/api/scripts/gen_test_resume.py`,uv 内存里临时装包后丢弃。中文用系统自带的 `STHeiti Medium.ttc`(macOS 标配),不需要捆绑字体。

## 5. fixture 落到 `fixtures/resumes/`(已 gitignore)

项目根 `.gitignore` 里早就有 `fixtures/`(给"本地 dogfood 测试简历/JD"用),`fixtures/resumes/` 已经有 `resume-lihang.txt` / `resume-zhangsan.txt`,新 PDF 同目录,保持一致。脚本默认输出路径取自 `Path(__file__).resolve().parents[3] / "fixtures/resumes/"`,不依赖 cwd。

---

# 踩坑

1. **第一遍报告把 description 重复说成"复制"** — 半角→全角逗号被当成显示问题没在意,后来读 raw API 返回才发现是 LLM 在 description 里**重新生成**了一遍同句话(全角逗号是阿里云 LLM 生成中文的默认习惯)。这是判定"幻觉" vs "复制" 的关键信号。
2. **PDF 第一次出在桌面** — 默认路径写成 `~/Desktop/`,用户要求挪到项目内才发现 `fixtures/` 已经为这种数据准备好了。习惯先看 `.gitignore` 找现成位置,别想当然放系统目录。
3. **生成的 PDF 边界压得太密** — 12 类边界进同一份 PDF 利于一次发现多 bug,但解析失败时无法快速二分定位是哪个边界触发。后续若要做正式 dataset,应每个边界单独一份小 PDF。

---

# 不在本文档范围

- **v1.0.2 留下的 4 条**(STATUS.md 待办 #11):① 技能切分一致性(`/`、`+` 拆得不规律)② partial-year project end_date 兜底("2022" 现兜底成 `2022-01-01` 让 start=end 显示成持续 1 月)③ tech_stack 剔除空泛词(jdk / 后端 等)④ 证书章节(AWS Solutions Architect / 阿里云 ACA)— schema 加 `certifications` 字段。本轮没碰,因为测试 PDF 没造这几类边界。下一轮 dogfood 顺手补。
- **可接受的设计选择**(不修):① summary 里 emoji 🚀 被吞 ② 「张明远 / Zhang Mingyuan」`full_name` 输出存在波动(有时取中文有时取全部)③ 「上海 · 浦东新区」截到「上海」(粒度统一)④ 「PostgreSQL(含 pgvector)」未单拆 pgvector skill(LLM 选择)⑤ 「AWS(EKS / RDS / S3)」未拆细
- **`parse_model` 字段显示「qwen3.6-flash」是否是真模型 ID** — 看着像拼接 bug 但没追查,留给下一轮
- **简历定制 dogfood (S18 主线)** — 本轮只跑 ProfileParser,匹配 / drafter / reviewer 链路未涉及

不在本文档范围:简历定制相关 prompt(drafter/reviewer)调优 → S18;profile_parser v1.0.2 的剩余 4 条 → 下一轮 dogfood;parse_model 显示串追查 → 下一轮顺手。
