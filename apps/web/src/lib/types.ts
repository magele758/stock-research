/**
 * TypeScript mirror of packages/quant-core/src/quant_core/schemas.py (Pydantic v2).
 * Field names are wire-exact; `date`/`datetime` arrive as ISO strings.
 */

export type Market = 'CN_A' | 'US_EQ';
export type GateVerdict = 'pass' | 'reject' | 'inconclusive';
export type TriState = 'pass' | 'warn' | 'fail';
export type BacktestEngineMode = 'screening' | 'event';
export type OptObjective = 'mean_variance' | 'min_var' | 'max_sharpe' | 'risk_parity';
export type UniverseId = 'CSI500' | 'CSI300' | 'CSI1000' | 'CN_ALL' | 'SP500_SAMPLE';

export interface ApiHealth {
  status: string;
  snapshot_id?: string | null;
  markets?: string[];
  mcp_port?: number;
  [k: string]: unknown;
}

// ---------------------------------------------------------------- Q1
export interface QualityCheckItem {
  check_id: string;
  name: string;
  status: TriState;
  detail: string;
  affected_count: number;
  sample_symbols: string[];
}

export interface DatasetSnapshot {
  snapshot_id: string;
  as_of: string;
  markets: Market[];
  created_at: string;
  manifest_sha256: string;
  table_counts: Record<string, number>;
  qa_status: TriState;
  delisted_count: number;
  source_provenance: string[];
}

export interface DatasetTableStatus {
  dataset: string;
  freq: string;
  primary_source: string;
  backup_source: string;
  coverage_pct: number;
  latest_date: string;
  pit_status: TriState;
  row_count: number;
  disk_mb: number;
}

export interface DataHealthReport {
  snapshot_id: string;
  as_of: string;
  overall_status: TriState;
  freshness_sec: number;
  coverage_pct: number;
  stale_datasets: string[];
  failed_checks: string[];
  checks: QualityCheckItem[];
  datasets: DatasetTableStatus[];
  fact_fingerprint: string;
}

export type SyncProvider = 'tushare' | 'baostock' | 'edgar';
/** quant-api `SyncRequest` is `extra="forbid"`: only these two fields are accepted. */
export interface DataSyncRequest {
  provider: SyncProvider;
  mode: 'check' | 'incremental';
}

// ---------------------------------------------------------------- Q2
export interface PreprocessConfig {
  winsorize_mad: boolean;
  mad_k: number;
  neutralize_industry: boolean;
  neutralize_size: boolean;
  orthogonalize_barra: boolean;
  filter_limit_st: boolean;
  min_coverage: number;
  standardize: 'zscore' | 'rank';
}

export interface FactorEvalRequest {
  factor_id: string;
  expression?: string | null;
  universe_id: string;
  snapshot_id?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  horizons?: number[];
  preprocess: PreprocessConfig;
}

export type GateCheckDict = Record<string, unknown>;

export interface FactorEvalResult {
  factor_id: string;
  expression: string;
  universe_id: string;
  snapshot_id: string;
  ast_safe: boolean;
  ast_warnings: string[];
  rank_ic_mean: number;
  ic_std: number;
  icir_ann: number;
  nw_t_stat: number;
  ic_win_rate: number;
  long_short_ann: number;
  top_excess_ann: number;
  daily_turnover_oneway: number;
  half_life_days: number;
  monotonicity_spearman: number;
  max_corr_in_library: number;
  max_corr_peer: string;
  coverage_pct: number;
  admission_passed: boolean;
  gate_checks: GateCheckDict[];
  residual_exposures: Record<string, number>;
  dates: string[];
  decile_navs: Record<string, number[]>;
  decile_ann_returns: number[];
  rolling_60d_ic: number[];
  cum_ic: number[];
  ic_decay: Record<string, number>;
  industry_ic: Record<string, number>;
  corr_matrix_labels: string[];
  corr_matrix_values: number[][];
  artifact_uri?: string | null;
}

/** `/factors/catalog` is not pinned by schemas.py; normalized in quant-api.ts. */
export interface FactorCatalogItem {
  factor_id: string;
  name: string;
  expression?: string;
  lang?: string;
  category?: string;
  source?: string;
  description?: string;
  builtin?: boolean;
}

// ---------------------------------------------------------------- Q3
export interface ExperimentRecord {
  experiment_id: string;
  family_id: string;
  name: string;
  model_type: string;
  feature_summary: string;
  universe_id: string;
  snapshot_id: string;
  trial_count_m: number;
  is_sharpe: number;
  oos_sharpe: number;
  oos_is_ratio: number;
  cpcv_pbo: number;
  dsr: number;
  oos_excess_ann: number;
  verdict: GateVerdict;
  status_badge: string;
  created_at: string;
  notes: string;
}

export interface CpcvRequest {
  experiment_id: string;
  n_splits?: number;
  k_test?: number;
  purge_days?: number;
  embargo_days?: number;
  trial_count_m?: number;
  snapshot_id?: string | null;
}

export interface CpcvResult {
  experiment_id: string;
  n_splits: number;
  k_test: number;
  n_combinations: number;
  n_oos_paths: number;
  purge_days: number;
  embargo_days: number;
  purge_conflicts: number;
  is_sharpe: number;
  oos_median_sharpe: number;
  oos_is_ratio: number;
  pbo: number;
  dsr: number;
  negative_path_count: number;
  path_sharpes: number[];
  baseline_path_sharpes: number[];
  walk_forward_folds: Record<string, unknown>[];
  verdict: GateVerdict;
  gate_details: GateCheckDict[];
}

// ---------------------------------------------------------------- Q4
export interface BarraStyleExposure {
  factor: string;
  pre_opt: number;
  post_opt: number;
  limit: number;
  breached: boolean;
}

export interface IndustryDeviation {
  industry: string;
  active_weight_pct: number;
  limit_pct: number;
}

export interface BarraExposureRequest {
  experiment_id: string;
  universe_id: string;
  snapshot_id?: string | null;
}

export interface BarraExposureResult {
  experiment_id: string;
  universe_id: string;
  snapshot_id: string;
  as_of: string;
  styles: BarraStyleExposure[];
  industries: IndustryDeviation[];
  predicted_te_pct: number;
  style_risk_share: number;
  industry_risk_share: number;
  specific_risk_share: number;
  psd_min_eigenvalue: number;
}

export interface OptConstraints {
  objective: OptObjective;
  max_stock_weight: number;
  max_industry_dev: number;
  turnover_lambda: number;
  max_turnover_oneway: number;
  target_te: number;
  max_size_z: number;
  max_style_z: number;
  min_benchmark_weight_sum: number;
}

export interface PortfolioOptRequest {
  experiment_id: string;
  universe_id: string;
  snapshot_id?: string | null;
  constraints: OptConstraints;
}

export interface TargetWeightItem {
  symbol: string;
  name: string;
  industry: string;
  target_weight_pct: number;
  bench_weight_pct: number;
  active_weight_pct: number;
  est_impact_bps: number;
  tradable_status: 'OK' | 'T+1_LOCK' | 'LIMIT_CAP' | 'SUSPENDED';
}

export interface PortfolioOptResult {
  opt_run_id: string;
  experiment_id: string;
  universe_id: string;
  snapshot_id: string;
  solver: string;
  solve_status: 'optimal' | 'optimal_inaccurate' | 'relaxed_optimal' | 'infeasible';
  solve_ms: number;
  relaxation_notes: string[];
  expected_gross_excess_pct: number;
  expected_cost_pct: number;
  expected_net_excess_pct: number;
  predicted_te_pct: number;
  expected_ir: number;
  oneway_turnover_pct: number;
  annualized_turnover: number;
  effective_n_stocks: number;
  max_constraint_violation: number;
  style_exposures: BarraStyleExposure[];
  industry_deviations: IndustryDeviation[];
  top_holdings: TargetWeightItem[];
  cvxpy_snippet: string;
}

// ---------------------------------------------------------------- Q5
export interface MicrostructureConfig {
  enforce_t_plus_1: boolean;
  enforce_price_limits: boolean;
  enforce_suspension: boolean;
  max_pov: number;
  commission_bps: number;
  stamp_duty_sell_bps: number;
  transfer_fee_bps: number;
  impact_coeff_y: number;
  rebalance_freq: 'daily' | 'weekly' | 'monthly';
}

export interface BacktestRequest {
  experiment_id: string;
  universe_id: string;
  engine: BacktestEngineMode;
  snapshot_id?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  initial_cash?: number;
  microstructure?: Partial<MicrostructureConfig>;
}

export interface RejectedOrderRecord {
  trade_date: string;
  symbol: string;
  name: string;
  side: 'BUY' | 'SELL';
  reason: string;
  loss_bps: number;
}

export interface FrictionBreakdown {
  gross_excess_pct: number;
  commission_pct: number;
  stamp_duty_pct: number;
  transfer_fee_pct: number;
  impact_slippage_pct: number;
  limit_suspend_pct: number;
  net_excess_pct: number;
  total_friction_bps: number;
}

export interface AttributionResult {
  run_id: string;
  brinson_allocation_pct: number;
  brinson_selection_pct: number;
  brinson_interaction_pct: number;
  brinson_closure_error_bp: number;
  barra_style_pct: number;
  barra_industry_pct: number;
  barra_specific_alpha_pct: number;
  barra_cost_pct: number;
  barra_closure_error_bp: number;
  barra_style_detail: Record<string, number>;
}

export interface BacktestResult {
  run_id: string;
  experiment_id: string;
  universe_id: string;
  engine: BacktestEngineMode;
  snapshot_id: string;
  ann_return_pct: number;
  bench_ann_return_pct: number;
  net_excess_ann_pct: number;
  info_ratio: number;
  max_drawdown_pct: number;
  excess_max_drawdown_pct: number;
  calmar: number;
  daily_win_rate_pct: number;
  profit_loss_ratio: number;
  ann_turnover_pct: number;
  total_fills: number;
  total_rejections: number;
  dates: string[];
  strategy_nav: number[];
  bench_nav: number[];
  excess_nav: number[];
  strategy_dd: number[];
  excess_dd: number[];
  friction: FrictionBreakdown;
  recent_rejections: RejectedOrderRecord[];
  attribution: AttributionResult;
  artifact_uri?: string | null;
}

// ---------------------------------------------------------------- Q6
export interface FactorDecayItem {
  factor_id: string;
  name: string;
  rank_ic_60d: number;
  hist_percentile: number;
  crowding_index: number;
  status: 'ACTIVE' | 'DEGRADING' | 'KILL-SWITCH';
  note: string;
}

export interface RebalanceSignalItem {
  symbol: string;
  name: string;
  side: 'BUY' | 'SELL' | 'HOLD';
  delta_weight_pct: number;
  est_shares: number;
  risk_check: 'PASS' | 'BLOCKED';
}

export interface AuthorityGateManifest {
  manifest_id: string;
  generated_at: string;
  snapshot_id: string;
  execution_mode: 'READ_ONLY_SIGNAL_EXPORT_ONLY';
  broker_connected: false;
  risk_passed: boolean;
  sha256: string;
  signals: RebalanceSignalItem[];
}

export interface ShadowDriftResult {
  shadow_portfolio_id: string;
  universe_id: string;
  snapshot_id: string;
  risk_state: 'ok' | 'alert' | 'kill' | 'unknown';
  median_is_delta_bps: number;
  total_is_bps: number;
  fill_rate_pct: number;
  dates: string[];
  theoretical_excess_bps: number[];
  shadow_excess_bps: number[];
  gap_bps: number[];
  liquidity_bps: number[];
  limit_queue_bps: number[];
  factor_decay: FactorDecayItem[];
  authority_manifest: AuthorityGateManifest;
  fact_fingerprint: string;
}

// ---------------------------------------------------------------- Q7
/** `/mcp/catalog` is not pinned by schemas.py; normalized in quant-api.ts. */
export interface McpToolStatus {
  name: string;
  layer?: string;
  side_effect?: string;
  approval_mode?: string;
  status?: string;
  calls_24h?: number;
  p50_ms?: number;
  p95_ms?: number;
  description?: string;
}

export interface McpCatalog {
  server?: string;
  version?: string;
  transport?: string;
  tools: McpToolStatus[];
  raw: unknown;
}
