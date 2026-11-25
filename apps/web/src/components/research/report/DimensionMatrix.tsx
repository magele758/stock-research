'use client';

import { useMemo, useState } from 'react';
import { fx } from '@/lib/format';
import { openStudyDrawer } from '@/lib/glossary/store';
import { DIM_LABEL } from '@/lib/research-api';
import { gateSpec } from '@/lib/thresholds';
import type { DimensionSliceItem, ResearchStudyDetail } from '@/lib/types';
import { dominantShare, normSlices } from '@/lib/v3';
import { MatrixHeatmap, type MatrixCell } from '../../charts/MatrixHeatmap';
import { TermTip } from '../../glossary/TermTip';
import { Icon } from '../../icons';
import { EmptyState, Panel, Seg, Tag } from '../../ui';

type Metric = 'alpha_vs_baseline_pct' | 'strategy_excess_pct' | 'rank_ic' | 'contribution_pct';
const METRICS: { key: Metric; label: string }[] = [
  { key: 'alpha_vs_baseline_pct', label: 'Δ超额' },
  { key: 'strategy_excess_pct', label: '策略超额' },
  { key: 'rank_ic', label: 'RankIC' },
  { key: 'contribution_pct', label: '贡献占比' }
];
const DIM_ORDER = ['market_regime', 'volatility_regime', 'market_cap_tier', 'sw_industry', 'barra_style'];
/** Gate is a fraction (0.6); slice `contribution_pct` is already in percent. */
const DIM_FLAG = (gateSpec('dimension_dependence')?.value ?? 0.6) * 100;
const MAX_BUCKETS = 6;

const val = (s: DimensionSliceItem, m: Metric) => {
  const v = s[m];
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
};
const pctS = (v: number) => (Number.isFinite(v) ? `${v >= 0 ? '+' : ''}${fx(v, 1)}%` : '—');

/** Dimension × bucket stability matrix + per-dimension baseline comparison; DIM FLAG above 60% single-bucket share. */
export function DimensionMatrix({ slices: raw, detail, title = '④ 跨维度稳定性 & 基线对照', consistency, height }: { slices: unknown; detail?: ResearchStudyDetail; title?: string; consistency?: number | null; height?: number }) {
  const slices = useMemo(() => normSlices(raw), [raw]);
  const [metric, setMetric] = useState<Metric>('alpha_vs_baseline_pct');
  const dims = useMemo(() => {
    const g = new Map<string, DimensionSliceItem[]>();
    for (const s of slices) g.set(s.dim, [...(g.get(s.dim) ?? []), s]);
    return [...g.entries()]
      .sort((a, b) => DIM_ORDER.indexOf(a[0]) - DIM_ORDER.indexOf(b[0]))
      .map(([dim, list]) => ({ dim, list: list.length > MAX_BUCKETS ? [...list].sort((a, b) => Math.abs(b.contribution_pct || 0) - Math.abs(a.contribution_pct || 0)).slice(0, MAX_BUCKETS) : list }));
  }, [slices]);
  const [sel, setSel] = useState<string | null>(null);
  const dom = dominantShare(slices);
  const flagged = (dom && dom.share > DIM_FLAG) || slices.some((s) => s.dominant_flag);
  const flagDim = dom && dom.share > DIM_FLAG ? dom : (slices.find((s) => s.dominant_flag) ? { dim: slices.find((s) => s.dominant_flag)!.dim, bucket: slices.find((s) => s.dominant_flag)!.bucket, share: slices.find((s) => s.dominant_flag)!.contribution_pct } : null);
  const activeDim = sel ?? flagDim?.dim ?? dims[0]?.dim ?? null;
  const rows = dims.find((d) => d.dim === activeDim)?.list ?? [];

  if (!slices.length) {
    return (
      <Panel title={<TermTip id="dimension_dependence">{title}</TermTip>}>
        <EmptyState height={height ?? 110} title="DIMENSION SLICES · 后端未返回" detail="需 V3 后端返回 dimension_slices（5 维 Regime / 波动 / 市值 / 行业 / 风格切片）" />
      </Panel>
    );
  }

  const cells: MatrixCell[][] = dims.map((d) =>
    d.list.map((s) => {
      const v = val(s, metric);
      const flag = s.dominant_flag || (Number.isFinite(s.contribution_pct) && s.contribution_pct > DIM_FLAG);
      return {
        v,
        text: v === null ? '—' : metric === 'rank_ic' ? fx(v, 3) : `${fx(v, 1)}%`,
        flag,
        tip: `${DIM_LABEL[s.dim] ?? s.dim} · ${s.bucket}  策略超额 ${pctS(s.strategy_excess_pct)} · 基线 ${pctS(s.baseline_return_pct)} · Δ ${pctS(s.alpha_vs_baseline_pct)} · RankIC ${s.rank_ic !== null && s.rank_ic !== undefined ? fx(s.rank_ic, 3) : '—'} · ${s.n_days}D · 贡献 ${pctS(s.contribution_pct)}${flag ? '  DIM FLAG' : ''}`
      };
    })
  );

  const redo = () => {
    if (!flagDim) return;
    const dl = DIM_LABEL[flagDim.dim] ?? flagDim.dim;
    openStudyDrawer({
      title: `${detail?.title ?? '当前因子'} · ${dl}中性化复研`.slice(0, 60),
      hypothesis: `超额 ${fx(flagDim.share, 0)}% 来自 ${dl}「${flagDim.bucket}」桶，疑似被单一维度带偏；对 ${dl} 做中性化后复检超额是否仍显著。`,
      kind: detail?.kind ?? 'factor',
      parent_study_id: detail?.study_id ?? null,
      family_id: detail?.family_id ?? null,
      lockParent: Boolean(detail),
      factor_id: detail?.factor_id ?? null,
      expression: detail?.expression ?? null,
      universe_id: detail?.universe_id,
      target_cluster: detail?.target_cluster,
      dimensions: [flagDim.dim, ...DIM_ORDER.filter((d) => d !== flagDim.dim).slice(0, 3)],
      source: detail ? `${detail.study_id} · DIM FLAG` : 'DIM FLAG'
    });
  };

  return (
    <Panel
      title={<TermTip id="dimension_dependence" value={dom ? dom.share / 100 : null} display={dom ? `${fx(dom.share, 1)}%` : undefined} context={dom ? { bucket: `${dom.dim}/${dom.bucket}` } : undefined}>{title}</TermTip>}
      sub={
        consistency !== undefined && consistency !== null ? (
          <TermTip id="regime_consistency_score" value={consistency} display={fx(consistency, 2)}>
            一致性 {fx(consistency, 2)}
          </TermTip>
        ) : undefined
      }
      tools={<Seg value={metric} options={METRICS} onChange={setMetric} />}
    >
      {flagged && flagDim ? (
        <div className="gs warn" style={{ marginBottom: 8, flexWrap: 'wrap' }}>
          <Tag tone="fail">DIM FLAG</Tag>
          <span style={{ flex: 1, minWidth: 0 }}>
            超额 <b>{fx(flagDim.share, 0)}%</b> 来自 {DIM_LABEL[flagDim.dim] ?? flagDim.dim}「{flagDim.bucket}」，超过 {DIM_FLAG}% 单维依赖红线
          </span>
          <button type="button" className="btn sm warnb" onClick={redo}>
            <Icon name="branch" size={10} sw={2} />
            派发{DIM_LABEL[flagDim.dim] ?? ''}中性化复研
          </button>
        </div>
      ) : null}
      <MatrixHeatmap
        height={Math.max(60, dims.length * 36 + 4)}
        rows={dims.map((d) => DIM_LABEL[d.dim] ?? d.dim)}
        cells={cells}
        colLabels={dims.map((d) => d.list.map((s) => s.bucket))}
        labelWidth={52}
        onCell={(r) => setSel(dims[r].dim)}
      />
      <div className="gsec" style={{ marginTop: 8 }}>
        <span>
          基线对照 · {activeDim ? (DIM_LABEL[activeDim] ?? activeDim) : '—'}
        </span>
        <span>点击热力图行切换维度</span>
      </div>
      <table className="tbl dense compact">
        <thead>
          <tr>
            <th>桶</th>
            <th className="n">策略超额</th>
            <th className="n">基线收益</th>
            <th className="n">Δ超额</th>
            <th className="n">
              <TermTip id="rank_ic">RankIC</TermTip>
            </th>
            <th className="n">样本日</th>
            <th className="n">
              <TermTip id="dimension_dependence">贡献</TermTip>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => {
            const flag = s.dominant_flag || s.contribution_pct > DIM_FLAG;
            return (
              <tr key={s.bucket}>
                <td>
                  {s.bucket} {flag ? <Tag tone="fail">DIM FLAG</Tag> : null}
                </td>
                <td className={`n ${s.strategy_excess_pct >= 0 ? 'c-up' : 'c-dn'}`}>{pctS(s.strategy_excess_pct)}</td>
                <td className="n c-2">{pctS(s.baseline_return_pct)}</td>
                <td className="n">{pctS(s.alpha_vs_baseline_pct)}</td>
                <td className="n">{s.rank_ic !== null && s.rank_ic !== undefined ? fx(s.rank_ic, 3) : '—'}</td>
                <td className="n c-3">{s.n_days}</td>
                <td className={`n ${flag ? 'c-up' : ''}`}>{Number.isFinite(s.contribution_pct) ? `${fx(s.contribution_pct, 0)}%` : '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Panel>
  );
}
