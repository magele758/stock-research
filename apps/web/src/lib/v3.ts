/**
 * Tolerant readers for V3 fields that the backend declares as `dict` / `list[dict]`
 * (fdr_items, param_sensitivity, invariants, capacity_frontier, dimension_slices).
 * Never synthesize numbers: missing → null / empty, and the UI shows EmptyState.
 */
import type { CapacityFrontierResult, CapacityPoint, DimensionSliceItem, InvariantCheckItem } from './types';

type Rec = Record<string, unknown>;
const isRec = (x: unknown): x is Rec => !!x && typeof x === 'object' && !Array.isArray(x);

export function num(x: unknown): number | null {
  if (typeof x === 'number') return Number.isFinite(x) ? x : null;
  if (typeof x === 'string' && x.trim() !== '') {
    const n = Number(x);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}
const str = (x: unknown, d = '') => (typeof x === 'string' ? x : typeof x === 'number' ? String(x) : d);
const first = (r: Rec, keys: string[]) => {
  for (const k of keys) if (r[k] !== undefined && r[k] !== null) return r[k];
  return undefined;
};
const numArr = (x: unknown): number[] | null => (Array.isArray(x) && x.length ? x.map((v) => num(v) ?? NaN) : null);

/** `[lo, hi]` confidence interval or null. */
export function ci(x: unknown): [number, number] | null {
  if (!Array.isArray(x) || x.length < 2) return null;
  const lo = num(x[0]);
  const hi = num(x[1]);
  return lo === null || hi === null ? null : [lo, hi];
}

// ---------------------------------------------------------------- BH-FDR
export interface FdrRow {
  id: string;
  label: string;
  p: number | null;
  q: number | null;
  rejected: boolean | null;
  rank: number | null;
}

export function normFdr(items: unknown): FdrRow[] {
  if (!Array.isArray(items)) return [];
  return items.filter(isRec).map((r, i) => {
    const rej = first(r, ['rejected', 'significant', 'passed', 'discovery']);
    return {
      id: str(first(r, ['experiment_id', 'id', 'model_id', 'factor_id', 'name']), `#${i + 1}`),
      label: str(first(r, ['label', 'name', 'experiment_id', 'model_id']), `#${i + 1}`),
      p: num(first(r, ['p_value', 'p', 'raw_p'])),
      q: num(first(r, ['q_value', 'q', 'bh_q', 'adjusted_p'])),
      rejected: typeof rej === 'boolean' ? rej : null,
      rank: num(first(r, ['rank', 'bh_rank']))
    };
  });
}

// ---------------------------------------------------------------- parameter cliff
export interface ParamCurve {
  param: string;
  values: number[];
  metric: number[];
  metricName: string;
  selected: number | null;
  dropPct: number | null;
}

export function normParamSensitivity(items: unknown): ParamCurve[] {
  if (!Array.isArray(items)) return [];
  const recs = items.filter(isRec);
  const curves: ParamCurve[] = [];
  const grouped = new Map<string, { v: number; m: number; sel: boolean; drop: number | null; metricName: string }[]>();
  for (const r of recs) {
    const param = str(first(r, ['param', 'name', 'parameter', 'key']), 'param');
    const values = numArr(first(r, ['values', 'grid', 'param_values', 'x']));
    const metricKey = ['oos_sharpe', 'metric', 'metrics', 'scores', 'y', 'sharpe'].find((k) => Array.isArray(r[k]));
    if (values && metricKey) {
      const metric = numArr(r[metricKey]) ?? [];
      const selIdx = num(first(r, ['selected_index', 'chosen_index', 'best_index']));
      const selVal = num(first(r, ['selected', 'selected_value', 'chosen', 'value']));
      const selected = selIdx !== null ? selIdx : selVal !== null ? values.findIndex((v) => v === selVal) : null;
      curves.push({
        param,
        values,
        metric,
        metricName: str(first(r, ['metric_name']), metricKey === 'metric' ? 'metric' : metricKey),
        selected: selected !== null && selected >= 0 ? selected : null,
        dropPct: num(first(r, ['max_neighbor_drop_pct', 'max_drop_pct', 'cliff_drop_pct', 'drop_pct']))
      });
      continue;
    }
    const v = num(first(r, ['value', 'param_value', 'x']));
    const m = num(first(r, ['oos_sharpe', 'metric', 'sharpe', 'score', 'y']));
    if (v === null || m === null) continue;
    const list = grouped.get(param) ?? [];
    list.push({
      v,
      m,
      sel: first(r, ['selected', 'is_selected', 'chosen']) === true,
      drop: num(first(r, ['max_neighbor_drop_pct', 'neighbor_drop_pct', 'drop_pct'])),
      metricName: r.oos_sharpe !== undefined ? 'oos_sharpe' : 'metric'
    });
    grouped.set(param, list);
  }
  for (const [param, list] of grouped) {
    list.sort((a, b) => a.v - b.v);
    const drops = list.map((x) => x.drop).filter((x): x is number => x !== null);
    const sel = list.findIndex((x) => x.sel);
    curves.push({
      param,
      values: list.map((x) => x.v),
      metric: list.map((x) => x.m),
      metricName: list[0]?.metricName ?? 'metric',
      selected: sel >= 0 ? sel : null,
      dropPct: drops.length ? Math.max(...drops) : null
    });
  }
  return curves;
}

/** Worst neighbour drop at the selected point, computed only from returned grid values. */
export function neighborDropPct(c: ParamCurve): number | null {
  if (c.dropPct !== null) return c.dropPct;
  if (c.selected === null) return null;
  const s = c.metric[c.selected];
  if (!Number.isFinite(s) || s === 0) return null;
  const nb = [c.metric[c.selected - 1], c.metric[c.selected + 1]].filter((x) => Number.isFinite(x));
  if (!nb.length) return null;
  return Math.max(...nb.map((x) => ((s - x) / Math.abs(s)) * 100), 0);
}

// ---------------------------------------------------------------- invariants
export function normInvariants(items: unknown): InvariantCheckItem[] {
  if (!Array.isArray(items)) return [];
  return items.filter(isRec).map((r, i) => {
    const passed = first(r, ['passed', 'ok', 'pass']);
    const status = str(r.status).toLowerCase();
    return {
      key: str(first(r, ['key', 'id', 'check_id', 'name']), `inv_${i}`),
      label: str(first(r, ['label', 'name', 'key']), `不变量 ${i + 1}`),
      value: str(first(r, ['value', 'observed', 'error', 'statistic']), '—'),
      tolerance: str(first(r, ['tolerance', 'tol', 'threshold']), '—'),
      passed: typeof passed === 'boolean' ? passed : status === 'pass' || status === 'passed',
      detail: str(first(r, ['detail', 'message', 'reason']))
    };
  });
}

// ---------------------------------------------------------------- capacity frontier
export function normCapacity(raw: unknown): CapacityFrontierResult | null {
  if (!isRec(raw)) return null;
  let points: CapacityPoint[] = [];
  if (Array.isArray(raw.points)) {
    points = raw.points.filter(isRec).flatMap((p) => {
      const aum = num(first(p, ['aum_cny', 'aum']));
      const net = num(first(p, ['net_excess_pct', 'net_excess', 'net']));
      if (aum === null || net === null) return [];
      return [
        {
          aum_cny: aum,
          gross_excess_pct: num(first(p, ['gross_excess_pct', 'gross_excess', 'gross'])) ?? NaN,
          net_excess_pct: net,
          ir_ann: num(first(p, ['ir_ann', 'ir'])) ?? NaN,
          impact_bps: num(first(p, ['impact_bps', 'impact'])) ?? NaN,
          explicit_cost_bps: num(first(p, ['explicit_cost_bps'])) ?? NaN,
          pov_pct: num(first(p, ['pov_pct', 'pov'])) ?? NaN,
          fill_rate_pct: num(first(p, ['fill_rate_pct', 'fill_rate'])) ?? NaN
        }
      ];
    });
  } else {
    const aum = numArr(first(raw, ['aum_cny', 'aum_grid', 'aum']));
    const net = numArr(first(raw, ['net_excess_pct', 'net_excess']));
    if (aum && net) {
      const at = (k: string[], i: number) => numArr(first(raw, k))?.[i] ?? NaN;
      points = aum.map((a, i) => ({
        aum_cny: a,
        gross_excess_pct: at(['gross_excess_pct', 'gross_excess'], i),
        net_excess_pct: net[i] ?? NaN,
        ir_ann: at(['ir_ann', 'ir'], i),
        impact_bps: at(['impact_bps'], i),
        explicit_cost_bps: at(['explicit_cost_bps'], i),
        pov_pct: at(['pov_pct'], i),
        fill_rate_pct: at(['fill_rate_pct'], i)
      }));
    }
  }
  points = points.filter((p) => Number.isFinite(p.aum_cny) && p.aum_cny > 0 && Number.isFinite(p.net_excess_pct)).sort((a, b) => a.aum_cny - b.aum_cny);
  if (!points.length) return null;
  const cal = str(raw.calibration_status);
  return {
    experiment_id: str(raw.experiment_id),
    universe_id: str(raw.universe_id),
    snapshot_id: str(raw.snapshot_id),
    calibration_status: cal === 'calibrated' ? 'calibrated' : 'uncalibrated_scenario',
    current_aum_cny: num(first(raw, ['current_aum_cny', 'current_aum'])) ?? NaN,
    half_alpha_aum_cny: num(first(raw, ['half_alpha_aum_cny', 'half_alpha_aum'])),
    breakeven_aum_cny: num(first(raw, ['breakeven_aum_cny', 'breakeven_aum'])),
    binding_bottleneck: str(first(raw, ['binding_bottleneck', 'bottleneck']), '—'),
    points,
    illiquid_names: Array.isArray(raw.illiquid_names) ? raw.illiquid_names.filter(isRec) : [],
    artifact_uri: typeof raw.artifact_uri === 'string' ? raw.artifact_uri : null
  };
}

// ---------------------------------------------------------------- dimension slices
export function normSlices(items: unknown): DimensionSliceItem[] {
  if (!Array.isArray(items)) return [];
  return items.filter(isRec).flatMap((r) => {
    const dim = str(first(r, ['dim', 'dimension', 'dimension_type']));
    const bucket = str(first(r, ['bucket', 'bucket_name', 'label']));
    if (!dim || !bucket) return [];
    return [
      {
        dim: dim as DimensionSliceItem['dim'],
        bucket,
        strategy_excess_pct: num(r.strategy_excess_pct) ?? NaN,
        baseline_return_pct: num(r.baseline_return_pct) ?? NaN,
        alpha_vs_baseline_pct: num(r.alpha_vs_baseline_pct) ?? NaN,
        rank_ic: num(r.rank_ic),
        icir: num(r.icir),
        win_rate_pct: num(r.win_rate_pct) ?? NaN,
        n_days: num(r.n_days) ?? 0,
        contribution_pct: num(first(r, ['contribution_pct', 'share_pct'])) ?? NaN,
        ci95: ci(r.ci95),
        dominant_flag: r.dominant_flag === true
      }
    ];
  });
}

/** Largest bucket contribution share per dimension (for DIM FLAG > 60%). */
export function dominantShare(slices: DimensionSliceItem[]): { dim: string; bucket: string; share: number } | null {
  let best: { dim: string; bucket: string; share: number } | null = null;
  for (const s of slices) {
    if (!Number.isFinite(s.contribution_pct)) continue;
    if (!best || s.contribution_pct > best.share) best = { dim: s.dim, bucket: s.bucket, share: s.contribution_pct };
  }
  return best;
}

export const fmtAum = (cny: number) =>
  !Number.isFinite(cny) ? '—' : cny >= 1e8 ? `${(cny / 1e8).toFixed(cny >= 1e9 ? 0 : 1)}亿` : cny >= 1e4 ? `${(cny / 1e4).toFixed(0)}万` : cny.toFixed(0);
