'use client';

import { fx } from '@/lib/format';
import type { CapacityFrontierResult } from '@/lib/types';
import { fmtAum } from '@/lib/v3';
import { XYChart } from '../charts/XYChart';
import { TermTip } from '../glossary/TermTip';
import { Legend, Tag } from '../ui';

const BOTTLENECK: Record<string, string> = {
  none: '无',
  pov: 'POV 参与率',
  impact: '冲击成本',
  liquidity: '流动性',
  limit: '涨跌停',
  turnover: '换手',
  fill_rate: '成交率'
};

/** AUM capacity frontier: net / gross excess vs AUM (log x) + impact on the right axis. */
export function CapacityFrontier({ data, height = 196 }: { data: CapacityFrontierResult; height?: number }) {
  const pts = data.points;
  const x = pts.map((p) => p.aum_cny);
  const hasGross = pts.some((p) => Number.isFinite(p.gross_excess_pct));
  const hasImpact = pts.some((p) => Number.isFinite(p.impact_bps));
  const at = (aum: number) => {
    let best = pts[0];
    for (const p of pts) if (Math.abs(Math.log(p.aum_cny) - Math.log(aum)) < Math.abs(Math.log(best.aum_cny) - Math.log(aum))) best = p;
    return best;
  };
  const cur = Number.isFinite(data.current_aum_cny) ? at(data.current_aum_cny) : null;
  const vlines = [
    Number.isFinite(data.current_aum_cny) ? { x: data.current_aum_cny, label: `当前 ${fmtAum(data.current_aum_cny)}`, color: 'var(--sig)' } : null,
    data.half_alpha_aum_cny ? { x: data.half_alpha_aum_cny, label: `半衰 ${fmtAum(data.half_alpha_aum_cny)}`, color: 'var(--warn)' } : null,
    data.breakeven_aum_cny ? { x: data.breakeven_aum_cny, label: `盈亏平衡 ${fmtAum(data.breakeven_aum_cny)}`, color: 'var(--up)' } : null
  ].filter((v): v is { x: number; label: string; color: string } => !!v);
  return (
    <div>
      <div className="capk">
        <div>
          <span>当前 AUM</span>
          <b>{fmtAum(data.current_aum_cny)}</b>
        </div>
        <div>
          <span>
            <TermTip id="capacity_frontier" value={data.half_alpha_aum_cny ?? null} display={data.half_alpha_aum_cny ? fmtAum(data.half_alpha_aum_cny) : '—'} context={{ current_aum: fmtAum(data.current_aum_cny) }}>
              超额半衰 AUM
            </TermTip>
          </span>
          <b className="c-warn">{data.half_alpha_aum_cny ? fmtAum(data.half_alpha_aum_cny) : '—'}</b>
        </div>
        <div>
          <span>盈亏平衡 AUM</span>
          <b className="c-up">{data.breakeven_aum_cny ? fmtAum(data.breakeven_aum_cny) : '—'}</b>
        </div>
        <div>
          <span>
            <TermTip id={data.binding_bottleneck === 'pov' ? 'pov' : 'sqrt_impact'}>约束瓶颈</TermTip>
          </span>
          <b>{BOTTLENECK[data.binding_bottleneck] ?? data.binding_bottleneck}</b>
        </div>
      </div>
      <div className="lg" style={{ marginBottom: 4 }}>
        <Legend
          items={[
            { label: '净超额 %', color: 'var(--sig)' },
            ...(hasGross ? [{ label: '毛超额 %', color: 'var(--tx3)', dash: true }] : []),
            ...(hasImpact ? [{ label: '冲击 bp（右轴）', color: 'var(--warn)' }] : [])
          ]}
        />
        <span style={{ marginLeft: 'auto' }}>
          {data.calibration_status === 'calibrated' ? <Tag tone="pass">CALIBRATED</Tag> : <Tag tone="warn">UNCALIBRATED · 情景估计</Tag>}
        </span>
      </div>
      <XYChart
        height={height}
        xLog
        xLabel="AUM (CNY, log)"
        xFmt={fmtAum}
        yFmt={(v) => `${fx(v, 1)}%`}
        y2Fmt={(v) => `${fx(v, 0)}bp`}
        zeroLine
        series={[
          { name: '净超额', color: 'var(--sig)', x, y: pts.map((p) => p.net_excess_pct), width: 2 },
          ...(hasGross ? [{ name: '毛超额', color: 'var(--tx3)', x, y: pts.map((p) => p.gross_excess_pct), dash: true }] : []),
          ...(hasImpact ? [{ name: '冲击', color: 'var(--warn)', x, y: pts.map((p) => p.impact_bps), axis: 'r' as const, width: 1.2, fmt: (v: number) => `${fx(v, 1)} bp` }] : [])
        ]}
        vlines={vlines}
        shades={data.breakeven_aum_cny ? [{ x0: data.breakeven_aum_cny, x1: Math.max(...x) * 10, label: '净超额 ≤ 0', color: 'var(--up)' }] : []}
        marks={cur ? [{ x: cur.aum_cny, y: cur.net_excess_pct, label: `${fx(cur.net_excess_pct, 1)}%` }] : []}
      />
    </div>
  );
}
