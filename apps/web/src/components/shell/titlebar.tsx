'use client';

import { usePathname } from 'next/navigation';

function resolveTitle(pathname: string): string {
  if (pathname === '/') return 'JobCopilot';
  if (pathname === '/jds/new') return '新建 JD';
  if (pathname.startsWith('/jds/')) return 'JD 详情';
  if (pathname === '/profiles/new') return '新建简历';
  if (pathname.startsWith('/profiles/')) return '简历详情';
  return 'JobCopilot';
}

export function TitleBar() {
  const pathname = usePathname();
  const title = resolveTitle(pathname);

  return (
    <header className="vibrancy-titlebar sticky top-0 z-10 flex h-11 items-center justify-center border-b border-border px-4">
      <div className="text-[13px] font-semibold tracking-tight text-foreground">{title}</div>
    </header>
  );
}
