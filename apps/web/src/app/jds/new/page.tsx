'use client';

import { FilePurpose, JDParseInputSource } from '@jobcopilot/schemas';
import type { Route } from 'next';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { ApiError, parseJd, uploadFile } from '@/lib/api';

type Mode = 'text' | 'pdf';
type Stage = 'idle' | 'uploading' | 'parsing';

const MIN_JD_LENGTH = 50;
const MAX_FILE_BYTES = 10 * 1024 * 1024;

export default function NewJdPage() {
  const router = useRouter();
  const [mode, setMode] = React.useState<Mode>('text');
  const [text, setText] = React.useState('');
  const [file, setFile] = React.useState<File | null>(null);
  const [stage, setStage] = React.useState<Stage>('idle');
  const [error, setError] = React.useState<string | null>(null);
  const [dragOver, setDragOver] = React.useState(false);

  const pending = stage !== 'idle';

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    try {
      if (mode === 'text') {
        if (text.trim().length < MIN_JD_LENGTH) {
          setError(`JD 正文太短(至少 ${MIN_JD_LENGTH} 字)`);
          return;
        }
        setStage('parsing');
        const res = await parseJd({ source: JDParseInputSource.text_paste, text });
        router.push(`/jds/${res.id}` as Route);
        return;
      }

      if (!file) {
        setError('请先选择 PDF 文件');
        return;
      }
      if (file.size > MAX_FILE_BYTES) {
        setError('PDF 不能超过 10 MB');
        return;
      }

      setStage('uploading');
      const uploaded = await uploadFile(file, FilePurpose.jd_pdf);
      setStage('parsing');
      const res = await parseJd({
        source: JDParseInputSource.pdf_upload,
        file_id: uploaded.id,
      });
      router.push(`/jds/${res.id}` as Route);
    } catch (err) {
      setStage('idle');
      setError(messageOf(err));
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped && dropped.type === 'application/pdf') setFile(dropped);
  }

  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <div className="mb-6">
        <Link href="/" className="text-sm text-muted hover:text-foreground">
          ← 返回首页
        </Link>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>粘贴 JD</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="mb-4 inline-flex rounded-full bg-input p-1 text-sm">
            <button
              type="button"
              className={`rounded-full px-4 py-1.5 transition-all ${mode === 'text' ? 'bg-surface text-foreground shadow-[var(--shadow-apple-sm)]' : 'text-muted hover:text-foreground'}`}
              onClick={() => setMode('text')}
              disabled={pending}
            >
              文本粘贴
            </button>
            <button
              type="button"
              className={`rounded-full px-4 py-1.5 transition-all ${mode === 'pdf' ? 'bg-surface text-foreground shadow-[var(--shadow-apple-sm)]' : 'text-muted hover:text-foreground'}`}
              onClick={() => setMode('pdf')}
              disabled={pending}
            >
              PDF 上传
            </button>
          </div>

          <form onSubmit={onSubmit} className="space-y-4">
            {mode === 'text' ? (
              <div className="space-y-2">
                <Label htmlFor="jd-text">职位描述</Label>
                <p className="text-xs text-muted">
                  一次只支持一份 JD;有多份请逐条粘贴提交,每次会创建一条独立的 JD 记录。
                </p>
                <Textarea
                  id="jd-text"
                  rows={16}
                  placeholder="把招聘网站的 JD 全文粘贴过来…"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  disabled={pending}
                />
                <p className="text-xs text-muted">{text.length} 字</p>
              </div>
            ) : (
              <div className="space-y-2">
                <Label htmlFor="jd-file">PDF 文件</Label>
                <p className="text-xs text-muted">
                  一份 JD 一个 PDF;若一个 PDF 含多份 JD,需要先拆开。
                </p>
                <label
                  htmlFor="jd-file"
                  onDragOver={(e) => {
                    e.preventDefault();
                    setDragOver(true);
                  }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={onDrop}
                  className={`flex cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed p-10 text-sm transition ${
                    dragOver
                      ? 'border-accent bg-accent/10'
                      : 'border-border bg-input hover:border-accent/60'
                  }`}
                >
                  {file ? (
                    <>
                      <span className="font-medium">{file.name}</span>
                      <span className="mt-1 text-xs text-muted">
                        {(file.size / 1024).toFixed(1)} KB · 点击或拖拽更换
                      </span>
                    </>
                  ) : (
                    <>
                      <span>把 PDF 拖到这里</span>
                      <span className="mt-1 text-xs text-muted">或点击选择(≤ 10 MB)</span>
                    </>
                  )}
                </label>
                <input
                  id="jd-file"
                  type="file"
                  accept="application/pdf"
                  className="hidden"
                  disabled={pending}
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                />
              </div>
            )}

            {pending ? (
              <p className="text-xs text-muted">
                {stage === 'uploading' ? '上传 PDF…' : 'LLM 解析中…'}
              </p>
            ) : null}

            {error ? <p className="text-sm text-[var(--color-danger)]">{error}</p> : null}

            <div className="flex justify-end">
              <Button
                type="submit"
                disabled={pending || (mode === 'text' && text.trim().length === 0)}
              >
                {pending ? '处理中…' : '解析'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}

function messageOf(err: unknown): string {
  if (err instanceof ApiError) {
    return err.problem.detail ?? err.problem.title ?? `HTTP ${err.status}`;
  }
  return err instanceof Error ? err.message : String(err);
}
