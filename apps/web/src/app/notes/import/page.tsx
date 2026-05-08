'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  ApiError,
  type BatchImportReport,
  type NoteBatchImportItem,
  batchImportNotes,
} from '@/lib/api';
import { cn } from '@/lib/utils';

const BATCH_SIZE = 50;

type Status =
  | { kind: 'idle' }
  | { kind: 'reading'; count: number }
  | { kind: 'ready'; items: NoteBatchImportItem[]; sourceLabel: string }
  | {
      kind: 'importing';
      total: number;
      done: number;
      report: BatchImportReport;
    }
  | { kind: 'finished'; report: BatchImportReport; sourceLabel: string }
  | { kind: 'error'; msg: string };

const EMPTY_REPORT: BatchImportReport = {
  imported: 0,
  skipped: 0,
  skipped_reasons: [],
  note_ids: [],
};

function mergeReport(a: BatchImportReport, b: BatchImportReport): BatchImportReport {
  return {
    imported: a.imported + b.imported,
    skipped: a.skipped + b.skipped,
    skipped_reasons: [...a.skipped_reasons, ...b.skipped_reasons],
    note_ids: [...a.note_ids, ...b.note_ids],
  };
}

function isMarkdown(name: string): boolean {
  return /\.md$/i.test(name);
}

function stripMdExt(name: string): string {
  return name.replace(/\.md$/i, '');
}

async function collectFromDirectory(
  // FileSystemDirectoryHandle / FileSystemHandle 的 lib.dom 类型
  // 不同 TS 版本下覆盖不齐,这里用 unknown + 收窄
  dirHandle: unknown,
  prefix: string[] = [],
): Promise<NoteBatchImportItem[]> {
  const items: NoteBatchImportItem[] = [];
  const handle = dirHandle as {
    values: () => AsyncIterable<{
      kind: 'file' | 'directory';
      name: string;
      getFile?: () => Promise<File>;
    }>;
  };
  for await (const entry of handle.values()) {
    if (entry.kind === 'directory') {
      const sub = await collectFromDirectory(entry, [...prefix, entry.name]);
      items.push(...sub);
    } else if (entry.kind === 'file' && isMarkdown(entry.name) && entry.getFile) {
      const file = await entry.getFile();
      const content = await file.text();
      items.push({
        folder_path: prefix,
        title: stripMdExt(entry.name),
        content_md: content,
      });
    }
  }
  return items;
}

export default function NotesImportPage() {
  const [supportsDir, setSupportsDir] = useState(false);
  const [supportsFile, setSupportsFile] = useState(false);
  const [rootFolder, setRootFolder] = useState('');
  const [overwrite, setOverwrite] = useState(false);
  const [status, setStatus] = useState<Status>({ kind: 'idle' });

  useEffect(() => {
    setSupportsDir(typeof window !== 'undefined' && 'showDirectoryPicker' in window);
    setSupportsFile(typeof window !== 'undefined' && 'showOpenFilePicker' in window);
  }, []);

  const handlePickDirectory = useCallback(async () => {
    setStatus({ kind: 'reading', count: 0 });
    try {
      // FS Access API 类型在 TS 5.6 lib.dom 里不全,直接 cast
      const w = window as unknown as { showDirectoryPicker: () => Promise<unknown> };
      const dirHandle = await w.showDirectoryPicker();
      const items = await collectFromDirectory(dirHandle);
      const dirName = (dirHandle as { name: string }).name;
      if (items.length === 0) {
        setStatus({ kind: 'error', msg: `目录 "${dirName}" 下没找到 .md 文件` });
        return;
      }
      setStatus({ kind: 'ready', items, sourceLabel: `目录 ${dirName}(${items.length} 篇)` });
    } catch (err) {
      // 用户取消选择 → AbortError,回 idle 不报错
      if (err instanceof DOMException && err.name === 'AbortError') {
        setStatus({ kind: 'idle' });
        return;
      }
      setStatus({ kind: 'error', msg: err instanceof Error ? err.message : String(err) });
    }
  }, []);

  const handlePickFiles = useCallback(async () => {
    setStatus({ kind: 'reading', count: 0 });
    try {
      const w = window as unknown as {
        showOpenFilePicker: (opts: object) => Promise<Array<{ getFile: () => Promise<File> }>>;
      };
      const handles = await w.showOpenFilePicker({
        multiple: true,
        types: [
          {
            description: 'Markdown 笔记',
            accept: { 'text/markdown': ['.md'] },
          },
        ],
      });
      const items: NoteBatchImportItem[] = [];
      for (const h of handles) {
        const file = await h.getFile();
        if (!isMarkdown(file.name)) continue;
        const content = await file.text();
        items.push({
          folder_path: [],
          title: stripMdExt(file.name),
          content_md: content,
        });
      }
      if (items.length === 0) {
        setStatus({ kind: 'error', msg: '没选到任何 .md 文件' });
        return;
      }
      setStatus({ kind: 'ready', items, sourceLabel: `${items.length} 篇单文件` });
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        setStatus({ kind: 'idle' });
        return;
      }
      setStatus({ kind: 'error', msg: err instanceof Error ? err.message : String(err) });
    }
  }, []);

  const handleStartImport = useCallback(async () => {
    if (status.kind !== 'ready') return;
    const allItems = status.items;
    const sourceLabel = status.sourceLabel;
    let report = EMPTY_REPORT;
    setStatus({ kind: 'importing', total: allItems.length, done: 0, report });
    try {
      for (let i = 0; i < allItems.length; i += BATCH_SIZE) {
        const batch = allItems.slice(i, i + BATCH_SIZE);
        const r = await batchImportNotes({
          items: batch,
          root_folder: rootFolder.trim() || null,
          overwrite,
        });
        report = mergeReport(report, r);
        setStatus({
          kind: 'importing',
          total: allItems.length,
          done: Math.min(i + BATCH_SIZE, allItems.length),
          report,
        });
      }
      setStatus({ kind: 'finished', report, sourceLabel });
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.problem.code ?? err.status}: ${err.problem.detail ?? err.message}`
          : err instanceof Error
            ? err.message
            : String(err);
      setStatus({ kind: 'error', msg: `导入中断:${msg}(已成功 ${report.imported} 篇)` });
    }
  }, [status, rootFolder, overwrite]);

  const handleReset = useCallback(() => {
    setStatus({ kind: 'idle' });
  }, []);

  // 浏览器不支持 — 整页只显示提示
  if (!supportsDir && !supportsFile) {
    return (
      <div className="mx-auto max-w-2xl px-6 py-16">
        <h1 className="text-3xl font-semibold">导入本地笔记</h1>
        <div className="mt-6 rounded-[var(--radius-apple)] border border-[var(--color-warning-border)] bg-[var(--color-warning-bg)] p-5 text-sm text-[var(--color-warning-fg)]">
          <p className="font-medium">当前浏览器不支持 File System Access API</p>
          <p className="mt-2">
            请用 Chrome / Edge / Arc 等基于 Chromium 的浏览器打开本页。Safari 仅支持选单文件,Firefox 暂不支持。
          </p>
        </div>
        <div className="mt-6">
          <Link href="/notes" className="text-sm text-accent hover:underline">
            ← 返回笔记
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">导入本地笔记</h1>
          <p className="mt-1 text-sm text-muted">
            浏览器直接读你电脑上的 .md 文件,免打包上传。content 仍会进数据库做 chunk + embedding。
          </p>
        </div>
        <Link href="/notes" className="text-sm text-accent hover:underline">
          ← 返回笔记
        </Link>
      </div>

      {/* 选项区 */}
      <div className="mt-8 rounded-[var(--radius-apple)] border border-border bg-surface p-5">
        <div className="grid grid-cols-[120px_1fr] items-center gap-3">
          <label className="text-sm text-muted" htmlFor="root-folder">
            根目录前缀
          </label>
          <Input
            id="root-folder"
            placeholder="可选 — 例:archive(所有笔记会挂到这个文件夹下)"
            value={rootFolder}
            onChange={(e) => setRootFolder(e.target.value)}
            disabled={status.kind === 'importing' || status.kind === 'reading'}
          />
          <span className="text-sm text-muted">同名处理</span>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={overwrite}
              onChange={(e) => setOverwrite(e.target.checked)}
              disabled={status.kind === 'importing' || status.kind === 'reading'}
            />
            <span>覆盖已存在的同 folder + title 笔记(默认跳过)</span>
          </label>
        </div>
      </div>

      {/* 选源按钮 */}
      {status.kind === 'idle' || status.kind === 'error' ? (
        <div className="mt-6 grid grid-cols-2 gap-4">
          <button
            type="button"
            onClick={handlePickDirectory}
            disabled={!supportsDir}
            className={cn(
              'rounded-[var(--radius-apple)] border border-border bg-surface p-6 text-left transition-all duration-150 ease-apple hover:shadow-[var(--shadow-apple-sm)]',
              !supportsDir && 'cursor-not-allowed opacity-50',
            )}
          >
            <div className="text-base font-semibold">选目录</div>
            <p className="mt-1 text-sm text-muted">
              选一个文件夹,递归读所有 .md,保留子目录结构作为 folder_path。
            </p>
            {!supportsDir ? (
              <p className="mt-2 text-xs text-[var(--color-warning-fg)]">
                Safari 不支持选目录,请用 Chrome / Edge
              </p>
            ) : null}
          </button>
          <button
            type="button"
            onClick={handlePickFiles}
            disabled={!supportsFile}
            className={cn(
              'rounded-[var(--radius-apple)] border border-border bg-surface p-6 text-left transition-all duration-150 ease-apple hover:shadow-[var(--shadow-apple-sm)]',
              !supportsFile && 'cursor-not-allowed opacity-50',
            )}
          >
            <div className="text-base font-semibold">选单篇 / 多篇</div>
            <p className="mt-1 text-sm text-muted">
              选一个或多个 .md 文件,默认放根。需要归到子文件夹请填上面的根目录前缀。
            </p>
          </button>
        </div>
      ) : null}

      {/* 读取中 */}
      {status.kind === 'reading' ? (
        <div className="mt-6 rounded-[var(--radius-apple)] border border-border bg-surface p-5 text-sm text-muted">
          读取本地文件中…
        </div>
      ) : null}

      {/* 已读完待确认 */}
      {status.kind === 'ready' ? (
        <div className="mt-6 rounded-[var(--radius-apple)] border border-border bg-surface p-5">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">已读取:{status.sourceLabel}</p>
              <p className="mt-1 text-xs text-muted">
                确认后将分批 {BATCH_SIZE} 篇 POST 到 /api/notes/batch-import
              </p>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" onClick={handleReset}>
                重选
              </Button>
              <Button onClick={handleStartImport}>开始导入</Button>
            </div>
          </div>
          <ul className="mt-4 max-h-64 overflow-y-auto rounded-md border border-border bg-background text-xs">
            {status.items.slice(0, 200).map((it, idx) => (
              <li
                key={`${it.folder_path.join('/')}/${it.title}-${idx}`}
                className="flex items-center gap-3 border-b border-border px-3 py-1.5 last:border-b-0"
              >
                <span className="text-muted">{it.folder_path.join('/') || '(根)'}</span>
                <span className="truncate">{it.title}.md</span>
              </li>
            ))}
            {status.items.length > 200 ? (
              <li className="px-3 py-1.5 text-muted">…还有 {status.items.length - 200} 篇</li>
            ) : null}
          </ul>
        </div>
      ) : null}

      {/* 导入中 */}
      {status.kind === 'importing' ? (
        <div className="mt-6 rounded-[var(--radius-apple)] border border-border bg-surface p-5">
          <p className="text-sm font-medium">
            导入中… {status.done} / {status.total}
          </p>
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-input">
            <div
              className="h-full bg-accent transition-all duration-200"
              style={{ width: `${(status.done / status.total) * 100}%` }}
            />
          </div>
          <p className="mt-3 text-xs text-muted">
            已成功 {status.report.imported} · 跳过 {status.report.skipped}
          </p>
        </div>
      ) : null}

      {/* 完成 */}
      {status.kind === 'finished' ? (
        <div className="mt-6 rounded-[var(--radius-apple)] border border-[var(--color-success-border)] bg-[var(--color-success-bg)] p-5 text-[var(--color-success-fg)]">
          <p className="text-sm font-medium">
            导入完成 — {status.sourceLabel}
          </p>
          <p className="mt-1 text-sm">
            成功 {status.report.imported} · 跳过 {status.report.skipped}
          </p>
          {status.report.skipped_reasons.length > 0 ? (
            <details className="mt-3 text-xs">
              <summary className="cursor-pointer">查看跳过明细({status.report.skipped_reasons.length})</summary>
              <ul className="mt-2 max-h-48 overflow-y-auto rounded-md bg-surface p-2 text-foreground">
                {status.report.skipped_reasons.map((r, idx) => (
                  <li key={idx} className="py-0.5">
                    <span className="text-muted">[{r.reason}]</span> {r.path}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
          <div className="mt-4 flex gap-2">
            <Button variant="outline" onClick={handleReset}>
              再来一次
            </Button>
            <Link
              href="/notes"
              className="inline-flex items-center justify-center rounded-full bg-accent px-5 py-2 text-sm font-medium text-white hover:bg-[var(--color-accent-hover)]"
            >
              去看笔记
            </Link>
          </div>
        </div>
      ) : null}

      {/* 错误 */}
      {status.kind === 'error' ? (
        <div className="mt-6 rounded-[var(--radius-apple)] border border-[var(--color-warning-border)] bg-[var(--color-warning-bg)] p-5 text-sm text-[var(--color-warning-fg)]">
          <p className="font-medium">出错了</p>
          <p className="mt-1">{status.msg}</p>
          <Button variant="outline" className="mt-3" onClick={handleReset}>
            重试
          </Button>
        </div>
      ) : null}
    </div>
  );
}
