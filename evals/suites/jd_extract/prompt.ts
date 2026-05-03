/**
 * promptfoo prompt loader for JDParser v1.0.1.
 *
 * Reads `apps/api/src/jobcopilot_api/prompts/jd_parser/v1.0.1.j2`, splits
 * SYSTEM / USER blocks (mirrors `apps/api/src/jobcopilot_api/infra/prompts.py`
 * regex), renders `{{ jd_text }}` placeholder, then appends the
 * JDStructured JSON schema to the user message — mirrors production
 * `LLMClient._augment_with_schema` (DashScope OpenAI compat does not
 * accept response_format=json_schema, so the schema rides in-prompt).
 *
 * The schema JSON is checked into the repo under `jd_structured.schema.json`
 * (regen via:  uv run --package jobcopilot-api python -c
 *   "from jobcopilot_api.schemas.jds import JDStructured;
 *    import json; print(json.dumps(JDStructured.model_json_schema(),
 *    ensure_ascii=False, indent=2))"  > suites/jd_extract/jd_structured.schema.json
 * ).
 *
 * Why not let promptfoo's built-in Nunjucks render the .j2 directly:
 * the file embeds `## SYSTEM` / `## USER` section markers that the
 * Python loader treats as structural — Nunjucks would emit them as
 * literal text and the model would see two role-labelled headings inside
 * a single user message.
 */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const TEMPLATE_PATH = resolve(
  __dirname,
  '../../../apps/api/src/jobcopilot_api/prompts/jd_parser/v1.0.1.j2',
);
const SCHEMA_PATH = resolve(__dirname, './jd_structured.schema.json');

const SYSTEM_RE = /^##\s*SYSTEM\s*$/m;
const USER_RE = /^##\s*USER\s*$/m;

function parseTemplate(content: string): { system: string; user: string } {
  const sysM = content.match(SYSTEM_RE);
  const userM = content.match(USER_RE);
  if (!sysM || sysM.index === undefined) throw new Error('template missing `## SYSTEM` header');
  if (!userM || userM.index === undefined) throw new Error('template missing `## USER` header');
  if (sysM.index >= userM.index) throw new Error('`## SYSTEM` must appear before `## USER`');
  const system = content.slice(sysM.index + sysM[0].length, userM.index).trim();
  const user = content.slice(userM.index + userM[0].length).trim();
  if (!system) throw new Error('`## SYSTEM` block is empty');
  if (!user) throw new Error('`## USER` block is empty');
  return { system, user };
}

const RAW = readFileSync(TEMPLATE_PATH, 'utf-8');
const { system: SYSTEM, user: USER_TPL } = parseTemplate(RAW);
const SCHEMA_JSON = readFileSync(SCHEMA_PATH, 'utf-8').trim();

interface Vars {
  jd_text: string;
  expected?: unknown;
}

export default function buildPrompt({ vars }: { vars: Vars }): {
  role: 'system' | 'user';
  content: string;
}[] {
  const userRendered = USER_TPL.replace(/\{\{\s*jd_text\s*\}\}/g, vars.jd_text);
  // Mirror LLMClient._augment_with_schema: append schema to user message
  const userWithSchema = `${userRendered}\n\nRespond with a single JSON object that matches this schema:\n${SCHEMA_JSON}`;
  return [
    { role: 'system', content: SYSTEM },
    { role: 'user', content: userWithSchema },
  ];
}
