'use client';

import { usePathname } from 'next/navigation';

import { SessionBadge } from './session-badge';

function resolveTitle(pathname: string): string {
  if (pathname === '/') return 'JobCopilot';
  return 'JobCopilot';
}

export function TitleBar() {
  const pathname = usePathname();
  const title = resolveTitle(pathname);

  return (
    <header className="vibrancy-titlebar sticky top-0 z-10 flex h-11 items-center justify-between border-b border-border px-4">
      <div className="w-40" />
      <div className="text-[13px] font-semibold tracking-tight text-foreground">{title}</div>
      <div className="flex w-40 justify-end">
        <SessionBadge />
      </div>
    </header>
  );
}
