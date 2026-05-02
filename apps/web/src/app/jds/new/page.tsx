'use client';

import { JDParseInputSource } from '@jobcopilot/schemas';
import type { Route } from 'next';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { ApiError, parseJd } from '@/lib/api';

const MIN_JD_LENGTH = 50;

export default function NewJdPage() {
  const router = useRouter();
  const [text, setText] = React.useState('');
  const [pending, setPending] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    if (text.trim().length < MIN_JD_LENGTH) {
      setError(`JD 正文太短(至少 ${MIN_JD_LENGTH} 字)`);
      return;
    }
    setPending(true);
    try {
      const res = await parseJd({ source: JDParseInputSource.text_paste, text });
      router.push(`/jds/${res.id}` as Route);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.problem.detail ?? err.problem.title ?? `HTTP ${err.status}`);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
      setPending(false);
    }
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
          <form onSubmit={onSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="jd-text">职位描述</Label>
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
            {error ? <p className="text-sm text-[var(--color-danger)]">{error}</p> : null}
            <div className="flex justify-end">
              <Button type="submit" disabled={pending || text.trim().length === 0}>
                {pending ? '解析中…' : '解析'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
