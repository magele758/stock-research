'use client';

import { useLayoutEffect, useState, type RefObject } from 'react';

export interface FloatPos {
  left: number;
  top: number;
  maxHeight: number;
  placement: 'bottom' | 'top';
  ready: boolean;
}

const GAP = 6;
const MARGIN = 8;

/** Right boundary of the usable area: the Bot Dock (`aside.bot`) is never covered. */
export function safeRight(): number {
  const vw = window.innerWidth;
  const bot = document.querySelector<HTMLElement>('aside.bot');
  if (bot && bot.offsetWidth > 0) {
    const r = bot.getBoundingClientRect();
    if (r.width > 0 && r.left < vw) return r.left - MARGIN;
  }
  return vw - MARGIN;
}

/**
 * Anchor-relative placement for a fixed-position card: below the anchor by default,
 * flips above when the bottom does not fit, clamps horizontally inside [8, dock.left-8],
 * re-measures on scroll (capture: `.main` is the scroll container) and resize.
 */
export function useFloating(anchor: HTMLElement | null, floating: RefObject<HTMLElement | null>, deps: unknown[] = []): FloatPos {
  const [pos, setPos] = useState<FloatPos>({ left: -9999, top: -9999, maxHeight: 480, placement: 'bottom', ready: false });

  useLayoutEffect(() => {
    if (!anchor) return;
    let raf = 0;
    const measure = () => {
      const el = floating.current;
      if (!el || !anchor.isConnected) return;
      const a = anchor.getBoundingClientRect();
      const w = el.offsetWidth;
      const h = el.scrollHeight;
      const vh = window.innerHeight;
      const right = safeRight();
      const below = vh - a.bottom - GAP - MARGIN;
      const above = a.top - GAP - MARGIN - 44;
      const placement: 'bottom' | 'top' = h <= below || below >= above ? 'bottom' : 'top';
      const maxHeight = Math.max(160, placement === 'bottom' ? below : above);
      const shown = Math.min(h, maxHeight);
      let left = a.left;
      if (left + w > right) left = right - w;
      if (left < MARGIN) left = MARGIN;
      const top = placement === 'bottom' ? a.bottom + GAP : a.top - GAP - shown;
      setPos((p) =>
        p.left === left && p.top === top && p.maxHeight === maxHeight && p.placement === placement && p.ready ? p : { left, top, maxHeight, placement, ready: true }
      );
    };
    const schedule = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(measure);
    };
    measure();
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(schedule) : null;
    if (floating.current) ro?.observe(floating.current);
    window.addEventListener('scroll', schedule, true);
    window.addEventListener('resize', schedule);
    return () => {
      cancelAnimationFrame(raf);
      ro?.disconnect();
      window.removeEventListener('scroll', schedule, true);
      window.removeEventListener('resize', schedule);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchor, ...deps]);

  return pos;
}
