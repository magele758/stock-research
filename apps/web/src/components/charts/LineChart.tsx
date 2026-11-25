'use client';

import { Fragment } from 'react';
import { esc } from '@/lib/format';
import { Bands, ChartBox, dateTicks, hideTip, nice, showTip, useSyncedHover, type Band } from './core';

export interface LineSeries {
  name: string;
  data: number[];
  color: string;
  axis?: 'l' | 'r';
  w?: number;
  dash?: string;
  op?: number;
  area?: boolean;
  areaBase?: number;
  areaOp?: number;
  noHover?: boolean;
  noTip?: boolean;
  fmt?: (v: number) => string;
}

export interface LineChartProps {
  height: number;
  xs: string[];
  series: LineSeries[];
  bands?: Band[];
  base?: number;
  ticks?: number;
  yLo?: number;
  yHi?: number;
  rLo?: number;
  rHi?: number;
  yFmt?: (v: number) => string;
  rFmt?: (v: number) => string;
  hlines?: { v: number; color: string; label?: string }[];
  pad?: Partial<{ l: number; r: number; t: number; b: number }>;
  xMax?: number;
  sync?: string;
  tipFn?: (i: number) => string;
}

function domain(ss: LineSeries[], n: number, lo?: number, hi?: number) {
  let a = Infinity;
  let b = -Infinity;
  ss.forEach((s) => s.data.forEach((v) => {
    if (!Number.isFinite(v)) return;
    if (v < a) a = v;
    if (v > b) b = v;
  }));
  if (lo !== undefined) a = Math.min(a, lo);
  if (hi !== undefined) b = Math.max(b, hi);
  if (!Number.isFinite(a)) {
    a = 0;
    b = 1;
  }
  const pd = (b - a) * 0.04;
  return nice(a - pd, b + pd, n);
}

export function LineChart(props: LineChartProps) {
  return <ChartBox height={props.height}>{(W, H) => <LineInner {...props} W={W} H={H} />}</ChartBox>;
}

function LineInner(o: LineChartProps & { W: number; H: number }) {
  const { W, H } = o;
  const [hover, emit] = useSyncedHover(o.sync);
  const hasR = o.series.some((s) => s.axis === 'r');
  const P = { l: 42, r: hasR ? 42 : 10, t: 8, b: 20, ...(o.pad ?? {}) };
  const n = o.xs.length;
  const iw = W - P.l - P.r;
  const ih = H - P.t - P.b;
  const X = (i: number) => P.l + (i * iw) / Math.max(1, n - 1);
  const Ls = o.series.filter((s) => s.axis !== 'r');
  const Rs = o.series.filter((s) => s.axis === 'r');
  const dl = domain(Ls, o.ticks ?? 4, o.yLo, o.yHi);
  const dr = Rs.length ? domain(Rs, o.ticks ?? 4, o.rLo, o.rHi) : null;
  const YL = (v: number) => P.t + ih - ((v - dl.lo) / (dl.hi - dl.lo)) * ih;
  const YR = dr ? (v: number) => P.t + ih - ((v - dr.lo) / (dr.hi - dr.lo)) * ih : YL;
  const yFmt = o.yFmt ?? ((v: number) => String(v));

  const path = (s: LineSeries) => {
    const Y = s.axis === 'r' ? YR : YL;
    let d = '';
    let pen = false;
    s.data.forEach((v, i) => {
      if (!Number.isFinite(v)) {
        pen = false;
        return;
      }
      d += `${pen ? 'L' : 'M'}${X(i).toFixed(1)} ${Y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };

  const tipHtml = (i: number) => {
    if (o.tipFn) return o.tipFn(i);
    const rows = o.series
      .filter((s) => !s.noTip)
      .map((s) => `<div class="tr"><span><i style="background:${s.color}"></i> ${esc(s.name)}</span><b>${(s.fmt ?? yFmt)(s.data[i])}</b></div>`);
    return `<div class="th">${esc(o.xs[i] ?? '')}</div><div class="${rows.length > 6 ? 'tg2' : ''}">${rows.join('')}</div>`;
  };

  const onMove = (e: React.MouseEvent<SVGRectElement>) => {
    const r = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
    const i = Math.max(0, Math.min(n - 1, Math.round(((e.clientX - r.left - P.l) / iw) * (n - 1))));
    emit(i);
    showTip(tipHtml(i), e.clientX, e.clientY);
  };

  if (!n) return null;
  return (
    <>
      <Bands bands={o.bands} xs={o.xs} X={X} top={P.t} h={ih} />
      {dl.ticks.map((t) => {
        const y = YL(t);
        return (
          <Fragment key={`l${t}`}>
            <line x1={P.l} x2={W - P.r} y1={y} y2={y} className={o.base !== undefined && Math.abs(t - o.base) < 1e-9 ? 'sv-base' : 'sv-grid'} />
            <text x={P.l - 6} y={y + 3} className="sv-ax" textAnchor="end">
              {yFmt(t)}
            </text>
          </Fragment>
        );
      })}
      {dr?.ticks.map((t) => (
        <text key={`r${t}`} x={W - P.r + 6} y={YR(t) + 3} className="sv-ax">
          {(o.rFmt ?? String)(t)}
        </text>
      ))}
      {dateTicks(o.xs, o.xMax ?? 6).map(([i, l]) => (
        <Fragment key={`x${i}`}>
          <line x1={X(i)} x2={X(i)} y1={P.t + ih} y2={P.t + ih + 3} className="sv-tick" />
          <text x={X(i)} y={H - 5} className="sv-ax" textAnchor="middle">
            {l}
          </text>
        </Fragment>
      ))}
      {o.series.map((s, si) => {
        const d = path(s);
        if (!d) return null;
        const Y = s.axis === 'r' ? YR : YL;
        const y0 = Y(s.areaBase ?? 0).toFixed(1);
        return (
          <Fragment key={si}>
            {s.area ? <path d={`${d}L${X(n - 1).toFixed(1)} ${y0}L${X(0).toFixed(1)} ${y0}Z`} style={{ fill: s.color, opacity: s.areaOp ?? 0.14 }} /> : null}
            <path d={d} fill="none" strokeLinejoin="round" style={{ stroke: s.color, strokeWidth: s.w ?? 1.4, strokeDasharray: s.dash, opacity: s.op ?? 1 }} />
          </Fragment>
        );
      })}
      {(o.hlines ?? []).map((h, k) => {
        const y = YL(h.v);
        return (
          <Fragment key={`h${k}`}>
            <line x1={P.l} x2={W - P.r} y1={y} y2={y} style={{ stroke: h.color, strokeDasharray: '4 3' }} />
            {h.label ? (
              <text x={W - P.r - 4} y={y - 4} textAnchor="end" style={{ fill: h.color, fontSize: 9.5 }}>
                {h.label}
              </text>
            ) : null}
          </Fragment>
        );
      })}
      {hover !== null && hover < n ? (
        <>
          <line className="sv-vx" x1={X(hover)} x2={X(hover)} y1={P.t} y2={P.t + ih} />
          {o.series.map((s, si) => {
            const v = s.data[hover];
            if (s.noHover || !Number.isFinite(v)) return null;
            const Y = s.axis === 'r' ? YR : YL;
            return <circle key={si} className="sv-dot" r={2.6} cx={X(hover)} cy={Y(v)} style={{ fill: s.color }} />;
          })}
        </>
      ) : null}
      <rect
        className="ov"
        x={P.l}
        y={P.t}
        width={Math.max(0, iw)}
        height={Math.max(0, ih)}
        fill="transparent"
        onMouseMove={onMove}
        onMouseLeave={() => {
          emit(null);
          hideTip();
        }}
      />
    </>
  );
}
