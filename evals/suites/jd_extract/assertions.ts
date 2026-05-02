/**
 * jd_extract 断言。MVP 3 个指标(EVAL_PLAN §3.2 初始列):
 *   - titleExact      ≥ 0.92
 *   - hardSkillF1     ≥ 0.85
 *   - salaryMatch     ≥ 0.85
 *
 * 阈值在 promptfooconfig.yaml 的 suite 级 metric 上设(单 case 不强制),
 * 单 case 返回 score ∈ [0,1] 让 promptfoo 算均值。
 */

interface JDSkillExpected {
  name: string;
}

interface JDExpected {
  title: string;
  hard_skills?: JDSkillExpected[];
  salary_min?: number | null;
  salary_max?: number | null;
}

interface AssertContext {
  vars: { jd_text: string; expected: JDExpected };
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

export function titleExact(output: string, ctx: AssertContext): AssertResult {
  const parsed = safeJson(output);
  if (!parsed) return { pass: false, score: 0, reason: 'schema_invalid: not JSON' };
  const got = String(parsed.title ?? '').trim();
  const want = ctx.vars.expected.title.trim();
  const pass = got === want;
  return {
    pass,
    score: pass ? 1 : 0,
    reason: pass ? 'match' : `wrong_title: got "${got}", want "${want}"`,
  };
}

/**
 * 硬技能 F1。归一化:lowercase + trim。集合比较。
 *
 * F1 = 2·P·R / (P+R),P=|got∩want|/|got|,R=|got∩want|/|want|。
 * 空集 vs 空集视为完全匹配(F1=1)。
 */
export function hardSkillF1(output: string, ctx: AssertContext): AssertResult {
  const parsed = safeJson(output);
  if (!parsed) return { pass: false, score: 0, reason: 'schema_invalid: not JSON' };

  const norm = (s: unknown): string =>
    String(s ?? '')
      .toLowerCase()
      .trim();

  const gotSkills = Array.isArray(parsed.hard_skills) ? parsed.hard_skills : [];
  const got = new Set(
    gotSkills.map((s: unknown) => norm((s as { name?: unknown })?.name)).filter(Boolean),
  );
  const want = new Set(
    (ctx.vars.expected.hard_skills ?? []).map((s) => norm(s.name)).filter(Boolean),
  );

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

/**
 * 薪资范围 ±10% 容差。expected 里 salary_min/max 为 null 时,允许模型输出 null
 * 或合理范围(只要不输出明显冲突的数字就给 1 分;此处简化为"want=null →
 * got=null 才 1 分,否则 0 分")。
 */
export function salaryMatch(output: string, ctx: AssertContext): AssertResult {
  const parsed = safeJson(output);
  if (!parsed) return { pass: false, score: 0, reason: 'schema_invalid: not JSON' };

  const wantMin = ctx.vars.expected.salary_min ?? null;
  const wantMax = ctx.vars.expected.salary_max ?? null;
  const gotMin = (parsed.salary_min as number | null | undefined) ?? null;
  const gotMax = (parsed.salary_max as number | null | undefined) ?? null;

  // 双 null:期望模糊薪资,模型识别出"未提供"才 1 分
  if (wantMin === null && wantMax === null) {
    const pass = gotMin === null && gotMax === null;
    return {
      pass,
      score: pass ? 1 : 0,
      reason: pass ? 'both null' : `wrong_salary: want null, got [${gotMin}, ${gotMax}]`,
    };
  }

  // 单边 null:简化处理 — 必须两端都有
  if (wantMin === null || wantMax === null || gotMin === null || gotMax === null) {
    return {
      pass: false,
      score: 0,
      reason: `wrong_salary: shape mismatch want [${wantMin}, ${wantMax}] got [${gotMin}, ${gotMax}]`,
    };
  }

  const within = (got: number, want: number): boolean => Math.abs(got - want) / want <= 0.1;
  const pass = within(gotMin, wantMin) && within(gotMax, wantMax);
  return {
    pass,
    score: pass ? 1 : 0,
    reason: pass
      ? 'match within ±10%'
      : `wrong_salary: want [${wantMin}, ${wantMax}], got [${gotMin}, ${gotMax}]`,
  };
}
