import Link from 'next/link';
import { notFound } from 'next/navigation';

import { ApiError, type MatchDetail, getMatch } from '@/lib/api';

import { MatchResultView } from './match-result';

export default async function MatchDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const matchId = Number.parseInt(id, 10);
  if (!Number.isFinite(matchId) || matchId <= 0) {
    notFound();
  }

  let match: MatchDetail;
  try {
    match = await getMatch(matchId);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      notFound();
    }
    throw err;
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      <div className="mb-6">
        <Link href="/matches" className="text-sm text-muted hover:text-foreground">
          ← 全部匹配
        </Link>
      </div>
      <MatchResultView match={match} />
    </div>
  );
}
