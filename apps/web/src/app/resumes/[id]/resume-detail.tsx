'use client';

import type { Route } from 'next';
import Link from 'next/link';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import type { ResumeDetail, ReviewFinding } from '@/lib/api';
import { formatRelative } from '@/lib/format';

const SEVERITY_LABELS: Record<ReviewFinding['severity'], string> = {
  high: '严重',
  medium: '中等',
  low: '轻微',
};

const SEVERITY_TONES: Record<ReviewFinding['severity'], string> = {
  high: 'bg-[var(--color-danger)]/10 text-[var(--color-danger)] border-[var(--color-danger)]/30',
  medium:
    'bg-[var(--color-warning-bg)] text-[var(--color-warning-fg)] border-[var(--color-warning-fg)]/30',
  low: 'bg-black/[0.04] text-foreground border-border',
};

const ISSUE_LABELS: Record<ReviewFinding['issue_type'], string> = {
  fabrication: '编造',
  exaggeration: '夸大',
  unsupported_number: '无依据数字',
  other: '其他',
};

export function ResumeDetailView({ resume }: { resume: ResumeDetail }) {
  return (
    <div className="space-y-6">
      <Header resume={resume} />

      {resume.status === 'generating' ? <GeneratingBanner /> : null}
      {resume.status === 'failed' ? <FailedBanner /> : null}
      {resume.status === 'review_failed' ? (
        <ReviewFailedBanner findings={resume.review_findings} />
      ) : null}

      {resume.markdown ? (
        <>
          <ResumeRender markdown={resume.markdown} />
          {resume.review_findings.length > 0 && resume.status !== 'review_failed' ? (
            <ReviewFindingsCard findings={resume.review_findings} />
          ) : null}
        </>
      ) : null}

      <DebugFooter resume={resume} />
    </div>
  );
}

function Header({ resume }: { resume: ResumeDetail }) {
  const title = resume.title?.trim() || `简历 #${resume.id}`;
  return (
    <div className="flex items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="truncate text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-1 text-sm text-muted">
          基于{' '}
          <Link href={`/jds/${resume.jd_id}` as Route} className="underline hover:text-foreground">
            JD #{resume.jd_id}
          </Link>{' '}
          + 简历 #{resume.profile_id}
          {resume.match_id != null ? (
            <>
              {' · '}
              来自{' '}
              <Link
                href={`/matches/${resume.match_id}` as Route}
                className="underline hover:text-foreground"
              >
                匹配 #{resume.match_id}
              </Link>
            </>
          ) : null}{' '}
          · {formatRelative(resume.created_at)}
        </p>
      </div>
      <DownloadButton resume={resume} />
    </div>
  );
}

function DownloadButton({ resume }: { resume: ResumeDetail }) {
  if (!resume.markdown) return null;
  function onDownload() {
    const filename = `${(resume.title?.trim() || `resume-${resume.id}`)
      .replace(/[/\\?%*:|"<>]/g, '_')
      .slice(0, 80)}.md`;
    const blob = new Blob([resume.markdown ?? ''], {
      type: 'text/markdown;charset=utf-8',
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }
  return (
    <Button variant="outline" size="sm" onClick={onDownload}>
      下载 .md
    </Button>
  );
}

function GeneratingBanner() {
  return (
    <div className="rounded-md border border-[var(--color-warning-fg)]/30 bg-[var(--color-warning-bg)] px-4 py-3 text-sm text-[var(--color-warning-fg)]">
      正在生成中,稍候刷新页面查看结果。
    </div>
  );
}

function FailedBanner() {
  return (
    <div className="rounded-md border border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 px-4 py-3 text-sm text-[var(--color-danger)]">
      本次简历生成失败 — 可以删除这条记录,在匹配详情页重新生成。
    </div>
  );
}

function ReviewFailedBanner({ findings }: { findings: readonly ReviewFinding[] }) {
  const high = findings.filter((f) => f.severity === 'high');
  return (
    <Card className="border-[var(--color-warning-fg)]/40 bg-[var(--color-warning-bg)]/40">
      <CardHeader>
        <CardTitle className="text-base text-[var(--color-warning-fg)]">
          ⚠ 事实核查未通过 — 请人工审阅
        </CardTitle>
        <CardDescription className="text-[var(--color-warning-fg)]/90">
          Reviewer 发现 {high.length} 条严重问题(共 {findings.length} 条标记)。简历正文仍展示
          供你修正,
          <span className="font-semibold">直接使用前请逐条核对</span>。
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ReviewFindingsList findings={findings} />
      </CardContent>
    </Card>
  );
}

function ReviewFindingsCard({ findings }: { findings: readonly ReviewFinding[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">事实核查标记 · {findings.length}</CardTitle>
        <CardDescription>不阻断使用,但建议核对后再投递</CardDescription>
      </CardHeader>
      <CardContent>
        <ReviewFindingsList findings={findings} />
      </CardContent>
    </Card>
  );
}

function ReviewFindingsList({ findings }: { findings: readonly ReviewFinding[] }) {
  return (
    <ul className="space-y-2">
      {findings.map((f, i) => (
        <li
          key={`${f.section}-${i}`}
          className={`rounded-md border px-3 py-2 ${SEVERITY_TONES[f.severity]}`}
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-black/10 px-1.5 py-0.5 text-[10px] font-medium tracking-wider uppercase">
              {SEVERITY_LABELS[f.severity]}
            </span>
            <span className="rounded-full bg-black/5 px-1.5 py-0.5 text-[10px]">
              {ISSUE_LABELS[f.issue_type]}
            </span>
            <span className="text-xs opacity-70">{f.section}</span>
          </div>
          <p className="mt-1.5 text-sm leading-5 italic opacity-90">"{f.quoted_text}"</p>
          <p className="mt-1 text-sm leading-5">{f.explanation}</p>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Mini markdown renderer
// ---------------------------------------------------------------------------
//
// drafter prompt 硬约束输出仅含:`## H2`、`- bullet`、纯文本段落、`**bold**`
// 内联强调。MVP 用本地渲染器避开 react-markdown 依赖。代码块 / 链接 /
// 图片 / 表格 / inline code 等不渲染(简历正文不会出现);若 drafter
// 罕见输出脱出约束,就走 fallback 段落分支显示 raw 文本(仍然可读)。

function ResumeRender({ markdown }: { markdown: string }) {
  const blocks = parseBlocks(markdown);
  return (
    <Card>
      <CardContent className="py-8">
        <article className="space-y-4">
          {blocks.map((b, i) => (
            <Block
              key={`${b.kind}-${i}-${b.kind === 'bullets' ? b.items.length : b.text.slice(0, 12)}`}
              block={b}
            />
          ))}
        </article>
      </CardContent>
    </Card>
  );
}

type Block =
  | { kind: 'h2'; text: string }
  | { kind: 'bullets'; items: string[] }
  | { kind: 'p'; text: string };

function parseBlocks(md: string): Block[] {
  // tsconfig 启用 noUncheckedIndexedAccess,`arr[i]` 返 `string | undefined`,
  // 这里循环里频繁 index,先固定 `line: string` 局部变量再判断,避免到处 ?? '' 兜底。
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i] ?? '';
    if (line.startsWith('## ')) {
      blocks.push({ kind: 'h2', text: line.slice(3).trim() });
      i++;
      continue;
    }
    if (line.startsWith('# ')) {
      // Drafter 不应输出 H1;若出现按 H2 渲染兜底(避免漏章)
      blocks.push({ kind: 'h2', text: line.slice(2).trim() });
      i++;
      continue;
    }
    if (line.startsWith('- ') || line.startsWith('* ')) {
      const items: string[] = [];
      while (i < lines.length) {
        const cur = lines[i] ?? '';
        if (!(cur.startsWith('- ') || cur.startsWith('* '))) break;
        items.push(cur.slice(2).trim());
        i++;
      }
      blocks.push({ kind: 'bullets', items });
      continue;
    }
    if (line.trim() === '') {
      i++;
      continue;
    }
    // 段落 — 收紧到下一个空行 / heading / bullet
    const buf: string[] = [];
    while (i < lines.length) {
      const l = lines[i] ?? '';
      if (
        l.trim() === '' ||
        l.startsWith('## ') ||
        l.startsWith('# ') ||
        l.startsWith('- ') ||
        l.startsWith('* ')
      ) {
        break;
      }
      buf.push(l);
      i++;
    }
    blocks.push({ kind: 'p', text: buf.join('\n') });
  }
  return blocks;
}

function Block({ block }: { block: Block }) {
  if (block.kind === 'h2') {
    return (
      <h2 className="mt-2 border-b border-border pb-2 text-lg font-semibold tracking-tight first:mt-0">
        {block.text}
      </h2>
    );
  }
  if (block.kind === 'bullets') {
    return (
      <ul className="list-disc space-y-1 pl-5 text-sm leading-6">
        {block.items.map((it, i) => (
          <li key={`${i}-${it.slice(0, 16)}`}>
            <Inline text={it} />
          </li>
        ))}
      </ul>
    );
  }
  return (
    <p className="text-sm leading-6 whitespace-pre-wrap">
      <Inline text={block.text} />
    </p>
  );
}

function Inline({ text }: { text: string }) {
  // 仅 `**bold**` 内联;遇到 ** 拆段交替渲染。
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith('**') && p.endsWith('**') ? (
          <strong key={`${i}-${p.slice(0, 12)}`} className="font-semibold">
            {p.slice(2, -2)}
          </strong>
        ) : (
          <React.Fragment key={`${i}-${p.slice(0, 12)}`}>{p}</React.Fragment>
        ),
      )}
    </>
  );
}

function DebugFooter({ resume }: { resume: ResumeDetail }) {
  return (
    <div className="rounded-md bg-black/[0.02] px-4 py-3 text-xs text-muted">
      <span>状态 {resume.status}</span>
      {resume.generation_model ? (
        <>
          {' · '}
          drafter <span className="text-foreground">{resume.generation_model}</span>
        </>
      ) : null}
      {resume.review_model ? (
        <>
          {' · '}
          reviewer <span className="text-foreground">{resume.review_model}</span>
        </>
      ) : null}
      {resume.tokens ? (
        <>
          {' · '}
          tokens{' '}
          <span className="text-foreground">
            {resume.tokens.input}/{resume.tokens.output}
          </span>
        </>
      ) : null}
      {resume.cost_cny != null ? (
        <>
          {' · '}¥<span className="text-foreground">{resume.cost_cny}</span>
        </>
      ) : null}
      {resume.latency_ms != null ? (
        <>
          {' · '}
          <span className="text-foreground">{resume.latency_ms} ms</span>
        </>
      ) : null}
    </div>
  );
}
