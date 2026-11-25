/**
 * Single source of institutional thresholds for the web terminal.
 * Mirrors `quant_core.schemas.INSTITUTIONAL_GATES` (V3-INSTITUTIONAL-CONTRACT §1); backend
 * `gate_checks[].threshold` still overrides these per request where it is reported.
 */
import { fx } from './format';

export const GATES = {
  rank_ic_min: 0.03,
  icir_ann_min: 1.5,
  nw_t_stat_min: 3.0,
  daily_turnover_max: 0.15,
  max_corr_library: 0.7,
  monotonicity_min: 0.9,
  min_coverage: 0.8,
  cpcv_pbo_max: 0.25,
  dsr_min: 0.95,
  oos_is_ratio_min: 0.5,
  bh_fdr_q_max: 0.05,
  spa_p_max: 0.05,
  param_cliff_drop_max: 0.3,
  prefix_invariance_tol: 1e-10,
  closure_error_bp_max: 1.0,
  barra_style_hard_limit: 0.5,
  single_dim_dominance_max: 0.6
} as const;

/** Front-end-only limits that are not part of the backend gate dict. */
export const UI_LIMITS = {
  residual_exposure_max: 0.3,
  specific_alpha_share_min: 65,
  psd_min_eigen: -1e-10,
  weight_sum_tol: 1e-6,
  constraint_violation_max: 1e-6,
  ic_win_rate_min: 0.55,
  negative_path_ratio_max: 0.2,
  cpcv_paths_min: 15,
  enc_min: 30,
  information_ratio_min: 0.5,
  calmar_min: 1.0,
  fill_rate_min: 95,
  crowding_max: 0.8,
  hist_percentile_min: 20,
  shadow_is_bp_max: 50,
  freshness_sec_max: 86_400 * 1.5
} as const;

export type Verdict = 'PASS' | 'WARN' | 'FAIL' | 'N/A';
export type GateOp = '>=' | '<=' | '<' | '>' | 'abs<=' | 'eq';
export type GateUnit = '' | '%' | 'pct' | 'bp' | 'σ' | 'x' | 'D' | 'sci';

/**
 * `value` is the hard gate. `warn` is an explicit observation band `[lo, hi]` (inclusive);
 * depending on the metric it lies inside the gate (approaching the limit, e.g. PBO 0.20–0.25)
 * or just outside it (e.g. RankIC 0.02–0.03). A value inside `warn` is WARN, otherwise PASS/FAIL.
 */
export interface GateSpec {
  op: GateOp;
  value: number;
  warn?: [number, number];
  /** `pct` = fraction rendered as percent (0.15 → 15%); `%` = already percent. */
  unit?: GateUnit;
  /** Axis bounds for the position bar in the term card. */
  range?: [number, number];
  label: string;
  source: 'contract' | 'code' | 'proposal' | 'backend';
}

export function fmtGateValue(v: number, unit: GateUnit = ''): string {
  if (!Number.isFinite(v)) return '—';
  switch (unit) {
    case 'pct':
      return `${fx(v * 100, Math.abs(v) < 0.1 ? 1 : 0)}%`;
    case '%':
      return `${fx(v, Math.abs(v) < 10 ? 1 : 0)}%`;
    case 'bp':
      return `${fx(v, 1)} bp`;
    case 'σ':
      return `${fx(v, 2)}σ`;
    case 'x':
      return `${fx(v, 2)}x`;
    case 'D':
      return `${fx(v, 0)}D`;
    case 'sci':
      return v === 0 ? '0' : v.toExponential(1);
    default:
      return Math.abs(v) >= 100 ? fx(v, 0) : Math.abs(v) < 0.1 && v !== 0 ? fx(v, 3) : fx(v, 2);
  }
}

const OP_TXT: Record<GateOp, string> = { '>=': '≥', '<=': '≤', '<': '<', '>': '>', 'abs<=': '|x| ≤', eq: '=' };

export function gateText(g: GateSpec): string {
  return `${OP_TXT[g.op]} ${fmtGateValue(g.value, g.unit)}`;
}

function passes(op: GateOp, v: number, t: number): boolean {
  switch (op) {
    case '>=':
      return v >= t;
    case '<=':
      return v <= t;
    case '<':
      return v < t;
    case '>':
      return v > t;
    case 'abs<=':
      return Math.abs(v) <= t;
    case 'eq':
      return Math.abs(v - t) <= 1e-12;
  }
}

export interface Judgement {
  verdict: Verdict;
  /** Signed distance to the hard gate in gate units (positive = beyond the gate in the failing direction). */
  gapAbs: number | null;
  gapPct: number | null;
  text: string;
}

export function judge(g: GateSpec | undefined, raw: number | string | null | undefined): Judgement {
  const v = typeof raw === 'number' ? raw : typeof raw === 'string' && raw.trim() !== '' ? Number(raw) : NaN;
  if (!g || !Number.isFinite(v)) return { verdict: 'N/A', gapAbs: null, gapPct: null, text: g ? '无当前值' : '无机构红线' };
  const x = g.op === 'abs<=' ? Math.abs(v) : v;
  const ok = passes(g.op, v, g.value);
  const inWarn = g.warn ? x >= Math.min(...g.warn) && x <= Math.max(...g.warn) : false;
  const verdict: Verdict = inWarn ? 'WARN' : ok ? 'PASS' : 'FAIL';
  const lowerIsBetter = g.op === '<=' || g.op === '<' || g.op === 'abs<=';
  const gapAbs = g.op === 'eq' ? x - g.value : lowerIsBetter ? x - g.value : g.value - x;
  const gapPct = g.value !== 0 ? (gapAbs / Math.abs(g.value)) * 100 : null;
  const gs = `${gapAbs >= 0 ? '+' : ''}${fmtGateValue(gapAbs, g.unit === 'pct' ? 'pct' : g.unit)}`;
  const text =
    verdict === 'PASS'
      ? `满足红线 ${gateText(g)}，余量 ${fmtGateValue(Math.abs(gapAbs), g.unit)}`
      : verdict === 'WARN'
        ? `处于观察区间 [${g.warn!.map((w) => fmtGateValue(w, g.unit)).join(', ')}]`
        : `超出红线 ${gs}${gapPct !== null ? `（${gapPct >= 0 ? '+' : ''}${fx(gapPct, 0)}%）` : ''}`;
  return { verdict, gapAbs, gapPct, text };
}

/** Tri-tone for the `Tag` primitive. */
export const verdictTone = (v: Verdict): 'pass' | 'warn' | 'fail' | 'mute' => (v === 'PASS' ? 'pass' : v === 'WARN' ? 'warn' : v === 'FAIL' ? 'fail' : 'mute');

/** Contract gate specs keyed by glossary term id (also used directly by views). */
export const GATE_SPECS = {
  rank_ic: { op: '>=', value: GATES.rank_ic_min, warn: [0.02, 0.03], range: [-0.02, 0.08], label: 'RankIC ≥ 0.03', source: 'contract' },
  icir: { op: '>=', value: GATES.icir_ann_min, warn: [1.0, 1.5], range: [0, 3], label: 'ICIR 年化 ≥ 1.50', source: 'contract' },
  nw_t_stat: { op: '>=', value: GATES.nw_t_stat_min, warn: [2.0, 3.0], range: [0, 6], label: 'NW t ≥ 3.00', source: 'contract' },
  turnover_oneway: { op: '<=', value: GATES.daily_turnover_max, warn: [0.15, 0.2], unit: 'pct', range: [0, 0.4], label: '单边日换手 ≤ 15%', source: 'contract' },
  max_corr_library: { op: '<', value: GATES.max_corr_library, warn: [0.5, 0.7], range: [0, 1], label: '库内 MaxCorr < 0.70', source: 'contract' },
  monotonicity: { op: '>=', value: GATES.monotonicity_min, warn: [0.7, 0.9], range: [-1, 1], label: '分层单调 ρ ≥ 0.90', source: 'contract' },
  coverage: { op: '>=', value: GATES.min_coverage, warn: [0.7, 0.8], unit: 'pct', range: [0.5, 1], label: '覆盖率 ≥ 80%', source: 'contract' },
  pbo: { op: '<=', value: GATES.cpcv_pbo_max, warn: [0.2, 0.25], range: [0, 1], label: 'PBO ≤ 0.25', source: 'contract' },
  dsr: { op: '>=', value: GATES.dsr_min, warn: [0.9, 0.95], range: [0, 1], label: 'DSR ≥ 0.95', source: 'contract' },
  oos_is_ratio: { op: '>=', value: GATES.oos_is_ratio_min, warn: [0.4, 0.5], range: [-0.5, 1.5], label: 'OOS/IS ≥ 0.50', source: 'contract' },
  bh_fdr: { op: '<=', value: GATES.bh_fdr_q_max, warn: [0.05, 0.1], range: [0, 0.3], label: 'BH q ≤ 0.05', source: 'contract' },
  hansen_spa: { op: '<=', value: GATES.spa_p_max, warn: [0.05, 0.1], range: [0, 0.3], label: 'SPA p ≤ 0.05', source: 'contract' },
  param_cliff: { op: '<=', value: GATES.param_cliff_drop_max, warn: [0.3, 0.5], unit: 'pct', range: [0, 1], label: '邻点衰减 ≤ 30%', source: 'contract' },
  prefix_invariance: { op: '<=', value: GATES.prefix_invariance_tol, unit: 'sci', range: [0, 1e-8], label: 'max|Δf| ≤ 1e-10', source: 'contract' },
  closure_error: { op: 'abs<=', value: GATES.closure_error_bp_max, unit: 'bp', range: [0, 3], label: '|ε| ≤ 1 bp', source: 'contract' },
  style_exposure: { op: 'abs<=', value: GATES.barra_style_hard_limit, warn: [0.3, 0.5], unit: 'σ', range: [0, 1], label: '|z| ≤ 0.50σ', source: 'contract' },
  dimension_dependence: { op: '<=', value: GATES.single_dim_dominance_max, warn: [0.5, 0.6], unit: 'pct', range: [0, 1], label: '单桶贡献 ≤ 60%', source: 'contract' },
  residual_exposure: { op: 'abs<=', value: UI_LIMITS.residual_exposure_max, unit: 'σ', range: [0, 1], label: '|z| ≤ 0.30σ', source: 'code' },
  psd: { op: '>=', value: UI_LIMITS.psd_min_eigen, unit: 'sci', range: [-1e-8, 1e-4], label: 'λmin ≥ −1e-10', source: 'code' },
  barra_attribution: { op: '>=', value: UI_LIMITS.specific_alpha_share_min, unit: '%', range: [0, 100], label: '特质 Alpha ≥ 65%', source: 'code' },
  ic_win_rate: { op: '>=', value: UI_LIMITS.ic_win_rate_min, unit: 'pct', range: [0.3, 0.8], label: 'IC 胜率 ≥ 55%', source: 'proposal' },
  negative_paths: { op: '<=', value: UI_LIMITS.negative_path_ratio_max, unit: 'pct', range: [0, 1], label: '负路径 ≤ 20%', source: 'proposal' },
  cpcv: { op: '>=', value: UI_LIMITS.cpcv_paths_min, range: [0, 60], label: 'OOS 路径 ≥ 15', source: 'proposal' },
  purge: { op: 'eq', value: 0, range: [0, 10], label: 'Purge 冲突 = 0', source: 'code' },
  enc: { op: '>=', value: UI_LIMITS.enc_min, range: [0, 120], label: 'ENC ≥ 30', source: 'proposal' },
  information_ratio: { op: '>=', value: UI_LIMITS.information_ratio_min, range: [-1, 3], label: 'IR ≥ 0.50', source: 'proposal' },
  calmar: { op: '>=', value: UI_LIMITS.calmar_min, range: [0, 4], label: 'Calmar ≥ 1.0', source: 'proposal' },
  fill_rate: { op: '>=', value: UI_LIMITS.fill_rate_min, unit: '%', range: [80, 100], label: '成交率 ≥ 95%', source: 'proposal' },
  crowding_index: { op: '<=', value: UI_LIMITS.crowding_max, range: [0, 2], label: '拥挤度 ≤ 0.8', source: 'proposal' },
  hist_percentile: { op: '>=', value: UI_LIMITS.hist_percentile_min, unit: '%', range: [0, 100], label: '历史分位 ≥ 20%', source: 'proposal' },
  implementation_shortfall: { op: 'abs<=', value: UI_LIMITS.shadow_is_bp_max, unit: 'bp', range: [0, 150], label: '|IS| ≤ 50 bp', source: 'proposal' }
} satisfies Record<string, GateSpec>;

export type GateSpecKey = keyof typeof GATE_SPECS;

export const gateSpec = (key: string): GateSpec | undefined => (GATE_SPECS as Record<string, GateSpec>)[key];

/** Copy of a contract spec with the hard gate replaced (e.g. by a backend `gate_checks[].threshold`). */
export function withGate(key: GateSpecKey, value: number | undefined, source: GateSpec['source'] = 'backend'): GateSpec {
  const base = GATE_SPECS[key] as GateSpec;
  if (value === undefined || !Number.isFinite(value) || value === base.value) return base;
  return { ...base, value, label: `${base.label.split(/[≥≤<>=]/)[0].trim()} ${gateText({ ...base, value })}`, source };
}
