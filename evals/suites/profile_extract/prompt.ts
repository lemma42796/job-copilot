/**
 * promptfoo prompt loader for ProfileParser v1.0.0.
 *
 * Reads `apps/api/src/jobcopilot_api/prompts/profile_parser/v1.0.0.j2`,
 * splits SYSTEM / USER blocks (mirrors `apps/api/src/jobcopilot_api/
 * infra/prompts.py` regex), renders `{{ resume_text }}` placeholder, then
 * appends the ProfileStructured JSON schema to the user message — mirrors
 * production `LLMClient._augment_with_schema` (DashScope OpenAI compat does
 * not accept response_format=json_schema, so the schema rides in-prompt).
 *
 * Regen schema:  uv run --project apps/api python -c
 *   "from jobcopilot_api.schemas.profiles import ProfileStructured;
 *    import json; print(json.dumps(ProfileStructured.model_json_schema(),
 *    ensure_ascii=False, indent=2))"
 *   > suites/profile_extract/profile_structured.schema.json
 */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const TEMPLATE_PATH = resolve(
  __dirname,
  '../../../apps/api/src/jobcopilot_api/prompts/profile_parser/v1.0.0.j2',
);
const SCHEMA_PATH = resolve(__dirname, './profile_structured.schema.json');

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
  resume_text: string;
  expected?: unknown;
}

export default function buildPrompt({ vars }: { vars: Vars }): {
  role: 'system' | 'user';
  content: string;
}[] {
  const userRendered = USER_TPL.replace(/\{\{\s*resume_text\s*\}\}/g, vars.resume_text);
  const userWithSchema = `${userRendered}\n\nRespond with a single JSON object that matches this schema:\n${SCHEMA_JSON}`;
  return [
    { role: 'system', content: SYSTEM },
    { role: 'user', content: userWithSchema },
  ];
}
