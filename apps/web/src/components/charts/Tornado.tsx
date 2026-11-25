'use client';

import { Fragment } from 'react';
import { clamp, esc, fx, isNum, sg } from '@/lib/format';
import { ChartBox } from './core';

export interface TornadoRow {
  key: string;
  label: string;
  pre: number;
  post: number;
  limit?: number;
  breached?: boolean;
  /** Live optimizer constraint (±σ) drawn as solid warn markers, e.g. max_size_z on the Size row. */
  constraint?: number;
}

export interface TornadoProps {
  rows: TornadoRow[];
  height?: number;
  hardLimit?: number;
}

export function Tornado(props: TornadoProps) {
  return <ChartBox height={props.height ?? 300}>{(W, H) => <TornadoInner {...props} W={W} H={H} />}</ChartBox>;
}

function TornadoInner({ rows, hardLimit = 0.5, W, H }: TornadoProps & { W: number; H: number }) {
  const n = rows.length;
  if (!n) return null;
  const P = { l: 104, r: 96, t: 18, b: 18 };
  const iw = W - P.l - P.r;
  const rh = (H - P.t - P.b) / n;
  const maxAbs = Math.max(
    hardLimit,
    ...rows.flatMap((r) => [r.pre, r.post, r.limit ?? 0, r.constraint ?? 0].filter(isNum).map(Math.abs))
  );
  const span = Math.max(1, Math.ceil((maxAbs * 1.05) / 0.5) * 0.5);
  const step = span <= 1.5 ? 0.5 : 1;
  const ticks: number[] = [];
  for (let v = -span; v <= span + 1e-9; v += step) ticks.push(+v.toFixed(6));
  const X = (v: number) => P.l + iw / 2 + (clamp(v, -span, span) / span) * (iw / 2);
  const hard = (v: number) => X(v).toFixed(1);

  return (
    <>
      {ticks.map((v) => {
        const x = X(v);
        return (
          <Fragment key={`t${v}`}>
            <line x1={x} x2={x} y1={P.t} y2={H - P.b} className={Math.abs(v) < 1e-9 ? 'sv-base' : 'sv-grid'} />
            <text x={x} y={H - 5} className="sv-ax" textAnchor="middle">
              {v > 0 ? '+' : ''}
              {v.toFixed(1)}σ
            </text>
          </Fragment>
        );
      })}
      {[-hardLimit, hardLimit].map((v) => (
        <line key={`h${v}`} x1={hard(v)} x2={hard(v)} y1={P.t - 6} y2={H - P.b} style={{ stroke: 'var(--up)', strokeDasharray: '4 3', strokeWidth: 1.2 }} />
      ))}
      <text x={X(hardLimit) + 4} y={P.t - 7} style={{ fill: 'var(--up)', fontSize: 9.5 }}>
        硬边界 +{fx(hardLimit, 1)}σ
      </text>
      <text x={X(-hardLimit) - 4} y={P.t - 7} textAnchor="end" style={{ fill: 'var(--up)', fontSize: 9.5 }}>
        -{fx(hardLimit, 1)}σ 硬边界
      </text>
      {rows.map((r, i) => {
        const y = P.t + i * rh;
        const cy = y + rh / 2;
        const bad = !!r.breached || Math.abs(r.post) > hardLimit;
        const pa = X(Math.min(0, r.pre));
        const pb = X(Math.max(0, r.pre));
        const qa = X(Math.min(0, r.post));
        const qb = X(Math.max(0, r.post));
        const showLimit = isNum(r.limit) && r.limit > 0 && !(isNum(r.constraint) && Math.abs(r.limit - r.constraint) < 1e-6);
        const tip = `${r.label} · 优化前 ${sg(r.pre)}σ → 优化后 ${sg(r.post)}σ${isNum(r.limit) ? ` · 限额 ±${fx(r.limit)}σ` : ''}${bad ? ` · 越界（${r.breached ? '约束触发' : `超出 ±${fx(hardLimit, 1)}σ 硬边界`}）` : ''}`;
        return (
          <g key={r.key}>
            {i % 2 === 0 ? <rect x={0} y={y} width={W} height={rh} style={{ fill: 'var(--bg2)', opacity: 0.45 }} /> : null}
            <text x={P.l - 10} y={cy + 3.5} textAnchor="end" style={{ fill: 'var(--tx2)', fontSize: 11, fontFamily: 'var(--f-sans)' }}>
              {r.label}
            </text>
            <rect
              x={pa}
              y={cy - rh * 0.32}
              width={Math.max(0, pb - pa)}
              height={rh * 0.64}
              style={{ fill: 'var(--tx3)', fillOpacity: 0.07, stroke: 'var(--tx2)', strokeDasharray: '3 2', strokeWidth: 1 }}
            />
            <rect x={qa} y={cy - rh * 0.17} width={Math.max(1, qb - qa)} height={rh * 0.34} style={{ fill: bad ? 'var(--up)' : 'var(--sig)' }} data-tip={esc(tip)} />
            {showLimit
              ? [-r.limit!, r.limit!].map((v) => (
                  <line key={`l${v}`} x1={X(v)} x2={X(v)} y1={y + 4} y2={y + rh - 4} style={{ stroke: 'var(--warn)', strokeWidth: 1.2, opacity: 0.75 }} />
                ))
              : null}
            {isNum(r.constraint) ? (
              <>
                {[-r.constraint, r.constraint].map((v) => (
                  <line key={`c${v}`} x1={X(v)} x2={X(v)} y1={y + 2} y2={y + rh - 2} style={{ stroke: 'var(--warn)', strokeWidth: 2 }} />
                ))}
                <text x={X(r.constraint) + 3} y={y + 9} style={{ fill: 'var(--warn)', fontSize: 9 }}>
                  约束 ±{fx(r.constraint)}σ
                </text>
              </>
            ) : null}
            <text x={W - P.r + 8} y={cy + 3.5} style={{ fontSize: 10.5, fill: 'var(--tx3)', fontFamily: 'var(--f-mono)' }}>
              {sg(r.pre)} → <tspan style={{ fill: bad ? 'var(--up)' : 'var(--tx)', fontWeight: 600 }}>{sg(r.post)}</tspan>
            </text>
          </g>
        );
      })}
    </>
  );
}
