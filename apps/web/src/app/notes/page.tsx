'use client';

import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  ApiError,
  type NoteOut,
  type TreeNode,
  createNote,
  deleteNote,
  getNote,
  listNotesTree,
  moveNote,
  updateNote,
} from '@/lib/api';
import { cn } from '@/lib/utils';

const MonacoEditor = dynamic(() => import('@monaco-editor/react'), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center text-sm text-muted">
      加载编辑器…
    </div>
  ),
});

type DraftMode = { kind: 'idle' } | { kind: 'creating' } | { kind: 'editing'; id: number };

type Draft = {
  title: string;
  folderPath: string;
  contentMd: string;
};

const EMPTY_DRAFT: Draft = { title: '', folderPath: '', contentMd: '' };

function parseFolder(input: string): string[] {
  return input
    .split('/')
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function joinFolder(parts: string[]): string {
  return parts.join('/');
}

function noteToDraft(n: NoteOut): Draft {
  return {
    title: n.title,
    folderPath: joinFolder(n.folder_path),
    contentMd: n.content_md,
  };
}

export default function NotesPage() {
  const [tree, setTree] = useState<TreeNode[] | null>(null);
  const [treeError, setTreeError] = useState<string | null>(null);
  const [mode, setMode] = useState<DraftMode>({ kind: 'idle' });
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<{ kind: 'ok' | 'err'; msg: string } | null>(null);

  const reloadTree = useCallback(async () => {
    try {
      const t = await listNotesTree();
      setTree(t);
      setTreeError(null);
    } catch (err) {
      setTreeError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void reloadTree();
  }, [reloadTree]);

  const selectNote = useCallback(async (id: number) => {
    try {
      const note = await getNote(id);
      setMode({ kind: 'editing', id });
      setDraft(noteToDraft(note));
      setFlash(null);
    } catch (err) {
      setFlash({ kind: 'err', msg: err instanceof Error ? err.message : String(err) });
    }
  }, []);

  const startCreate = useCallback(() => {
    setMode({ kind: 'creating' });
    setDraft(EMPTY_DRAFT);
    setFlash(null);
  }, []);

  const handleSave = useCallback(async () => {
    if (!draft.title.trim()) {
      setFlash({ kind: 'err', msg: '标题不能为空' });
      return;
    }
    setBusy(true);
    try {
      const folder_path = parseFolder(draft.folderPath);
      if (mode.kind === 'creating') {
        const created = await createNote({
          folder_path,
          title: draft.title.trim(),
          content_md: draft.contentMd,
        });
        setMode({ kind: 'editing', id: created.id });
        setDraft(noteToDraft(created));
      } else if (mode.kind === 'editing') {
        const updated = await updateNote(mode.id, {
          folder_path,
          title: draft.title.trim(),
          content_md: draft.contentMd,
        });
        setDraft(noteToDraft(updated));
      }
      await reloadTree();
      setFlash({ kind: 'ok', msg: '已保存' });
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.problem.code ?? err.status}: ${err.problem.detail ?? err.message}`
          : err instanceof Error
            ? err.message
            : String(err);
      setFlash({ kind: 'err', msg });
    } finally {
      setBusy(false);
    }
  }, [draft, mode, reloadTree]);

  const handleDelete = useCallback(async () => {
    if (mode.kind !== 'editing') return;
    if (!window.confirm(`删除笔记 "${draft.title}" ?(软删,chunks 物理删)`)) return;
    setBusy(true);
    try {
      await deleteNote(mode.id);
      setMode({ kind: 'idle' });
      setDraft(EMPTY_DRAFT);
      await reloadTree();
      setFlash({ kind: 'ok', msg: '已删除' });
    } catch (err) {
      setFlash({ kind: 'err', msg: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }, [mode, draft.title, reloadTree]);

  const handleMove = useCallback(async () => {
    if (mode.kind !== 'editing') return;
    const next = window.prompt('新 folder 路径(用 / 分隔,留空表示根):', draft.folderPath);
    if (next === null) return;
    setBusy(true);
    try {
      const updated = await moveNote(mode.id, parseFolder(next));
      setDraft(noteToDraft(updated));
      await reloadTree();
      setFlash({ kind: 'ok', msg: '已移动' });
    } catch (err) {
      setFlash({ kind: 'err', msg: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }, [mode, draft.folderPath, reloadTree]);

  return (
    <div className="grid h-full grid-cols-[280px_1fr]">
      <aside className="flex h-full flex-col border-r border-border bg-surface">
        <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
          <span className="text-[11px] font-semibold tracking-wider text-muted uppercase">
            笔记
          </span>
          <div className="flex items-center gap-1">
            <Button size="sm" variant="ghost" onClick={startCreate}>
              新建
            </Button>
            <Link
              href="/notes/import"
              className="rounded-md px-3 py-1 text-xs text-muted hover:bg-input"
            >
              导入
            </Link>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto px-2 py-2">
          {treeError ? (
            <p className="px-2 py-2 text-xs text-[var(--color-danger)]">树加载失败:{treeError}</p>
          ) : tree === null ? (
            <p className="px-2 py-2 text-xs text-muted">加载中…</p>
          ) : tree.length === 0 ? (
            <div className="px-2 py-4 text-xs text-muted">
              <p>没有笔记。</p>
              <Link href="/notes/import" className="mt-2 block text-accent hover:underline">
                从本地目录导入 →
              </Link>
            </div>
          ) : (
            tree.map((node) => (
              <TreeNodeView
                key={node.folder_path.join('/') || '__root__'}
                node={node}
                selectedId={mode.kind === 'editing' ? mode.id : null}
                onSelect={selectNote}
                depth={0}
              />
            ))
          )}
        </div>
      </aside>

      <section className="flex h-full flex-col bg-background">
        {mode.kind === 'idle' ? (
          <div className="flex h-full items-center justify-center text-sm text-muted">
            选一篇笔记,或点左上角 <span className="px-1 font-medium">新建</span>
          </div>
        ) : (
          <>
            <div className="flex flex-col gap-2 border-b border-border bg-surface px-6 py-3">
              <div className="flex items-center gap-2">
                <Input
                  className="flex-1"
                  placeholder="标题"
                  value={draft.title}
                  onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
                />
                <Button onClick={handleSave} disabled={busy}>
                  {mode.kind === 'creating' ? '创建' : '保存'}
                </Button>
                {mode.kind === 'editing' ? (
                  <>
                    <Button variant="outline" onClick={handleMove} disabled={busy}>
                      移动
                    </Button>
                    <Button variant="destructive" onClick={handleDelete} disabled={busy}>
                      删除
                    </Button>
                  </>
                ) : null}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted">folder</span>
                <Input
                  className="flex-1"
                  placeholder="例:python/list  (留空 = 根)"
                  value={draft.folderPath}
                  onChange={(e) => setDraft((d) => ({ ...d, folderPath: e.target.value }))}
                />
              </div>
              {flash ? (
                <p
                  className={cn(
                    'text-xs',
                    flash.kind === 'ok' ? 'text-[var(--color-success-fg)]' : 'text-[var(--color-danger)]',
                  )}
                >
                  {flash.msg}
                </p>
              ) : null}
            </div>
            <div className="flex-1 overflow-hidden">
              <MonacoEditor
                height="100%"
                defaultLanguage="markdown"
                language="markdown"
                value={draft.contentMd}
                onChange={(v) => setDraft((d) => ({ ...d, contentMd: v ?? '' }))}
                options={{
                  minimap: { enabled: false },
                  fontSize: 14,
                  wordWrap: 'on',
                  scrollBeyondLastLine: false,
                  lineNumbers: 'off',
                  renderLineHighlight: 'none',
                  padding: { top: 16, bottom: 16 },
                }}
              />
            </div>
          </>
        )}
      </section>
    </div>
  );
}

function TreeNodeView({
  node,
  selectedId,
  onSelect,
  depth,
}: {
  node: TreeNode;
  selectedId: number | null;
  onSelect: (id: number) => void;
  depth: number;
}) {
  const folderName = node.folder_path[node.folder_path.length - 1] ?? '(根)';
  return (
    <div>
      <div
        className="text-[11px] font-medium tracking-wide text-muted uppercase"
        style={{ paddingLeft: depth * 12 + 6, paddingTop: 8, paddingBottom: 2 }}
      >
        {folderName}
      </div>
      <ul>
        {node.notes.map((n) => (
          <li key={n.id}>
            <button
              type="button"
              onClick={() => onSelect(n.id)}
              className={cn(
                'block w-full truncate rounded-md py-1 pr-2 text-left text-[13px] transition-colors duration-150 ease-apple',
                selectedId === n.id
                  ? 'bg-[var(--color-selection)] text-[var(--color-selection-fg)]'
                  : 'text-foreground hover:bg-black/[0.04]',
              )}
              style={{ paddingLeft: depth * 12 + 18 }}
            >
              {n.title}
            </button>
          </li>
        ))}
      </ul>
      {node.children.map((c) => (
        <TreeNodeView
          key={c.folder_path.join('/')}
          node={c}
          selectedId={selectedId}
          onSelect={onSelect}
          depth={depth + 1}
        />
      ))}
    </div>
  );
}
