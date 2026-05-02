/**
 * Screenshot → candidate dataset row.
 *
 * Two-pass pipeline:
 *   1. qwen-vl-max-latest reads the image and emits raw JD text
 *      (handles Chinese OCR + layout reconstruction)
 *   2. qwen-plus reads that text and emits JDStructured ground truth
 *      (uses a labelling-specific prompt distinct from the production
 *      JDParser prompt v1.0.0 — different prompt avoids baking JDParser's
 *      bias into ground truth)
 *
 * Output: one promptfoo Test JSONL line per image to stdout. Users review
 * and append to `suites/jd_extract/dataset.jsonl` after manual fixup
 * (anonymizing company names → [CompanyA]/[CompanyB], correcting any
 * wrong fields).
 *
 * Usage:
 *   pnpm --filter @jobcopilot/evals run prep:screenshot raw/boss-001.png raw/boss-002.png
 *
 * Cost: roughly ¥0.05 per image (vl) + ¥0.02 (plus). 15 images ≈ ¥1.
 */
import { readFileSync } from 'node:fs';
import { basename, extname } from 'node:path';
import { OpenAI } from 'openai';

const apiKey = process.env.DASHSCOPE_API_KEY_EVAL ?? process.env.DASHSCOPE_API_KEY;
if (!apiKey) {
  console.error('error: set DASHSCOPE_API_KEY_EVAL (preferred) or DASHSCOPE_API_KEY in env');
  process.exit(1);
}

const client = new OpenAI({
  apiKey,
  baseURL: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
});

const VL_MODEL = process.env.JD_OCR_MODEL ?? 'qwen-vl-max-latest';
const TEXT_MODEL = process.env.JD_LABEL_MODEL ?? 'qwen-plus';

const OCR_PROMPT =
  '请把这张招聘 JD 截图里的所有可见文字按原版面顺序完整提取出来。只输出 JD 正文文字本身,不要任何解释、标题、排版说明。保留段落分隔,空行用空行表示。';

const LABEL_SYSTEM = `你是 JD 数据标注员,从 JD 纯文本里抽取结构化字段。

仅输出符合下述 schema 的 JSON 对象,不要任何解释。

schema:
{
  "title": string,                 // 岗位名,完整保留(如 "Python 高级开发工程师")
  "company": string | null,         // 公司全称(若 JD 内可见)
  "location": string | null,        // 工作地点
  "salary_min": int | null,         // 月薪下限(整数,人民币元),"15-25k" → 15000
  "salary_max": int | null,         // 月薪上限
  "salary_period": "monthly" | "yearly",
  "job_level": "intern"|"junior"|"middle"|"senior"|"lead" | null,
  "years_required": int | null,     // 经验年数门槛,"3 年以上" → 3
  "education": "专科"|"本科"|"硕士"|"博士" | null,  // 取硬性下限
  "hard_skills": [ { "name": string } ],  // 归一化:lowercase + 合并空格(LangChain → langchain)
  "responsibilities": [ string ]
}

规则:
- 字段不可见 / 不确定时返回 null,不要猜。
- 薪资模糊词("面议" / "薪资到位")→ salary_min/max 都 null。
- "15-25K·14 薪" → monthly 月薪 15000-25000。
- "30万-50万" → yearly 年薪 300000-500000。
- 学历:"本科及以上"→"本科"(取硬性下限);"硕士优先"也→"本科"。
- hard_skills 不超过 12 个,只挑 JD 明确点名的硬技能(语言/框架/工具/数据库),不要软技能。`;

function buildLabelUser(jdText: string): string {
  return `请抽取以下 JD:\n\n<jd>\n${jdText}\n</jd>\n\n直接返回 JSON。`;
}

async function imageToText(imagePath: string): Promise<string> {
  const buf = readFileSync(imagePath);
  const extRaw = extname(imagePath).slice(1).toLowerCase();
  const mime = extRaw === 'jpg' ? 'jpeg' : extRaw || 'png';
  const dataUrl = `data:image/${mime};base64,${buf.toString('base64')}`;

  const resp = await client.chat.completions.create({
    model: VL_MODEL,
    messages: [
      {
        role: 'user',
        content: [
          { type: 'image_url', image_url: { url: dataUrl } },
          { type: 'text', text: OCR_PROMPT },
        ],
      },
    ],
    temperature: 0,
  });
  return resp.choices[0]?.message?.content ?? '';
}

async function textToStructured(jdText: string): Promise<Record<string, unknown>> {
  const resp = await client.chat.completions.create({
    model: TEXT_MODEL,
    messages: [
      { role: 'system', content: LABEL_SYSTEM },
      { role: 'user', content: buildLabelUser(jdText) },
    ],
    response_format: { type: 'json_object' },
    temperature: 0,
    seed: 42,
  });
  const raw = resp.choices[0]?.message?.content ?? '{}';
  return JSON.parse(raw) as Record<string, unknown>;
}

async function processOne(imagePath: string, idx: number): Promise<void> {
  const stem = basename(imagePath, extname(imagePath));
  const id = `jd_extract_${String(idx).padStart(3, '0')}_${stem}`;
  process.stderr.write(`→ [${idx}] ${imagePath}: OCR ... `);
  const text = await imageToText(imagePath);
  process.stderr.write(`(${text.length} chars) labelling ... `);
  const structured = await textToStructured(text);
  process.stderr.write('done\n');

  const line = {
    description: id,
    vars: { jd_text: text, expected: structured },
  };
  console.log(JSON.stringify(line));
}

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  if (args.length === 0) {
    console.error('usage: pnpm prep:screenshot <image>...');
    console.error('  outputs one promptfoo Test JSONL line per image to stdout');
    console.error('  → review, anonymize company names to [CompanyA]/[CompanyB]/...');
    console.error('  → append to suites/jd_extract/dataset.jsonl');
    process.exit(1);
  }
  for (let i = 0; i < args.length; i++) {
    const imagePath = args[i];
    if (!imagePath) continue;
    try {
      await processOne(imagePath, i + 1);
    } catch (err) {
      process.stderr.write(`✗ ${imagePath}: ${(err as Error).message}\n`);
    }
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
