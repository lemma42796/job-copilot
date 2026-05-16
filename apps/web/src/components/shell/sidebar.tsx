'use client';

import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import type { Route } from 'next';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';
import { useEffect, useState } from 'react';

import { cn } from '@/lib/utils';

type NavItem = { href: Route; label: string; icon: ReactNode; match?: 'exact' | 'prefix' };
type NavGroup = { key: string; title?: string; items: NavItem[] };

const NAV: NavGroup[] = [
  { key: 'home', items: [{ href: '/', label: '首页', icon: <HomeIcon />, match: 'exact' }] },
  { key: 'quiz', items: [{ href: '/quiz', label: '练习', icon: <QuizIcon />, match: 'prefix' }] },
  { key: 'notes', items: [{ href: '/notes', label: '笔记', icon: <NotesIcon />, match: 'prefix' }] },
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
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    const raw = window.localStorage.getItem('jobcopilot.sidebar.collapsed');
    if (raw === 'true') setCollapsed(true);
  }, []);

  const toggleCollapsed = () => {
    setCollapsed((value) => {
      const next = !value;
      window.localStorage.setItem('jobcopilot.sidebar.collapsed', String(next));
      return next;
    });
  };

  return (
    <aside
      className={cn(
        'vibrancy-sidebar flex h-screen shrink-0 flex-col border-r border-border transition-[width] duration-200 ease-apple',
        collapsed ? 'w-16' : 'w-[220px]',
      )}
    >
      <div className={cn('px-3 pt-3 pb-2', collapsed ? 'flex justify-center' : '')}>
        <button
          type="button"
          onClick={toggleCollapsed}
          className={cn(
            'flex size-8 items-center justify-center rounded-lg text-muted transition-colors hover:bg-black/[0.05] hover:text-foreground',
            collapsed ? '' : 'ml-auto',
          )}
          title={collapsed ? '展开边栏' : '收起边栏'}
          aria-label={collapsed ? '展开边栏' : '收起边栏'}
        >
          {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
        </button>
      </div>

      <nav className={cn('flex-1 overflow-y-auto pb-6', collapsed ? 'px-2' : 'px-3')}>
        {NAV.map((group) => (
          <div key={group.key} className={cn('mt-4 first:mt-2', collapsed ? 'flex justify-center' : '')}>
            {group.title && !collapsed ? (
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
                      title={collapsed ? item.label : undefined}
                      className={cn(
                        'flex h-10 items-center rounded-xl text-[13px] transition-colors duration-150 ease-apple',
                        collapsed ? 'w-10 justify-center px-0' : 'gap-2 px-2',
                        active
                          ? 'bg-[var(--color-selection)] text-[var(--color-selection-fg)]'
                          : 'text-foreground hover:bg-black/[0.04]',
                      )}
                    >
                      <span className="flex size-5 shrink-0 items-center justify-center opacity-80">
                        {item.icon}
                      </span>
                      {!collapsed ? <span className="truncate">{item.label}</span> : null}
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

function NotesIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path d="M3.5 2h6.5l3 3v9a.5.5 0 0 1-.5.5h-9a.5.5 0 0 1-.5-.5v-11a.5.5 0 0 1 .5-.5Z" />
      <path d="M10 2v3h3" />
      <path d="M5.5 8h5M5.5 10.5h5M5.5 6h2" />
    </svg>
  );
}

function QuizIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path d="M4 2.5h8a1 1 0 0 1 1 1v6.2a1 1 0 0 1-1 1H8.8L5.5 13.5v-2.8H4a1 1 0 0 1-1-1V3.5a1 1 0 0 1 1-1Z" />
      <path d="M5.7 5.2h4.6M5.7 7.4h3.2" />
    </svg>
  );
}
