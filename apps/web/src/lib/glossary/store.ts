'use client';

/**
 * Singleton overlay store (TermTip card, page values, Glossary / NewStudy drawers).
 * Kept outside the Workbench context so hovering 40+ triggers never re-renders the workbench tree.
 */
import { useSyncExternalStore } from 'react';
import type { StudyDraft } from '../research-api';
import type { PageValue, TermValue } from './types';

export const HOVER_OPEN_MS = 180;
export const HOVER_CLOSE_MS = 140;

export interface ActiveTip {
  /** Monotonic id so the card can re-measure when the target changes. */
  key: number;
  triggerId: string;
  termId: string;
  anchor: HTMLElement;
  tv: TermValue;
  pinned: boolean;
}

export type DrawerState = { kind: 'glossary'; focus?: string; query?: string; nonce: number } | { kind: 'study'; draft: StudyDraft; nonce: number } | null;

type Listener = () => void;

let active: ActiveTip | null = null;
let drawer: DrawerState = null;
let pageVals = new Map<string, PageValue>();
let pageSnap: PageValue[] = [];
let seq = 0;
let openTimer: ReturnType<typeof setTimeout> | undefined;
let closeTimer: ReturnType<typeof setTimeout> | undefined;
let pendingTrigger: string | null = null;

const tipL = new Set<Listener>();
const drawerL = new Set<Listener>();
const pageL = new Set<Listener>();
const emit = (s: Set<Listener>) => s.forEach((l) => l());

function setActive(next: ActiveTip | null) {
  active = next;
  emit(tipL);
}

function clearOpen() {
  if (openTimer) clearTimeout(openTimer);
  openTimer = undefined;
  pendingTrigger = null;
}
function clearClose() {
  if (closeTimer) clearTimeout(closeTimer);
  closeTimer = undefined;
}

export const tipStore = {
  get: () => active,
  hoverEnter(triggerId: string, anchor: HTMLElement, termId: string, tv: TermValue) {
    clearClose();
    if (active?.pinned) return;
    if (active?.triggerId === triggerId) return;
    clearOpen();
    pendingTrigger = triggerId;
    openTimer = setTimeout(() => {
      openTimer = undefined;
      pendingTrigger = null;
      setActive({ key: ++seq, triggerId, termId, anchor, tv, pinned: false });
    }, HOVER_OPEN_MS);
  },
  hoverLeave(triggerId: string) {
    if (pendingTrigger === triggerId) clearOpen();
    if (active && !active.pinned) tipStore.scheduleClose();
  },
  scheduleClose() {
    clearClose();
    closeTimer = setTimeout(() => {
      closeTimer = undefined;
      if (active && !active.pinned) setActive(null);
    }, HOVER_CLOSE_MS);
  },
  cardEnter: clearClose,
  cardLeave() {
    if (active && !active.pinned) tipStore.scheduleClose();
  },
  focusOpen(triggerId: string, anchor: HTMLElement, termId: string, tv: TermValue) {
    clearOpen();
    clearClose();
    if (active?.pinned && active.triggerId !== triggerId) return;
    if (active?.triggerId === triggerId) return;
    setActive({ key: ++seq, triggerId, termId, anchor, tv, pinned: false });
  },
  togglePin(triggerId: string, anchor: HTMLElement, termId: string, tv: TermValue) {
    clearOpen();
    clearClose();
    if (active?.triggerId === triggerId && active.pinned) return setActive(null);
    setActive({ key: active?.triggerId === triggerId ? active.key : ++seq, triggerId, termId, anchor, tv, pinned: true });
  },
  /** Related-term chip inside the card: keep anchor, switch content, pin. */
  switchTerm(termId: string) {
    if (!active) return;
    const pv = [...pageVals.values()].find((p) => p.termId === termId);
    setActive({ ...active, key: ++seq, termId, tv: pv ? { value: pv.value, display: pv.display, gate: pv.gate, context: pv.context } : {}, pinned: true });
  },
  updateValue(triggerId: string, tv: TermValue) {
    if (active?.triggerId === triggerId) setActive({ ...active, tv });
  },
  unmount(triggerId: string) {
    if (pendingTrigger === triggerId) clearOpen();
    if (active?.triggerId === triggerId) setActive(null);
  },
  close() {
    clearOpen();
    clearClose();
    if (active) setActive(null);
  },
  subscribe(l: Listener) {
    tipL.add(l);
    return () => tipL.delete(l);
  }
};

export const pageStore = {
  set(triggerId: string, pv: PageValue) {
    const prev = pageVals.get(triggerId);
    if (prev && prev.termId === pv.termId && prev.value === pv.value && prev.display === pv.display && prev.verdict === pv.verdict) return;
    pageVals = new Map(pageVals).set(triggerId, pv);
    pageSnap = [...pageVals.values()];
    emit(pageL);
  },
  remove(triggerId: string) {
    if (!pageVals.has(triggerId)) return;
    pageVals = new Map(pageVals);
    pageVals.delete(triggerId);
    pageSnap = [...pageVals.values()];
    emit(pageL);
  },
  get: () => pageSnap,
  subscribe(l: Listener) {
    pageL.add(l);
    return () => pageL.delete(l);
  }
};

export const drawerStore = {
  get: () => drawer,
  openGlossary(focus?: string, query?: string) {
    tipStore.close();
    drawer = { kind: 'glossary', focus, query, nonce: ++seq };
    emit(drawerL);
  },
  toggleGlossary() {
    if (drawer?.kind === 'glossary') drawerStore.close();
    else drawerStore.openGlossary();
  },
  openStudy(draft: StudyDraft) {
    tipStore.close();
    drawer = { kind: 'study', draft, nonce: ++seq };
    emit(drawerL);
  },
  close() {
    if (!drawer) return;
    drawer = null;
    emit(drawerL);
  },
  subscribe(l: Listener) {
    drawerL.add(l);
    return () => drawerL.delete(l);
  }
};

const nullSnap = () => null;
const emptyPage = (): PageValue[] => [];

export const useActiveTip = () => useSyncExternalStore(tipStore.subscribe, tipStore.get, nullSnap);
export const useDrawer = () => useSyncExternalStore(drawerStore.subscribe, drawerStore.get, nullSnap);
export const usePageValues = () => useSyncExternalStore(pageStore.subscribe, pageStore.get, emptyPage);

export const openGlossary = (focus?: string, query?: string) => drawerStore.openGlossary(focus, query);
export const openStudyDrawer = (draft: StudyDraft) => drawerStore.openStudy(draft);
