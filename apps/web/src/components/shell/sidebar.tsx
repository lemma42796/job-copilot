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
  {
    key: 'jobs',
    title: '工作',
    items: [
      { href: '/jds', label: '全部 JD', icon: <ListIcon />, match: 'prefix' },
      { href: '/jds/new', label: '新建 JD', icon: <DocIcon />, match: 'exact' },
    ],
  },
  {
    key: 'profiles',
    title: '简历',
    items: [
      { href: '/profiles', label: '全部简历', icon: <ListIcon />, match: 'prefix' },
      { href: '/profiles/new', label: '新建简历', icon: <UserIcon />, match: 'exact' },
    ],
  },
  {
    key: 'matches',
    title: '匹配',
    items: [{ href: '/matches', label: '全部匹配', icon: <SparkIcon />, match: 'prefix' }],
  },
  {
    key: 'resumes',
    title: '简历定制',
    items: [{ href: '/resumes', label: '全部定制简历', icon: <PenIcon />, match: 'prefix' }],
  },
];

function isActive(item: NavItem, pathname: string, siblings: readonly NavItem[]): boolean {
  if (item.match === 'exact') return pathname === item.href;
  if (pathname === item.href) return true;
  if (!pathname.startsWith(`${item.href}/`)) return false;
  // prefix match — yield to sibling whose exact href is a longer prefix (e.g. /jds/new under /jds)
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

function DocIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path d="M3.5 2h6L13 5.5V14a.5.5 0 0 1-.5.5h-9A.5.5 0 0 1 3 14V2.5a.5.5 0 0 1 .5-.5Z" />
      <path d="M9.5 2v3.5H13" />
    </svg>
  );
}

function UserIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <circle cx="8" cy="6" r="2.5" />
      <path d="M3 13.5c.8-2.4 2.8-3.5 5-3.5s4.2 1.1 5 3.5" />
    </svg>
  );
}

function ListIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path d="M5 4h8M5 8h8M5 12h8" strokeLinecap="round" />
      <circle cx="2.75" cy="4" r="0.6" fill="currentColor" stroke="none" />
      <circle cx="2.75" cy="8" r="0.6" fill="currentColor" stroke="none" />
      <circle cx="2.75" cy="12" r="0.6" fill="currentColor" stroke="none" />
    </svg>
  );
}

function SparkIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path
        d="M8 2 L9.5 6.5 L14 8 L9.5 9.5 L8 14 L6.5 9.5 L2 8 L6.5 6.5 Z"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function PenIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path d="M11.5 2.5 13.5 4.5 5 13H3v-2L11.5 2.5Z" strokeLinejoin="round" />
      <path d="M10 4 12 6" strokeLinecap="round" />
    </svg>
  );
}
