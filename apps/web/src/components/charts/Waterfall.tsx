'use client';

import { Fragment } from 'react';
import { esc, fx, isNum, sg } from '@/lib/format';
import { ChartBox, nice } from './core';

export interface WaterfallItem {
  label: string;
  /** Totals are absolute levels; non-totals are signed deltas applied to the running level. */
  value: number;
  total?: boolean;
  /** Second axis line under the label (e.g. GROSS / NET / 12bp). */
  sub?: string;
  tip?: string;
}

export interface WaterfallProps {
  items: WaterfallItem[];
  height?: number;
}

export function Waterfall(props: WaterfallProps) {
  return <ChartBox height={props.height ?? 248}>{(W, H) => <WaterfallInner {...props} W={W} H={H} />}</ChartBox>;
}

function WaterfallInner({ items, W, H }: WaterfallProps & { W: number; H: number }) {
  const n = items.length;
  if (!n) return null;
  const P = { l: 40, r: 8, t: 18, b: 36 };
  const iw = W - P.l - P.r;
  const ih = H - P.t - P.b;
  const bw = iw / n;

  let cur = 0;
  const spans = items.map((it) => {
    const v = isNum(it.value) ? it.value : 0;
    if (it.total) {
      cur = v;
      return { a: 0, b: v, end: v };
    }
    const a = cur;
    cur += v;
    return { a, b: cur, end: cur };
  });
  const levels = spans.flatMap((s) => [s.a, s.b]);
  const lo = Math.min(0, ...levels);
  const hi = Math.max(0, ...levels);
  const pd = (hi - lo) * 0.06;
  const d = nice(lo < 0 ? lo - pd : lo, hi > 0 ? hi + pd : hi, 4);
  const Y = (v: number) => P.t + ih - ((v - d.lo) / (d.hi - d.lo)) * ih;
  const dec = d.step >= 1 ? 0 : d.step >= 0.1 ? 1 : 2;

  return (
    <>
      {d.ticks.map((t) => {
        const y = Y(t);
        return (
          <Fragment key={`t${t}`}>
            <line x1={P.l} x2={W - P.r} y1={y} y2={y} className={Math.abs(t) < 1e-12 ? 'sv-base' : 'sv-grid'} />
            <text x={P.l - 6} y={y + 3} className="sv-ax" textAnchor="end">
              {t.toFixed(dec)}%
            </text>
          </Fragment>
        );
      })}
      {items.map((it, i) => {
        const s = spans[i];
        const x = P.l + i * bw + bw * 0.18;
        const w = bw * 0.64;
        const top = Y(Math.max(s.a, s.b));
        const h = Math.max(1, Math.abs(Y(s.a) - Y(s.b)));
        const v = isNum(it.value) ? it.value : 0;
        const fill = it.total ? 'var(--sig)' : v < 0 ? 'var(--dn)' : 'var(--up)';
        const below = it.total ? v < 0 : false;
        const tip = it.tip ?? `${it.label} ${it.total ? fx(v, 2) + '%' : sg(v, 3) + '% · ' + fx(Math.abs(v) * 100, 1) + ' bps/年'}`;
        return (
          <Fragment key={i}>
            <rect x={x} y={top} width={w} height={h} style={{ fill, opacity: it.total && i === 0 ? 0.5 : 0.9 }} data-tip={esc(tip)} />
            <text
              x={x + w / 2}
              y={below ? top + h + 11 : top - 4}
              className="sv-axl"
              textAnchor="middle"
              style={it.total ? { fill: 'var(--tx)', fontWeight: 600 } : undefined}
            >
              {it.total ? fx(v, 2) + '%' : sg(v, 2)}
            </text>
            {i < n - 1 ? (
              <line x1={x + w} x2={x + bw} y1={Y(s.end)} y2={Y(s.end)} style={{ stroke: 'var(--tx3)', strokeDasharray: '2 2' }} />
            ) : null}
            <text x={x + w / 2} y={H - 20} textAnchor="middle" style={{ fill: 'var(--tx2)', fontSize: 10, fontFamily: 'var(--f-sans)' }}>
              {it.label}
            </text>
            {it.sub ? (
              <text x={x + w / 2} y={H - 7} className="sv-ax" textAnchor="middle">
                {it.sub}
              </text>
            ) : null}
          </Fragment>
        );
      })}
    </>
  );
}
