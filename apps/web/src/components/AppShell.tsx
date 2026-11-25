'use client';

import type { ReactNode } from 'react';
import { WorkbenchProvider, useWorkbench } from '@/lib/workbench';
import { TooltipHost } from './charts/core';
import { TopBar } from './TopBar';
import { SideNav } from './SideNav';
import { AgentHomeDock } from './AgentHomeDock';

function Frame({ children }: { children: ReactNode }) {
  const { botOpen } = useWorkbench();
  return (
    <>
      <TopBar />
      <div className={`app ${botOpen ? '' : 'bot-off'}`}>
        <SideNav />
        <main className="main">{children}</main>
        <AgentHomeDock />
      </div>
      <TooltipHost />
    </>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <WorkbenchProvider>
      <Frame>{children}</Frame>
    </WorkbenchProvider>
  );
}
