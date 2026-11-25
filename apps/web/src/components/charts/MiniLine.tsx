'use client';

/** Fixed-size inline sparkline (no ResizeObserver) for list rows / baseline panel. */
export function MiniLine({ values, width = 64, height = 18, color = 'var(--sig)', zero = false }: { values: number[]; width?: number; height?: number; color?: string; zero?: boolean }) {
  const v = values.filter((x) => Number.isFinite(x));
  if (v.length < 2) return <svg width={width} height={height} aria-hidden />;
  const lo = Math.min(...v, ...(zero ? [0] : []));
  const hi = Math.max(...v, ...(zero ? [0] : []));
  const span = hi - lo || 1;
  const X = (i: number) => 1 + (i / (v.length - 1)) * (width - 2);
  const Y = (x: number) => 1 + (1 - (x - lo) / span) * (height - 2);
  const d = v.map((x, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)} ${Y(x).toFixed(1)}`).join('');
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden style={{ display: 'block' }}>
      {zero && lo < 0 && hi > 0 ? <line x1={0} x2={width} y1={Y(0)} y2={Y(0)} className="sv-grid" /> : null}
      <path d={d} fill="none" style={{ stroke: color, strokeWidth: 1.3 }} />
      <circle cx={X(v.length - 1)} cy={Y(v[v.length - 1])} r={1.8} style={{ fill: color }} />
    </svg>
  );
}
