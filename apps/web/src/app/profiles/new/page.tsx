'use client';

import { FilePurpose, ProfileParseInputSource } from '@jobcopilot/schemas';
import type { Route } from 'next';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { ApiError, type ProfileParseInput, parseProfile, uploadFile } from '@/lib/api';
import { useSessionDraft } from '@/lib/use-session-draft';

type Mode = 'text' | 'pdf';
type Stage = 'idle' | 'uploading' | 'started' | 'chunking_embedding' | 'result' | 'done';

const STAGE_PERCENT: Record<Stage, number> = {
  idle: 0,
  uploading: 10,
  started: 30,
  chunking_embedding: 60,
  result: 90,
  done: 100,
};

const STAGE_LABEL: Record<Stage, string> = {
  idle: '',
  uploading: '上传 PDF…',
  started: 'LLM 解析中…',
  chunking_embedding: '生成 chunks 与向量…',
  result: '保存结果…',
  done: '完成',
};

const MIN_TEXT_LEN = 50;
const MAX_FILE_BYTES = 10 * 1024 * 1024;
const DRAFT_KEY = 'jobcopilot.draft.profile.text';

export default function NewProfilePage() {
  const router = useRouter();
  const [mode, setMode] = React.useState<Mode>('text');
  const [text, setText, clearDraft] = useSessionDraft(DRAFT_KEY);
  const [file, setFile] = React.useState<File | null>(null);
  const [stage, setStage] = React.useState<Stage>('idle');
  const [error, setError] = React.useState<{
    message: string;
    existingProfileId: number | null;
  } | null>(null);
  const [warning, setWarning] = React.useState<string | null>(null);
  const [dragOver, setDragOver] = React.useState(false);

  const pending = stage !== 'idle' && stage !== 'done';

  function reset() {
    setError(null);
    setWarning(null);
  }

  async function startParse(input: ProfileParseInput) {
    let profileId: number | null = null;
    try {
      for await (const frame of parseProfile(input)) {
        if (frame.event === 'started') {
          setStage('started');
          profileId = frame.data.resource_id;
        } else if (frame.event === 'chunking_embedding') {
          setStage('chunking_embedding');
          if (!frame.data.ok) {
            setWarning(
              `自动生成 chunks 失败(${frame.data.error})。简历已保存,可在详情页手动重建。`,
            );
          }
        } else if (frame.event === 'result') {
          setStage('result');
          profileId = frame.data.resource_id;
        } else if (frame.event === 'error') {
          handleSseError(frame.data.code, frame.data.detail);
          return;
        } else if (frame.event === 'done') {
          if (frame.data.ok && profileId != null) {
            setStage('done');
            clearDraft();
            router.push(`/profiles/${profileId}` as Route);
            return;
          }
          setStage('idle');
          if (!error) setError({ message: '解析未完成', existingProfileId: null });
          return;
        }
      }
    } catch (err) {
      setStage('idle');
      setError({ message: messageOf(err), existingProfileId: null });
    }
  }

  function handleSseError(code: string, detail: string) {
    setStage('idle');
    if (code === 'PROFILE_EXISTS') {
      const m = detail.match(/profile (\d+)/);
      const existingId = m ? Number(m[1]) : null;
      setError({
        message: existingId
          ? `你已经有一份简历(#${existingId})。请先到详情页删除,再重新上传。`
          : '你已经有一份简历。请先删除再重新上传。',
        existingProfileId: existingId,
      });
    } else {
      setError({ message: detail || code, existingProfileId: null });
    }
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    reset();

    if (mode === 'text') {
      if (text.trim().length < MIN_TEXT_LEN) {
        setError({
          message: `简历正文太短(至少 ${MIN_TEXT_LEN} 字)`,
          existingProfileId: null,
        });
        return;
      }
      await startParse({ source: ProfileParseInputSource.text_paste, text });
      return;
    }

    if (!file) {
      setError({ message: '请先选择 PDF 文件', existingProfileId: null });
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      setError({ message: 'PDF 不能超过 10 MB', existingProfileId: null });
      return;
    }

    setStage('uploading');
    try {
      const uploaded = await uploadFile(file, FilePurpose.profile_pdf);
      await startParse({
        source: ProfileParseInputSource.pdf_upload,
        file_id: uploaded.id,
      });
    } catch (err) {
      setStage('idle');
      setError({ message: messageOf(err), existingProfileId: null });
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped && dropped.type === 'application/pdf') setFile(dropped);
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-16">
      <div className="mb-6">
        <Link href="/profiles" className="text-sm text-muted hover:text-foreground">
          ← 全部简历
        </Link>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>上传简历</CardTitle>
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
                <Label htmlFor="profile-text">简历正文</Label>
                <Textarea
                  id="profile-text"
                  rows={16}
                  placeholder="把简历全文粘贴过来…"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  disabled={pending}
                />
                <p className="text-xs text-muted">{text.length} 字</p>
              </div>
            ) : (
              <div className="space-y-2">
                <Label htmlFor="profile-file">PDF 文件</Label>
                <label
                  htmlFor="profile-file"
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
                  id="profile-file"
                  type="file"
                  accept="application/pdf"
                  className="hidden"
                  disabled={pending}
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                />
              </div>
            )}

            {pending ? (
              <div className="space-y-1">
                <div className="h-2 w-full overflow-hidden rounded bg-input">
                  <div
                    className="h-full bg-accent transition-all"
                    style={{ width: `${STAGE_PERCENT[stage]}%` }}
                  />
                </div>
                <p className="text-xs text-muted">{STAGE_LABEL[stage]}</p>
              </div>
            ) : null}

            {warning ? (
              <p className="rounded-xl border border-[var(--color-warning-border)]/40 bg-[var(--color-warning-bg)] p-3 text-sm text-[var(--color-warning-fg)]">
                {warning}
              </p>
            ) : null}

            {error ? (
              <div className="space-y-2 rounded border border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 p-2 text-sm text-[var(--color-danger)]">
                <p>{error.message}</p>
                {error.existingProfileId != null ? (
                  <Button asChild size="sm" variant="outline">
                    <Link href={`/profiles/${error.existingProfileId}` as Route}>
                      查看 / 删除现有简历
                    </Link>
                  </Button>
                ) : null}
              </div>
            ) : null}

            <div className="flex justify-end">
              <Button type="submit" disabled={pending}>
                {pending ? '处理中…' : '解析'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

function messageOf(err: unknown): string {
  if (err instanceof ApiError) {
    return err.problem.detail ?? err.problem.title ?? `HTTP ${err.status}`;
  }
  return err instanceof Error ? err.message : String(err);
}
