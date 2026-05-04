import Link from 'next/link';
import { notFound } from 'next/navigation';

import { ApiError, type JDDetail, getJd } from '@/lib/api';

import { JdEditForm } from './jd-edit-form';

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

  return (
    <div className="mx-auto max-w-3xl px-6 py-16">
      <div className="mb-6">
        <Link href="/" className="text-sm text-muted hover:text-foreground">
          ← 返回首页
        </Link>
      </div>
      <JdEditForm jd={jd} />
    </div>
  );
}
