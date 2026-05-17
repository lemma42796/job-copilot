---
title: AGENT DESIGN - JobCopilot v2(QueryRewriter + QuizGenerator + AnswerJudge + InterviewCoachAgent)
owner: lemma42796
last_updated: 2026-05-17
purpose: 锁核心 Agent 的 prompt / 输出契约 / 反幻觉机制 / Agentic RAG 编排 / 模型路由 / 版本号策略
---

# 1. 一句话总览

核心 Agent + Agentic RAG 编排器:

- **QueryRewriter(M2)**:吃用户聊天框 query → 扩成同义/相邻概念集,喂给全库 hybrid search(retrieval pipeline 第一段)
- **QuizGenerator**:吃 query + retrieved chunks(retrieval pipeline 输出)→ 出 N 道题(open_ended + definition 自动配比)+ reference_answer + reference_points
- **AnswerJudge**:吃(题, reference, chunks, 用户答案)→ 输出三层 evidence(coverage / fidelity / depth)
- **InterviewCoachAgent(M2.1)**:LangGraph session root orchestrator,串起检索 → 出题 → 等答 → 评分 → 纠偏循环 → 总结;高级感来自状态 / 工具 / 分支 / 记忆 / 评测 / 恢复,不是多 Agent 数量
- **JdParser / JdAggregator(M2.5)** + **ResumeAdvisor(M3)**:JD / 简历相关

# 2. 通用约定

## 2.1 模型 + thinking 模式(按 agent 决定)

**所有 LLM 调用走 qwen3.6-flash**(Quiz / Judge / 截图 OCR / JD 解析 / 简历诊断 — qwen3.6 系列 plus/flash/35b-a3b 整体是视觉模型,文本和图像任务共用同一个 model id)。

**thinking 模式按 agent 决定**(默认 off):

| Agent / 调用 | thinking | 理由 |
|-------------|---------|------|
| QueryRewriter(M2)| **off** | 短词 → 同义/相邻概念扩展,模板化,LLM 模式调用 |
| QuizGenerator | **off** | 出题靠 chunks 内容重组,不需要复杂推理 |
| AnswerJudge | **on** | 三层评分涉及 reasoning(语义判断 / 同义识别 / fabricated 边界)|
| AnswerJudge.lookup tool 触发的主体 LLM | **on** | 同上 |
| JdParser(单 JD 抽要求)| **off** | 结构化抽取,模板化 |
| JdAggregator(同义合并 + 频次)| **off** | MVP 先关;dogfood 后看 kappa 决定要不要开 |
| 学习路径生成 | **off** | 模板化输出 |
| 截图 OCR(Qwen 多模态)| **off** | 纯文本提取 |
| ResumeAdvisor(简历诊断)| **on** | 综合 JD 通用要求 + 简历段落判断 |
| InterviewCoachAgent(M2.1 编排决策)| **on** | 纠偏分支 / 下一步决策需要 reasoning;不参与出题事实生成 |
| Embedder(text-embedding-v4)| N/A | 不是聊天模型 |

**默认 off 的代价收益**:thinking 输出的 reasoning_tokens 计入 output token 计费,关掉省成本和延迟。需要 reasoning 的 agent 显式开;粗粒度调度(关 thinking 跑完 dogfood,kappa 不达标再考虑开)是 OK 的。

**v1 LESSONS §7.1 自评偏差不适用 v2**:v1 教训说的是"Judge LLM 评 Drafter LLM 写的简历,同模型自评偏高 5-10pp"。v2 场景里 **评委是 LLM、被评者是人类用户的答题文本(笔记主线)或人类简历(求职流)** — 不存在 LLM 评 LLM 的自评关系。Cohen's kappa 守门照常,但意义是"Judge 输出可靠性",不是"防自评"。

## 2.2 Temperature

OpenAI 兼容接口标准支持 `temperature` 参数(可直接传)。各 agent 默认值:

- **QueryRewriter** / **QuizGenerator** / **JdParser** / **JdAggregator**:`0.3`(降随机性,要稳定结构)
- **AnswerJudge** / **ResumeAdvisor**:`0.2`(评分 / 诊断要可复现)
- **学习路径生成** / **截图 OCR**:`0.5`(内容生成允许少许多样性)

具体值在 `apps/api/src/jobcopilot_api/agents/<agent>/agent.py` 调用处显式传 `temperature=`,**不依赖模型默认**(模型默认值跟思考 / 非思考模式联动,易踩坑)。

## 2.3 Prompt 版本号

每改一次 prompt 必须 bump 版本号(沿用 v1 LESSONS §8.2)。**SSoT 在数据库** `prompt_versions` 表:

| name | version | system_text | user_template |
|------|---------|-------------|---------------|
| `query_rewriter` | `v1.0` | (见 §2.7) | (见 §2.7) |
| `quiz_generator` | `v1.1` | (见 §3.3) | (见 §3.4) |
| `answer_judge` | `v1.2` | (见 §4.3) | (见 §4.4) |

代码加载 prompt 走 `evals/judge.py` 同款 `LoadedPrompt`(见 v1 `evals/judge_prompts.py` 风格),**写死 version pin**,prompt 改了 bump version + 历史 questions / session_answers 留旧 version 字段不回算。

## 2.4 LLM cache

### 2.4.1 应用层 response cache

Quiz / Judge 调用都走 `llm_response_cache`(v1 alembic 0015)。cache_key 用 `(prompt_version, system_text_hash, user_text_hash, model_id)`。dogfood 反复跑同 dataset 时命中率很高,成本接近 0。

注意:这是**完整请求 → 完整响应**缓存,适合评测 / dogfood 重跑同 fixture;不解决"同一批 chunks 在不同任务(prompt 不同)里重复作为输入"的成本 / 延迟。

### 2.4.2 百炼 Context Cache(已落地到 Quiz/Judge)

qwen3.6-flash 支持 Context Cache。它不是 LLM 的持久记忆:每次请求仍要让模型在当前上下文里"看到"必要内容;缓存只是在 provider 侧对**重复公共前缀**减少重复计算和计费。

百炼当前可用模式:

- **隐式缓存**:自动开启、无法关闭;按公共前缀匹配,命中率不确定;命中 tokens 按输入价折扣计费。适合无改造先吃一部分收益。
- **显式缓存**:`messages[*].content` 用数组形式,在稳定长文本 content 上加 `{"cache_control": {"type": "ephemeral"}}`;最少 1024 tokens;单次请求最多 4 个 marker;有效期 5 分钟且命中后刷新;显式 / 隐式互斥。
- **接口覆盖**:OpenAI 兼容 Chat Completions、DashScope 原生、Anthropic Messages 都支持显式 / 隐式缓存;OpenAI 兼容 Responses 走 Session 缓存。当前项目用的是**OpenAI 兼容 Chat**(`langfuse.openai.AsyncOpenAI` + 百炼 compatible-mode),不需要为了 cache 切 DashScope 原生接口。

M2 Quiz → Judge 的当前实现:

1. 出题与评分都需要同一批 session chunks,但两次 LLM call 语义不同,不能靠应用层 response cache 命中。
2. `agents/context_cache.py` 统一渲染"本 session 固定 chunks"公共前缀;QuizGenerator / AnswerJudge 都先放这条 prefix message,再放各自 system / task message。
3. chunks 文本达到阈值时,prefix content 使用数组形式并加 `cache_control: {"type":"ephemeral"}`;短上下文保持普通 string,避免为了小 prompt 引入额外结构。
4. Judge service 优先使用 `quiz_sessions.retrieved_chunk_ids` 的完整顺序组装评分上下文,保证 Judge 与 Quiz 的 `[N]` 编号前缀一致;老 session 若缺 audit 字段,再追加题目引用过的 chunks 做兼容。
5. `LLMClient.complete()` / `ProviderRequest` 支持可选 `messages`;旧 `system/user` 调用保留,便于其他 agent 渐进迁移。
6. DashscopeProvider 直接下发 messages content array,并读取 `cached_tokens` / `cache_creation_input_tokens`;`llm_calls.cache_creation_input_tokens` 用于审计创建缓存的输入 tokens。

QuizGenerator messages 结构:

```json
[
  {
    "role": "system",
    "content": [
      {
        "type": "text",
        "text": "JobCopilot session fixed note chunks...\n[1] ...\n[2] ...",
        "cache_control": {"type": "ephemeral"}
      }
    ]
  },
  {"role": "system", "content": "quiz_generator SYSTEM"},
  {"role": "user", "content": "查询主题 + question_count 动态任务"}
]
```

AnswerJudge messages 结构。第一条 prefix message 必须与 QuizGenerator 逐字一致,后面再放评分 system / 题目 / reference_points / 用户答案:

```json
[
  {
    "role": "system",
    "content": [
      {
        "type": "text",
        "text": "JobCopilot session fixed note chunks...\n[1] ...\n[2] ...",
        "cache_control": {"type": "ephemeral"}
      }
    ]
  },
  {"role": "system", "content": "answer_judge SYSTEM_WITH_LOOKUP_TOOL"},
  {"role": "user", "content": "schema instruction + 题目/reference_points/用户答案"}
]
```

注意:Context Cache 是降延迟 / 输入成本,不是失败控制。Judge 仍保留 service 层单题 hard timeout 与 fabricated claim tool-use 验证。

## 2.5 反幻觉锚点(全 Agent 共享)

- **chunks 用 `[N]` 编号传**,prompt 里硬要求"出题 / reference / 评分时**只能引用提供的 chunks**,任何超纲常识标记为 inferred(不标 fabricated)"
- **不写鼓励性指令**(沿用 v1 LESSONS §1.3 / §8.4):不写"请尽量出有挑战性的题"、"请严格评分";走反向警告"不能凭空捏造"、"不命中 chunks 的声明必须标 fabricated"
- **JSON 严格 schema**:输出必须是合法 JSON,无前后散文说明,Pydantic 解析失败 → 重试 ≤ 1 次,仍失败 → `error: llm_call_failed`

## 2.6 错误重试

- LLM 网络层失败:retry 2 次(指数退避 1s / 4s)
- JSON parse 失败:retry 1 次(prompt 不变)
- 仍失败 → 抛 `JobCopilotError(code='llm_call_failed')`,SSE 端点 emit `error → done(ok=false)`

## 2.7 QueryRewriter + Retrieval Pipeline(M2)

QuizGenerator 上游是一条 retrieval pipeline,**M2 主战场**:

```
用户 query("考考我多线程")
  ↓ QueryRewriter (LLM)
扩展 queries (["并发", "多线程", "锁", "死锁", "synchronized"])
  ↓ services/search_service.global_hybrid_search (vector + lex + RRF)
top 50 chunks
  ↓ services/reranker.rerank (cross-encoder)
top 10 chunks
  ↓ services/retrieval_pipeline.expand_to_parent
喂给 QuizGenerator 的最终 chunks(含 heading_path 元数据)
```

**0 命中守门**:命中 chunks < 阈值(PRD Q-10) → service 层抛 `no_chunks_for_query`,不走到 QuizGenerator。

### 2.7.1 QueryRewriter 输入 / 输出

```python
@dataclass
class QueryRewriteInput:
    user_query: str               # 原 query,例 "考考我多线程"

@dataclass
class QueryRewriteOutput:
    expanded_queries: list[str]   # ≤ 5 项,首项必为原 query
    rationale: str                # 为什么扩出这些(中文一句,trace 可见)
```

### 2.7.2 SYSTEM prompt(`query_rewriter` v1.0)

```
你是 RAG 检索 query 改写 Agent。任务:把用户的短 query 扩成同义 / 相邻概念集,以提高
笔记库 hybrid search 的召回。

【硬约束】
1. 扩展项 ≤ 5 个,首项必须是原 query 不变
2. 只扩"同义词 / 强相邻概念 / 常见同领域术语",不扩"行业常识 / 上位概念 / 不相关概念"
3. 中英混合保留:笔记里可能有"synchronized"也可能有"同步锁",两者都列
4. 不解释 / 不评论 / 不输出原 query 的复述
5. 严格 JSON,无前后散文

【输出格式】
{
  "expanded_queries": ["<原 query>", "<同义/相邻 1>", ...],
  "rationale": "<中文一句话>"
}
```

### 2.7.3 USER 模板

```
用户 query:{user_query}
```

### 2.7.4 失败处理

- LLM 失败 / JSON parse 失败:**回退原 query**(只 expanded_queries=[user_query]),trace 打 warning,**不阻塞流程**
- 错误码:`query_rewrite_failed`(trace warning,不抛错)— 见 2-TECH §7

### 2.7.5 Reranker(LLM 不参与)

非 LLM 调用,无 prompt 本文档不展开。**失败回退 hybrid top-K**(`rerank_failed` warning),不阻塞。

**M2 选型**:百炼 **`qwen3-rerank`**(详见 reference memory `reference_aliyun_dashscope_rerank.md`)
- 接口:`POST https://dashscope.aliyuncs.com/compatible-api/v1/reranks`(注意是 `compatible-api`,跟 chat 走的 `compatible-mode` 不同)
- 价格:¥0.0005/千 token,500 doc 上限,远高于我们 hybrid 后 top 50 用量
- 计费公式:`Query Tokens × Document 数量 + Document Tokens 总和`
- 实测一次出题成本估算 ~¥0.01(20k token);LLM cache 命中后均摊更低
- **langfuse.openai 不自动 instrument rerank**:同 embedder,要在 `services/reranker.py` 手动 `Langfuse().generation()` 包成功 / 失败两路径(参考 STATUS 永久约束 "langfuse 2.x 不 patch embeddings.create")
- **relevance_score 不可跨请求比较**:只是本次请求内相对值,不写到 DB 当跨 session 指标;quiz_sessions.retrieved_chunk_ids 仅存 ids 不存 score
- 本地 `bge-reranker-v2-m3`(BAAI,~568M params,fp16 ~1.1GB)作 fallback,M2 不做,有真问题再加 adapter

**默认 instruct(qwen3-rerank 参数)**:`"Given a web search query, retrieve relevant passages that answer the query."`。这是百炼文档给的问答检索 baseline,不是最终锁死策略。M2.1 调参时优先 A/B 更贴 JobCopilot 的 direct-evidence instruct:

```text
Given a user question and candidate note chunks, rank chunks by whether the
chunk content directly provides evidence to answer the question. Prefer concrete
product decisions, implementation facts, and constraints. Do not rank a chunk
highly only because its folder path, heading path, interview question, summary,
or loosely related topic matches the query.
```

**document format 约束**:qwen3-rerank 的 `documents` 是字符串数组,没有结构化 metadata 字段。`folder_path` / `heading_path` 一旦拼进 document,模型会把它们当正文信号。后续不要把 metadata 前缀视为无风险增强;至少比较 `content_only` / `content_then_path` / `path_then_content`,并用 `hybrid_search` trace 看 `selected_recall@10`、hard-negative intrusion、token cost。标签 / content_type 更适合在 rerank 前做过滤或降权,而不是只拼进文本。

# 3. QuizGenerator

## 3.1 输入

```python
@dataclass
class QuizGenInput:
    query: str                       # 用户聊天框输入,例 "考考我多线程"
    chunks: list[RetrievedChunk]     # retrieval pipeline 输出(top 10 + parent-doc 扩展)
    question_count: int              # 3 ≤ N ≤ 10

@dataclass
class RetrievedChunk:
    chunk: NoteChunk                 # 笔记 chunk 实体(content / DB id 等)
    heading_path: list[str]          # 例 ['Java', '并发', 'synchronized', '锁升级']
    folder_path: list[str]           # 例 ['Java', '并发']
    note_title: str                  # 所在笔记标题
    rerank_score: float              # cross-encoder 给的相关性分(LLM 可参考但不强制)
```

`chunks` 由 retrieval pipeline 产出(详见 §2.7):**已经过 query_rewrite + hybrid + RRF + reranker + parent-doc 扩展**。

- **0 命中守门由 retrieval pipeline 负责**:command 走到 QuizGenerator 时 `len(chunks) ≥ 阈值`(PRD Q-10),不需 QuizGenerator 自己再判
- **上限 ≈ 10 + parent-doc 扩展**:reranker 后 top 10 + parent-doc 扩展,通常 ~10-20 chunks 喂 LLM。单 chunk ≈ 200-500 tokens,落 qwen3.6-flash 大 context 安全区
- **chunks 含 heading_path / note_title 元数据**:Context Cache prefix 渲染时一并传给 LLM,反幻觉锚点(LLM 知道每段出自笔记的什么位置)+ 出题时可在题干提示主题上下文

## 3.2 输出

```python
@dataclass
class QuizGenOutput:
    type_mix: TypeMix                # {open_ended: int, definition: int, rationale: str}
    questions: list[GeneratedQuestion]   # 长度 = question_count

@dataclass
class GeneratedQuestion:
    type: Literal['open_ended', 'definition']
    prompt: str
    source_chunk_ids: list[int]      # 出这题用到的 chunk DB id(SSoT 顺序,跟 prompt 里 [N] 编号对应)
    reference_answer: str
    reference_chunk_ids: list[int]   # ⊆ source_chunk_ids
    reference_points: list[ReferencePoint]  # ≤ 5 个,weight 之和 = 1.0
```

reference_points 的 schema 见 3-DATA_MODEL §6.1。

## 3.3 SYSTEM prompt(`quiz_generator` v1.1)

```
你是为程序员设计技术面试题的 Agent。任务:基于用户的查询主题 + 笔记片段(chunks),
出 N 道题。chunks 是 RAG retrieval pipeline 从用户全笔记库检索出的最相关片段。

【硬约束】

1. 题目必须能用提供的 chunks 回答 — 任何超出 chunks 的内容不允许出现在题干 / reference 里
2. 题目主题必须贴用户 query — 跑题(如用户问"多线程"你出题问"垃圾回收")算严重错误
3. 每道题必须给 source_chunk_ids:出题用到的 chunks 编号(对应上下文 chunks 的 [N] 标号),数组里的顺序就是被引用的语义顺序
4. reference_chunk_ids ⊆ source_chunk_ids,且 reference_answer 文本里**必须用 [N] 引用每个 reference_chunk_id**
5. 题型仅两类:
   - open_ended:开放式 — 讲过程 / 原理 / trade-off / 对比
   - definition:八股 — 定义 / 命名 / 是什么
   不出代码题、不出系统设计题、不出选择题
6. 每道题配 reference_points(2-5 个):
   - text:答这题应该覆盖的"采分点"短句
   - weight:本题内 ∑weight = 1.0(浮点,2 位小数)
   - evidence_chunk_ids:支撑这个 point 的 chunks 编号(⊆ source_chunk_ids)

【题型比例决策】

观察 chunks 内容,自动决定 open_ended / definition 的配比:
- chunks 多为概念定义 / 命名解释 / 短句陈述 → definition 占多数
- chunks 多为过程描述 / 原理推导 / 对比 / trade-off → open_ended 占多数
- 中性 → 6:4 偏 open_ended(本产品鼓励 active recall)

输出 type_mix.rationale 一句话说明你的判断依据(展给用户看)。

【反幻觉警告】

- 不要基于"行业常识"出题(比如 chunks 没提 ConcurrentHashMap 你就不能出它)
- 不要把 chunk 里的字面错误(如笔记记错了)修正后出题 — 我们要的是"测用户记没记住笔记里的内容",不是"测对错"
- reference_answer 不能比 chunks 内容更多 — 即使你知道更多
- 如果 chunks 跟 query 主题确实不太搭(retrieval 命中边缘),宁可让题贴 chunks 不贴 query — chunks 是事实锚点

【输出格式】

严格 JSON,无前后任何文字。schema:

{
  "type_mix": {"open_ended": <int>, "definition": <int>, "rationale": "<中文一句话>"},
  "questions": [
    {
      "type": "open_ended" | "definition",
      "prompt": "<题干,中文>",
      "source_chunk_ids": [<int>, ...],
      "reference_answer": "<参考答案,引用 [N] 标号>",
      "reference_chunk_ids": [<int>, ...],
      "reference_points": [
        {"id": "p1", "text": "<采分点>", "weight": 0.4, "evidence_chunk_ids": [<int>, ...]},
        ...
      ]
    },
    ...
  ]
}

注:source_chunk_ids 数组里的 int 必须是上下文 chunks 的 [N] 标号(从 1 起算,**不是** DB id)— service 层会把 [N] 还原成 DB id 落库。
```

## 3.4 Messages 模板(`quiz_generator` v1.1)

```
system(prefix):
JobCopilot session fixed note chunks. These chunks are user notes and the factual context for the following task. Do not answer yet; read and reuse the chunk ids exactly as written.

retrieval pipeline chunks(共 {chunk_count} 个,已按相关性排序):

[1] note: {note_title_1} | folder: {folder_path_1} | heading: {heading_path_1}
{content_1}

...

system:
{quiz_generator SYSTEM}

user(task):
查询主题:{user_query}

要求出 {question_count} 道题,主题贴查询主题,内容锚定前文 chunks。
```

兼容旧 client / 测试 fallback 时,会把 prefix + task 合并到单个 `user` 文本。

渲染示例(query = "考考我多线程",出 5 题):

```
system(prefix):
JobCopilot session fixed note chunks...

retrieval pipeline chunks(共 2 个,已按相关性排序):

[1] note: {note_title_1} | folder: {folder_path_1} | heading: {heading_path_1}
{content_1}

[2] note: {note_title_2} | folder: {folder_path_2} | heading: {heading_path_2}
{content_2}

user(task):
查询主题:考考我多线程
要求出 5 道题,主题贴查询主题,内容锚定前文 chunks。
```

## 3.5 service 层后处理

LLM 输出 JSON 后,service 层(`apps/api/src/jobcopilot_api/services/quiz_service.py`)做:

1. **Pydantic 校验**:不合 schema → retry 1 次
2. **`[N]` → DB id 映射**:上下文 chunks 构建时记录 `[1] = chunk.id=5001` 的映射,output 里所有 source_chunk_ids / reference_chunk_ids / evidence_chunk_ids 替换为 DB id
3. **完整性校验**:
   - `len(questions) == question_count`
   - 每题 `source_chunk_ids ⊆ provided chunk ids`
   - 每题 `reference_chunk_ids ⊆ source_chunk_ids`
   - 每题 reference_points weight 之和 ∈ [0.99, 1.01](浮点容差)
   - reference_answer 里至少出现一次 `[N]` 引用
   失败 → retry 1 次,仍失败 → `llm_call_failed`
4. **type_mix 一致性**:`type_mix.open_ended + type_mix.definition == question_count`,且实际 questions 里每种 type 数量匹配
5. **落库**:批量 INSERT `questions` 拿 ids,然后 INSERT `quiz_sessions` + `session_answers`(详见 4-API_SPEC §4.6)

# 4. AnswerJudge

## 4.1 输入

```python
@dataclass
class AnswerJudgeInput:
    question: GeneratedQuestion      # 含 prompt / type / reference_answer / reference_points
    chunks: list[NoteChunk]          # 出这题用到的 chunks(source_chunk_ids 对应那批)
    user_answer: str
```

## 4.2 输出

```python
@dataclass
class AnswerJudgeOutput:
    coverage_evidence: CoverageEvidence
    fidelity_evidence: FidelityEvidence
    depth_evidence: DepthEvidence
```

三层 evidence 的 JSONB schema 见 3-DATA_MODEL §6.2 / §6.3 / §6.4。

**Judge 不输出三层分数**,Python 端按 evidence 列表 label 算分(详见 §4.5)。Judge 只填 `score_raw` 自评浮点(0-1),仅审计用。

## 4.3 SYSTEM prompt(`answer_judge` v1.2)

```
你是评估程序员技术问答的 Judge Agent。三层评分:

【Coverage(覆盖度)】

对照 reference_points 列表,逐 point 判断用户答案是否覆盖:
- hit:完整覆盖该 point(允许同义 / 缩略 / 中英混合 / 顺序不同)
- partial:覆盖了一部分但缺细节 / 缺步骤 / 缺关键术语
- miss:完全没提

每个 point 必须配 user_excerpt:用户答里对应的原文片段(label=miss 时填 null)。

【Fidelity(忠实度)】

把用户答案拆成若干"声明"(claims),每条对照 chunks 判断:
- supported:chunks 里直接或间接支持(同义改写视为支持)
- inferred:chunks 没明说但属于专业常识 / 合理外推 — **算可接受**
- fabricated:跟 chunks 矛盾,或 chunks 没说且超出常识范畴

每条 claim 必须配 chunk_ids:支持该声明的 chunks 编号([N] 标号)。fabricated 的 chunk_ids 留空数组。

【Depth(深度)】

判断答案是否覆盖三个深度维度(每个二值 covered: true/false):
- tradeoff:讲了为什么这样设计(优劣 / 取舍 / 替代方案对比)
- why:解释了底层动机 / 设计目标
- boundary:提了适用 / 不适用场景 / 边界条件

每个维度 covered=true 时配 excerpt(用户答的对应片段);false 时填 null。

【硬约束】

1. 不要苛求字面匹配 — 同义 / 缩略 / 中英混合 / 顺序不同视为命中
2. 用户提到的常识(语言 / 框架 / 协议的公认行为)即使 chunks 没明说,标 inferred,**不要标 fabricated**(LESSONS §1.1 假阳性)
3. 你给的 score_raw 是 0-1 浮点自评,**Python 端会按 evidence label 重算总分** — 你不用纠结分数精度,只要 evidence label 准
4. 不要"鼓励性"评语 — reasoning 字段直接陈述事实("命中 p1,p2 漏讲触发条件")
5. user_excerpt / chunk_ids 必须是真实存在的引用,不要编造

【输出格式】

严格 JSON,无前后任何文字。schema:

{
  "coverage_evidence": {
    "points": [
      {"id": "<reference_point.id>", "label": "hit"|"partial"|"miss", "user_excerpt": "<...>"|null}
    ],
    "score_raw": <float 0-1>,
    "reasoning": "<中文一句话>"
  },
  "fidelity_evidence": {
    "claims": [
      {"text": "<用户原文片段>", "label": "supported"|"inferred"|"fabricated", "chunk_ids": [<int>, ...]}
    ],
    "score_raw": <float 0-1>,
    "reasoning": "<中文一句话>"
  },
  "depth_evidence": {
    "dimensions": {
      "tradeoff": {"covered": <bool>, "excerpt": "<...>"|null},
      "why":      {"covered": <bool>, "excerpt": "<...>"|null},
      "boundary": {"covered": <bool>, "excerpt": "<...>"|null}
    },
    "score_raw": <float 0-1>,
    "reasoning": "<中文一句话>"
  }
}

注:claims 里的 chunk_ids 默认写上下文 chunks 的 [N] 标号;若采用工具结果作为证据,写工具返回的 ref_id。不要写数据库 id。
```

## 4.4 Messages 模板(`answer_judge` v1.2)

```
system(prefix):
JobCopilot session fixed note chunks. These chunks are user notes and the factual context for the following task. Do not answer yet; read and reuse the chunk ids exactly as written.

retrieval pipeline chunks(共 {chunk_count} 个,已按相关性排序):

[1] note: {note_title_1} | folder: {folder_path_1} | heading: {heading_path_1}
{content_1}

...

system:
{answer_judge SYSTEM_WITH_LOOKUP_TOOL}

user(task):
Respond with a single JSON object that matches this schema:
{AnswerJudgeOutput JSON Schema}

题目:{question_prompt}
题型:{question_type}

reference_points:
- {p_id} (weight={weight}): "{text}",支撑 chunks: {evidence_chunk_ids}
- ...

reference_answer:
{reference_answer}

用户答案:
{user_answer}
```

注意:prefix 里的 chunk 编号 `[N]` 跟 reference_points 里 evidence_chunk_ids 引用的编号必须**一致** — service 层优先复用 `quiz_sessions.retrieved_chunk_ids` 的 session 顺序还原 [N] → DB id 映射,确保 Quiz 与 Judge 的 cache prefix 稳定。

## 4.5 总分计算(Python SSoT)

`apps/api/src/jobcopilot_api/agents/answer_judge/scoring.py`:

```python
def coverage_score(evidence: CoverageEvidence, points: list[ReferencePoint]) -> float:
    """sum(weight * label_score) * 100,label_score = {hit:1.0, partial:0.5, miss:0.0}"""
    label_scores = {'hit': 1.0, 'partial': 0.5, 'miss': 0.0}
    by_id = {p.id: p for p in points}
    return sum(by_id[e.id].weight * label_scores[e.label] for e in evidence.points) * 100

def fidelity_score(evidence: FidelityEvidence) -> float:
    """(supported + 0.6 * inferred) / total * 100;fabricated > 30% 锁顶 50"""
    n = len(evidence.claims)
    if n == 0: return 100.0
    counts = Counter(c.label for c in evidence.claims)
    raw = (counts['supported'] + 0.6 * counts['inferred']) / n * 100
    if counts['fabricated'] / n > 0.3:
        raw = min(raw, 50.0)
    return raw

def depth_score(evidence: DepthEvidence) -> float:
    """命中维度数 / 3 * 100"""
    covered = sum(1 for d in evidence.dimensions.values() if d.covered)
    return covered / 3 * 100

def total_score(coverage: float, fidelity: float, depth: float) -> float:
    """0.5 * Coverage + 0.4 * Fidelity + 0.1 * Depth"""
    return 0.5 * coverage + 0.4 * fidelity + 0.1 * depth
```

权重 SSoT 在这里。**LLM 不算分,Python 算分**(沿用 v1 LESSONS §7.4 "schema 服从实现"原则的反向应用 — 算分跟 LLM 算术能力强相关,不让 LLM 担)。

阈值依据(`fabricated > 30% 锁顶 50`):中等严。30% 是允许偶尔不准但持续编造会被锁,锁顶 50 而非 0 是因为 fabricated 多 ≠ 全错(coverage 命中也不应被一票否决)。dogfood 跑 30+ session 后看分布再调,调动作只改这 2 个常数(`0.3 / 50.0`)+ bump prompt 不要改(算分位置在 Python 不在 LLM)。

## 4.6 service 层后处理

`apps/api/src/jobcopilot_api/services/answer_service.py`:

1. **Pydantic 校验** + retry(同 §3.5)
2. **`[N]` → DB id 映射**:复用 questions 表 source_chunk_ids 顺序,反向把 evidence 里的 `[N]` 还原成 DB id
3. **完整性校验**:
   - coverage_evidence.points 数 == reference_points 数,且 id 一一对应
   - fidelity_evidence.claims 至少 1 条
   - depth_evidence.dimensions 必须三个 key 齐全
4. **算分 + 落库**:三层分 + 总分写 `session_answers`,evidence JSONB 整体存

## 4.7 工具调用 — `lookup_in_notes_global`(反假阳性强化)

LESSONS §1.1 的假阳性 fabricated 是 v1 真实事故 — Reviewer 把候选人**真实**的教育经历标编造,因为它**只看到节点局部 chunks**,不知道用户在另一篇笔记里写过同样的内容。v2 给 AnswerJudge 加一个工具(走 DashScope `function_call` API),**强制**它在标 fabricated 前先全笔记库搜一遍。

### 工具定义

```python
@tool
def lookup_in_notes_global(claim: str, top_k: int = 3) -> list[ChunkRef]:
    """
    在用户**全部笔记库**(不限本题 chunks)做 hybrid search,
    返回 Top-K 最相关的 chunks 摘要。
    Judge 在标某条 claim 为 fabricated 前必须先调用此工具验证。
    """
```

输出:

```json
[
  {"chunk_id": 8231, "folder_path": ["Java","JVM"], "heading_path": ["类加载"], "snippet": "..."},
  {"chunk_id": 9012, "folder_path": ["Java","并发"], "heading_path": ["volatile"], "snippet": "..."},
  {"chunk_id": null}   // 没匹配上时单元素 list,chunk_id=null
]
```

### 调用约束(SYSTEM prompt 加在 §4.3 末尾)

```
【工具使用】

在 fidelity 评分时,**任何想标 fabricated 的 claim,必须先调用 lookup_in_notes_global(claim) 验证一次**。流程:

1. 看到一条声明,判断它在本题 chunks 里没支撑
2. 调用 lookup_in_notes_global(claim_text)
3. 如果工具返回的 chunks 里有支持该声明的内容(跟用户答的语义对得上)→ 标 supported,在 chunk_ids 里写工具返回的 chunk_id
4. 没匹配上 → 才能标 fabricated

不要为 supported / inferred 的 claim 调工具(浪费成本)。每个 user_answer 工具调用次数 ≤ 5(超过则后续可疑 claim 直接标 fabricated 不再调)。
```

### 落库 / Trace

工具调用 input / output 不存 DB,**只走 Langfuse trace**(2-TECH_DESIGN §6),按 session 维度可查。Cost 影响:Judge 调用变成 multi-turn(初次 prompt + 每个工具调用 round-trip),token 成本预期增加 30-60%(看用户答的 fabricated 候选数);LLM cache 仍生效(同 prompt + 同 tool result 命中)。

### 评测影响

6-EVAL_PLAN §3 跑 answer_judge suite 时**不禁工具**(评测就是要测真实行为)。dataset 里"用户讲常识 / 讲了别的笔记内容"的样本(11-15 那批)期望 Judge 调工具后正确降级 → 该批样本 Fidelity kappa 应当显著上升;若不升,排查工具调用是否真的发生(看 trace)。

### 失败处理

- 工具返回空 / 报错:Judge 视为"未命中",可以标 fabricated(不阻塞流程)
- Judge 不调工具就直接标 fabricated:**post-processing 校验**(service 层) — 检查 evidence 里 fabricated 的 claim 是否对应至少一次 trace 里的 lookup 调用;没对应 → 后处理强制重跑 Judge 一次。两次都不调 → 接受 Judge 输出但在 trace 打 warning(防 prompt regression 后续告警)

# 5. JdParser(单 JD 解析,M2.5)

JD 上传时**立即**调一次,把单条 JD 文本解析成结构化 parsed_payload。

## 5.1 输入 / 输出

```python
@dataclass
class JdParseInput:
    raw_text: str          # JD 原文(截图场景已经过 Qwen 多模态 OCR 转成文本)

@dataclass
class JdParseOutput:
    title: str             # 岗位名(从 JD 抽 + 兜底"未标注岗位")
    responsibilities: list[str]
    hard_skills: list[str]   # 保留原文短语,不在此阶段同义合并
    soft_skills: list[str]
    experience_years: str | None  # "3-5" / "应届" / null
    education: str | None         # "本科及以上" / null
    extras: dict[str, Any]        # 公司 / 薪资 / 地点等不强约束字段
```

## 5.2 SYSTEM prompt(`jd_parser` v1.0,要点)

```
你是 JD 解析 Agent。任务:把一条岗位 JD 文本拆成结构化字段。

【硬约束】
1. 只抽 JD 文本里**明确出现的内容**,不做"行业常识"补全(LESSONS §1.4 ProfileParser 经验)
2. hard_skills / soft_skills 保留原文短语(如"Redis 集群"不要扩成"Redis Cluster 集群模式"),便于后续聚合
3. responsibilities 用原文片段,可适当合并相邻句但不改写
4. OR 关系不要误抽成 AND(LESSONS §5.1 JDParser B1 教训) — JD 里"熟悉 X 或 Y"两条独立 entry,不是"X+Y 合一"
5. 平台标签 / IDE 名 / 学术名词不算 hard_skill(LESSONS §5.3 / §5.4)

【输出格式】严格 JSON, schema 见 3-DATA_MODEL §6.6
```

USER 模板:`JD 原文(可能含格式符 / OCR 残留):\n{raw_text}\n`

## 5.3 service 层后处理

- Pydantic 校验
- title 兜底:LLM 没抽到或 "" → 取 raw_text 前 50 字符 + "(未明确岗位)"
- 落 jds 表(parsed_payload + parse_model + cost)

# 6. JdAggregator(多 JD 一键分析,M2.5)

对累积型 JD 库做 **hierarchical map-reduce**(map 已在上传时完成,这里只跑 reduce + 二次合并 + Python 重算频次)。

## 6.1 输入 / 输出

```python
@dataclass
class JdAggregateInput:
    parsed_jds: list[JdParseOutput]    # 100+ 条 JD 的 parsed_payload(从 jds 表批量读)
    
@dataclass
class JdAggregateOutput:
    aggregated_requirements: list[Requirement]
    learning_path_md: str

@dataclass
class Requirement:
    id: str                            # "req_1"
    canonical_text: str
    category: Literal["硬技能","软技能","经验","学历"]
    raw_phrases: list[str]             # 同义词原文
    supporting_jd_ids: list[int]       # 哪几条 JD 的 raw 列表里命中过(同义匹配建立)
    frequency: float                   # Python 重算 = len(supporting_jd_ids) / len(parsed_jds)
```

## 6.2 三阶段流水线

### Stage 1:分批 reduce(LLM)

- 把所有 raw skills(`hard_skills + soft_skills`)聚合成长 list,跨 JD
- 分 batch(每 batch 500-600 项 raw skill)
- 每 batch 单独调 LLM 同义合并 → 输出 partial canonical list,每个 canonical 带 `raw_phrases` + 在该 batch 内见过的 JD index 集合

```
LLM prompt 要点:
- 输入:raw skill 列表(每项跟上 jd_index 元数据)
- 任务:同义合并,输出 canonical 列表 + 每 canonical 的 raw 来源
- 不算频次(频次 Python 算)
- thinking off(同义判断 Qwen 中文常识强,不需 reasoning)
- temperature 0.3
```

### Stage 2:二次 reduce / merge(LLM)

- 把 N 个 batch 的 partial canonical list 喂给 LLM,让它跨 batch 同义合并
- 输入是 canonical 列表(已经短了,可能 200-500 项),token 安全
- 输出:全局 canonical list + 每 canonical 的 raw_phrases 总集 + supporting_jd_ids 全集

```
LLM prompt 要点:
- 输入:N 个 partial canonical list
- 任务:跨 batch 同义合并,输出 unified canonical 列表
- 跨 batch 同义 e.g. "Redis 集群" (batch 1) + "Redis cluster" (batch 3) 应合一
```

### Stage 3:Python 重算频次

```python
# 不信 LLM 算术,自己 group by canonical 数 supporting jd
for req in unified_canonicals:
    req.supporting_jd_ids = list(set(req.supporting_jd_ids))   # 去重
    req.frequency = round(len(req.supporting_jd_ids) / len(parsed_jds), 3)
sort_by_frequency_desc()
```

### Stage 4:学习路径生成(LLM)

把 Python 算好的 requirement 列表喂 LLM,直接生成 markdown 学习路径:

```
LLM prompt 要点:
- 输入:requirement 列表(已按频次降序)
- 任务:按"高频(≥80%)/ 中频(50-80%)/ 软要求"分组,markdown 输出
- 强约束:不在 markdown 里编造"建议 X 资源 / Y 教程"等具体推荐(只复述 + 排序 + 分组)
- thinking off, temperature 0.5
```

## 6.3 Map-Reduce 拓扑(单次上限 200 条)

```
≤ 200 条 JD parsed_payload(已从 DB 读)
   ↓ 抽 raw_skills(平均 30/条 → 6000 项)
   ↓ 分批 (每 batch 600 项 → 10 batch)
   ↓ 10 次并发 LLM batch reduce
   ↓ 10 个 partial canonical list(每个 ~50-100 项)
   ↓ 1 次 LLM 二次 merge(输入 ~500-1000 canonical 项)
   ↓ Python 重算频次 + 排序
   ↓ 1 次 LLM 学习路径生成
   = 总 LLM 调用数 ≈ 12 次,P95 ≤ 60s
```

## 6.4 service 层后处理

- 严格 schema 校验(每个 canonical 都要有 supporting_jd_ids ≥ 1)
- 频次 Python 重算(LLM 给的频次字段忽略)
- 落 jd_analyses 表 + emit SSE result

# 7. ResumeAdvisor(简历诊断,M3)

诊断 JD 通用要求 vs 用户简历的覆盖度 + 给出"该补什么主题"建议。**两方锚点严格,永不输出改写文案**。

## 7.1 输入 / 输出

```python
@dataclass
class ResumeAdvisorInput:
    requirements: list[Requirement]   # 来自 jd_analyses.aggregated_requirements
    resume_chunks: list[ResumeChunk]  # 来自 resumes.parsed_chunks

@dataclass
class ResumeAdvisorOutput:
    suggestions: list[ResumeSuggestion]  # schema 见 3-DATA_MODEL §6.10
```

## 7.2 SYSTEM prompt(`resume_advisor` v1.0,要点)

```
你是简历诊断 Agent。任务:对照 JD 通用要求清单 + 用户简历段落,逐条诊断覆盖度。

【硬约束】

1. **永远不输出改写文案**:suggestion_topic 字段只描述"该补什么主题"(如"在项目经历中补一段跟 Redis 集群相关的实战");**禁止写"建议改写为 'XXX'"**这类替用户编经验的文字
2. **两方锚点严格**:
   - 每条 requirement 必须给 resume_position(简历段落 §N 标号)或 null
   - 不要凭空挂位置:简历里搜不到对应内容时填 null,不要乱挂段落
3. **覆盖度判定**:
   - strong:简历明确体现该要求(项目经历或技能列表里有具体描述)
   - weak:简历提到该主题词但缺细节 / 缺实战
   - missing:简历完全没体现
4. **不替用户判断价值**:不要写"这个要求对你不重要"或"建议跳过";只陈述事实

【输出格式】严格 JSON, schema 见 3-DATA_MODEL §6.10
```

USER 模板:requirements 列表(canonical_text + frequency + raw_phrases)+ resume parsed_chunks 列表(position + type + content)逐项编号传入。

## 7.3 service 层后处理(锚点校验)

```python
for s in suggestions:
    if s.req_id and s.resume_position:
        s.tag = "anchored"
    else:
        s.tag = "unanchored"
        # unanchored 的 suggestion_topic 强制清空(LLM 没锚点还给建议=废话)
        if s.tag == "unanchored":
            s.suggestion_topic = None
```

**硬性 prompt 漏洞检测**(防 LLM 越界写文案):

```python
FORBIDDEN_PATTERNS = [
    r'建议改写为',
    r'可以这样写[::]',
    r'^\s*"[^"]+"\s*$',   # 整段被引号包裹的"文案"
    # ... dogfood 中持续累积
]
for s in suggestions:
    if s.suggestion_topic and any(re.search(p, s.suggestion_topic) for p in FORBIDDEN_PATTERNS):
        # 模式越界 → 强制 retry Judge 一次,prompt 加更狠的反向警告
        # 仍越界 → 把 suggestion_topic 截断到第一个换行,trace 打 warning
        ...
```

## 7.4 anchored ratio 守门(M3 DoD)

`anchored_count / (anchored_count + unanchored_count) ≥ 0.7` — 不达标说明 prompt / chunker 有问题(LLM 找不到 resume_position 锚点的比例过高),触发 prompt 改版。

# 8. M2.1:InterviewCoachAgent(Agentic RAG 编排)

M2.1 把 M2 的 RAG 出题闭环升级成 **Agentic RAG 面试教练**。设计目标不是堆多个 Agent,而是让一次 session 具备:

- **状态**:跨多题 / 多轮纠偏保存上下文
- **工具**:可查笔记、查 source chunks、记录 session、后续接入 knowledge_gap
- **分支**:根据 Coverage / Fidelity / Depth evidence 决定提示哪里答不好、继续补答或进入下一题
- **记忆**:M3 接入 SR 后把薄弱 heading_path 写入长期弱点
- **评测**:流程型样本可复现追问 / 不追问 / fabricated / 恢复
- **可恢复**:用户退出后能从 `wait_user_answer` 继续

当前 M2.1 第一版编排落在 `apps/api/src/jobcopilot_api/services/interview_service.py`,复用 QuizGenerator / AnswerJudge,不重写两者 prompt。后续如果引入 LangGraph 或独立 `agents/interview_coach/` 包,也必须保持这里的状态机语义和事件契约不变。

## 8.1 Harness Engineering 边界

M2.1 的目标不是做一个"更会聊天的 prompt",而是做 **interview coaching harness engineering**:把 LLM 放进面试陪练专用运行框架里,由系统负责状态、工具、证据、分支、恢复、回放和评测。

对标 Codex / Claude Code 时,不要照搬 coding harness 的文件系统 / shell / git 能力;JobCopilot 的垂直 harness 是:

```
笔记 RAG
→ evidence-bound 出题
→ 用户答题暂停点
→ AnswerJudge 结构化评分
→ deterministic decision policy
→ remediation loop
→ context pack / session_events / recovery
→ eval replay / Langfuse trace
```

Harness 边界:

- **LLM 不是自由面试官**:它只在明确节点里做局部生成 / 判断,不能自己规划长任务、改写状态机或绕过证据。
- **长期记忆不靠聊天历史**:`session_events` 保存原始事件,`InterviewCoachState` 保存结构化状态,LLM 当前输入只拿 context pack。
- **关键分支由程序治理**:`coverage_score`、`fabricated_ratio`、depth 维度、退出条件由 deterministic policy 决定,LLM 只能给 evidence / reasoning 辅助。
- **工具不是开放市场**:每个 tool 必须对应面试闭环里的确定动作,并有输入 / 输出 schema、错误码、trace。
- **输出必须可审计**:题目、评分、纠偏 prompt、session summary 都要能追到 source chunks / reference points / Judge gaps。
- **成功标准不是"聊得像"**:而是可恢复、可回放、可评测、可观测,并能在 fixture 中稳定走到人工期望分支。

一句话:Prompt engineering 负责局部语言质量;M2.1 的 harness engineering 负责让这个面试教练在真实 session 里可靠做事。

## 8.2 State / Nodes

State:

```python
@dataclass
class InterviewCoachState:
    session_id: int
    query: str
    query_type: Literal["topic", "job", "auto"]
    expanded_queries: list[str]
    retrieved_chunk_ids: list[int]
    questions: list[GeneratedQuestion]
    current_question_index: int
    user_answers: list[str]              # 每题累计答案:初答 + 后续补答合并
    answer_turns: list[AnswerTurn]       # 每轮原始答案 / 补答,用于回放
    judge_evidences: list[AnswerJudgeOutput]
    remediation_events: list[RemediationEvent]
    unresolved_gaps: list[AnswerGap]
    question_summaries: list[QuestionContextSummary]
    next_action: Literal["ask_next", "remediate", "summarize", "finish"]
    final_summary: str | None
```

Nodes:

```
retrieve_context
  → generate_question
  → wait_user_answer
  → build_context_pack
  → judge_answer
  → decide_next_action
      ├ generate_remediation_prompt → wait_user_answer → build_context_pack → judge_answer → decide_next_action
      ├ generate_question(next question)
      └ summarize_session → finish_session
```

当前已落地节点:

- `wait_user_answer → build_context_pack → judge_answer → decide_next_action`:由 `POST /api/quiz/sessions/{id}/answers/{order_index}/turns` 推进;`turn_type=auto` 会先分流为初答 / 补答 / 追问教练。初答和补答追加到 `answer_turns`,并用累计答案重新跑 AnswerJudge。
- `coach_question → coach_chat`:用户追问教练反馈时走独立解释链路,只写 `session_events`,不改 `user_answer`,不重评,不推进题目状态。前端可以保持单输入框;状态边界仍由后端分流维护。
- `summarize_session → finish_session`:由 `POST /api/quiz/sessions/{id}/finish` 推进;只汇总已经完成的每题最新 Judge 结果,不重新评分。结果写入 `agent_state.final_summary / question_summaries / summary_context_pack`,并追加 `session_summarized / session_finished` 事件。

## 8.3 工具边界

| Tool | 用途 | M2.1 状态 |
|------|------|----------|
| `search_notes(query)` | 主题检索,底层复用 retrieval pipeline | 必做 |
| `lookup_claim_in_notes(claim)` | Judge 标 fabricated 前验证 | 必做(复用 §4.7) |
| `get_source_chunks(question_id)` | 展开题目引用,供追问 / UI 对照 | 必做 |
| `record_session_summary(session_id)` | 写 `notes/_recall/{session_id}.md` | 待接;当前先把 markdown 放在 `agent_state.final_summary.markdown`,并写逻辑 `recall_md_path` |
| `update_knowledge_gap(...)` | 写弱点与 SR 队列 | M3 接入,M2.1 只留接口 |

工具不是给 LLM 任意调用的开放工具市场,每个 tool 都对应产品闭环里的明确动作。

## 8.4 多轮纠偏循环策略

M2.1 不再把追问限制成"单题最多 1 轮"。`InterviewCoachAgent` 的核心能力是 **remediation loop**:

```
用户答题
→ Judge 评分
→ decide_next_action 判断哪里没答好
→ 明确提示问题 + 生成针对性追问 / 引导
→ 用户补答
→ 对累计答案重新 Judge
→ 再判断继续纠偏、进入下一题或总结
```

`decide_next_action` 只做有限状态决策,不让 LLM 自主开放规划:

- `coverage_score < 60` → 追问漏掉的 reference point
- `fabricated_ratio > 0.3` → 追问"依据来自哪里",把用户拉回笔记证据
- depth 缺 `tradeoff / why / boundary` 任一维度 → 深挖一轮
- 否则进入下一题或总结

补答后的 Judge 输入必须是**累计答案**:`初答 + 历次补答`,同时保留每轮原文用于回放和可观测。这样用户补齐内容后分数可以真实变好,不会只评最后一句补答。

停止条件:

- 用户显式选择"跳过 / 下一题 / 结束本场"
- 当前题达到目标,例如 `coverage_score >= 80`、`fabricated_ratio <= 0.1` 且 depth 不再缺核心维度
- 连续多轮提升很小:当前 v0 实现要求答案轮次 `>= 3`,最近两次 `total_score` 增量都 `< 5`,且 unresolved gap keys 没有变化,则 `exit_reason=no_meaningful_improvement`
- 用户明显偏题,改为总结当前题缺口并建议进入下一题
- context pack 超过 token budget,先压缩历史轮次;当前 v0 用答案轮次数近似预算,`>= 3` 轮生成 `prior_turn_summary`,`>= 6` 轮仍需纠偏则 `exit_reason=token_budget`

因此删除"单题最多 1 轮"限制,但仍禁止无退出条件的自主循环。

每次 `decide_next_action` 会把最近 Judge 分数写入 `SessionAnswer.remediation_state.judge_score_history`,只保留最近 8 条。这个 history 是无明显提升判断和 finish summary 的结构化输入,不依赖重新读聊天文本猜状态。

## 8.5 长上下文与幻觉治理

多轮模拟面试不能靠"把全量聊天历史都塞进 prompt"解决。原始对话全量落库用于回放;每次 LLM 调用只构造当前节点需要的 **context pack**:

```python
@dataclass
class InterviewContextPack:
    question: GeneratedQuestion
    source_chunks: list[SourceChunk]
    reference_points: list[ReferencePoint]
    cumulative_answer: str
    latest_judge_evidence: AnswerJudgeOutput | None
    unresolved_gaps: list[AnswerGap]
    recent_turns: list[AnswerTurn]        # 最近 1-2 轮原文
    prior_turn_summary: str | None        # 更早轮次压缩摘要
```

Token budget 优先级:

1. 不能丢:当前题、source chunks、reference_points、unresolved_gaps
2. 尽量保留:累计答案、上一轮 Judge evidence、最近 1-2 轮原文
3. 可压缩:更早答题轮次、面试官提示、UI 事件
4. 可丢弃:寒暄、重复确认、与当前题无关的聊天

Context Cache / prompt caching 只用于降低重复长前缀成本,不是会话记忆。真正的记忆是 `InterviewCoachState + session_events + per-question summary`。

当前 v0 的 compaction 是 deterministic,不调用 LLM:达到阈值后从已落库的 `answer_turns`、`judge_score_history`、`unresolved_gaps` 拼出 `prior_turn_summary`,只把最近 1-2 轮原文留给当前节点。落库事件为 `context_compacted`,随后 `context_pack_built` 带 `compacted / prior_turn_summary / token_budget_exhausted`。

追问 / 纠偏也必须 evidence-bound:

- 每个 `RemediationEvent` 必须记录 `triggered_by: coverage | fidelity | depth | mixed`
- coverage 追问必须带 `missing_reference_point_ids`
- fidelity 追问必须带 `fabricated_claim_ids` 和 lookup 结果
- depth 追问必须声明缺的是 `tradeoff / why / boundary` 哪些维度
- `generate_remediation_prompt` 只能围绕当前题 source chunks、reference_points、Judge gaps 生成,不能引入新标准答案来源
- Judge 在标 fabricated 前继续走 `lookup_claim_in_notes`
- 保存前校验 followup / remediation prompt 里的 chunk id、reference point id 不越界

## 8.6 不做的"伪高级 Agent"

明确不做:

- `PlannerAgent / ResearcherAgent / CriticAgent / ExecutorAgent` 泛化多 Agent 互聊
- 自主浏览网页、自动改笔记、自动写简历文案、自动投递
- 与面试陪练无关的开放式长任务规划

判断标准:如果一个 Agent / tool 不能改善"找出用户不会的知识点并追问",就不进 M2.1。

## 8.7 评测与可观测

- Langfuse trace 根节点按 `session_id` 聚合,完整链路:`retrieve_context → generate_question → wait_user_answer → build_context_pack → judge_answer → decide_next_action → generate_remediation_prompt? → summarize_session`
- `evals/suites/interview_coach/` 收流程型样本;当前已有 `dataset.flow_smoke.jsonl` 10 条最小 fixture,`apps/api/scripts/eval_interview_coach.py` 已接入离线 runner。最近一次 `2026-05-17` 跑通 10/10,各聚合指标为 1.000。至少覆盖:
  - 答得好 → 不追问
  - 漏 reference point → 提示缺口 → 补答后累计答案重评
  - fabricated ratio 高 → 依据追问
  - depth 缺 trade-off / why / boundary → 深挖纠偏
  - 多轮后无明显提升 → 总结当前题缺口,不无限循环
  - 用户中途退出 → 从 `wait_user_answer` 恢复
- 指标先用流程准确率(是否走到人工期望分支)、纠偏成功率(补答后是否达标或正确退出)、上下文治理通过率,不直接用 kappa;Judge 的 label 可靠性仍由 `answer_judge` suite 守门,自然语言 `turn_type=auto` 分类质量不放进本 suite。

# 9. v1 教训如何应用

| LESSONS § | 风险 | v2 应用 |
|-----------|------|--------|
| §1.1 假阳性 fabricate | Reviewer 把真实经历标编造 | AnswerJudge §4.3 硬约束 #2:常识标 inferred 不标 fabricated;6-EVAL_PLAN dataset 里专门有"用户讲了 chunks 没明说的常识"样本守门 |
| §1.3 鼓励性文案诱导 | "请尽量挑战" → 题难到超 chunks | §3.3 全反向警告语;不写"出有挑战的题"/"严格评分"等鼓励性指令 |
| §7.1 评委即被评者 | LLM 自评 LLM 偏高 +5-10pp | **不适用**:v2 评委是 LLM,被评者是人类答题文本;无自评关系。kappa 仍守门(测 Judge vs 人类标注一致性),不达标改 prompt 不切模型 |
| §8.2 Prompt 是产品代码 | 没版本号 → 无法回退 / ablation | §2.3 prompt 落 `prompt_versions` 表,改一次 bump version,questions / session_answers 留旧 version 字段 |
| §8.3 信任输入差异化 | 一刀切 retrieval | QuizGenerator 吃节点 prefix 全量 chunks(完整性);AnswerJudge 只吃 source_chunk_ids 那批(相关性聚焦) |
| §8.4 动态 user message 是权威指令位 | 鼓励性文案 = 命令 | SYSTEM 写硬约束,user message 只放任务数据(chunks / 题 / 用户答),不放 hint |
| §1.2 Drafter 镜像 JD | 把候选人没有的技能抄进简历 | ResumeAdvisor §7.2 硬约束 #1 + service 层 FORBIDDEN_PATTERNS 拦截"建议改写为 X"句式 |
| §1.4 ProfileParser description 幻觉 | 从 bullets[0] 改写复述 | JdParser §5.2 硬约束 #1:只抽 JD 文本明确出现的内容,不"行业常识"补全 |
| §5.1 JDParser OR 误抽 AND | "熟悉 X 或 Y" 误抽成"X+Y 合一" | JdParser §5.2 硬约束 #4 显式警告 |
| §5.3-5.4 JDParser 杂项污染 | 平台标签 / IDE / 学术名混入 hard_skills | JdParser §5.2 硬约束 #5 显式警告 |

# 10. 已锁定的关键决策

| 项 | 决策 | 备注 |
|----|------|------|
| 模型 | qwen3.6-flash thinking on,Quiz / Judge 同模型,全程不变 | 评委是 LLM、被评者是人,v1 §7.1 自评偏差不适用 |
| Judge kappa 守门 | M2 DoD `κ ≥ 0.7`;不达标改 prompt(bump version)+ 重跑历史评测,**不切模型** | 守 Judge 输出可靠性,跟自评无关 |
| Quiz chunks 数 | M2 起 chunks 由 retrieval pipeline 产出(top 10 + parent-doc 扩展);0 命中守门由 pipeline 负责,QuizGenerator 不再自判 | retrieval pipeline 端阈值见 PRD Q-10;sourceChunks 上限 ~20 落 qwen3.6-flash 大 context 安全区 |
| QuizGenerator 输入 | **query + retrieved chunks**(含 heading_path / note_title 元数据);M2 出题入口由"节点点击"改为"聊天框 query" | 见 §3.1 + 2-TECH §5.2 数据流 |
| QueryRewriter | M2 retrieval pipeline 第一段;扩 ≤5 项,首项必为原 query;失败回退原 query 不阻塞 | 见 §2.7 |
| Reranker | cross-encoder(本地 / 百炼 reranker 接口),非 LLM 调用,不进 prompt_versions | 见 §2.7.5 + PRD Q-07 |
| query 形态 | M2 仅主题类;M3 加岗位类(三源融合)+ 空 query(SR 系统自选) | 见 PRD §5.2 + 7-ROADMAP M3 |
| Agent 编排深度 | M2.1 上 `InterviewCoachAgent` 状态机;高级感来自状态 / 工具 / 分支 / 记忆 / 评测 / 恢复,不做泛化多 Agent 互聊 | 见 §8 |
| 简历存储 | 单条记录(全库一行 resumes),无"简历库 / 多份切换";岗位类 query 拼"这一份 + 选定 JD 子集" | 一个人就一份简历;PRD §6 锁定 |
| M2.1 单输入框 | 前端只有一个发送按钮;后端 `turn_type=auto` 分流初答 / 补答 / 追问教练 | UX 简化不改变状态边界;追问教练不进入 `user_answer` 评分 |
| 多轮纠偏触发(M2.1) | `coverage_score < 60` / `fabricated_ratio > 0.3` / depth 缺维度 任一触发,进入 remediation loop | 不设单题固定 1 轮上限;靠达标、用户跳过、无明显提升、偏题、token budget 等条件退出 |
| 多轮上下文治理(M2.1) | 原始对话落库回放,LLM 每次只拿 context pack | source chunks / reference_points / unresolved_gaps 优先级最高;Context Cache 不是记忆 |
| Temperature | DashScope 默认,不暴露 | prompt 端约束逼近低温度 |
| Prompt 版本号 | DB `prompt_versions` 表,改一次 bump | questions / session_answers 留旧 version |
| LLM cache | `(prompt_version, system_hash, user_hash, model)` | dogfood 重跑成本 ≈ 0 |
| chunk 编号 | 上下文 prefix 用 `[N]`(1-based),service 层映射 DB id | LLM 友好 + 数据库无关 |
| 反幻觉 | 反向警告,不写鼓励语 | 沿用 §1.3 / §8.4 |
| 算分位置 | Python 算,LLM 只给 label + 自评 score_raw | LLM 算术不可信;权重 SSoT 在代码 |
| Judge JSON schema | 严格,Pydantic 校验,retry ≤ 1 | 失败抛 `llm_call_failed` |
| 题型比例 | LLM 自动决策(看 chunks 内容);默认偏 6:4 open_ended | 4-API_SPEC §4.1 决定 |
| reference_points 数 | 每题 2-5 个,weight 之和 = 1.0 | 太多碎片;太少粒度粗 |
| Coverage label | `hit` / `partial` / `miss` (1.0 / 0.5 / 0.0) | 评分函数 SSoT |
| Fidelity label | `supported` / `inferred` / `fabricated`;fabricated > 30% 锁顶 50 | 防 hallucination |
| Depth 维度 | tradeoff / why / boundary 三个二值 | 简单粗暴,均权 |
| Judge tool use | `lookup_in_notes_global(claim, top_k=3)` 工具,fabricated 前必调 | 直接对应 LESSONS §1.1 假阳性;搜全笔记库不限本节点 |
| Tool 调用次数上限 | 单 user_answer ≤ 5 次 | 防 Judge 滥调;超过直接标 fabricated |
| Tool 落地形式 | 走 DashScope function_call API | LLM cache 仍生效(同 prompt + 同 tool result 命中) |
| Tool 失败处理 | 工具报错 = 未命中 = 可标 fabricated;Judge 不调工具就标 fabricated → service 层强制重跑 1 次 | 防 prompt regression |
| LLM SDK | OpenAI Python SDK 走百炼 OpenAI 兼容接口 | `from langfuse.openai import OpenAI` 自动 instrument;详见 reference memory |
| thinking 默认 off | 按 agent 显式开 | §2.1 决策表;省成本和延迟 |
| Temperature 显式传 | 各 agent 调用处显式传 0.2-0.5 | 不依赖模型默认值(易踩坑) |
| JD 解析时机 | **上传即解析**(M2.5 US-15) | parsed_payload 持久化,后续一键分析 reduce 复用 |
| JD 一键分析 | 三阶段 hierarchical reduce(分批 → 二次 merge → Python 重算频次) | 单次上限 200 条;M3+ 才考虑跨批增量 |
| 频次重算位置 | Python(SSoT)| LLM 不算 — 同 §4.5 算分原则 |
| 简历诊断锚点 | 两方严格(req_id + resume_position 双非空 = anchored)| anchored ratio ≥ 0.7 守门 |
| 简历改写文案 | **永不输出**(prompt 硬约束 + service 层 forbidden_patterns 拦截) | 直接撞 v1 失败模式;只描述"该补什么主题" |

---

# 不在本文档范围

- 表 schema(reference_points / coverage_evidence 等 JSONB) → `docs/3-DATA_MODEL.md` §6
- API 端点 / SSE 事件 → `docs/4-API_SPEC.md`
- evals/suites/{quiz_generator, answer_judge}/ dataset / kappa 算法实现 → `docs/6-EVAL_PLAN.md`
- service 层 / LangGraph 编排落地 → `docs/2-TECH_DESIGN.md`
- 仓库目录 / prompt_versions 表怎么填 → `docs/8-ENGINEERING.md`
