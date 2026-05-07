# Synthetic Persona Fixtures(用于评测,不是真实用户)

每个 yaml 文件 = 一个 synthetic persona,通过 `evals/fixtures/load_personas.py` 入库到
`profiles` + `profile_educations` + `profile_experiences` + `profile_projects` +
`profile_skills` 表,然后调 `chunk_service.rebuild_for_profile` 切 chunks + 跑 embedding。

入库后挂在 `user_id=1`(test@local),`source='manual'`,`status='parsed'`。

Persona 命名:`persona-{方向}.yaml`(例:`persona-frontend-to-ai.yaml`)。

## 数据真实性约束

- 公司名 / 学校 / 项目用**化名或常见名**(蚂蚁集团 / 浙工大 / 某 SaaS 公司),不伪造为真用户
- 人物姓名英文化(Lin Xiao / Wang Hao 等)+ 邮箱用 `@example.com`,避免污染真用户搜索
- chunks 内容里的数字 / 比例可以编造,但要内部一致(experience 写 80w 用户日活,在 achievements 里就保持 80w 不变)

## 已造 persona

| 文件 | 方向 | 设计意图 |
|------|------|---------|
| `persona-frontend-to-ai.yaml` | 三年前端 + 一年 AI 应用转入 | 跟 AI Agent JD 部分相关 → 大量中桶 case |
