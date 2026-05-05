/**
 * JD 粘贴预处理 — B4(JDParser bug 归档,见 docs/slices/jd-parser-bugs-2026-05.md)。
 *
 * BOSS 直聘 / 拉勾等平台,JD 正文上方有「平台筛选标签」(如孤立的 `Java` `React`),
 * 用户全选复制粘贴会带进来,LLM 把它们当 hard_skill 抽。这个 prompt 修不了
 * (LLM 看到孤立的 token 就是会理解为技术要求),只能在前端剥离。
 *
 * 启发式策略:
 * - 只看文本最开头(跳空行后)
 * - 连续 ≥ 2 行,每行 trim 后长度 ≤ 8,无中英文标点 / 数字 / 横线(避开 "3-5年")
 * - 块后必须接空行(BOSS 标签与正文之间有视觉间隔)
 * - 满足以上才剥离;否则原文返回(避免误剥公司名 / 短标题)
 */

const FORBIDDEN_CHARS = /[,。:;!?,.;:!?\d\-—–·•|()()[\]【】{}「」]/;

export interface PasteCleanResult {
  cleaned: string;
  strippedLines: number;
  strippedTokens: string[];
}

export function stripBossTagsBlock(raw: string): PasteCleanResult {
  const lines = raw.split('\n');

  let i = 0;
  while (i < lines.length && lines[i].trim() === '') i++;
  const blockStart = i;

  const tokens: string[] = [];
  while (i < lines.length) {
    const t = lines[i].trim();
    if (t === '') break;
    if (t.length > 8) break;
    if (FORBIDDEN_CHARS.test(t)) break;
    tokens.push(t);
    i++;
  }
  const blockEnd = i;

  if (blockEnd - blockStart < 2) {
    return { cleaned: raw, strippedLines: 0, strippedTokens: [] };
  }
  if (blockEnd >= lines.length || lines[blockEnd].trim() !== '') {
    return { cleaned: raw, strippedLines: 0, strippedTokens: [] };
  }

  const cleaned = [...lines.slice(0, blockStart), ...lines.slice(blockEnd + 1)].join('\n');
  return { cleaned, strippedLines: blockEnd - blockStart, strippedTokens: tokens };
}
