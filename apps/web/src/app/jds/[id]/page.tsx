import Link from 'next/link';
import { notFound } from 'next/navigation';

import { ApiError, type JDDetail, getJd, listProfiles } from '@/lib/api';

import { JdEditForm } from './jd-edit-form';
import { MatchTrigger } from './match-trigger';

export default async function JdDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const jdId = Number.parseInt(id, 10);
  if (!Number.isFinite(jdId) || jdId <= 0) {
    notFound();
  }

  let jd: JDDetail;
  try {
    jd = await getJd(jdId);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      notFound();
    }
    throw err;
  }

  // M1 partial unique: 单 user 单 profile,直接用 list 第一项作为本次匹配的 profile。
  // M3+ 多 profile 时再加挑选 UI。
  const profilesResp = await listProfiles({ limit: 1 }).catch(() => null);
  const profile = profilesResp?.data[0] ?? null;

  return (
    <div className="mx-auto max-w-3xl px-6 py-16">
      <div className="mb-6">
        <Link href="/jds" className="text-sm text-muted hover:text-foreground">
          ← 全部 JD
        </Link>
      </div>
      <JdEditForm jd={jd} />
      <div className="mt-8">
        <MatchTrigger
          jdId={jd.id}
          jdParsed={jd.status === 'parsed'}
          profileId={profile?.id ?? null}
          profileParsed={profile?.status === 'parsed'}
        />
      </div>
    </div>
  );
}
