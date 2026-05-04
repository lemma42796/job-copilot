import Link from 'next/link';
import { notFound } from 'next/navigation';

import { ApiError, type ProfileDetail, getProfile } from '@/lib/api';

import { ChunksDebugSection } from './chunks-debug';
import { ProfileEditForm } from './profile-edit-form';

export default async function ProfileDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const profileId = Number.parseInt(id, 10);
  if (!Number.isFinite(profileId) || profileId <= 0) {
    notFound();
  }

  let profile: ProfileDetail;
  try {
    profile = await getProfile(profileId);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      notFound();
    }
    throw err;
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-12">
      <div className="mb-6 flex items-center justify-between">
        <Link href="/" className="text-sm text-muted hover:text-foreground">
          ← 返回首页
        </Link>
      </div>
      <ProfileEditForm profile={profile} />
      <div className="mt-8">
        <ChunksDebugSection profileId={profile.id} />
      </div>
    </div>
  );
}
