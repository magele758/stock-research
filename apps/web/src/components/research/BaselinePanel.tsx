'use client';

import { fx, shortHash } from '@/lib/format';
import { DIM_LABEL } from '@/lib/research-api';
import type { DimensionBaselineRecord } from '@/lib/types';
import { num } from '@/lib/v3';
import { MiniLine } from '../charts/MiniLine';
import { TermTip } from '../glossary/TermTip';

const ORDER = ['market_regime', 'volatility_regime', 'market_cap_tier', 'sw_industry', 'barra_style'];

/** Snapshot-bound dimension baselines (bucket annualized return); the current state is highlighted. */
export function BaselinePanel({ items }: { items: DimensionBaselineRecord[] }) {
  if (!items.length) {
    return (
      <div className="empty" style={{ height: 100, margin: 8 }}>
        <b>NO BASELINES</b>
        <span>当前快照尚未构建维度基线</span>
      </div>
    );
  }
  const sorted = [...items].sort((a, b) => ORDER.indexOf(a.dimension_type) - ORDER.indexOf(b.dimension_type));
  return (
    <div>
      {sorted.map((b) => {
        const stats = (b.bucket_stats ?? []).map((r) => ({
          bucket: String(r.bucket ?? r.name ?? ''),
          ret: num(r.ann_return_pct ?? r.return_pct ?? r.ret_pct),
          n: num(r.n_days)
        }));
        const shown = b.dimension_type === 'sw_industry' ? [...stats].sort((x, y) => (y.ret ?? -1e9) - (x.ret ?? -1e9)) : stats;
        const top = b.dimension_type === 'sw_industry' && shown.length > 10 ? [...shown.slice(0, 5), ...shown.slice(-5)] : shown;
        return (
          <div key={b.baseline_id} style={{ borderBottom: '1px solid var(--line)' }}>
            <div className="nsec" style={{ padding: '7px 9px 3px', alignItems: 'center' }}>
              <span>
                <TermTip id={b.dimension_type === 'market_regime' ? 'regime_classification' : 'dimension_dependence'} context={{ dimension: b.dimension_type, current: b.current_state, sha: shortHash(b.baseline_sha256, 4) }}>
                  {DIM_LABEL[b.dimension_type] ?? b.dimension_type}
                </TermTip>
                {b.current_state ? <span className="c-sig" style={{ marginLeft: 6, letterSpacing: 0 }}>当前 {b.current_state}</span> : null}
              </span>
              <MiniLine values={stats.map((s) => s.ret ?? NaN)} width={56} height={14} zero />
            </div>
            {top.map((s) => (
              <div key={s.bucket} className="blrow" style={s.bucket === b.current_state ? { background: 'var(--sig-bg)' } : undefined}>
                <span title={s.bucket}>{s.bucket}</span>
                <span className="mono c-3" style={{ fontSize: 9.5 }}>
                  {s.n !== null ? `${s.n}D` : ''}
                </span>
                <b className={s.ret !== null && s.ret < 0 ? 'c-dn' : 'c-up'}>{s.ret !== null ? `${s.ret >= 0 ? '+' : ''}${fx(s.ret, 1)}%` : '—'}</b>
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}
