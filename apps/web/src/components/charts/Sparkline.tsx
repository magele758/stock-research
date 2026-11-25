'use client';

import { fx, isNum } from '@/lib/format';
import type { FactorDecayItem } from '@/lib/types';

const STATUS_COLOR: Record<FactorDecayItem['status'], string> = {
  ACTIVE: 'var(--sig)',
  DEGRADING: 'var(--warn)',
  'KILL-SWITCH': 'var(--up)'
};

/**
 * Compact RankIC gauge. The shadow API exposes only the latest 60D RankIC per factor
 * (no per-day series), so this marks that single value on a fixed scale instead of
 * drawing a trend line.
 */
export function Sparkline({
  value,
  status,
  lo = -0.05,
  hi = 0.1,
  width = 96,
  height = 22
}: {
  value: number | null | undefined;
  status: FactorDecayItem['status'];
  lo?: number;
  hi?: number;
  width?: number;
  height?: number;
}) {
  const pad = 3;
  const X = (v: number) => pad + ((Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo)) * (width - pad * 2);
  const cy = height / 2;
  const x0 = X(0);
  const col = STATUS_COLOR[status] ?? 'var(--tx3)';
  const ok = isNum(value);
  const xv = ok ? X(value) : x0;
  const clipped = ok && (value < lo || value > hi);
  const tip = `<div class='th'>60D RankIC</div>${ok ? fx(value, 4) : '—'} · scale [${lo}, ${hi}]${clipped ? ' · 超出刻度' : ''}`;
  return (
    <svg width={width} height={height} style={{ display: 'block' }} data-tip={tip}>
      <line x1={pad} x2={width - pad} y1={cy} y2={cy} className="sv-tick" />
      <line x1={x0} x2={x0} y1={3} y2={height - 3} style={{ stroke: 'var(--tx3)', strokeDasharray: '2 2' }} />
      {ok ? (
        <>
          <rect x={Math.min(x0, xv)} y={cy - 2.5} width={Math.max(1, Math.abs(xv - x0))} height={5} style={{ fill: col, opacity: 0.55 }} />
          <rect x={xv - 1.5} y={cy - 6} width={3} height={12} style={{ fill: col }} />
          {clipped ? <path d={value > hi ? `M${width - 1} ${cy}l-4 -3v6Z` : `M1 ${cy}l4 -3v6Z`} style={{ fill: col }} /> : null}
        </>
      ) : null}
    </svg>
  );
}
