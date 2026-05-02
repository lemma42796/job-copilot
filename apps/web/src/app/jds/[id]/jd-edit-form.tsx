'use client';

import { JDStructuredSalary_period } from '@jobcopilot/schemas';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { ApiError, type JDDetail, type JDSkill, type JDStructured, patchJd } from '@/lib/api';

type EditableState = {
  company: string;
  title: string;
  location: string;
  salary_min: string;
  salary_max: string;
  description: string;
};

function detailToEditable(jd: JDDetail): EditableState {
  return {
    company: jd.company ?? '',
    title: jd.title ?? '',
    location: jd.location ?? '',
    salary_min: jd.salary_min == null ? '' : String(jd.salary_min),
    salary_max: jd.salary_max == null ? '' : String(jd.salary_max),
    description: jd.description ?? '',
  };
}

function buildStructured(jd: JDDetail, edits: EditableState): JDStructured {
  const toInt = (s: string): number | null => {
    const t = s.trim();
    if (t === '') return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  };
  const salaryPeriod =
    jd.salary_period === 'yearly'
      ? JDStructuredSalary_period.yearly
      : JDStructuredSalary_period.monthly;
  return {
    company: edits.company.trim() || null,
    title: edits.title.trim(),
    location: edits.location.trim() || null,
    salary_min: toInt(edits.salary_min),
    salary_max: toInt(edits.salary_max),
    salary_period: salaryPeriod,
    job_level: jd.job_level as JDStructured['job_level'],
    years_required: jd.years_required,
    education: jd.education as JDStructured['education'],
    hard_skills: jd.hard_skills,
    soft_skills: jd.soft_skills,
    bonus_skills: jd.bonus_skills,
    responsibilities: jd.responsibilities,
    description: edits.description,
    confidence: Number(jd.parse_confidence ?? '0'),
  };
}

function StatusBadge({ status }: { status: JDDetail['status'] }) {
  const map: Record<JDDetail['status'], { label: string; className: string }> = {
    parsing: { label: '解析中', className: 'bg-yellow-500/20 text-yellow-300' },
    parsed: { label: '已解析', className: 'bg-green-500/20 text-green-300' },
    parse_failed: {
      label: '解析失败',
      className: 'bg-[var(--color-danger)]/20 text-[var(--color-danger)]',
    },
  };
  const v = map[status];
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${v.className}`}>{v.label}</span>
  );
}

function SkillList({ title, skills }: { title: string; skills: readonly JDSkill[] }) {
  if (skills.length === 0) return null;
  return (
    <div>
      <p className="text-sm font-medium">{title}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {skills.map((s) => (
          <span
            key={s.name}
            className="rounded border border-border bg-input px-2 py-0.5 text-xs"
            title={`weight ${s.weight} · ${s.required ? 'required' : 'nice'}`}
          >
            {s.name_raw}
          </span>
        ))}
      </div>
    </div>
  );
}

export function JdEditForm({ jd }: { jd: JDDetail }) {
  const [form, setForm] = React.useState<EditableState>(() => detailToEditable(jd));
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [savedAt, setSavedAt] = React.useState<string | null>(null);
  const [currentJd, setCurrentJd] = React.useState<JDDetail>(jd);

  function update<K extends keyof EditableState>(key: K, value: EditableState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
    setSavedAt(null);
  }

  async function onSave() {
    setError(null);
    if (form.title.trim() === '') {
      setError('职位名称不能为空');
      return;
    }
    setSaving(true);
    try {
      const updated = await patchJd(jd.id, {
        structured: buildStructured(currentJd, form),
      });
      setCurrentJd(updated);
      setForm(detailToEditable(updated));
      setSavedAt(new Date().toLocaleTimeString());
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.problem.detail ?? err.problem.title ?? `HTTP ${err.status}`);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setSaving(false);
    }
  }

  const confidence =
    currentJd.parse_confidence == null
      ? null
      : Math.round(Number(currentJd.parse_confidence) * 100);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>JD #{currentJd.id}</CardTitle>
            <StatusBadge status={currentJd.status} />
          </div>
          <CardDescription>
            {confidence != null ? <>置信度 {confidence}% · </> : null}
            模型 {currentJd.parse_model ?? '—'} · cost ¥{currentJd.parse_cost_cny ?? '0'} · 创建于{' '}
            {new Date(currentJd.created_at).toLocaleString()}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="title">职位 *</Label>
              <Input
                id="title"
                value={form.title}
                onChange={(e) => update('title', e.target.value)}
                disabled={saving}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="company">公司</Label>
              <Input
                id="company"
                value={form.company}
                onChange={(e) => update('company', e.target.value)}
                disabled={saving}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="location">地点</Label>
              <Input
                id="location"
                value={form.location}
                onChange={(e) => update('location', e.target.value)}
                disabled={saving}
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-2">
                <Label htmlFor="salary_min">薪资下限</Label>
                <Input
                  id="salary_min"
                  type="number"
                  inputMode="numeric"
                  value={form.salary_min}
                  onChange={(e) => update('salary_min', e.target.value)}
                  disabled={saving}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="salary_max">薪资上限</Label>
                <Input
                  id="salary_max"
                  type="number"
                  inputMode="numeric"
                  value={form.salary_max}
                  onChange={(e) => update('salary_max', e.target.value)}
                  disabled={saving}
                />
              </div>
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="description">描述</Label>
            <Textarea
              id="description"
              rows={6}
              value={form.description}
              onChange={(e) => update('description', e.target.value)}
              disabled={saving}
            />
          </div>

          {error ? <p className="text-sm text-[var(--color-danger)]">{error}</p> : null}

          <div className="flex items-center justify-end gap-3">
            {savedAt ? <span className="text-xs text-muted">已保存 {savedAt}</span> : null}
            <Button onClick={onSave} disabled={saving}>
              {saving ? '保存中…' : '保存'}
            </Button>
          </div>
        </CardContent>
      </Card>

      {(currentJd.hard_skills.length > 0 ||
        currentJd.soft_skills.length > 0 ||
        currentJd.bonus_skills.length > 0) && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">技能</CardTitle>
            <CardDescription>S5 第一刀只读;后续支持编辑</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <SkillList title="硬技能" skills={currentJd.hard_skills} />
            <SkillList title="软技能" skills={currentJd.soft_skills} />
            <SkillList title="加分项" skills={currentJd.bonus_skills} />
          </CardContent>
        </Card>
      )}

      {currentJd.responsibilities.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">职责</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {currentJd.responsibilities.map((r, i) => (
                <li key={`${i}-${r.slice(0, 16)}`}>{r}</li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
