import type { GateSpec, Verdict } from '../thresholds';

export type TermDomain = 'Q1' | 'Q2' | 'Q3' | 'Q4' | 'Q5' | 'Q6' | 'Q7' | 'MATH';

export const DOMAINS: { key: TermDomain; label: string }[] = [
  { key: 'Q1', label: '数据底座' },
  { key: 'Q2', label: 'Alpha 因子' },
  { key: 'Q3', label: '模型·过拟合' },
  { key: 'Q4', label: '组合·风险' },
  { key: 'Q5', label: '回测·归因' },
  { key: 'Q6', label: '影子·衰减' },
  { key: 'Q7', label: '研究·系统' },
  { key: 'MATH', label: '统计·形式化' }
];

export const TERM_IDS = [
  // Q1 · data (11)
  'point_in_time', 'survivorship_bias', 'look_ahead_bias', 'corp_action_adj', 'ohlcv_geometry', 'historical_constituents',
  'sw_industry_pit', 'price_limit_rules', 'coverage', 'freshness', 'fact_fingerprint',
  // Q2 · alpha (18)
  'rank_ic', 'icir', 'nw_t_stat', 'ic_win_rate', 'ic_decay', 'half_life', 'monotonicity', 'long_short', 'top_excess',
  'turnover_oneway', 'max_corr_library', 'mad_winsorize', 'industry_neutral', 'size_neutral', 'barra_orthogonal',
  'zscore_rank', 'residual_exposure', 'ast_safe',
  // Q3 · models (10)
  'sharpe_is_oos', 'oos_is_ratio', 'cpcv', 'purge', 'embargo', 'pbo', 'dsr', 'trial_count_m', 'walk_forward', 'negative_paths',
  // Q4 · portfolio (13)
  'barra_cne6', 'style_exposure', 'industry_active_dev', 'tracking_error', 'information_ratio', 'turnover_lambda', 'enc',
  'ledoit_wolf', 'psd', 'risk_decomposition', 'opt_objective', 'solve_status', 'capacity_frontier',
  // Q5 · backtest (13)
  't_plus_1', 'pov', 'sqrt_impact', 'stamp_duty', 'gross_vs_net', 'brinson_allocation', 'brinson_selection',
  'brinson_interaction', 'closure_error', 'barra_attribution', 'max_drawdown', 'calmar', 'excess_drawdown',
  // Q6 · shadow (11)
  'implementation_shortfall', 'perold_is', 'open_gap', 'liquidity_impact', 'limit_queue', 'fill_rate', 'crowding_index',
  'hist_percentile', 'kill_switch', 'authority_gate', 'regime_shift',
  // Q7 · research & system (7)
  'study_lineage', 'falsified_tombstone', 'cluster_dispatch', 'ic_memo', 'mcp_side_effect', 'pit_snapshot_lock', 'kv_cache_safe',
  // MATH · statistics & formal verification (12)
  'newey_west_hac', 'bootstrap_ci', 'bh_fdr', 'hansen_spa', 'prefix_invariance', 'ast_causal_proof', 'scale_invariance',
  'conservation_law', 'param_cliff', 'regime_classification', 'dimension_dependence', 'regime_consistency_score'
] as const;

export type BuiltinTermId = (typeof TERM_IDS)[number];
/** Builtin ids are type-checked; backend-only `/api/v1/glossary` ids are plain strings. */
export type TermId = BuiltinTermId | (string & {});

export interface GlossaryTerm {
  id: string;
  zh: string;
  en: string;
  domain: TermDomain;
  prd?: string;
  aliases: string[];
  /** 1–2 sentence plain-language explanation. */
  plain: string;
  /** FormulaBlock lines (`_{}` / `^{}` / `\\sum` … micro-TeX). */
  formula: string[];
  symbols?: string[];
  /** Contract gate (judged against the live canvas value). */
  gate?: GateSpec;
  /** Textual red line when there is no numeric gate (or from the backend glossary). */
  redline?: string;
  pitfalls: string[];
  related: string[];
  /** Where the term is mounted (A/M/P/B/D/S/R/K/T). */
  mounts?: string;
  botPrompt?: string;
  source: 'builtin' | 'api' | 'merged';
}

export type TermContext = Record<string, string | number | boolean | null | undefined>;

/** Live value attached to a trigger on the current page. */
export interface TermValue {
  value?: number | string | null;
  /** Pre-formatted display string (e.g. "0.31", "26.4%"). */
  display?: string;
  /** Overrides the term's contract gate (e.g. backend gate_checks threshold). */
  gate?: GateSpec;
  context?: TermContext;
}

export interface PageValue extends TermValue {
  termId: string;
  verdict: Verdict;
}

/** Structured payload sent to Quant-Lead by "让 Quant-Lead 结合当前数据解读". */
export interface TermQuery {
  termId: string;
  term?: string;
  value?: number | string | null;
  display?: string;
  threshold?: string;
  verdict?: Verdict;
  gap?: string;
  view: string;
  context?: TermContext;
}
