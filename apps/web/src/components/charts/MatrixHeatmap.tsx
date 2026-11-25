'use client';

import { Fragment } from 'react';
import { esc, fx } from '@/lib/format';
import { ChartBox } from './core';

export interface MatrixCell {
  v: number | null;
  /** Text inside the cell (defaults to fx(v, 1)). */
  text?: string;
  tip?: string;
  flag?: boolean;
}

/**
 * Generic rows × cols heatmap (dimension × bucket stability). Signed values use the
 * --heat-pos / --heat-neg ramps; `flag` cells get a red border + corner mark.
 * Rows may have different column counts (ragged buckets per dimension).
 */
export function MatrixHeatmap({
  height,
  rows,
  cells,
  colLabels,
  absMax,
  labelWidth = 70,
  onCell
}: {
  height: number;
  rows: string[];
  cells: MatrixCell[][];
  /** Per-row column labels. */
  colLabels: string[][];
  absMax?: number;
  labelWidth?: number;
  onCell?: (r: number, c: number) => void;
}) {
  const maxCols = Math.max(1, ...cells.map((r) => r.length));
  const am = absMax ?? Math.max(1e-9, ...cells.flat().map((c) => (c.v === null || !Number.isFinite(c.v) ? 0 : Math.abs(c.v))));
  return (
    <ChartBox height={height}>
      {(W, H) => {
        const P = { l: labelWidth, t: 2, r: 4, b: 2 };
        const cw = (W - P.l - P.r) / maxCols;
        const rh = (H - P.t - P.b) / Math.max(1, rows.length);
        return (
          <>
            {rows.map((name, i) => (
              <Fragment key={name}>
                <text x={P.l - 7} y={P.t + rh * i + rh / 2 + 3.5} textAnchor="end" className="sv-axl" style={{ fontSize: 10 }}>
                  {name}
                </text>
                {(cells[i] ?? []).map((c, j) => {
                  const x = P.l + cw * j;
                  const y = P.t + rh * i;
                  const ok = c.v !== null && Number.isFinite(c.v);
                  const a = ok ? Math.min(1, Math.abs(c.v as number) / am) : 0;
                  const fill = ok ? `rgba(var(${(c.v as number) >= 0 ? '--heat-pos' : '--heat-neg'}), ${(0.08 + 0.7 * a).toFixed(2)})` : 'var(--bg2)';
                  const lab = colLabels[i]?.[j] ?? '';
                  return (
                    <g key={j} style={onCell ? { cursor: 'pointer' } : undefined} onClick={onCell ? () => onCell(i, j) : undefined}>
                      <rect
                        x={x + 1}
                        y={y + 1}
                        width={Math.max(0, cw - 2)}
                        height={Math.max(0, rh - 2)}
                        style={{ fill, ...(c.flag ? { stroke: 'var(--up)', strokeWidth: 1.6 } : {}) }}
                        data-tip={esc(c.tip ?? `${name} · ${lab}  ${ok ? fx(c.v, 2) : '—'}`)}
                      />
                      <text x={x + 5} y={y + 12} style={{ fontSize: 9, fill: a > 0.6 ? '#0A0D12' : 'var(--tx3)', pointerEvents: 'none' }}>
                        {lab.length > 10 && cw < 90 ? `${lab.slice(0, 9)}…` : lab}
                      </text>
                      <text x={x + cw / 2} y={y + rh / 2 + 8} textAnchor="middle" style={{ fontSize: 11, fill: a > 0.6 ? '#0A0D12' : 'var(--tx)', fontWeight: c.flag ? 700 : 500, pointerEvents: 'none' }}>
                        {c.text ?? (ok ? fx(c.v, 1) : '—')}
                      </text>
                      {c.flag ? <path d={`M${(x + cw - 9).toFixed(1)} ${(y + 1).toFixed(1)}h8v8Z`} style={{ fill: 'var(--up)' }} /> : null}
                    </g>
                  );
                })}
              </Fragment>
            ))}
          </>
        );
      }}
    </ChartBox>
  );
}
