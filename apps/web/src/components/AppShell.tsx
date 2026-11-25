'use client';

import { useEffect, type ReactNode } from 'react';
import { WorkbenchProvider, useWorkbench } from '@/lib/workbench';
import { drawerStore, tipStore, useDrawer } from '@/lib/glossary/store';
import { TooltipHost } from './charts/core';
import { TopBar } from './TopBar';
import { SideNav } from './SideNav';
import { AgentHomeDock } from './AgentHomeDock';
import { TermPopoverHost } from './glossary/TermPopoverHost';
import { GlossaryDrawer } from './glossary/GlossaryDrawer';
import { NewStudyDrawer } from './research/NewStudyDrawer';

function DrawerHost() {
  const d = useDrawer();
  if (!d) return null;
  if (d.kind === 'glossary') return <GlossaryDrawer key={d.nonce} focus={d.focus} query={d.query} />;
  return <NewStudyDrawer key={d.nonce} draft={d.draft} />;
}

function Frame({ children }: { children: ReactNode }) {
  const { botOpen, view } = useWorkbench();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        drawerStore.toggleGlossary();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    tipStore.close();
  }, [view]);

  return (
    <>
      <TopBar />
      <div className={`app ${botOpen ? '' : 'bot-off'}`}>
        <SideNav />
        <main className="main">{children}</main>
        <AgentHomeDock />
      </div>
      <TooltipHost />
      <TermPopoverHost />
      <DrawerHost />
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
