'use client';

import { Fragment } from 'react';
import { esc, fx } from '@/lib/format';
import { Bands, ChartBox, dateTicks, nice, type Band } from './core';

export interface BarChartProps {
  height: number;
  values: number[];
  labels?: string[];
  dates?: string[];
  bands?: Band[];
  color?: (v: number, i: number) => string;
  op?: number;
  gap?: number;
  ticks?: number;
  yFmt?: (v: number) => string;
  rFmt?: (v: number) => string;
  showVal?: (v: number, i: number) => string;
  hlines?: { v: number; color: string; label?: string }[];
  line?: { data: number[]; color: string };
  tip?: (v: number, i: number) => string;
  pad?: Partial<{ l: number; r: number; t: number; b: number }>;
  xMax?: number;
  labelClass?: (i: number) => string;
}

export function BarChart(props: BarChartProps) {
  return <ChartBox height={props.height}>{(W, H) => <BarInner {...props} W={W} H={H} />}</ChartBox>;
}

function BarInner(o: BarChartProps & { W: number; H: number }) {
  const { W, H } = o;
  const v = o.values.map((x) => (Number.isFinite(x) ? x : 0));
  const n = v.length;
  if (!n) return null;
  const P = { l: 42, r: o.line ? 42 : 10, t: 12, b: 20, ...(o.pad ?? {}) };
  const iw = W - P.l - P.r;
  const ih = H - P.t - P.b;
  const bw = iw / n;
  const gp = o.gap !== undefined ? Math.min(o.gap, bw * 0.6) : Math.min(10, bw * 0.3);
  const hv = (o.hlines ?? []).map((h) => h.v);
  const lo = Math.min(0, ...v, ...hv);
  const hi = Math.max(0, ...v, ...hv);
  const pd = (hi - lo) * 0.1;
  const d = nice(lo < 0 ? lo - pd : lo, hi > 0 ? hi + pd : hi, o.ticks ?? 4);
  const Y = (x: number) => P.t + ih - ((x - d.lo) / (d.hi - d.lo)) * ih;
  const Xc = (i: number) => P.l + i * bw + bw / 2;
  const yFmt = o.yFmt ?? ((x: number) => String(x));

  let lineEl = null;
  if (o.line && o.line.data.length) {
    const ld = o.line.data.filter(Number.isFinite);
    const dr = nice(Math.min(0, ...ld), Math.max(0, ...ld) * 1.05 || 1, o.ticks ?? 4);
    const YR = (x: number) => P.t + ih - ((x - dr.lo) / (dr.hi - dr.lo)) * ih;
    const p = o.line.data.map((x, i) => `${i ? 'L' : 'M'}${Xc(i).toFixed(1)} ${YR(Number.isFinite(x) ? x : 0).toFixed(1)}`).join('');
    lineEl = (
      <>
        <path d={p} fill="none" style={{ stroke: o.line.color, strokeWidth: 1.6 }} />
        {dr.ticks.map((t) => (
          <text key={`r${t}`} x={W - P.r + 6} y={YR(t) + 3} className="sv-ax" style={{ fill: o.line!.color }}>
            {(o.rFmt ?? String)(t)}
          </text>
        ))}
      </>
    );
  }

  return (
    <>
      {o.dates ? <Bands bands={o.bands} xs={o.dates} X={Xc} top={P.t} h={ih} /> : null}
      {d.ticks.map((t) => {
        const y = Y(t);
        return (
          <Fragment key={t}>
            <line x1={P.l} x2={W - P.r} y1={y} y2={y} className={Math.abs(t) < 1e-12 ? 'sv-base' : 'sv-grid'} />
            <text x={P.l - 6} y={y + 3} className="sv-ax" textAnchor="end">
              {yFmt(t)}
            </text>
          </Fragment>
        );
      })}
      {v.map((x, i) => {
        const bx = P.l + i * bw + gp / 2;
        const w = Math.max(1, bw - gp);
        const y0 = Y(0);
        const y1 = Y(x);
        const y = Math.min(y0, y1);
        const h = Math.max(0.6, Math.abs(y1 - y0));
        const c = o.color ? o.color(x, i) : x >= 0 ? 'var(--up)' : 'var(--dn)';
        return (
          <Fragment key={i}>
            <rect x={bx} y={y} width={w} height={h} style={{ fill: c, opacity: o.op ?? 0.88 }} data-tip={esc(o.tip ? o.tip(x, i) : fx(x, 4))} />
            {o.showVal ? (
              <text x={bx + w / 2} y={x >= 0 ? y - 4 : y + h + 10} className="sv-axl" textAnchor="middle">
                {o.showVal(x, i)}
              </text>
            ) : null}
          </Fragment>
        );
      })}
      {o.dates
        ? dateTicks(o.dates, o.xMax ?? 6).map(([i, l]) => (
            <text key={`x${i}`} x={Xc(i)} y={H - 5} className="sv-ax" textAnchor="middle">
              {l}
            </text>
          ))
        : (o.labels ?? []).map((l, i) => (
            <text key={`x${i}`} x={Xc(i)} y={H - 6} className={o.labelClass ? o.labelClass(i) : 'sv-axl'} textAnchor="middle" style={{ fontFamily: 'var(--f-sans)' }}>
              {l}
            </text>
          ))}
      {(o.hlines ?? []).map((h, k) => {
        const y = Y(h.v);
        return (
          <Fragment key={`h${k}`}>
            <line x1={P.l} x2={W - P.r} y1={y} y2={y} style={{ stroke: h.color, strokeDasharray: '4 3' }} />
            {h.label ? (
              <text x={W - P.r - 3} y={y - 3} textAnchor="end" style={{ fill: h.color, fontSize: 9.5 }}>
                {h.label}
              </text>
            ) : null}
          </Fragment>
        );
      })}
      {lineEl}
    </>
  );
}
