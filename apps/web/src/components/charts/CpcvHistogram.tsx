'use client';

import { Fragment } from 'react';
import { clamp, esc, fx, isNum, median } from '@/lib/format';
import { ChartBox, nice } from './core';

export interface CpcvHistogramProps {
  height: number;
  paths: number[];
  baseline?: number[];
  isSharpe?: number | null;
  oosMedian?: number | null;
  color: string;
  label: string;
  baselineLabel?: string;
  nSplits?: number;
  kTest?: number;
  nCombinations?: number;
}

const BIN = 0.5;

/** CPCV path OOS-Sharpe histogram: overfit zone, baseline outline, IS / OOS-median markers, path strip. */
export function CpcvHistogram(props: CpcvHistogramProps) {
  return <ChartBox height={props.height}>{(W, H) => <CpcvInner {...props} W={W} H={H} />}</ChartBox>;
}

function CpcvInner(o: CpcvHistogramProps & { W: number; H: number }) {
  const { W, H } = o;
  const paths = o.paths.filter(isNum);
  const ref = (o.baseline ?? []).filter(isNum);
  if (!paths.length) return null;

  const all = paths.concat(ref, isNum(o.isSharpe) ? [o.isSharpe] : []);
  const lo = Math.floor(Math.min(-2, Math.min(...all) - 0.5) / BIN) * BIN;
  const hi = Math.ceil(Math.max(4.5, Math.max(...all) + 0.5) / BIN) * BIN;
  const nb = Math.max(1, Math.round((hi - lo) / BIN));
  const count = (a: number[]) => {
    const c = new Array<number>(nb).fill(0);
    a.forEach((v) => c[clamp(Math.floor((v - lo) / BIN), 0, nb - 1)]++);
    return c;
  };
  const cs = count(paths);
  const cr = count(ref);

  const P = { l: 34, r: 12, t: 24, b: 66 };
  const iw = W - P.l - P.r;
  const ih = H - P.t - P.b;
  const X = (v: number) => P.l + ((v - lo) / (hi - lo)) * iw;
  const yd = nice(0, Math.max(6, ...cs, ...cr), 4);
  const ymax = yd.hi;
  const Y = (c: number) => P.t + ih - (c / ymax) * ih;
  const bw = iw / nb;
  const med = isNum(o.oosMedian) ? o.oosMedian : median(paths);
  const neg = paths.filter((v) => v < 0).length;
  const xStep = Math.max(1, Math.ceil((hi - lo) / 10));
  const xTicks: number[] = [];
  for (let v = Math.ceil(lo); v <= Math.floor(hi); v += xStep) xTicks.push(v);
  const sy = H - 30;
  const x0 = X(0);

  return (
    <>
      <rect x={X(lo)} y={P.t} width={Math.max(0, x0 - X(lo))} height={ih} style={{ fill: 'var(--up)', opacity: 0.07 }} />
      <text x={X(lo) + 5} y={P.t + 12} style={{ fill: 'var(--up)', fontSize: 9.5 }}>
        OOS SR&lt;0 · 过拟合区
      </text>
      {yd.ticks.map((c) => {
        const y = Y(c);
        return (
          <Fragment key={`y${c}`}>
            <line x1={P.l} x2={W - P.r} y1={y} y2={y} className={c ? 'sv-grid' : 'sv-base'} />
            <text x={P.l - 6} y={y + 3} className="sv-ax" textAnchor="end">
              {c}
            </text>
          </Fragment>
        );
      })}
      {cs.map((c, k) => {
        const x = P.l + k * bw + 2;
        const w = Math.max(1, bw - 4);
        const a = lo + k * BIN;
        const r = cr[k];
        return (
          <Fragment key={`b${k}`}>
            {r ? (
              <rect x={x} y={Y(r)} width={w} height={Y(0) - Y(r)} style={{ fill: 'none', stroke: 'var(--tx2)', strokeDasharray: '3 2', opacity: 0.7 }} />
            ) : null}
            {c ? (
              <rect
                x={x + Math.min(3, w / 4)}
                y={Y(c)}
                width={Math.max(1, w - Math.min(6, w / 2))}
                height={Y(0) - Y(c)}
                style={{ fill: o.color, opacity: 0.82 }}
                data-tip={esc(`SR ∈ [${fx(a, 1)}, ${fx(a + BIN, 1)}) · ${c} 条路径${ref.length ? ` · ${o.baselineLabel ?? '对照'} ${r}` : ''}`)}
              />
            ) : null}
          </Fragment>
        );
      })}
      {xTicks.map((v) => (
        <text key={`x${v}`} x={X(v)} y={P.t + ih + 13} className="sv-ax" textAnchor="middle">
          {v}
        </text>
      ))}
      <line x1={x0} x2={x0} y1={P.t} y2={P.t + ih} style={{ stroke: 'var(--up)', strokeWidth: 1.5 }} />
      {isNum(o.isSharpe) ? (
        <>
          <line x1={X(o.isSharpe)} x2={X(o.isSharpe)} y1={P.t - 6} y2={P.t + ih} style={{ stroke: 'var(--warn)', strokeDasharray: '4 3', strokeWidth: 1.3 }} />
          <text x={X(o.isSharpe) + 3} y={P.t - 9} style={{ fill: 'var(--warn)', fontSize: 9.5 }}>
            IS {fx(o.isSharpe)}
          </text>
        </>
      ) : null}
      {isNum(med) ? (
        <>
          <line x1={X(med)} x2={X(med)} y1={P.t - 6} y2={P.t + ih} style={{ stroke: 'var(--sig)', strokeWidth: 1.5 }} />
          <text x={X(med) - 3} y={P.t - 9} textAnchor="end" style={{ fill: 'var(--sig)', fontSize: 9.5 }}>
            OOS 中位 {fx(med)}
          </text>
        </>
      ) : null}
      <text x={P.l} y={H - 4} className="sv-ax">
        PATHS · {paths.length} 条 CPCV 路径
        {o.nSplits && o.kTest ? ` · 测试组合 C(${o.nSplits},${o.kTest})${o.nCombinations ? ` = ${o.nCombinations}` : ''}` : ''}
      </text>
      <line x1={P.l} x2={W - P.r} y1={sy} y2={sy} className="sv-grid" />
      {paths.map((v, k) => (
        <rect
          key={`p${k}`}
          x={X(clamp(v, lo, hi)) - 3.5}
          y={sy - 4 + ((k % 3) - 1) * 7}
          width={7}
          height={7}
          style={{ fill: v < 0 ? 'var(--up)' : o.color, opacity: 0.9 }}
          data-tip={esc(`${o.label} · Path ${String(k + 1).padStart(2, '0')} · OOS SR ${fx(v)}`)}
        />
      ))}
      <text x={W - P.r} y={H - 4} className="sv-ax" textAnchor="end">
        OOS Sharpe (年化) · 负路径 {neg}/{paths.length}
      </text>
    </>
  );
}
