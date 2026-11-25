import type { Metadata, Viewport } from 'next';
import type { ReactNode } from 'react';
import { AppShell } from '@/components/AppShell';
import './globals.css';

export const metadata: Metadata = {
  title: 'QuantLab Pro · 机构级量化投研终端 V3',
  description: '研究工作台与集群派发 · 形式化验证证明链 · PIT 数据底座 · Alpha 因子工坊 · CPCV 过拟合防线 · Barra CNE6 + cvxpy · A 股微结构回测 · 影子实盘 · Agent Home Quant Bot'
};

export const viewport: Viewport = { width: 'device-width', initialScale: 1 };

const themeBoot = `try{var p=JSON.parse(localStorage.getItem('quantlab.v2.prefs')||'{}');document.documentElement.dataset.theme=p.theme==='light'?'light':'dark';}catch(e){document.documentElement.dataset.theme='dark';}`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBoot }} />
      </head>
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
