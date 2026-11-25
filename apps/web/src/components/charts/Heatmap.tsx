'use client';

import { Fragment } from 'react';
import { esc, fx } from '@/lib/format';
import { ChartBox } from './core';

/** Correlation heatmap; row/col 0 is the candidate factor (highlighted). |r| ≥ 0.70 flagged red. */
export function Heatmap({ height, labels, short, matrix, riskAbs = 0.7, labelWidth = 146 }: { height: number; labels: string[]; short?: string[]; matrix: number[][]; riskAbs?: number; labelWidth?: number }) {
  const shortL = short ?? labels.map((l) => (l.length > 8 ? l.slice(0, 8) : l));
  return (
    <ChartBox height={height}>
      {(W, H) => {
        const n = labels.length;
        if (!n) return null;
        const P = { l: labelWidth, t: 18, r: 6, b: 4 };
        const cw = (W - P.l - P.r) / n;
        const ch = (H - P.t - P.b) / n;
        return (
          <>
            {shortL.map((s, j) => (
              <text key={`c${j}`} x={P.l + cw * j + cw / 2} y={12} className="sv-ax" textAnchor="middle" style={j === 0 ? { fill: 'var(--sig)', fontWeight: 600 } : undefined}>
                {s}
              </text>
            ))}
            {labels.map((nm, i) => (
              <Fragment key={`r${i}`}>
                <text x={P.l - 8} y={P.t + ch * i + ch / 2 + 3.5} className="sv-axl" textAnchor="end" style={{ fontSize: 9.5, ...(i === 0 ? { fill: 'var(--sig)', fontWeight: 600 } : {}) }}>
                  {nm.length > 22 ? `${nm.slice(0, 21)}…` : nm}
                </text>
                {(matrix[i] ?? []).map((r, j) => {
                  const x = P.l + cw * j;
                  const y = P.t + ch * i;
                  const a = Math.min(1, Math.abs(r));
                  const diag = i === j;
                  const risk = !diag && a >= riskAbs;
                  const fill = diag ? 'var(--bg3)' : `rgba(var(${r >= 0 ? '--heat-pos' : '--heat-neg'}), ${(0.06 + 0.78 * a).toFixed(2)})`;
                  return (
                    <Fragment key={j}>
                      <rect
                        x={x + 1}
                        y={y + 1}
                        width={Math.max(0, cw - 2)}
                        height={Math.max(0, ch - 2)}
                        style={{ fill, ...(risk ? { stroke: 'var(--up)', strokeWidth: 1.6 } : {}) }}
                        data-tip={esc(`${labels[i]} × ${labels[j]}  r = ${fx(r, 2)}${risk ? `  |r|≥${riskAbs.toFixed(2)} 冗余风险` : ''}`)}
                      />
                      <text x={x + cw / 2} y={y + ch / 2 + 3.5} textAnchor="middle" style={{ fontSize: 10.5, fill: a > 0.55 && !diag ? '#0A0D12' : 'var(--tx2)', fontWeight: risk ? 700 : 400, pointerEvents: 'none' }}>
                        {diag ? '1.00' : fx(r, 2)}
                      </text>
                      {risk ? <path d={`M${(x + cw - 9).toFixed(1)} ${(y + 1).toFixed(1)}h8v8Z`} style={{ fill: 'var(--up)' }} /> : null}
                    </Fragment>
                  );
                })}
              </Fragment>
            ))}
            <rect x={P.l} y={P.t} width={cw * n} height={ch} fill="none" style={{ stroke: 'var(--sig)', strokeWidth: 1, strokeDasharray: '3 2' }} />
          </>
        );
      }}
    </ChartBox>
  );
}
