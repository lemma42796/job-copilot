'use client';

import * as React from 'react';

import { Button } from '@/components/ui/button';
import { ApiError, type ProfileChunkItem, listProfileChunks, rechunkProfile } from '@/lib/api';

type RebuildStage = 'idle' | 'started' | 'chunking_embedding' | 'done';

const STAGE_PERCENT: Record<RebuildStage, number> = {
  idle: 0,
  started: 30,
  chunking_embedding: 70,
  done: 100,
};

const STAGE_LABEL: Record<RebuildStage, string> = {
  idle: '',
  started: '启动…',
  chunking_embedding: '重建 chunks 与向量…',
  done: '完成',
};

export function ChunksDebugSection({ profileId }: { profileId: number }) {
  const [chunks, setChunks] = React.useState<readonly ProfileChunkItem[] | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [rebuildStage, setRebuildStage] = React.useState<RebuildStage>('idle');
  const [rebuildError, setRebuildError] = React.useState<string | null>(null);
  const [lastRebuildSummary, setLastRebuildSummary] = React.useState<string | null>(null);

  const refresh = React.useCallback(async () => {
    setLoadError(null);
    try {
      const res = await listProfileChunks(profileId);
      setChunks(res.data);
    } catch (err) {
      setLoadError(messageOf(err));
    }
  }, [profileId]);

  React.useEffect(() => {
    void refresh();
  }, [refresh]);

  async function onRebuild() {
    setRebuildError(null);
    setLastRebuildSummary(null);
    setRebuildStage('started');
    try {
      for await (const frame of rechunkProfile(profileId)) {
        if (frame.event === 'started') {
          setRebuildStage('started');
        } else if (frame.event === 'chunking_embedding') {
          setRebuildStage('chunking_embedding');
          if (frame.data.ok) {
            setLastRebuildSummary(
              `${frame.data.chunks_written} chunks · ${frame.data.embed_model} · ¥${frame.data.cost_cny}`,
            );
          }
        } else if (frame.event === 'error') {
          setRebuildStage('idle');
          setRebuildError(frame.data.detail || frame.data.code);
          return;
        } else if (frame.event === 'done') {
          if (frame.data.ok) {
            setRebuildStage('done');
            await refresh();
            setTimeout(() => setRebuildStage('idle'), 1500);
          } else {
            setRebuildStage('idle');
          }
          return;
        }
      }
    } catch (err) {
      setRebuildStage('idle');
      setRebuildError(messageOf(err));
    }
  }

  const rebuilding = rebuildStage !== 'idle' && rebuildStage !== 'done';

  return (
    <details className="rounded border border-border bg-input/30">
      <summary className="cursor-pointer px-4 py-3 text-sm font-medium hover:bg-input/60">
        调试:chunks{chunks ? ` (${chunks.length})` : ''}
      </summary>
      <div className="space-y-4 border-t border-border px-4 py-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-muted">
            5 表 → ChunkInput → 1024 维向量。改了简历内容后建议重建。
          </p>
          <Button size="sm" variant="outline" onClick={onRebuild} disabled={rebuilding}>
            {rebuilding ? '重建中…' : '重建 chunks'}
          </Button>
        </div>

        {rebuilding || rebuildStage === 'done' ? (
          <div className="space-y-1">
            <div className="h-2 w-full overflow-hidden rounded bg-input">
              <div
                className="h-full bg-accent transition-all"
                style={{ width: `${STAGE_PERCENT[rebuildStage]}%` }}
              />
            </div>
            <p className="text-xs text-muted">
              {STAGE_LABEL[rebuildStage]}
              {lastRebuildSummary ? ` · ${lastRebuildSummary}` : ''}
            </p>
          </div>
        ) : null}

        {rebuildError ? (
          <p className="rounded border border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 p-2 text-sm text-[var(--color-danger)]">
            {rebuildError}
          </p>
        ) : null}

        {loadError ? (
          <p className="text-sm text-[var(--color-danger)]">加载 chunks 失败:{loadError}</p>
        ) : null}

        {chunks == null ? (
          <p className="text-xs text-muted">加载中…</p>
        ) : chunks.length === 0 ? (
          <p className="text-xs text-muted">还没有 chunks。点上方「重建 chunks」生成。</p>
        ) : (
          <ul className="space-y-2">
            {chunks.map((c) => (
              <li key={c.id} className="rounded border border-border bg-background p-3 text-xs">
                <div className="mb-1 flex flex-wrap items-baseline gap-2 text-muted">
                  <span className="font-medium text-foreground">#{c.id}</span>
                  <span className="rounded bg-input px-1.5 py-0.5">{c.granularity}</span>
                  <span>
                    {c.source_table} #{c.source_id}
                  </span>
                  <span>
                    {c.embed_model ?? '—'}
                    {c.embed_version ? ` (${c.embed_version})` : ''}
                  </span>
                </div>
                <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-snug">
                  {c.content}
                </pre>
                {Object.keys(c.metadata).length > 0 ? (
                  <p className="mt-1 truncate text-[10px] text-muted">
                    metadata: {JSON.stringify(c.metadata)}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </details>
  );
}

function messageOf(err: unknown): string {
  if (err instanceof ApiError) {
    return err.problem.detail ?? err.problem.title ?? `HTTP ${err.status}`;
  }
  return err instanceof Error ? err.message : String(err);
}
