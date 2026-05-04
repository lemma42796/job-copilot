'use client';

import { useRouter } from 'next/navigation';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  ApiError,
  type ProfileDetail,
  type ProfileEducationItem,
  type ProfileExperienceItem,
  type ProfileProjectItem,
  type ProfileSkillItem,
  type ProfileStructured,
  deleteProfile,
  patchProfile,
} from '@/lib/api';

type EditableTop = {
  full_name: string;
  phone: string;
  email: string;
  location: string;
  summary: string;
};

function topFromProfile(p: ProfileDetail): EditableTop {
  const s = p.structured;
  return {
    full_name: s.full_name ?? '',
    phone: s.phone ?? '',
    email: s.email ?? '',
    location: s.location ?? '',
    summary: s.summary ?? '',
  };
}

function buildStructured(p: ProfileDetail, top: EditableTop): ProfileStructured {
  const s = p.structured;
  return {
    full_name: top.full_name.trim() || null,
    phone: top.phone.trim() || null,
    email: top.email.trim() || null,
    location: top.location.trim() || null,
    summary: top.summary.trim() || null,
    target_titles: s.target_titles ?? [],
    experiences: s.experiences ?? [],
    projects: s.projects ?? [],
    skills: s.skills ?? [],
    educations: s.educations ?? [],
  };
}

function StatusBadge({ status }: { status: ProfileDetail['status'] }) {
  const map: Record<ProfileDetail['status'], { label: string; cls: string }> = {
    parsing: { label: '解析中', cls: 'bg-yellow-500/20 text-yellow-300' },
    parsed: { label: '已解析', cls: 'bg-green-500/20 text-green-300' },
    parse_failed: {
      label: '解析失败',
      cls: 'bg-[var(--color-danger)]/20 text-[var(--color-danger)]',
    },
  };
  const v = map[status];
  return <span className={`rounded px-2 py-0.5 text-xs font-medium ${v.cls}`}>{v.label}</span>;
}

export function ProfileEditForm({ profile }: { profile: ProfileDetail }) {
  const router = useRouter();
  const [current, setCurrent] = React.useState(profile);
  const [form, setForm] = React.useState<EditableTop>(() => topFromProfile(profile));
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [savedAt, setSavedAt] = React.useState<string | null>(null);
  const [deleting, setDeleting] = React.useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = React.useState(false);

  function update<K extends keyof EditableTop>(key: K, value: EditableTop[K]) {
    setForm((p) => ({ ...p, [key]: value }));
    setSavedAt(null);
  }

  async function onSave() {
    setError(null);
    setSaving(true);
    try {
      const updated = await patchProfile(current.id, {
        structured: buildStructured(current, form),
      });
      setCurrent(updated);
      setForm(topFromProfile(updated));
      setSavedAt(new Date().toLocaleTimeString());
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setSaving(false);
    }
  }

  async function onDelete() {
    setDeleting(true);
    try {
      await deleteProfile(current.id);
      router.push('/');
    } catch (err) {
      setError(messageOf(err));
      setDeleting(false);
      setShowDeleteConfirm(false);
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between">
            <div>
              <CardTitle>简历 #{current.id}</CardTitle>
              <CardDescription className="mt-1 flex items-center gap-2">
                <StatusBadge status={current.status} />
                <span className="text-xs text-muted">
                  来源 {current.source} · 模型 {current.parse_model ?? '—'} · cost ¥
                  {current.parse_cost_cny ?? '0'} · {new Date(current.created_at).toLocaleString()}
                </span>
              </CardDescription>
            </div>
            <div className="flex flex-col items-end gap-2">
              {showDeleteConfirm ? (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted">确认删除?</span>
                  <Button size="sm" variant="destructive" onClick={onDelete} disabled={deleting}>
                    {deleting ? '删除中…' : '确认'}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setShowDeleteConfirm(false)}
                    disabled={deleting}
                  >
                    取消
                  </Button>
                </div>
              ) : (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setShowDeleteConfirm(true)}
                  disabled={saving}
                >
                  删除
                </Button>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <StatsRow detail={current} />

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <FieldText
              id="full_name"
              label="姓名"
              value={form.full_name}
              onChange={(v) => update('full_name', v)}
              disabled={saving}
            />
            <FieldText
              id="email"
              label="邮箱"
              value={form.email}
              onChange={(v) => update('email', v)}
              disabled={saving}
            />
            <FieldText
              id="phone"
              label="电话"
              value={form.phone}
              onChange={(v) => update('phone', v)}
              disabled={saving}
            />
            <FieldText
              id="location"
              label="所在地"
              value={form.location}
              onChange={(v) => update('location', v)}
              disabled={saving}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="summary">简介</Label>
            <Textarea
              id="summary"
              rows={4}
              value={form.summary}
              onChange={(e) => update('summary', e.target.value)}
              disabled={saving}
            />
          </div>

          {error ? <p className="text-sm text-[var(--color-danger)]">{error}</p> : null}

          <div className="flex items-center justify-end gap-3">
            {savedAt ? (
              <span className="text-xs text-muted">
                已保存 {savedAt} · 内容变更后建议到下方调试区重建 chunks
              </span>
            ) : null}
            <Button onClick={onSave} disabled={saving}>
              {saving ? '保存中…' : '保存'}
            </Button>
          </div>
        </CardContent>
      </Card>

      <ListSection
        title="工作经历"
        empty="暂无工作经历"
        items={current.structured.experiences ?? []}
        renderKey={(e, i) => `${i}-${e.company}`}
        render={renderExperience}
      />
      <ListSection
        title="项目"
        empty="暂无项目"
        items={current.structured.projects ?? []}
        renderKey={(p, i) => `${i}-${p.name}`}
        render={renderProject}
      />
      <SkillsSection skills={current.structured.skills ?? []} />
      <ListSection
        title="教育"
        empty="暂无教育经历"
        items={current.structured.educations ?? []}
        renderKey={(e, i) => `${i}-${e.school}`}
        render={renderEducation}
      />
    </div>
  );
}

function StatsRow({ detail }: { detail: ProfileDetail }) {
  // 技能数与 SkillsSection 展示一致:LLM 把组合工具拆成多 skill 但 name_raw
  // 填整段原文,前端按 (name_raw, category) 去重展示;stats 也用同口径,
  // 不用 detail.stats.skills(DB 真实行数,会与 UI 对不上)。
  const skillsShown = dedupSkillsByDisplay(detail.structured.skills ?? []).length;
  const items: Array<[string, number]> = [
    ['工作', detail.stats.experiences],
    ['项目', detail.stats.projects],
    ['技能', skillsShown],
    ['教育', detail.stats.educations],
    ['chunks', detail.stats.chunks],
  ];
  return (
    <div className="flex flex-wrap gap-3 text-xs text-muted">
      {items.map(([label, n]) => (
        <span key={label} className="rounded border border-border bg-input px-2 py-1">
          {label} <span className="font-medium text-foreground">{n}</span>
        </span>
      ))}
    </div>
  );
}

function FieldText({
  id,
  label,
  value,
  onChange,
  disabled,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} />
    </div>
  );
}

function ListSection<T>({
  title,
  empty,
  items,
  renderKey,
  render,
}: {
  title: string;
  empty: string;
  items: readonly T[];
  renderKey: (item: T, index: number) => string;
  render: (item: T) => React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          {title} <span className="text-xs text-muted">({items.length})</span>
        </CardTitle>
        <CardDescription>当前只读,编辑能力后续切片再开</CardDescription>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <p className="text-sm text-muted">{empty}</p>
        ) : (
          <ul className="space-y-3">
            {items.map((item, i) => (
              <li
                key={renderKey(item, i)}
                className="rounded border border-border bg-input/40 p-3 text-sm"
              >
                {render(item)}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function renderExperience(e: ProfileExperienceItem): React.ReactNode {
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-medium">
          {e.title} · {e.company}
        </span>
        <span className="text-xs text-muted">
          {fmtMonth(e.start_date)} ~ {e.is_current ? '至今' : fmtMonth(e.end_date)}
        </span>
      </div>
      {e.location ? <p className="text-xs text-muted">{e.location}</p> : null}
      {e.description ? <p className="whitespace-pre-line">{e.description}</p> : null}
      {e.bullets && e.bullets.length > 0 ? (
        <ul className="ml-4 list-disc space-y-0.5">
          {e.bullets.map((b, i) => (
            <li key={`${i}-${b.slice(0, 12)}`}>{b}</li>
          ))}
        </ul>
      ) : null}
      {e.tech_stack && e.tech_stack.length > 0 ? (
        <p className="text-xs text-muted">技术栈:{e.tech_stack.join(', ')}</p>
      ) : null}
    </div>
  );
}

function renderProject(p: ProfileProjectItem): React.ReactNode {
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-medium">
          {p.name}
          {p.role ? ` · ${p.role}` : ''}
        </span>
        <span className="text-xs text-muted">
          {fmtMonth(p.start_date)} ~ {fmtMonth(p.end_date)}
        </span>
      </div>
      {p.description ? <p className="whitespace-pre-line">{p.description}</p> : null}
      {p.bullets && p.bullets.length > 0 ? (
        <ul className="ml-4 list-disc space-y-0.5">
          {p.bullets.map((b, i) => (
            <li key={`${i}-${b.slice(0, 12)}`}>{b}</li>
          ))}
        </ul>
      ) : null}
      {p.tech_stack && p.tech_stack.length > 0 ? (
        <p className="text-xs text-muted">技术栈:{p.tech_stack.join(', ')}</p>
      ) : null}
      {p.repo_url ? (
        <p className="text-xs text-muted">
          <a href={p.repo_url} target="_blank" rel="noreferrer" className="underline">
            {p.repo_url}
          </a>
        </p>
      ) : null}
    </div>
  );
}

// LLM 把组合工具(`Prometheus + Grafana` / `OpenAI / Anthropic API`)拆成
// 多个 skill 但 name_raw 都填整段原文,按 name 去重不够,展示会重复。
// 按 (name_raw, category) 去重,只展示一次。stats.skills 保留 DB 真实数。
function dedupSkillsByDisplay(skills: readonly ProfileSkillItem[]): ProfileSkillItem[] {
  const seen = new Map<string, ProfileSkillItem>();
  for (const s of skills) {
    const key = `${(s.name_raw ?? s.name).toLowerCase()}|${s.category ?? ''}`;
    if (!seen.has(key)) seen.set(key, s);
  }
  return Array.from(seen.values());
}

const SKILL_CATEGORY_ORDER: readonly string[] = [
  'language',
  'framework',
  'database',
  'tool',
  'cloud',
  'other',
];

const SKILL_CATEGORY_LABEL: Record<string, string> = {
  language: '编程语言',
  framework: '框架',
  database: '数据库',
  tool: '工具',
  cloud: '云原生',
  other: '其他',
};

function groupSkillsByCategory(
  skills: readonly ProfileSkillItem[],
): Array<[string, ProfileSkillItem[]]> {
  const groups = new Map<string, ProfileSkillItem[]>();
  for (const s of skills) {
    const cat = s.category ?? 'other';
    const list = groups.get(cat) ?? [];
    list.push(s);
    groups.set(cat, list);
  }
  return [...groups.entries()].sort(([a], [b]) => {
    const ai = SKILL_CATEGORY_ORDER.indexOf(a);
    const bi = SKILL_CATEGORY_ORDER.indexOf(b);
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
  });
}

function SkillsSection({ skills }: { skills: readonly ProfileSkillItem[] }) {
  const deduped = dedupSkillsByDisplay(skills);
  const groups = groupSkillsByCategory(deduped);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          技能 <span className="text-xs text-muted">({deduped.length})</span>
        </CardTitle>
        <CardDescription>当前只读,编辑能力后续切片再开</CardDescription>
      </CardHeader>
      <CardContent>
        {deduped.length === 0 ? (
          <p className="text-sm text-muted">暂无技能</p>
        ) : (
          <div className="grid grid-cols-[5rem_1fr] gap-x-3 gap-y-3">
            {groups.map(([cat, list]) => (
              <React.Fragment key={cat}>
                <div className="pt-1 text-xs tracking-wide text-muted">
                  {SKILL_CATEGORY_LABEL[cat] ?? cat}
                </div>
                <div className="flex flex-wrap gap-2">
                  {list.map((s) => {
                    const meta = [s.level, s.years != null ? `${s.years}y` : null]
                      .filter(Boolean)
                      .join(' · ');
                    return (
                      <span
                        key={s.name}
                        className="rounded border border-border bg-input/40 px-2 py-0.5 text-xs whitespace-nowrap"
                      >
                        <span className="font-medium">{s.name_raw}</span>
                        {meta ? <span className="ml-1 text-muted">{meta}</span> : null}
                      </span>
                    );
                  })}
                </div>
              </React.Fragment>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function renderEducation(e: ProfileEducationItem): React.ReactNode {
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-medium">
          {e.school}
          {e.major ? ` · ${e.major}` : ''}
        </span>
        <span className="text-xs text-muted">
          {fmtMonth(e.start_date)} ~ {fmtMonth(e.end_date)}
        </span>
      </div>
      {e.degree ? <p className="text-xs text-muted">{e.degree}</p> : null}
      {e.gpa != null ? <p className="text-xs text-muted">GPA {e.gpa}</p> : null}
    </div>
  );
}

function fmtMonth(d: string | null | undefined): string {
  if (!d) return '—';
  const m = d.match(/^(\d{4})-(\d{2})/);
  return m ? `${m[1]}-${m[2]}` : d;
}

function messageOf(err: unknown): string {
  if (err instanceof ApiError) {
    return err.problem.detail ?? err.problem.title ?? `HTTP ${err.status}`;
  }
  return err instanceof Error ? err.message : String(err);
}
