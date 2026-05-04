import Link from 'next/link';
import { notFound } from 'next/navigation';

import { ApiError, type ResumeDetail, getResume } from '@/lib/api';

import { ResumeDetailView } from './resume-detail';

export default async function ResumeDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const resumeId = Number.parseInt(id, 10);
  if (!Number.isFinite(resumeId) || resumeId <= 0) {
    notFound();
  }

  let resume: ResumeDetail;
  try {
    resume = await getResume(resumeId);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      notFound();
    }
    throw err;
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      <div className="mb-6">
        <Link href="/resumes" className="text-sm text-muted hover:text-foreground">
          ← 全部定制简历
        </Link>
      </div>
      <ResumeDetailView resume={resume} />
    </div>
  );
}
