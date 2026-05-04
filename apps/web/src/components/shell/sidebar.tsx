'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

type NavItem = { href: string; label: string; icon: ReactNode };
type NavGroup = { title?: string; items: NavItem[] };

const NAV: NavGroup[] = [
  { items: [{ href: '/', label: '首页', icon: <HomeIcon /> }] },
  { title: '工作', items: [{ href: '/jds/new', label: '新建 JD', icon: <DocIcon /> }] },
  { title: '简历', items: [{ href: '/profiles/new', label: '新建简历', icon: <UserIcon /> }] },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="vibrancy-sidebar flex h-screen w-[220px] flex-col border-r border-border">
      <div className="flex h-11 items-center gap-2 px-4">
        <span className="size-3 rounded-full bg-[var(--color-traffic-close)]" aria-hidden />
        <span className="size-3 rounded-full bg-[var(--color-traffic-min)]" aria-hidden />
        <span className="size-3 rounded-full bg-[var(--color-traffic-max)]" aria-hidden />
      </div>

      <nav className="flex-1 overflow-y-auto px-3 pb-6">
        {NAV.map((group, gi) => (
          <div key={gi} className="mt-4 first:mt-2">
            {group.title ? (
              <div className="px-2 pb-1 text-[11px] font-semibold tracking-wider text-muted uppercase">
                {group.title}
              </div>
            ) : null}
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const active =
                  item.href === '/'
                    ? pathname === '/'
                    : pathname === item.href || pathname.startsWith(`${item.href}/`);
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className={cn(
                        'flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px] transition-colors duration-150 ease-apple',
                        active
                          ? 'bg-[var(--color-selection)] text-[var(--color-selection-fg)]'
                          : 'text-foreground hover:bg-black/[0.04]',
                      )}
                    >
                      <span className="flex size-4 items-center justify-center opacity-80">
                        {item.icon}
                      </span>
                      <span className="truncate">{item.label}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
    </aside>
  );
}

function HomeIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
      <path d="M2 7.5 8 2.5l6 5V13a1 1 0 0 1-1 1h-3v-4H6v4H3a1 1 0 0 1-1-1V7.5Z" />
    </svg>
  );
}

function DocIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
      <path d="M3.5 2h6L13 5.5V14a.5.5 0 0 1-.5.5h-9A.5.5 0 0 1 3 14V2.5a.5.5 0 0 1 .5-.5Z" />
      <path d="M9.5 2v3.5H13" />
    </svg>
  );
}

function UserIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
      <circle cx="8" cy="6" r="2.5" />
      <path d="M3 13.5c.8-2.4 2.8-3.5 5-3.5s4.2 1.1 5 3.5" />
    </svg>
  );
}
