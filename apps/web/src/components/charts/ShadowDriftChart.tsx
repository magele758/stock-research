'use client';

import { Fragment } from 'react';
import { esc, fx, sg } from '@/lib/format';
import { ChartBox, nice } from './core';

export interface ShadowDriftChartProps {
  height: number;
  dates: string[];
  theoretical: number[];
  shadow: number[];
  gap: number[];
  liquidity: number[];
  limitQueue: number[];
  totalIs?: number;
}

export function ShadowDriftChart(props: ShadowDriftChartProps) {
  return <ChartBox height={props.height}>{(W, H) => <DriftInner {...props} W={W} H={H} />}</ChartBox>;
}

const fin = (v: number | undefined) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
const COLS = ['var(--warn)', 'var(--sig)', 'var(--up)'];
const OPS = [0.9, 0.55, 0.95];

function DriftInner(o: ShadowDriftChartProps & { W: number; H: number }) {
  const { W, H, dates } = o;
  const n = dates.length;
  if (!n) return null;
  const th = dates.map((_, i) => fin(o.theoretical[i]));
  const sh = dates.map((_, i) => fin(o.shadow[i]));
  const parts = dates.map((_, i) => [fin(o.gap[i]), fin(o.liquidity[i]), fin(o.limitQueue[i])]);

  const P = { l: 46, r: 10, t: 14, b: 20 };
  const iw = W - P.l - P.r;
  const hh = H - P.t - P.b - 18;
  const h1 = hh * 0.56;
  const h2 = hh - h1;
  const t2 = P.t + h1 + 18;
  const bw = iw / n;
  const X = (i: number) => P.l + (i + 0.5) * bw;

  const d1 = nice(Math.min(0, ...th, ...sh), Math.max(...th, ...sh), 3);
  const Y1 = (v: number) => P.t + h1 - ((v - d1.lo) / (d1.hi - d1.lo)) * h1;
  const pos = parts.map((p) => p.filter((v) => v > 0).reduce((a, b) => a + b, 0));
  const neg = parts.map((p) => p.filter((v) => v < 0).reduce((a, b) => a + b, 0));
  const d2 = nice(Math.min(0, ...neg), Math.max(1, ...pos), 3);
  const Y2 = (v: number) => t2 + h2 - ((v - d2.lo) / (d2.hi - d2.lo)) * h2;

  const pth = (a: number[]) => a.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)} ${Y1(v).toFixed(1)}`).join('');
  const back = sh
    .map((v, i) => [X(i), Y1(v)] as const)
    .reverse()
    .map(([x, y]) => `L${x.toFixed(1)} ${y.toFixed(1)}`)
    .join('');
  const tickEvery = Math.max(1, Math.round(n / 6));
  const lastTh = th[n - 1];
  const lastSh = sh[n - 1];
  const isLabel = typeof o.totalIs === 'number' && Number.isFinite(o.totalIs) ? ` · IS ${fx(o.totalIs, 0)}` : '';

  return (
    <>
      <text x={P.l} y={P.t - 4} className="sv-ax">
        累计超额 (bps)
      </text>
      <text x={P.l} y={t2 - 5} className="sv-ax">
        每日偏离归因 (bps)
      </text>
      {d1.ticks.map((t) => (
        <Fragment key={`a${t}`}>
          <line x1={P.l} x2={W - P.r} y1={Y1(t)} y2={Y1(t)} className={t ? 'sv-grid' : 'sv-base'} />
          <text x={P.l - 6} y={Y1(t) + 3} className="sv-ax" textAnchor="end">
            {t}
          </text>
        </Fragment>
      ))}
      {d2.ticks.map((t) => (
        <Fragment key={`b${t}`}>
          <line x1={P.l} x2={W - P.r} y1={Y2(t)} y2={Y2(t)} className={t ? 'sv-grid' : 'sv-base'} />
          <text x={P.l - 6} y={Y2(t) + 3} className="sv-ax" textAnchor="end">
            {t}
          </text>
        </Fragment>
      ))}
      <path d={`${pth(th)}${back}Z`} style={{ fill: 'var(--warn)', opacity: 0.13 }} />
      <path d={pth(th)} fill="none" style={{ stroke: 'var(--tx2)', strokeWidth: 1.3, strokeDasharray: '4 3' }} />
      <path d={pth(sh)} fill="none" style={{ stroke: 'var(--sig)', strokeWidth: 1.7 }} />
      <text x={X(n - 1) - 4} y={Y1(lastTh) - 6} textAnchor="end" style={{ fill: 'var(--tx2)', fontSize: 9.5 }}>
        理论 {fx(lastTh, 0)}
      </text>
      <text x={X(n - 1) - 4} y={Y1(lastSh) + 13} textAnchor="end" style={{ fill: 'var(--sig)', fontSize: 9.5 }}>
        影子 {fx(lastSh, 0)}
        {isLabel}
      </text>
      {parts.map((p, i) => {
        let up = 0;
        let dn = 0;
        return (
          <Fragment key={`d${i}`}>
            {p.map((v, k) => {
              let ya: number;
              let yb: number;
              if (v >= 0) {
                ya = Y2(up + v);
                yb = Y2(up);
                up += v;
              } else {
                ya = Y2(dn);
                yb = Y2(dn + v);
                dn += v;
              }
              return <rect key={k} x={X(i) - bw * 0.34} y={ya} width={bw * 0.68} height={Math.max(0.5, yb - ya)} style={{ fill: COLS[k], opacity: OPS[k] }} />;
            })}
          </Fragment>
        );
      })}
      {dates.map((dt, i) =>
        i % tickEvery === 0 || i === n - 1 ? (
          <text key={`x${i}`} x={X(i)} y={H - 5} className="sv-ax" textAnchor="middle">
            {dt.slice(5, 10)}
          </text>
        ) : null
      )}
      {dates.map((dt, i) => {
        const [g, l, q] = parts[i];
        const html = `<div class='th'>${esc(dt)}</div>理论 ${fx(th[i], 1)} · 影子 ${fx(sh[i], 1)} bps<br>开盘跳空 ${sg(g, 2)} · 流动性 ${sg(l, 2)}<br>涨跌停排队 ${sg(q, 2)} · 合计 <b>${sg(g + l + q, 2)}</b> bps`;
        return <rect key={`t${i}`} x={P.l + i * bw} y={P.t} width={bw} height={H - P.t - P.b} fill="transparent" data-tip={html} />;
      })}
    </>
  );
}
