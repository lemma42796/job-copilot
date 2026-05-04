import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import type { ReactNode } from 'react';

import { Sidebar } from '@/components/shell/sidebar';
import { TitleBar } from '@/components/shell/titlebar';

import './globals.css';

const inter = Inter({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-inter',
});

export const metadata: Metadata = {
  title: 'JobCopilot',
  description: 'AI 求职助手',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN" className={inter.variable}>
      <body className="h-screen overflow-hidden">
        <div className="grid h-screen grid-cols-[220px_1fr]">
          <Sidebar />
          <div className="flex min-w-0 flex-col overflow-hidden bg-surface">
            <TitleBar />
            <main className="flex-1 overflow-y-auto">{children}</main>
          </div>
        </div>
      </body>
    </html>
  );
}
