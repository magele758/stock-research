'use client';

import { useState } from 'react';
import { esc, fx } from '@/lib/format';
import { ChartBox, hideTip, nice, showTip } from './core';

export interface XYSeries {
  name: string;
  color: string;
  x: number[];
  y: number[];
  axis?: 'l' | 'r';
  dash?: boolean;
  /** Draw markers only. */
  scatter?: boolean;
  width?: number;
  fmt?: (v: number) => string;
}

export interface XYChartProps {
  height: number;
  series: XYSeries[];
  xLog?: boolean;
  xFmt?: (v: number) => string;
  yFmt?: (v: number) => string;
  y2Fmt?: (v: number) => string;
  xLabel?: string;
  yLabel?: string;
  y2Label?: string;
  vlines?: { x: number; label: string; color?: string }[];
  hlines?: { y: number; label?: string; color?: string; axis?: 'l' | 'r' }[];
  /** Vertical shaded regions in x units. */
  shades?: { x0: number; x1: number; label?: string; color?: string }[];
  /** Emphasized points (e.g. selected parameter / current AUM). */
  marks?: { x: number; y: number; axis?: 'l' | 'r'; label?: string; color?: string }[];
  zeroLine?: boolean;
}

const finite = (v: number) => Number.isFinite(v);

/** Numeric-x line / scatter chart (linear or log10 x, optional right axis). Pure SVG. */
export function XYChart(p: XYChartProps) {
  const [hx, setHx] = useState<number | null>(null);
  return (
    <ChartBox height={p.height}>
      {(W, H) => {
        const hasR = p.series.some((s) => s.axis === 'r');
        const P = { l: 44, r: hasR ? 46 : 12, t: 12, b: p.xLabel ? 30 : 20 };
        const xs = p.series.flatMap((s) => s.x).filter((v) => finite(v) && (!p.xLog || v > 0));
        if (!xs.length) return null;
        const tx = (v: number) => (p.xLog ? Math.log10(v) : v);
        let x0 = Math.min(...xs.map(tx));
        let x1 = Math.max(...xs.map(tx));
        if (x1 - x0 < 1e-9) {
          x0 -= 0.5;
          x1 += 0.5;
        }
        const yr = (axis: 'l' | 'r') => {
          const ys = p.series.filter((s) => (s.axis ?? 'l') === axis).flatMap((s) => s.y.filter(finite));
          const hs = (p.hlines ?? []).filter((h) => (h.axis ?? 'l') === axis).map((h) => h.y);
          const ms = (p.marks ?? []).filter((m) => (m.axis ?? 'l') === axis).map((m) => m.y);
          const all = [...ys, ...hs, ...ms, ...(p.zeroLine && axis === 'l' ? [0] : [])].filter(finite);
          return nice(all.length ? Math.min(...all) : 0, all.length ? Math.max(...all) : 1, 4);
        };
        const L = yr('l');
        const R = hasR ? yr('r') : L;
        const X = (v: number) => P.l + ((tx(v) - x0) / (x1 - x0)) * (W - P.l - P.r);
        const Y = (v: number, axis: 'l' | 'r' = 'l') => {
          const s = axis === 'r' ? R : L;
          return P.t + (1 - (v - s.lo) / (s.hi - s.lo || 1)) * (H - P.t - P.b);
        };
        const xf = p.xFmt ?? ((v: number) => fx(v, 2));
        const yf = p.yFmt ?? ((v: number) => fx(v, 2));
        const y2f = p.y2Fmt ?? yf;
        const xTicks: number[] = [];
        if (p.xLog) {
          for (let e = Math.floor(x0); e <= Math.ceil(x1); e++) {
            for (const m of [1, 2, 5]) {
              const v = m * 10 ** e;
              if (tx(v) >= x0 - 1e-9 && tx(v) <= x1 + 1e-9) xTicks.push(v);
            }
          }
        } else xTicks.push(...nice(x0, x1, 5).ticks.filter((v) => v >= x0 - 1e-9 && v <= x1 + 1e-9));
        const ticks = xTicks.length > 7 ? xTicks.filter((_, i) => i % Math.ceil(xTicks.length / 7) === 0) : xTicks;
        const base = p.series[0];
        const near = (s: XYSeries, xv: number) => {
          let bi = -1;
          let bd = Infinity;
          s.x.forEach((v, i) => {
            if (!finite(v) || !finite(s.y[i])) return;
            const d = Math.abs(tx(v) - tx(xv));
            if (d < bd) {
              bd = d;
              bi = i;
            }
          });
          return bi;
        };
        const onMove = (e: React.MouseEvent<SVGRectElement>) => {
          if (!base) return;
          const r = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
          const px = e.clientX - r.left;
          const tv = x0 + ((px - P.l) / (W - P.l - P.r)) * (x1 - x0);
          const xv = p.xLog ? 10 ** tv : tv;
          const i = near(base, xv);
          if (i < 0) return;
          const bx = base.x[i];
          setHx(bx);
          const rows = p.series
            .map((s) => {
              const j = near(s, bx);
              if (j < 0) return '';
              const f = s.fmt ?? (s.axis === 'r' ? y2f : yf);
              return `<div class="tr"><span><i style="background:${s.color}"></i> ${esc(s.name)}</span><b>${esc(f(s.y[j]))}</b></div>`;
            })
            .join('');
          showTip(`<div class="th">${esc(p.xLabel ?? 'x')} = ${esc(xf(bx))}</div>${rows}`, e.clientX, e.clientY);
        };
        return (
          <>
            {(p.shades ?? []).map((s, i) => {
              const a = X(Math.max(s.x0, p.xLog ? 10 ** x0 : x0));
              const b = X(Math.min(s.x1, p.xLog ? 10 ** x1 : x1));
              if (!(b > a)) return null;
              return (
                <g key={`s${i}`}>
                  <rect x={a} y={P.t} width={b - a} height={H - P.t - P.b} style={{ fill: s.color ?? 'var(--warn)', opacity: 0.07 }} />
                  {s.label ? (
                    <text x={a + 3} y={P.t + 9} className="sv-bandt" style={s.color ? { fill: s.color } : undefined}>
                      {s.label}
                    </text>
                  ) : null}
                </g>
              );
            })}
            {L.ticks.map((t) => (
              <g key={`yl${t}`}>
                <line x1={P.l} x2={W - P.r} y1={Y(t)} y2={Y(t)} className="sv-grid" />
                <text x={P.l - 5} y={Y(t) + 3} textAnchor="end" className="sv-ax">
                  {yf(t)}
                </text>
              </g>
            ))}
            {hasR
              ? R.ticks.map((t) => (
                  <text key={`yr${t}`} x={W - P.r + 5} y={Y(t, 'r') + 3} className="sv-ax">
                    {y2f(t)}
                  </text>
                ))
              : null}
            {p.zeroLine && L.lo < 0 && L.hi > 0 ? <line x1={P.l} x2={W - P.r} y1={Y(0)} y2={Y(0)} className="sv-base" /> : null}
            {ticks.map((t) => (
              <text key={`x${t}`} x={X(t)} y={H - P.b + 12} textAnchor="middle" className="sv-ax">
                {xf(t)}
              </text>
            ))}
            {p.xLabel ? (
              <text x={(P.l + W - P.r) / 2} y={H - 3} textAnchor="middle" className="sv-ax">
                {p.xLabel}
              </text>
            ) : null}
            {p.yLabel ? (
              <text x={P.l} y={P.t - 3} className="sv-ax">
                {p.yLabel}
              </text>
            ) : null}
            {p.y2Label && hasR ? (
              <text x={W - P.r} y={P.t - 3} textAnchor="end" className="sv-ax">
                {p.y2Label}
              </text>
            ) : null}
            {(p.hlines ?? []).map((h, i) => (
              <g key={`h${i}`}>
                <line x1={P.l} x2={W - P.r} y1={Y(h.y, h.axis)} y2={Y(h.y, h.axis)} style={{ stroke: h.color ?? 'var(--up)', strokeDasharray: '4 3', strokeWidth: 1 }} />
                {h.label ? (
                  <text x={W - P.r - 3} y={Y(h.y, h.axis) - 3} textAnchor="end" className="sv-ax" style={{ fill: h.color ?? 'var(--up)' }}>
                    {h.label}
                  </text>
                ) : null}
              </g>
            ))}
            {(p.vlines ?? []).map((v, i) => {
              if (!finite(v.x) || (p.xLog && v.x <= 0)) return null;
              const x = X(v.x);
              if (x < P.l - 1 || x > W - P.r + 1) return null;
              return (
                <g key={`v${i}`}>
                  <line x1={x} x2={x} y1={P.t} y2={H - P.b} style={{ stroke: v.color ?? 'var(--tx3)', strokeDasharray: '3 3', strokeWidth: 1 }} />
                  <text x={x + (x > W - P.r - 80 ? -3 : 3)} y={P.t + 9 + (i % 3) * 11} textAnchor={x > W - P.r - 80 ? 'end' : 'start'} className="sv-ax" style={{ fill: v.color ?? 'var(--tx2)' }}>
                    {v.label}
                  </text>
                </g>
              );
            })}
            {p.series.map((s, si) => {
              const pts = s.x.map((xv, i) => [xv, s.y[i]] as const).filter(([xv, yv]) => finite(xv) && finite(yv) && (!p.xLog || xv > 0));
              if (s.scatter)
                return (
                  <g key={si}>
                    {pts.map(([xv, yv], i) => (
                      <circle key={i} cx={X(xv)} cy={Y(yv, s.axis)} r={2.6} style={{ fill: s.color }} className="sv-dot" />
                    ))}
                  </g>
                );
              const d = pts.map(([xv, yv], i) => `${i ? 'L' : 'M'}${X(xv).toFixed(1)} ${Y(yv, s.axis).toFixed(1)}`).join('');
              return (
                <g key={si}>
                  <path d={d} fill="none" style={{ stroke: s.color, strokeWidth: s.width ?? 1.6, strokeDasharray: s.dash ? '4 3' : undefined }} />
                  {pts.length <= 24
                    ? pts.map(([xv, yv], i) => <circle key={i} cx={X(xv)} cy={Y(yv, s.axis)} r={1.9} style={{ fill: s.color }} />)
                    : null}
                </g>
              );
            })}
            {(p.marks ?? []).map((m, i) =>
              finite(m.x) && finite(m.y) && (!p.xLog || m.x > 0) ? (
                <g key={`m${i}`}>
                  <circle cx={X(m.x)} cy={Y(m.y, m.axis)} r={4.2} style={{ fill: 'none', stroke: m.color ?? 'var(--sig)', strokeWidth: 1.6 }} />
                  {m.label ? (
                    <text x={X(m.x) + 7} y={Y(m.y, m.axis) - 6} className="sv-ax" style={{ fill: m.color ?? 'var(--sig)', fontWeight: 600 }}>
                      {m.label}
                    </text>
                  ) : null}
                </g>
              ) : null
            )}
            {hx !== null ? <line x1={X(hx)} x2={X(hx)} y1={P.t} y2={H - P.b} className="sv-vx" /> : null}
            <rect
              x={P.l}
              y={P.t}
              width={Math.max(0, W - P.l - P.r)}
              height={Math.max(0, H - P.t - P.b)}
              fill="transparent"
              className="ov"
              onMouseMove={onMove}
              onMouseLeave={() => {
                setHx(null);
                hideTip();
              }}
            />
          </>
        );
      }}
    </ChartBox>
  );
}
