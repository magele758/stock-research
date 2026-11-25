'use client';

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';

// ------------------------------------------------------------------ tooltip (singleton #qtip)

export function showTip(html: string, clientX: number, clientY: number) {
  const el = document.getElementById('qtip');
  if (!el) return;
  el.innerHTML = html;
  el.style.display = 'block';
  const w = el.offsetWidth;
  const h = el.offsetHeight;
  let x = clientX + 14;
  let y = clientY + 14;
  if (x + w > window.innerWidth - 8) x = clientX - w - 14;
  if (y + h > window.innerHeight - 8) y = clientY - h - 14;
  el.style.left = `${x}px`;
  el.style.top = `${y}px`;
}

export function hideTip() {
  const el = document.getElementById('qtip');
  if (el) el.style.display = 'none';
}

/** Mount once (AppShell): `[data-tip]` delegation identical to the prototype. */
export function TooltipHost() {
  useEffect(() => {
    let cur: Element | null = null;
    const over = (e: MouseEvent) => {
      const t = (e.target as Element | null)?.closest?.('[data-tip]') ?? null;
      cur = t && t.getAttribute('data-tip') ? t : null;
      if (!cur && !(e.target as Element | null)?.classList?.contains('ov')) hideTip();
    };
    const move = (e: MouseEvent) => {
      if (cur) showTip(cur.getAttribute('data-tip') ?? '', e.clientX, e.clientY);
    };
    document.addEventListener('mouseover', over);
    document.addEventListener('mousemove', move);
    return () => {
      document.removeEventListener('mouseover', over);
      document.removeEventListener('mousemove', move);
    };
  }, []);
  return <div id="qtip" />;
}

// ------------------------------------------------------------------ sizing

export function useSize<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setSize((s) => (s.width === el.clientWidth && s.height === el.clientHeight ? s : { width: el.clientWidth, height: el.clientHeight }));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return { ref, ...size };
}

/** Sized SVG canvas: renders children(width, height) once the container has a width. */
export function ChartBox({ height, children, className, style }: { height: number; children: (w: number, h: number) => ReactNode; className?: string; style?: React.CSSProperties }) {
  const { ref, width } = useSize<HTMLDivElement>();
  return (
    <div ref={ref} className={`cv ${className ?? ''}`} style={{ height, ...style }}>
      {width > 0 ? (
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
          {children(width, height)}
        </svg>
      ) : null}
    </div>
  );
}

// ------------------------------------------------------------------ scales

export function nice(lo: number, hi: number, n = 4) {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) {
    lo = 0;
    hi = 1;
  }
  if (!(hi - lo > 1e-12)) hi = lo + (Math.abs(lo) > 1e-9 ? Math.abs(lo) * 0.1 : 1);
  const raw = (hi - lo) / n;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const r = raw / mag;
  const step = (r > 5 ? 10 : r > 2 ? 5 : r > 1 ? 2 : 1) * mag;
  const a = Math.floor(lo / step + 1e-9) * step;
  const b = Math.ceil(hi / step - 1e-9) * step;
  const ticks: number[] = [];
  for (let v = a; v <= b + step * 1e-6; v += step) ticks.push(+v.toFixed(12));
  return { lo: a, hi: b, ticks, step };
}

/** Month-boundary date ticks (≤ max), falling back to evenly spaced MM-DD. */
export function dateTicks(xs: string[], max = 6): [number, string][] {
  const c: number[] = [];
  let pm = '';
  xs.forEach((d, i) => {
    const m = d.slice(0, 7);
    if (m !== pm) {
      if (i > 0) c.push(i);
      pm = m;
    }
  });
  if (c.length < 3) {
    const k = Math.max(1, Math.floor(xs.length / max));
    const o: [number, string][] = [];
    for (let i = 0; i < xs.length; i += k) o.push([i, xs[i].slice(5, 10)]);
    return o;
  }
  for (const st of [1, 2, 3, 6, 12, 24]) {
    const f = c.filter((i) => (+xs[i].slice(5, 7) - 1) % st === 0 && (st < 24 || +xs[i].slice(0, 4) % 2 === 0));
    if (f.length <= max) return f.map((i) => [i, st >= 12 ? xs[i].slice(0, 4) : xs[i].slice(2, 7).replace('-', '/')]);
  }
  return [];
}

export interface Band {
  a: string;
  b: string;
  label: string;
}

function indexOfDate(xs: string[], d: string): number {
  for (let i = 0; i < xs.length; i++) if (xs[i] >= d) return i;
  return xs.length - 1;
}

export function Bands({ bands, xs, X, top, h }: { bands?: Band[]; xs: string[]; X: (i: number) => number; top: number; h: number }) {
  if (!bands?.length || !xs.length) return null;
  return (
    <>
      {bands
        .filter((b) => b.b >= xs[0] && b.a <= xs[xs.length - 1])
        .map((b) => {
          const a = X(indexOfDate(xs, b.a));
          const c = X(indexOfDate(xs, b.b));
          return (
            <g key={b.a}>
              <rect x={a} y={top} width={Math.max(2, c - a)} height={h} className="sv-band" />
              <text x={a + 3} y={top + 9} className="sv-bandt">
                {b.label}
              </text>
            </g>
          );
        })}
    </>
  );
}

/** A-share regime bands from the prototype; only drawn when inside the data range. */
export const REGIME_BANDS: Band[] = [
  { a: '2024-01-15', b: '2024-02-20', label: '微盘踩踏' },
  { a: '2024-09-24', b: '2024-10-16', label: '924 急升' },
  { a: '2025-04-03', b: '2025-04-28', label: '关税冲击' }
];

// ------------------------------------------------------------------ crosshair sync bus

type SyncFn = (i: number | null) => void;
const SYNC = new Map<string, Set<SyncFn>>();

export function useSyncedHover(syncKey?: string) {
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    if (!syncKey) return;
    const set = SYNC.get(syncKey) ?? new Set<SyncFn>();
    SYNC.set(syncKey, set);
    set.add(setHover);
    return () => {
      set.delete(setHover);
    };
  }, [syncKey]);
  const emit = (i: number | null) => {
    if (syncKey) SYNC.get(syncKey)?.forEach((fn) => fn(i));
    else setHover(i);
  };
  return [hover, emit] as const;
}

export const f2 = (v: number) => v.toFixed(1);
