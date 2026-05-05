'use client';

import type { Route } from 'next';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  ApiError,
  type ResumeDetail,
  type ResumeVersionItem,
  type ReviewFinding,
  createResumeVersion,
  listResumeVersions,
} from '@/lib/api';
import { formatRelative } from '@/lib/format';

import { MarkdownDiff, MarkdownEditor } from './resume-editor';

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
  const router = useRouter();
  const editable = resume.status === 'ready' || resume.status === 'review_failed';

  // 版本列表(W8):页面挂载时拉一次;保存编辑后再次刷新。`null` = 还没拉到。
  const [versions, setVersions] = React.useState<ResumeVersionItem[] | null>(null);
  const [versionsError, setVersionsError] = React.useState<string | null>(null);

  const reloadVersions = React.useCallback(async () => {
    try {
      const r = await listResumeVersions(resume.id);
      // 倒序展示(最新在上),后端按 version_number asc 返回。
      setVersions([...r.data].sort((a, b) => b.version_number - a.version_number));
      setVersionsError(null);
    } catch (err) {
      setVersionsError(messageOf(err));
    }
  }, [resume.id]);

  React.useEffect(() => {
    reloadVersions();
  }, [reloadVersions]);

  // 编辑器(W8):editMode = true 时显示 Monaco;editorMarkdown 默认载 resume.markdown。
  const [editMode, setEditMode] = React.useState(false);
  const [editorMarkdown, setEditorMarkdown] = React.useState('');
  const [editorNote, setEditorNote] = React.useState('');
  const [saving, setSaving] = React.useState(false);
  const [saveError, setSaveError] = React.useState<string | null>(null);

  // 历史版本预览(W8):非 null 时主区显示该 version 的 markdown(只读),
  // 顶部提示条带"返回最新"按钮。`null` = 显示最新(resume.markdown)。
  const [previewVersionId, setPreviewVersionId] = React.useState<number | null>(null);
  const previewVersion =
    previewVersionId != null
      ? versions?.find((v) => v.id === previewVersionId) ?? null
      : null;

  // Reviewer 标记交互(W8):
  // - activeFindingIdx:点击 finding 行设为 active → 主区高亮 quoted_text +
  //   滚动到对应 section
  // - dismissedSet:UI 局部"忽略"集合(刷新页面会重置;不入库 — 简化)
  // - adoptingIdx:正在执行"采纳"的 idx,disable 按钮防止重复
  const [activeFindingIdx, setActiveFindingIdx] = React.useState<number | null>(null);
  const [dismissedSet, setDismissedSet] = React.useState<ReadonlySet<number>>(
    () => new Set(),
  );
  const [adoptingIdx, setAdoptingIdx] = React.useState<number | null>(null);
  const [adoptError, setAdoptError] = React.useState<string | null>(null);

  function activateFinding(idx: number | null) {
    setActiveFindingIdx(idx);
    if (idx == null) return;
    const f = resume.review_findings[idx];
    if (!f) return;
    const slug = sectionSlugOf(f.section);
    if (!slug) return;
    // 等下一帧让 highlight 先 commit,再滚动 — Monaco 编辑器场景该函数也会被
    // 按到(虽然 review banner 在编辑模式下隐藏,但用户从历史版本切回时 banner
    // 会回来)。
    requestAnimationFrame(() => {
      document.getElementById(`section-${slug}`)?.scrollIntoView({
        block: 'start',
        behavior: 'smooth',
      });
    });
  }

  function dismissFinding(idx: number) {
    setDismissedSet((prev) => {
      const next = new Set(prev);
      next.add(idx);
      return next;
    });
    if (activeFindingIdx === idx) setActiveFindingIdx(null);
  }

  async function adoptFinding(idx: number) {
    const f = resume.review_findings[idx];
    if (!f || !resume.markdown) return;
    const stripped = stripQuoted(resume.markdown, f.quoted_text);
    if (stripped === resume.markdown) {
      setAdoptError(
        '未在简历中精确找到该原文片段(LLM 标记可能与正文有微小出入),请用编辑器手动修订',
      );
      return;
    }
    setAdoptingIdx(idx);
    setAdoptError(null);
    try {
      const noteSnippet = f.quoted_text.trim().slice(0, 40);
      await createResumeVersion(resume.id, {
        markdown: stripped,
        note: `采纳 reviewer 标记: ${noteSnippet}`.slice(0, 200),
      });
      // 采纳成功后顺带 dismiss(避免下一刷新前看到老标记仍在列表)
      setDismissedSet((prev) => {
        const next = new Set(prev);
        next.add(idx);
        return next;
      });
      if (activeFindingIdx === idx) setActiveFindingIdx(null);
      await reloadVersions();
      router.refresh();
    } catch (err) {
      setAdoptError(messageOf(err));
    } finally {
      setAdoptingIdx(null);
    }
  }

  const activeQuoted =
    activeFindingIdx != null ? resume.review_findings[activeFindingIdx]?.quoted_text ?? null : null;

  // Reviewer findings 是 graph 生成时存的快照,不会随用户 edit 重跑;若用
  // 户已经 adopt(或手动改掉了那段内容),quoted_text 就不再出现在 current
  // markdown 里 — 这种 finding 标"已处理"灰化展示,避免误导。
  const obsoleteFindings = React.useMemo(() => {
    if (!resume.markdown) return new Set<number>();
    const md = resume.markdown;
    const set = new Set<number>();
    resume.review_findings.forEach((f, i) => {
      if (findQuotedInText(md, f.quoted_text) == null) set.add(i);
    });
    return set;
  }, [resume.markdown, resume.review_findings]);

  function startEdit() {
    setEditorMarkdown(resume.markdown ?? '');
    setEditorNote('');
    setSaveError(null);
    setEditMode(true);
    setPreviewVersionId(null); // 编辑总是基于最新版,不从历史预览进编辑
  }

  function cancelEdit() {
    setEditMode(false);
    setEditorMarkdown('');
    setEditorNote('');
    setSaveError(null);
  }

  async function saveEdit() {
    if (!editorMarkdown.trim()) {
      setSaveError('内容不能为空');
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      await createResumeVersion(resume.id, {
        markdown: editorMarkdown,
        note: editorNote.trim() ? editorNote.trim() : null,
      });
      setEditMode(false);
      setEditorMarkdown('');
      setEditorNote('');
      await reloadVersions();
      router.refresh(); // 让 server component 重新拉 resume(markdown 已被后端更新)
    } catch (err) {
      setSaveError(messageOf(err));
    } finally {
      setSaving(false);
    }
  }

  const displayMarkdown = previewVersion ? previewVersion.markdown : resume.markdown;

  return (
    <div className="space-y-6">
      <Header
        resume={resume}
        editable={editable}
        editMode={editMode}
        previewVersion={previewVersion}
        onStartEdit={startEdit}
      />

      {previewVersion ? (
        <PreviewBanner
          version={previewVersion}
          onDismiss={() => setPreviewVersionId(null)}
        />
      ) : null}

      {resume.status === 'generating' ? <GeneratingBanner /> : null}
      {resume.status === 'failed' ? <FailedBanner /> : null}
      {resume.status === 'review_failed' && !previewVersion ? (
        <ReviewFailedBanner
          findings={resume.review_findings}
          activeIdx={activeFindingIdx}
          dismissed={dismissedSet}
          obsolete={obsoleteFindings}
          adoptingIdx={adoptingIdx}
          adoptError={adoptError}
          onActivate={activateFinding}
          onAdopt={adoptFinding}
          onDismiss={dismissFinding}
        />
      ) : null}

      {editMode ? (
        <EditorPanel
          markdown={editorMarkdown}
          note={editorNote}
          saving={saving}
          error={saveError}
          onMarkdownChange={setEditorMarkdown}
          onNoteChange={setEditorNote}
          onCancel={cancelEdit}
          onSave={saveEdit}
        />
      ) : displayMarkdown ? (
        <>
          <ResumeRender markdown={displayMarkdown} activeQuoted={activeQuoted} />
          {!previewVersion &&
          resume.review_findings.length > 0 &&
          resume.status !== 'review_failed' ? (
            <ReviewFindingsCard
              findings={resume.review_findings}
              activeIdx={activeFindingIdx}
              dismissed={dismissedSet}
              adoptingIdx={adoptingIdx}
              adoptError={adoptError}
              onActivate={activateFinding}
              onAdopt={adoptFinding}
              onDismiss={dismissFinding}
            />
          ) : null}
        </>
      ) : null}

      <VersionHistory
        versions={versions}
        error={versionsError}
        currentVersionId={previewVersionId}
        onPreview={setPreviewVersionId}
      />

      <DebugFooter resume={resume} />
    </div>
  );
}

function Header({
  resume,
  editable,
  editMode,
  previewVersion,
  onStartEdit,
}: {
  resume: ResumeDetail;
  editable: boolean;
  editMode: boolean;
  previewVersion: ResumeVersionItem | null;
  onStartEdit: () => void;
}) {
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
      <div className="flex items-center gap-2">
        {editable && !editMode && previewVersion == null ? (
          <Button variant="outline" size="sm" onClick={onStartEdit}>
            编辑
          </Button>
        ) : null}
        <DownloadButton resume={resume} />
      </div>
    </div>
  );
}

function PreviewBanner({
  version,
  onDismiss,
}: {
  version: ResumeVersionItem;
  onDismiss: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-[var(--color-accent)]/30 bg-[var(--color-accent)]/5 px-3 py-2 text-sm">
      <span className="text-foreground">
        正在查看 <span className="font-semibold">v{version.version_number}</span>
        (历史版本,{formatRelative(version.created_at)}
        {version.edit_note ? ` · "${version.edit_note}"` : ''})
        — 这不是当前活动版本。
      </span>
      <Button variant="outline" size="sm" onClick={onDismiss}>
        返回最新
      </Button>
    </div>
  );
}

function EditorPanel({
  markdown,
  note,
  saving,
  error,
  onMarkdownChange,
  onNoteChange,
  onCancel,
  onSave,
}: {
  markdown: string;
  note: string;
  saving: boolean;
  error: string | null;
  onMarkdownChange: (next: string) => void;
  onNoteChange: (next: string) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">编辑简历</CardTitle>
        <CardDescription>
          保存会创建一个新版本,可在下方"版本历史"里随时回看与对比。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <MarkdownEditor value={markdown} onChange={onMarkdownChange} height="32rem" />
        <div className="space-y-1.5">
          <label
            htmlFor="resume-edit-note"
            className="text-xs font-medium text-muted"
          >
            编辑备注(可选,200 字以内)
          </label>
          <input
            id="resume-edit-note"
            type="text"
            value={note}
            maxLength={200}
            onChange={(e) => onNoteChange(e.target.value)}
            placeholder="例如:精简项目经历开头,补充技能标签"
            className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm outline-none focus:border-[var(--color-accent)]"
          />
        </div>
        {error ? (
          <div className="rounded-md border border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 px-3 py-2 text-sm text-[var(--color-danger)]">
            保存失败:{error}
          </div>
        ) : null}
        <div className="flex items-center justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onCancel} disabled={saving}>
            取消
          </Button>
          <Button size="sm" onClick={onSave} disabled={saving}>
            {saving ? '保存中…' : '保存为新版本'}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function VersionHistory({
  versions,
  error,
  currentVersionId,
  onPreview,
}: {
  versions: ResumeVersionItem[] | null;
  error: string | null;
  currentVersionId: number | null;
  onPreview: (id: number | null) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const [diffPair, setDiffPair] = React.useState<{
    left: ResumeVersionItem;
    right: ResumeVersionItem;
  } | null>(null);

  if (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">版本历史</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-[var(--color-danger)]">加载失败:{error}</p>
        </CardContent>
      </Card>
    );
  }

  if (versions == null) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">版本历史</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted">加载中…</p>
        </CardContent>
      </Card>
    );
  }

  if (versions.length === 0) {
    return null;
  }

  // versions 已倒序;activeVersion = 第一条(version_number 最大)。
  const activeId = versions[0]?.id ?? null;

  function openDiffWithPrev(v: ResumeVersionItem) {
    const idx = versions!.findIndex((x) => x.id === v.id);
    // versions 是倒序(最新在前),"上一版"是 idx+1(版本号更小的)
    const prev = idx >= 0 && idx + 1 < versions!.length ? versions![idx + 1] : null;
    if (!prev) return;
    setDiffPair({ left: prev, right: v });
  }

  return (
    <>
      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <div>
            <CardTitle className="text-base">版本历史 · {versions.length}</CardTitle>
            <CardDescription>每次保存编辑会创建新版本;点击"对比上一版"看 diff。</CardDescription>
          </div>
          <Button variant="outline" size="sm" onClick={() => setOpen(!open)}>
            {open ? '收起' : '展开'}
          </Button>
        </CardHeader>
        {open ? (
          <CardContent>
            <ul className="divide-y divide-border">
              {versions.map((v) => {
                const isActive = v.id === activeId;
                const isPreviewed = currentVersionId === v.id;
                const idx = versions.findIndex((x) => x.id === v.id);
                const hasPrev = idx + 1 < versions.length;
                return (
                  <li key={v.id} className="flex items-start gap-3 py-2.5">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 text-sm">
                        <span className="font-semibold">v{v.version_number}</span>
                        <EditTypeTag type={v.edit_type} />
                        {isActive ? (
                          <span className="rounded-full bg-[var(--color-success-border)]/20 px-1.5 py-0.5 text-[10px] text-[var(--color-success-border)]">
                            当前
                          </span>
                        ) : null}
                        <span className="text-xs text-muted">
                          {formatRelative(v.created_at)}
                        </span>
                      </div>
                      {v.edit_note ? (
                        <p className="mt-0.5 truncate text-xs text-muted">"{v.edit_note}"</p>
                      ) : null}
                    </div>
                    <div className="flex items-center gap-1.5">
                      {!isActive ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => onPreview(isPreviewed ? null : v.id)}
                        >
                          {isPreviewed ? '取消预览' : '预览'}
                        </Button>
                      ) : null}
                      {hasPrev ? (
                        <Button variant="ghost" size="sm" onClick={() => openDiffWithPrev(v)}>
                          对比上一版
                        </Button>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          </CardContent>
        ) : null}
      </Card>

      {diffPair ? (
        <DiffPanel
          left={diffPair.left}
          right={diffPair.right}
          onClose={() => setDiffPair(null)}
        />
      ) : null}
    </>
  );
}

function EditTypeTag({ type }: { type: ResumeVersionItem['edit_type'] }) {
  if (type == null) return null;
  const label =
    type === 'generated' ? '生成' : type === 'edited' ? '编辑' : '重生成';
  const tone =
    type === 'generated'
      ? 'bg-[var(--color-accent)]/10 text-[var(--color-accent)]'
      : type === 'edited'
        ? 'bg-[var(--color-warning-bg)] text-[var(--color-warning-fg)]'
        : 'bg-black/5 text-foreground';
  return (
    <span className={`rounded-full px-1.5 py-0.5 text-[10px] ${tone}`}>{label}</span>
  );
}

function DiffPanel({
  left,
  right,
  onClose,
}: {
  left: ResumeVersionItem;
  right: ResumeVersionItem;
  onClose: () => void;
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <div>
          <CardTitle className="text-base">
            对比 v{left.version_number} → v{right.version_number}
          </CardTitle>
          <CardDescription>
            左:v{left.version_number}({formatRelative(left.created_at)})· 右:v
            {right.version_number}({formatRelative(right.created_at)})
          </CardDescription>
        </div>
        <Button variant="outline" size="sm" onClick={onClose}>
          关闭
        </Button>
      </CardHeader>
      <CardContent>
        <MarkdownDiff original={left.markdown} modified={right.markdown} height="32rem" />
      </CardContent>
    </Card>
  );
}

function messageOf(err: unknown): string {
  if (err instanceof ApiError) {
    return err.problem.detail ?? err.problem.title ?? `HTTP ${err.status}`;
  }
  return err instanceof Error ? err.message : String(err);
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

type FindingActions = {
  activeIdx: number | null;
  dismissed: ReadonlySet<number>;
  obsolete: ReadonlySet<number>;
  adoptingIdx: number | null;
  adoptError: string | null;
  onActivate: (idx: number | null) => void;
  onAdopt: (idx: number) => void;
  onDismiss: (idx: number) => void;
};

function ReviewFailedBanner({
  findings,
  ...actions
}: { findings: readonly ReviewFinding[] } & FindingActions) {
  const high = findings.filter((f) => f.severity === 'high');
  return (
    <Card className="border-[var(--color-warning-fg)]/40 bg-[var(--color-warning-bg)]/40">
      <CardHeader>
        <CardTitle className="text-base text-[var(--color-warning-fg)]">
          ⚠ 事实核查未通过 — 请人工审阅
        </CardTitle>
        <CardDescription className="text-[var(--color-warning-fg)]/90">
          Reviewer 发现 {high.length} 条严重问题(共 {findings.length} 条标记)。简历正文仍展示
          供你修正,<span className="font-semibold">直接使用前请逐条核对</span>。
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ReviewFindingsList findings={findings} {...actions} />
      </CardContent>
    </Card>
  );
}

function ReviewFindingsCard({
  findings,
  ...actions
}: { findings: readonly ReviewFinding[] } & FindingActions) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">事实核查标记 · {findings.length}</CardTitle>
        <CardDescription>不阻断使用,但建议核对后再投递</CardDescription>
      </CardHeader>
      <CardContent>
        <ReviewFindingsList findings={findings} {...actions} />
      </CardContent>
    </Card>
  );
}

function ReviewFindingsList({
  findings,
  activeIdx,
  dismissed,
  obsolete,
  adoptingIdx,
  adoptError,
  onActivate,
  onAdopt,
  onDismiss,
}: { findings: readonly ReviewFinding[] } & FindingActions) {
  const visible = findings
    .map((f, i) => ({ f, i }))
    .filter(({ i }) => !dismissed.has(i));
  if (visible.length === 0) {
    return <p className="text-xs text-muted">所有标记已处理</p>;
  }
  // 排序:未处理在前,obsolete(已处理)在后
  visible.sort((a, b) => {
    const ao = obsolete.has(a.i) ? 1 : 0;
    const bo = obsolete.has(b.i) ? 1 : 0;
    return ao - bo;
  });
  return (
    <div className="space-y-2">
      {adoptError ? (
        <div className="rounded-md border border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 px-3 py-2 text-sm text-[var(--color-danger)]">
          {adoptError}
        </div>
      ) : null}
      <ul className="space-y-2">
        {visible.map(({ f, i }) => {
          const isActive = activeIdx === i;
          const isAdopting = adoptingIdx === i;
          const isObsolete = obsolete.has(i);
          return (
            <li
              key={`${f.section}-${i}`}
              className={`group cursor-pointer rounded-md border px-3 py-2 transition-colors ${
                SEVERITY_TONES[f.severity]
              } ${isActive ? 'ring-2 ring-[var(--color-accent)] ring-offset-1' : ''} ${
                isObsolete ? 'opacity-55' : ''
              }`}
              onClick={() => onActivate(isActive ? null : i)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onActivate(isActive ? null : i);
                }
              }}
              role="button"
              tabIndex={0}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-full bg-black/10 px-1.5 py-0.5 text-[10px] font-medium tracking-wider uppercase">
                  {SEVERITY_LABELS[f.severity]}
                </span>
                <span className="rounded-full bg-black/5 px-1.5 py-0.5 text-[10px]">
                  {ISSUE_LABELS[f.issue_type]}
                </span>
                <span className="text-xs opacity-70">{f.section}</span>
                {isObsolete ? (
                  <span
                    className="rounded-full bg-[var(--color-success-border)]/15 px-1.5 py-0.5 text-[10px] text-[var(--color-success-fg)]"
                    title="标记的内容已不在当前简历正文中(可能已被采纳或手动编辑掉)"
                  >
                    已处理
                  </span>
                ) : null}
              </div>
              <p className="mt-1.5 text-sm leading-5 italic opacity-90">"{f.quoted_text}"</p>
              <p className="mt-1 text-sm leading-5">{f.explanation}</p>
              <div
                className="mt-2 flex items-center gap-2"
                onClick={(e) => e.stopPropagation()}
                onKeyDown={(e) => e.stopPropagation()}
                role="presentation"
              >
                {!isObsolete ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => onAdopt(i)}
                    disabled={isAdopting}
                  >
                    {isAdopting ? '采纳中…' : '采纳(删除该片段)'}
                  </Button>
                ) : null}
                <Button variant="ghost" size="sm" onClick={() => onDismiss(i)}>
                  {isObsolete ? '从列表移除' : '忽略'}
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
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

function ResumeRender({
  markdown,
  activeQuoted,
}: {
  markdown: string;
  activeQuoted: string | null;
}) {
  const blocks = parseBlocks(markdown);
  // 高亮策略:
  //   1. 先 normalize 双方(空白 collapse + 中文标点全角→半角)再做 indexOf
  //   2. 命中后用 normalize 前的原始字符串去定位 inline 段
  //   3. 仍找不到 → 顶部 fallback 提示让用户知道这条 finding 是 LLM 标记
  //      位置不精确(对 LLM-as-reviewer 是常态),但还是滚到了 section
  // 预扫一遍判断是否能命中,不能命中 → 顶部出"未对上"提示。用与 Inline
  // 同一套 findQuotedInText(精确 → fuzzy regex → 头尾双锚)避免精度不一致。
  let matched = false;
  if (activeQuoted) {
    for (const b of blocks) {
      const blockTexts = b.kind === 'bullets' ? b.items : [b.text];
      for (const t of blockTexts) {
        if (findQuotedInText(t, activeQuoted) != null) {
          matched = true;
          break;
        }
      }
      if (matched) break;
    }
  }
  const ctx: HighlightCtx = {
    remaining: activeQuoted ? 1 : 0,
    quoted: activeQuoted,
  };
  return (
    <Card>
      <CardContent className="py-8">
        {activeQuoted && !matched ? (
          <div className="mb-4 rounded-md border border-[var(--color-warning-fg)]/30 bg-[var(--color-warning-bg)] px-3 py-2 text-xs text-[var(--color-warning-fg)]">
            ⚠ Reviewer 引用的"{activeQuoted.slice(0, 30)}
            {activeQuoted.length > 30 ? '…' : ''}"
            未与正文精确对上(可能是 LLM 标记时改写了标点 / 空白)。已滚动到对应章节,请人工核对。
          </div>
        ) : null}
        <article
          data-active-quoted={activeQuoted ?? undefined}
          className="space-y-4"
        >
          {blocks.map((b, i) => (
            <Block
              key={`${b.kind}-${i}-${b.kind === 'bullets' ? b.items.length : b.text.slice(0, 12)}`}
              block={b}
              ctx={ctx}
            />
          ))}
        </article>
      </CardContent>
    </Card>
  );
}

type HighlightCtx = {
  remaining: number;
  quoted: string | null;
};

/** 在 `text` 中找 `quoted` 的位置,返回原始 text 上的 [start, end);找不到
 *  返回 null。
 *
 *  策略:
 *  1. 精确 substring
 *  2. fallback 1 — fuzzy 正则吃掉 `\s+` 与中英标点等价(LLM reviewer 偶尔
 *     会把全角逗号写成半角或反之),返回 *原始 text* 上的实际匹配范围以
 *     便 slice 正确
 *  3. fallback 2 — 取 quoted 头 6 个非空白字符 + 末 6 个非空白字符做"锚定式"
 *     模糊匹配。reviewer 偶尔在中段加 / 删一两个字,锚 + 锚之间的 ≤ 3× 长度
 *     的内容包成一段命中
 *  全部失败返回 null。*/
function findQuotedInText(
  text: string,
  quoted: string,
): { start: number; end: number } | null {
  // 1. exact
  const exact = text.indexOf(quoted);
  if (exact >= 0) return { start: exact, end: exact + quoted.length };

  // 2. fuzzy regex — \s+ + 中文/英文标点等价
  try {
    const escaped = quoted.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const flexed = escaped
      .replace(/[,,]/g, '[,,]')
      .replace(/[.。]/g, '[.。]')
      .replace(/[;;]/g, '[;;]')
      .replace(/[::]/g, '[::]')
      .replace(/[!!]/g, '[!!]')
      .replace(/[??]/g, '[??]')
      .replace(/\s+/g, '\\s+');
    const m = text.match(new RegExp(flexed));
    if (m && m.index != null) {
      return { start: m.index, end: m.index + m[0].length };
    }
  } catch {
    // 过长 quoted 编出过大正则时,RegExp ctor 抛 — 安静吞掉走 fallback 3
  }

  // 3. anchor (head + tail) — 双锚 6 字符,quoted 完整出现在同一 block 时
  const stripped = quoted.replace(/\s+/g, '');
  if (stripped.length >= 12) {
    const head = stripped.slice(0, 6);
    const tail = stripped.slice(-6);
    const headIdx = text.indexOf(head);
    if (headIdx >= 0) {
      const tailIdx = text.indexOf(tail, headIdx + head.length);
      if (tailIdx >= 0 && tailIdx - headIdx < quoted.length * 3) {
        return { start: headIdx, end: tailIdx + tail.length };
      }
    }
  }

  // 4. head-only — 跨 block 的 quote(reviewer 引整章节)退化:本 block 含
  //    quote 起点片段就算命中,只标这一截。让用户至少看到正确的起点位置;
  //    完整内容仍在 finding 卡片里展示。
  //    用**原始** quoted 头 16 字符(保留空格 / 标点),指 reviewer 引文与
  //    正文那一段开头逐字一致 — 这是 LLM reviewer 最稳的匹配点。
  if (quoted.length >= 8) {
    const headLen = Math.min(16, quoted.length);
    const head = quoted.slice(0, headLen);
    const headIdx = text.indexOf(head);
    if (headIdx >= 0) {
      return { start: headIdx, end: headIdx + headLen };
    }
    // fuzzy 头 — 空白 / 标点等价
    try {
      const escaped = head.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const flexed = escaped
        .replace(/[,,]/g, '[,,]')
        .replace(/[.。]/g, '[.。]')
        .replace(/[;;]/g, '[;;]')
        .replace(/[::]/g, '[::]')
        .replace(/\s+/g, '\\s+');
      const m = text.match(new RegExp(flexed));
      if (m && m.index != null) {
        return { start: m.index, end: m.index + m[0].length };
      }
    } catch {
      // 忽略
    }
  }

  return null;
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

function Block({ block, ctx }: { block: Block; ctx: HighlightCtx }) {
  if (block.kind === 'h2') {
    const slug = sectionSlugOf(block.text);
    return (
      <h2
        id={slug ? `section-${slug}` : undefined}
        className="mt-2 scroll-mt-20 border-b border-border pb-2 text-lg font-semibold tracking-tight first:mt-0"
      >
        {block.text}
      </h2>
    );
  }
  if (block.kind === 'bullets') {
    return (
      <ul className="list-disc space-y-1 pl-5 text-sm leading-6">
        {block.items.map((it, i) => (
          <li key={`${i}-${it.slice(0, 16)}`}>
            <Inline text={it} ctx={ctx} />
          </li>
        ))}
      </ul>
    );
  }
  return (
    <p className="text-sm leading-6 whitespace-pre-wrap">
      <Inline text={block.text} ctx={ctx} />
    </p>
  );
}

function Inline({ text, ctx }: { text: string; ctx: HighlightCtx }) {
  // 高亮 reviewer 标记的 quoted_text:简化策略 — 仅当片段完整出现在本行内
  // (跨段落的暂不支持)且高亮余额 > 0 时,把首处出现拆出 <mark>。`<mark>`
  // 内仍要支持 **bold** 内联,故先按 quoted 拆,再各段按 bold 拆。
  const quoted = ctx.quoted;
  if (quoted && ctx.remaining > 0) {
    const span = findQuotedInText(text, quoted);
    if (span != null) {
      ctx.remaining -= 1;
      // Tailwind 4 preflight 会把 <mark> 默认黄底重置掉,直接 inline style 强
      // 制上色;同时 box-shadow 顶下避免靠 ring(`ring-*` 也可能被 utility
      // 顺序压住)。颜色:macOS Highlights 的暖黄 + 浅边线。
      return (
        <>
          <BoldInline text={text.slice(0, span.start)} />
          <mark
            data-finding-highlight
            style={{
              backgroundColor: 'rgba(253, 224, 71, 0.85)',
              color: '#1d1d1f',
              padding: '0 2px',
              borderRadius: '3px',
              boxShadow: '0 0 0 1px rgba(202, 138, 4, 0.55)',
            }}
          >
            <BoldInline text={text.slice(span.start, span.end)} />
          </mark>
          <BoldInline text={text.slice(span.end)} />
        </>
      );
    }
  }
  return <BoldInline text={text} />;
}

function BoldInline({ text }: { text: string }) {
  // `**bold**` 内联;遇到 ** 拆段交替渲染。
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

// ---------------------------------------------------------------------------
// Reviewer 标记交互助手(W8)
// ---------------------------------------------------------------------------

const SECTION_SLUGS: Record<string, string> = {
  基本信息: 'basic',
  求职意向: 'intent',
  专业概要: 'summary',
  工作经历: 'work',
  项目经历: 'projects',
  技能: 'skills',
  教育背景: 'education',
};

function sectionSlugOf(section: string): string | null {
  // reviewer.section 形如 '## 项目经历';drafter h2 同款。统一去 # 前缀。
  const trimmed = section.replace(/^#+\s*/, '').trim();
  return SECTION_SLUGS[trimmed] ?? null;
}

/** 从 markdown 中删除首处 `quoted_text`,顺手清理常见连接符 / 空行 / 空 bullet。
 *  匹配失败时返回原文(由 caller 提示用户手动修订)。*/
function stripQuoted(md: string, quoted: string): string {
  if (!quoted) return md;
  const idx = md.indexOf(quoted);
  if (idx < 0) return md;
  let next = md.slice(0, idx) + md.slice(idx + quoted.length);
  // 行首孤立连接符:如 "Python(精通)、Golang..." 删 "Python(精通)" 后留下
  // "、Golang..." — 把行首的中英标点 + 紧跟空白吃掉
  next = next.replace(/^[,,;;、]\s*/gm, '');
  // 行内孤立连接符紧接着断点(句号 / 问号 / 换行 / EOF)— 吞掉前面的连接符
  next = next.replace(/[,,;;、]\s*([。.!!??\n]|$)/g, '$1');
  // 行内多空白 collapse
  next = next.replace(/[ \t]{2,}/g, ' ');
  // 空 bullet / 空 heading 行整行删除
  next = next
    .split('\n')
    .filter((line) => {
      const t = line.trim();
      return t !== '-' && t !== '*' && t !== '##' && t !== '#';
    })
    .join('\n');
  // 三连空行 collapse 为双空行
  next = next.replace(/\n{3,}/g, '\n\n');
  return next;
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
