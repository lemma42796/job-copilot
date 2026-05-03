/**
 * profile_extract 断言。MVP 4 个指标(EVAL_PLAN §4.2 子集 + chunker §4.3):
 *   - schemaValid          ≥ 0.95(Pydantic 必填类型校验,等价 jd salaryMatch 兜底)
 *   - experienceRecall     ≥ 0.90(每段经历的 (company, title) 都被抽到)
 *   - skillF1              ≥ 0.85(归一化 name 集合 F1)
 *   - chunkRecall          ≥ 0.90(expected chunk_queries 在 chunker content 中子串命中)
 *
 * chunkRecall 在 S10 阶段用纯文本子串包含降级,**不跑真 embedding**。理由:
 * baseline 阶段没有跑着的 PG + embedding service;真 retrieval 留给 M2 接 EVAL_PLAN
 * §4.3 的 pgvector_search。子串版仍能验证"chunker 把 expected entity 编进了 chunk
 * content 而不是塞 metadata"——chunker 漏字段直接零分。
 *
 * 阈值在 promptfooconfig.yaml suite 级 metric 上设(M2 ratchet);单 case 返回
 * score ∈ [0,1] 让 promptfoo 算均值。
 */

interface ExperienceExpected {
  company: string;
  title: string;
}

interface SkillExpected {
  name: string;
}

interface ProfileExpected {
  full_name?: string | null;
  experiences?: ExperienceExpected[];
  skills?: SkillExpected[];
  /** 关键 entity 列表,断言"chunker 输出至少一个 chunk 子串包含此 query"。
   *  通常含 expected role(公司+职位组合)+ key skill;每条 case 标 3-6 条。 */
  chunk_queries?: string[];
}

interface AssertContext {
  vars: { resume_text: string; expected: ProfileExpected };
}

interface AssertResult {
  pass: boolean;
  score: number;
  reason: string;
}

function safeJson(output: string): Record<string, unknown> | null {
  try {
    return JSON.parse(output);
  } catch {
    return null;
  }
}

function norm(s: unknown): string {
  return String(s ?? '')
    .toLowerCase()
    .trim();
}

/**
 * Schema validity:JSON.parse + 必填字段形态正确(experiences/projects/skills/educations
 * 是 array;full_name 是 string|null)。Pydantic 实际还会校 PartialDate 等,但 JS 端
 * 不复刻 Pydantic;能 JSON.parse + 顶层 array 字段对就给 1。失败原因细分,便于诊断。
 */
export function schemaValid(output: string): AssertResult {
  const parsed = safeJson(output);
  if (!parsed) return { pass: false, score: 0, reason: 'schema_invalid: not JSON' };

  const arrays: (keyof typeof parsed)[] = ['experiences', 'projects', 'skills', 'educations'];
  for (const k of arrays) {
    const v = parsed[k];
    if (v !== undefined && !Array.isArray(v)) {
      return { pass: false, score: 0, reason: `schema_invalid: ${k} not array` };
    }
  }
  const strs: (keyof typeof parsed)[] = ['full_name', 'phone', 'email', 'location', 'summary'];
  for (const k of strs) {
    const v = parsed[k];
    if (v !== undefined && v !== null && typeof v !== 'string') {
      return { pass: false, score: 0, reason: `schema_invalid: ${k} not string|null` };
    }
  }
  return { pass: true, score: 1, reason: 'ok' };
}

/**
 * Experience recall:每条 expected.experiences 是否在 LLM 输出里有"同公司同职位"
 * 一段(归一化后子串包含,允许 LLM 抽出更长/更短的版本)。Recall 形式,不算 precision
 * (LLM 多抽一段实习不扣分,baseline 阶段重点是别漏)。
 */
export function experienceRecall(output: string, ctx: AssertContext): AssertResult {
  const parsed = safeJson(output);
  if (!parsed) return { pass: false, score: 0, reason: 'schema_invalid: not JSON' };

  const want = ctx.vars.expected.experiences ?? [];
  if (want.length === 0) return { pass: true, score: 1, reason: 'no expected experiences' };

  const got = Array.isArray(parsed.experiences) ? (parsed.experiences as unknown[]) : [];
  const gotPairs = got.map((e) => {
    const obj = e as { company?: unknown; title?: unknown };
    return { company: norm(obj.company), title: norm(obj.title) };
  });

  let hit = 0;
  const missed: string[] = [];
  for (const w of want) {
    const wc = norm(w.company);
    const wt = norm(w.title);
    const found = gotPairs.some(
      (g) => (g.company.includes(wc) || wc.includes(g.company)) && (g.title.includes(wt) || wt.includes(g.title)),
    );
    if (found) hit += 1;
    else missed.push(`${w.company} / ${w.title}`);
  }
  const recall = hit / want.length;
  const pass = recall >= 0.9;
  return {
    pass,
    score: recall,
    reason: pass
      ? `recall=${recall.toFixed(3)} (${hit}/${want.length})`
      : `low_recall=${recall.toFixed(3)} missed=${missed.join('; ') || '∅'}`,
  };
}

/**
 * Skill F1。归一化 lowercase + trim,集合比较。空 vs 空 = 1。
 */
export function skillF1(output: string, ctx: AssertContext): AssertResult {
  const parsed = safeJson(output);
  if (!parsed) return { pass: false, score: 0, reason: 'schema_invalid: not JSON' };

  const gotSkills = Array.isArray(parsed.skills) ? parsed.skills : [];
  const got = new Set(
    gotSkills.map((s: unknown) => norm((s as { name?: unknown })?.name)).filter(Boolean),
  );
  const want = new Set((ctx.vars.expected.skills ?? []).map((s) => norm(s.name)).filter(Boolean));

  if (got.size === 0 && want.size === 0) {
    return { pass: true, score: 1, reason: 'both empty' };
  }

  const intersect = [...got].filter((s) => want.has(s)).length;
  const precision = got.size === 0 ? 0 : intersect / got.size;
  const recall = want.size === 0 ? 0 : intersect / want.size;
  const f1 = precision + recall === 0 ? 0 : (2 * precision * recall) / (precision + recall);
  const pass = f1 >= 0.85;
  return {
    pass,
    score: f1,
    reason: pass
      ? `f1=${f1.toFixed(3)}`
      : `low_f1=${f1.toFixed(3)} (P=${precision.toFixed(2)} R=${recall.toFixed(2)}); ` +
        `extra=${[...got].filter((s) => !want.has(s)).join(',') || '∅'}; ` +
        `missed=${[...want].filter((s) => !got.has(s)).join(',') || '∅'}`,
  };
}

// ---------------------------------------------------------------------------
// Chunker JS 端复刻(对照 apps/api/src/jobcopilot_api/agents/chunker.py)
// ---------------------------------------------------------------------------

interface ParsedExperience {
  company?: string;
  title?: string;
  location?: string;
  description?: string;
  bullets?: string[];
  tech_stack?: string[];
  achievements?: string[];
}
interface ParsedProject {
  name?: string;
  role?: string;
  description?: string;
  bullets?: string[];
  tech_stack?: string[];
  achievements?: string[];
}
interface ParsedSkill {
  name?: string;
  category?: string;
  level?: string;
  years?: number;
}
interface ParsedProfile {
  summary?: string | null;
  experiences?: ParsedExperience[];
  projects?: ParsedProject[];
  skills?: ParsedSkill[];
}

function strList(items: unknown): string[] {
  if (!Array.isArray(items)) return [];
  return items
    .filter((x): x is string | number => x !== null && x !== undefined && String(x).trim() !== '')
    .map((x) => String(x));
}

function buildChunkContents(p: ParsedProfile): string[] {
  const chunks: string[] = [];

  const summary = (p.summary ?? '').trim();
  if (summary) chunks.push(`个人简介:${summary}`);

  for (const e of p.experiences ?? []) {
    const parts: string[] = [`公司:${e.company ?? ''}`, `职位:${e.title ?? ''}`];
    if (e.location) parts.push(`地点:${e.location}`);
    const tech = strList(e.tech_stack);
    if (tech.length) parts.push(`技术栈:${tech.join(', ')}`);
    parts.push(`描述:${e.description ?? ''}`);
    const bullets = strList(e.bullets);
    if (bullets.length) parts.push(`亮点:${bullets.join(' | ')}`);
    const ach = strList(e.achievements);
    if (ach.length) parts.push(`成就:${ach.join(' | ')}`);
    chunks.push(parts.join('\n'));
  }

  for (const proj of p.projects ?? []) {
    const parts: string[] = [`项目名:${proj.name ?? ''}`];
    if (proj.role) parts.push(`角色:${proj.role}`);
    const tech = strList(proj.tech_stack);
    if (tech.length) parts.push(`技术栈:${tech.join(', ')}`);
    parts.push(`描述:${proj.description ?? ''}`);
    const bullets = strList(proj.bullets);
    if (bullets.length) parts.push(`亮点:${bullets.join(' | ')}`);
    const ach = strList(proj.achievements);
    if (ach.length) parts.push(`成就:${ach.join(' | ')}`);
    chunks.push(parts.join('\n'));
  }

  for (const sk of p.skills ?? []) {
    const parts: string[] = [`技能:${sk.name ?? ''}`];
    if (sk.category) parts.push(`分类:${sk.category}`);
    if (sk.level) parts.push(`水平:${sk.level}`);
    if (typeof sk.years === 'number') parts.push(`年限:${sk.years} 年`);
    chunks.push(parts.join('\n'));
  }

  return chunks;
}

/**
 * Chunk recall:expected.chunk_queries 列出关键 entity(role/company/skill 子串),
 * 断言每条 query 在 chunker 输出的某个 chunk content 中子串命中。
 *
 * 用 chunker 输出做断言而非 LLM 原始字段,目的是验证"解析正确 + 切块正确"端到端
 * 链路——LLM 抽对了但 chunker 漏掉(比如把 entity 塞 metadata 不入 content)就零分。
 *
 * 真 embedding 召回(EVAL_PLAN §4.3 pgvector_search)留给 M2,届时这个降级断言被
 * 替换成"top-k 中 content 子串命中"。
 */
export function chunkRecall(output: string, ctx: AssertContext): AssertResult {
  const parsed = safeJson(output) as ParsedProfile | null;
  if (!parsed) return { pass: false, score: 0, reason: 'schema_invalid: not JSON' };

  const queries = ctx.vars.expected.chunk_queries ?? [];
  if (queries.length === 0) return { pass: true, score: 1, reason: 'no chunk queries' };

  const chunks = buildChunkContents(parsed).map(norm);
  let hit = 0;
  const missed: string[] = [];
  for (const q of queries) {
    const qn = norm(q);
    if (chunks.some((c) => c.includes(qn))) hit += 1;
    else missed.push(q);
  }
  const recall = hit / queries.length;
  const pass = recall >= 0.9;
  return {
    pass,
    score: recall,
    reason: pass
      ? `recall=${recall.toFixed(3)} (${hit}/${queries.length})`
      : `low_recall=${recall.toFixed(3)} missed=${missed.join('; ') || '∅'}`,
  };
}
