'use client';

import type { Route } from 'next';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

type NavItem = { href: Route; label: string; icon: ReactNode; match?: 'exact' | 'prefix' };
type NavGroup = { key: string; title?: string; items: NavItem[] };

const NAV: NavGroup[] = [
  { key: 'home', items: [{ href: '/', label: '首页', icon: <HomeIcon />, match: 'exact' }] },
];

function isActive(item: NavItem, pathname: string, siblings: readonly NavItem[]): boolean {
  if (item.match === 'exact') return pathname === item.href;
  if (pathname === item.href) return true;
  if (!pathname.startsWith(`${item.href}/`)) return false;
  return !siblings.some(
    (s) => s !== item && (pathname === s.href || pathname.startsWith(`${s.href}/`)),
  );
}

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="vibrancy-sidebar flex h-screen w-[220px] flex-col border-r border-border">
      <div className="flex h-11 items-center gap-2 px-4">
        <span className="size-3 rounded-full bg-[var(--color-traffic-close)]" aria-hidden="true" />
        <span className="size-3 rounded-full bg-[var(--color-traffic-min)]" aria-hidden="true" />
        <span className="size-3 rounded-full bg-[var(--color-traffic-max)]" aria-hidden="true" />
      </div>

      <nav className="flex-1 overflow-y-auto px-3 pb-6">
        {NAV.map((group) => (
          <div key={group.key} className="mt-4 first:mt-2">
            {group.title ? (
              <div className="px-2 pb-1 text-[11px] font-semibold tracking-wider text-muted uppercase">
                {group.title}
              </div>
            ) : null}
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const active = isActive(item, pathname, group.items);
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
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path d="M2 7.5 8 2.5l6 5V13a1 1 0 0 1-1 1h-3v-4H6v4H3a1 1 0 0 1-1-1V7.5Z" />
    </svg>
  );
}
