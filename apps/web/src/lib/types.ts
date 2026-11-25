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
  // V3 · optional statistical & formal fields
  rank_ic_ci95?: number[] | null;
  icir_ci95?: number[] | null;
  long_short_ci95?: number[] | null;
  bootstrap_b?: number;
  prefix_invariance_passed?: boolean | null;
  max_prefix_drift?: number | null;
  dimension_slices?: DimensionSliceItem[];
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
  // V3 · multiple testing & parameter cliff (dict shapes normalized in lib/v3.ts)
  fdr_q_value?: number | null;
  spa_p_value?: number | null;
  fdr_items?: Record<string, unknown>[];
  param_sensitivity?: Record<string, unknown>[];
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
  invariants?: Record<string, unknown>[];
  capacity_frontier?: Record<string, unknown> | null;
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
  invariants?: Record<string, unknown>[];
  capacity_frontier?: Record<string, unknown> | null;
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

// ---------------------------------------------------------------- V3 · Q8 glossary / formal / regime / capacity / research
export interface ApiGlossaryTerm {
  term_id: string;
  term: string;
  en_name: string;
  domain: 'Q1' | 'Q2' | 'Q3' | 'Q4' | 'Q5' | 'Q6' | 'MATH';
  category: string;
  aliases: string[];
  short_tip: string;
  formula_latex: string;
  formula_lines: string[];
  institutional_threshold: string;
  common_pitfalls: string[];
  related_terms: string[];
  bot_prompt_template: string;
}

export interface GlossaryListResponse {
  count: number;
  categories: string[];
  terms: ApiGlossaryTerm[];
}

export interface InvariantCheckItem {
  key: string;
  label: string;
  value: string;
  tolerance: string;
  passed: boolean;
  detail: string;
}

export type ProofStatus = 'pass' | 'warn' | 'fail' | 'insufficient' | 'not_applicable';

export interface ProofCheckItem {
  check_id: string;
  suite: 'T1' | 'T2' | 'T3' | 'T4';
  label: string;
  term_id: string;
  status: ProofStatus;
  statistic: string;
  threshold: string;
  error_metric: string;
  reason_code: string;
  n_observed: number;
  detail: Record<string, unknown>;
  witness_keys: string[];
  evidence_uri?: string | null;
}

export interface MathDerivationSpec {
  economic_hypothesis: string;
  mechanism: string;
  formula_latex: string;
  derivation_steps: string[];
  assumptions: Record<string, unknown>[];
  boundary_conditions: string[];
  expected_sign: 'positive' | 'negative' | 'two_sided';
  expected_failure_regimes: string[];
}

export interface FormalProofResult {
  verification_id: string;
  target_id: string;
  universe_id: string;
  snapshot_id: string;
  as_of: string;
  passed: boolean;
  verdict: GateVerdict;
  proofs_passed: number;
  proofs_total: number;
  max_prefix_drift?: number | null;
  bootstrap_rank_ic_ci95?: number[] | null;
  bootstrap_p_value?: number | null;
  bh_fdr_q_value?: number | null;
  spa_p_value?: number | null;
  max_param_cliff_drop_pct?: number | null;
  blocking_failures: string[];
  checks: ProofCheckItem[];
  param_sensitivity: Record<string, unknown>[];
  proof_sha256: string;
  artifact_uri?: string | null;
}

export type DimensionKey = 'market_regime' | 'volatility_regime' | 'market_cap_tier' | 'sw_industry' | 'barra_style';

export interface DimensionSliceItem {
  dim: DimensionKey;
  bucket: string;
  strategy_excess_pct: number;
  baseline_return_pct: number;
  alpha_vs_baseline_pct: number;
  rank_ic?: number | null;
  icir?: number | null;
  win_rate_pct: number;
  n_days: number;
  contribution_pct: number;
  ci95?: number[] | null;
  dominant_flag: boolean;
}

export interface DimensionBaselineRecord {
  baseline_id: string;
  snapshot_id: string;
  universe_id: string;
  dimension_type: DimensionKey;
  date_range_start: string;
  date_range_end: string;
  current_state: string;
  bucket_definitions: Record<string, unknown>;
  bucket_stats: Record<string, unknown>[];
  baseline_sha256: string;
  created_at: string;
}

export interface RegimeAnalysisResult {
  factor_id: string;
  universe_id: string;
  snapshot_id: string;
  regime_consistency_score?: number | null;
  single_dim_dominance_flag: boolean;
  dominant_bucket?: string | null;
  dominant_share_pct?: number | null;
  warning_summary?: string | null;
  slices: DimensionSliceItem[];
  baselines: DimensionBaselineRecord[];
  artifact_uri?: string | null;
}

export interface CapacityPoint {
  aum_cny: number;
  gross_excess_pct: number;
  net_excess_pct: number;
  ir_ann: number;
  impact_bps: number;
  explicit_cost_bps: number;
  pov_pct: number;
  fill_rate_pct: number;
}

export interface CapacityFrontierResult {
  experiment_id: string;
  universe_id: string;
  snapshot_id: string;
  calibration_status: 'uncalibrated_scenario' | 'calibrated';
  current_aum_cny: number;
  half_alpha_aum_cny?: number | null;
  breakeven_aum_cny?: number | null;
  binding_bottleneck: string;
  points: CapacityPoint[];
  illiquid_names: Record<string, unknown>[];
  artifact_uri?: string | null;
}

export interface DiagnosisItem {
  id: string;
  rule_id: string;
  root_cause: string;
  severity: 'critical' | 'major' | 'minor';
  claim: string;
  evidence: Record<string, unknown>[];
  guidance: string[];
  constraints_patch?: Partial<OptConstraints> | Record<string, unknown> | null;
  child_study_patch?: Record<string, unknown> | null;
}

export interface ClusterNodeStatus {
  cluster_id: string;
  display_name: string;
  adapter: string;
  agent_cli: string;
  status: 'online' | 'degraded' | 'offline';
  slots_busy: number;
  slots_total: number;
  queue_depth: number;
  capabilities: string[];
  last_heartbeat: string;
  detail: string;
}

export type StudyStatus = 'DRAFT' | 'QUEUED' | 'DISPATCHED' | 'DEVELOPING' | 'VERIFYING' | 'DIAGNOSING' | 'COMPLETED' | 'FALSIFIED' | 'FAILED';
export type StudyVerdict = 'PENDING' | 'VERIFIED' | 'FALSIFIED' | 'INCONCLUSIVE' | 'ERROR';
export type StudyKind = 'factor' | 'model' | 'portfolio_method' | 'execution_method';
export type DispatchMode = 'engine_sandbox' | 'cluster_dev_worker' | 'external_publish';

export interface ResearchDispatchRequest {
  title: string;
  hypothesis: string;
  kind?: StudyKind;
  parent_study_id?: string | null;
  family_id?: string | null;
  topic_tags?: string[];
  dispatch_mode?: DispatchMode;
  target_cluster?: string;
  universe_id?: string;
  snapshot_id?: string | null;
  factor_id?: string | null;
  expression?: string | null;
  math_derivation?: MathDerivationSpec | null;
  preprocess?: PreprocessConfig;
  constraints?: OptConstraints | null;
  dimensions?: string[];
  trial_count_m?: number;
  bootstrap_b?: number;
  allow_duplicate?: boolean;
  override_rationale?: string | null;
  idempotency_key?: string | null;
  created_by?: string;
}

export interface StudyEventRecord {
  event_id: string;
  study_id: string;
  seq: number;
  stage: string;
  event_type: string;
  progress_pct: number;
  message: string;
  payload: Record<string, unknown>;
  actor: string;
  created_at: string;
}

export interface FalsifiedTombstoneRecord {
  tombstone_id: string;
  study_id?: string | null;
  factor_id: string;
  expression: string;
  expr_hash: string;
  ast_fingerprint: string;
  universe_id: string;
  snapshot_id: string;
  failure_code: string;
  failure_metrics: Record<string, unknown>;
  lesson_summary: string;
  memory_line: string;
  created_by: string;
  created_at: string;
}

export interface ResearchStudySummary {
  study_id: string;
  parent_study_id?: string | null;
  family_id: string;
  title: string;
  kind: StudyKind;
  topic_tags: string[];
  status: StudyStatus;
  verdict: StudyVerdict;
  stage: string;
  dispatch_mode: DispatchMode;
  target_cluster: string;
  universe_id: string;
  snapshot_id: string;
  job_id?: string | null;
  oos_sharpe?: number | null;
  rank_ic_mean?: number | null;
  dsr?: number | null;
  pbo?: number | null;
  net_excess_ann_pct?: number | null;
  proofs_passed: number;
  proofs_total: number;
  created_by: string;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
}

export interface ResearchStudyDetail extends ResearchStudySummary {
  hypothesis: string;
  factor_id?: string | null;
  expression?: string | null;
  math_derivation?: MathDerivationSpec | null;
  formal_proof?: FormalProofResult | null;
  metrics_summary: Record<string, unknown>;
  dimension_analysis?: RegimeAnalysisResult | null;
  capacity_frontier?: CapacityFrontierResult | null;
  diagnosis: DiagnosisItem[];
  improvement_plan: Record<string, unknown>[];
  stages: Record<string, unknown>[];
  events: StudyEventRecord[];
  lineage: ResearchStudySummary[];
  notes: Record<string, unknown>[];
  tombstone_warnings: Record<string, unknown>[];
  fact_fingerprint: string;
  artifact_uri?: string | null;
}

export interface AsyncJobRecord {
  job_id: string;
  job_type: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  created_at: string;
  updated_at: string;
  snapshot_id: string;
  progress_pct: number;
  summary?: Record<string, unknown> | null;
  artifact_uri?: string | null;
  error?: string | null;
}

export interface ResearchDispatchResponse {
  study: ResearchStudySummary;
  job?: AsyncJobRecord | null;
  detail?: ResearchStudyDetail | null;
  tombstone_warnings: Record<string, unknown>[];
  duplicate_blocked: boolean;
}
