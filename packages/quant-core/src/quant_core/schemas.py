"""Canonical Pydantic v2 domain, API, and MCP contracts for Pro-Quant V2.0.

All workers (quant-lake, quant-engines, quant-api-mcp) MUST import and reuse
these shared schemas so every service, test, and frontend endpoint aligns 100%.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Market(str, Enum):
    CN_A = "CN_A"
    US_EQ = "US_EQ"


class BoardType(str, Enum):
    MAIN = "MAIN"
    CHINEXT = "CHINEXT"
    STAR = "STAR"
    BSE = "BSE"
    NASDAQ = "NASDAQ"
    NYSE = "NYSE"


class AvailabilityMode(str, Enum):
    STRICT_PUBLIC = "strict_public"
    RECORDED_REPLAY = "recorded_replay"


class BacktestEngineMode(str, Enum):
    SCREENING = "screening"
    EVENT = "event"


class GateVerdict(str, Enum):
    PASS = "pass"
    REJECT = "reject"
    INCONCLUSIVE = "inconclusive"


class OptObjective(str, Enum):
    MEAN_VARIANCE = "mean_variance"
    MIN_VAR = "min_var"
    MAX_SHARPE = "max_sharpe"
    RISK_PARITY = "risk_parity"


class QualityState(str, Enum):
    OBSERVED = "observed"
    RECONSTRUCTED = "reconstructed"
    STALE = "stale"
    UNKNOWN = "unknown"


class McpErrorCode(str, Enum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    DATA_STALE = "DATA_STALE"
    FORBIDDEN = "FORBIDDEN"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INFEASIBLE = "INFEASIBLE"
    NOT_FOUND = "NOT_FOUND"
    TIMEOUT = "TIMEOUT"


# ---------------------------------------------------------------------------
# Q1 · PIT Data Lake & QA Sentinel Entities
# ---------------------------------------------------------------------------


class InstrumentLifecycle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listing_id: str
    issuer_id: str
    symbol: str
    name: str
    market: Market
    board: BoardType
    exchange: str
    list_date: date
    delist_date: date | None = None
    is_st: bool = False
    st_intervals: list[tuple[date, date | None]] = Field(default_factory=list)
    currency: str = "CNY"


class MarketBarRaw(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listing_id: str
    symbol: str
    market: Market
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    pre_close: Decimal
    vwap: Decimal
    volume_shares: int
    amount_ccy: Decimal
    adj_factor: Decimal
    up_limit: Decimal | None = None
    down_limit: Decimal | None = None
    is_suspended: bool = False
    is_st: bool = False
    float_market_cap: Decimal
    free_float_market_cap: Decimal | None = None
    source_id: str
    available_at: datetime
    quality_state: QualityState = QualityState.OBSERVED


class CorpAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    listing_id: str
    symbol: str
    ex_date: date
    record_date: date
    pay_date: date
    cash_dividend_per_share: Decimal = Decimal(0)
    split_ratio: Decimal = Decimal(1)  # e.g., 2.0 for 2-for-1 split
    bonus_share_ratio: Decimal = Decimal(0)  # e.g., 0.5 for 10送5
    announced_at: datetime
    source_id: str


class PitFinancialFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str
    issuer_id: str
    symbol: str
    market: Market
    metric_code: str  # revenue, net_profit_deducted, roe_ttm, ocf, equity, total_debt, eps
    report_period_end: date
    ann_date: date
    published_at: datetime
    first_seen_at: datetime
    revision_seq: int = 1
    is_restated: bool = False
    is_withdrawn: bool = False
    value: Decimal
    currency: str = "CNY"
    source_id: str
    quality_state: QualityState = QualityState.OBSERVED


class UniverseMember(BaseModel):
    model_config = ConfigDict(extra="forbid")

    universe_id: str  # CSI300, CSI500, CSI1000, CN_ALL, SP500_SAMPLE
    as_of_date: date
    listing_id: str
    symbol: str
    weight: float | None = None
    weight_quality: Literal["official", "estimated", "partial", "missing"] = "official"
    effective_from: date
    effective_to: date | None = None


class IndustryMembership(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listing_id: str
    symbol: str
    taxonomy: str = "SW2021"
    l1_code: str
    l1_name: str
    valid_from: date
    valid_to: date | None = None


class QualityCheckItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_id: str
    name: str
    status: Literal["pass", "warn", "fail"]
    detail: str
    affected_count: int = 0
    sample_symbols: list[str] = Field(default_factory=list)


class DatasetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    as_of: datetime
    markets: list[Market]
    created_at: datetime
    manifest_sha256: str
    table_counts: dict[str, int]
    qa_status: Literal["pass", "warn", "fail"]
    delisted_count: int = 0
    source_provenance: list[str] = Field(default_factory=list)


class DatasetTableStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: str
    freq: str
    primary_source: str
    backup_source: str
    coverage_pct: float
    latest_date: str
    pit_status: Literal["pass", "warn", "fail"]
    row_count: int
    disk_mb: float


class DataHealthReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    as_of: datetime
    overall_status: Literal["pass", "warn", "fail"]
    freshness_sec: int
    coverage_pct: float
    stale_datasets: list[str] = Field(default_factory=list)
    failed_checks: list[str] = Field(default_factory=list)
    checks: list[QualityCheckItem]
    datasets: list[DatasetTableStatus]
    fact_fingerprint: str


# ---------------------------------------------------------------------------
# Q2 · Alpha Factor Lab & Cross-Sectional Diagnostics
# ---------------------------------------------------------------------------


class PreprocessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    winsorize_mad: bool = True
    mad_k: float = 3.5
    neutralize_industry: bool = True
    neutralize_size: bool = True
    orthogonalize_barra: bool = True
    filter_limit_st: bool = True
    min_coverage: float = 0.80
    standardize: Literal["zscore", "rank"] = "zscore"


class FactorEvalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factor_id: str = "alpha_vp_corr_20d"
    expression: str | None = None
    universe_id: str = "CSI500"
    snapshot_id: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    horizons: list[int] = Field(default_factory=lambda: [1, 5, 10, 20])
    preprocess: PreprocessConfig = Field(default_factory=PreprocessConfig)


class FactorEvalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factor_id: str
    expression: str
    universe_id: str
    snapshot_id: str
    ast_safe: bool
    ast_warnings: list[str] = Field(default_factory=list)
    rank_ic_mean: float
    ic_std: float
    icir_ann: float
    nw_t_stat: float
    ic_win_rate: float
    long_short_ann: float
    top_excess_ann: float
    daily_turnover_oneway: float
    half_life_days: float
    monotonicity_spearman: float
    max_corr_in_library: float
    max_corr_peer: str
    coverage_pct: float
    admission_passed: bool
    gate_checks: list[dict[str, Any]]
    residual_exposures: dict[str, float]
    dates: list[str]
    decile_navs: dict[str, list[float]]  # Q1..Q10 + LS + BENCH
    decile_ann_returns: list[float]  # len 10
    rolling_60d_ic: list[float]
    cum_ic: list[float]
    ic_decay: dict[str, float]  # "1D", "3D", "5D", "10D", "20D", "60D"
    industry_ic: dict[str, float]
    corr_matrix_labels: list[str]
    corr_matrix_values: list[list[float]]
    artifact_uri: str | None = None


class FactorCorrRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factor_ids: list[str]
    universe_id: str = "CSI500"
    snapshot_id: str | None = None
    method: Literal["spearman", "pearson"] = "spearman"


class FactorCorrResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    universe_id: str
    snapshot_id: str
    method: str
    labels: list[str]
    matrix: list[list[float]]
    high_corr_pairs: list[dict[str, Any]]
    max_abs_corr: float


class FactorHypothesisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factor_id: str
    hypothesis: str
    expression: str
    universe_id: str = "CSI500"
    snapshot_id: str | None = None
    idempotency_key: str | None = None


class FactorHypothesisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    factor_id: str
    expr_hash: str
    ast_safe: bool
    duplicate: bool = False
    status: Literal["pending_approval", "queued", "evaluated", "rejected"]
    trial_id: str
    rejection_reason: str | None = None


# ---------------------------------------------------------------------------
# Q3 · Multi-Factor Models & CPCV/DSR Overfitting Gate
# ---------------------------------------------------------------------------


class ExperimentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    family_id: str
    name: str
    model_type: str
    feature_summary: str
    universe_id: str
    snapshot_id: str
    trial_count_m: int
    is_sharpe: float
    oos_sharpe: float
    oos_is_ratio: float
    cpcv_pbo: float
    dsr: float
    oos_excess_ann: float
    verdict: GateVerdict
    status_badge: str
    created_at: datetime
    notes: str = ""


class CpcvRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    n_splits: int = 6
    k_test: int = 2
    purge_days: int = 5
    embargo_days: int = 10
    trial_count_m: int = 20
    snapshot_id: str | None = None


class CpcvResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    n_splits: int
    k_test: int
    n_combinations: int
    n_oos_paths: int
    purge_days: int
    embargo_days: int
    purge_conflicts: int
    is_sharpe: float
    oos_median_sharpe: float
    oos_is_ratio: float
    pbo: float
    dsr: float
    negative_path_count: int
    path_sharpes: list[float]
    baseline_path_sharpes: list[float]
    walk_forward_folds: list[dict[str, Any]]
    verdict: GateVerdict
    gate_details: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Q4 · Barra CNE6 Risk Model & cvxpy Portfolio Optimization
# ---------------------------------------------------------------------------


class BarraExposureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str = "EXP-0427"
    universe_id: str = "CSI500"
    snapshot_id: str | None = None


class BarraStyleExposure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factor: str
    pre_opt: float
    post_opt: float
    limit: float
    breached: bool


class IndustryDeviation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    industry: str
    active_weight_pct: float
    limit_pct: float


class BarraExposureResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    universe_id: str
    snapshot_id: str
    as_of: str
    styles: list[BarraStyleExposure]
    industries: list[IndustryDeviation]
    predicted_te_pct: float
    style_risk_share: float
    industry_risk_share: float
    specific_risk_share: float
    psd_min_eigenvalue: float


class OptConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: OptObjective = OptObjective.MAX_SHARPE
    max_stock_weight: float = 0.05
    max_industry_dev: float = 0.03
    turnover_lambda: float = 0.60
    max_turnover_oneway: float = 0.35
    target_te: float = 0.05
    max_size_z: float = 0.50
    max_style_z: float = 0.50
    min_benchmark_weight_sum: float = 0.80


class PortfolioOptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str = "EXP-0427"
    universe_id: str = "CSI500"
    snapshot_id: str | None = None
    constraints: OptConstraints = Field(default_factory=OptConstraints)


class TargetWeightItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    name: str
    industry: str
    target_weight_pct: float
    bench_weight_pct: float
    active_weight_pct: float
    est_impact_bps: float
    tradable_status: Literal["OK", "T+1_LOCK", "LIMIT_CAP", "SUSPENDED"] = "OK"


class PortfolioOptResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opt_run_id: str
    experiment_id: str
    universe_id: str
    snapshot_id: str
    solver: str
    solve_status: Literal["optimal", "optimal_inaccurate", "relaxed_optimal", "infeasible"]
    solve_ms: float
    relaxation_notes: list[str] = Field(default_factory=list)
    expected_gross_excess_pct: float
    expected_cost_pct: float
    expected_net_excess_pct: float
    predicted_te_pct: float
    expected_ir: float
    oneway_turnover_pct: float
    annualized_turnover: float
    effective_n_stocks: int
    max_constraint_violation: float
    style_exposures: list[BarraStyleExposure]
    industry_deviations: list[IndustryDeviation]
    top_holdings: list[TargetWeightItem]
    cvxpy_snippet: str


# ---------------------------------------------------------------------------
# Q5 · Dual-Engine A-Share Microstructure Backtest & Brinson/Barra Attribution
# ---------------------------------------------------------------------------


class MicrostructureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enforce_t_plus_1: bool = True
    enforce_price_limits: bool = True
    enforce_suspension: bool = True
    max_pov: float = 0.05
    commission_bps: float = 2.5
    stamp_duty_sell_bps: float = 5.0  # 0.05% since 2023-08-28
    transfer_fee_bps: float = 0.1  # 0.001%
    impact_coeff_y: float = 0.45
    rebalance_freq: Literal["daily", "weekly", "monthly"] = "weekly"


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str = "EXP-0427"
    universe_id: str = "CSI500"
    engine: BacktestEngineMode = BacktestEngineMode.EVENT
    snapshot_id: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    initial_cash: float = 10_000_000.0
    microstructure: MicrostructureConfig = Field(default_factory=MicrostructureConfig)


class RejectedOrderRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trade_date: str
    symbol: str
    name: str
    side: Literal["BUY", "SELL"]
    reason: str
    loss_bps: float


class FrictionBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gross_excess_pct: float
    commission_pct: float
    stamp_duty_pct: float
    transfer_fee_pct: float
    impact_slippage_pct: float
    limit_suspend_pct: float
    net_excess_pct: float
    total_friction_bps: float


class AttributionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    brinson_allocation_pct: float
    brinson_selection_pct: float
    brinson_interaction_pct: float
    brinson_closure_error_bp: float
    barra_style_pct: float
    barra_industry_pct: float
    barra_specific_alpha_pct: float
    barra_cost_pct: float
    barra_closure_error_bp: float
    barra_style_detail: dict[str, float]


class BacktestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    experiment_id: str
    universe_id: str
    engine: BacktestEngineMode
    snapshot_id: str
    ann_return_pct: float
    bench_ann_return_pct: float
    net_excess_ann_pct: float
    info_ratio: float
    max_drawdown_pct: float
    excess_max_drawdown_pct: float
    calmar: float
    daily_win_rate_pct: float
    profit_loss_ratio: float
    ann_turnover_pct: float
    total_fills: int
    total_rejections: int
    dates: list[str]
    strategy_nav: list[float]
    bench_nav: list[float]
    excess_nav: list[float]
    strategy_dd: list[float]
    excess_dd: list[float]
    friction: FrictionBreakdown
    recent_rejections: list[RejectedOrderRecord]
    attribution: AttributionResult
    artifact_uri: str | None = None


# ---------------------------------------------------------------------------
# Q6 · Shadow Trading Drift & Factor Decay Sentinel + Authority Gate
# ---------------------------------------------------------------------------


class ShadowDriftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shadow_portfolio_id: str = "SHADOW-CSI500-MAIN"
    universe_id: str = "CSI500"
    window_days: int = 60
    snapshot_id: str | None = None


class FactorDecayItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factor_id: str
    name: str
    rank_ic_60d: float
    hist_percentile: float
    crowding_index: float
    status: Literal["ACTIVE", "DEGRADING", "KILL-SWITCH"]
    note: str


class RebalanceSignalItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    name: str
    side: Literal["BUY", "SELL", "HOLD"]
    delta_weight_pct: float
    est_shares: int
    risk_check: Literal["PASS", "BLOCKED"]


class AuthorityGateManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_id: str
    generated_at: datetime
    snapshot_id: str
    execution_mode: Literal["READ_ONLY_SIGNAL_EXPORT_ONLY"] = "READ_ONLY_SIGNAL_EXPORT_ONLY"
    broker_connected: Literal[False] = False
    risk_passed: bool
    sha256: str
    signals: list[RebalanceSignalItem]


class ShadowDriftResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shadow_portfolio_id: str
    universe_id: str
    snapshot_id: str
    risk_state: Literal["ok", "alert", "kill", "unknown"]
    median_is_delta_bps: float
    total_is_bps: float
    fill_rate_pct: float
    dates: list[str]
    theoretical_excess_bps: list[float]
    shadow_excess_bps: list[float]
    gap_bps: list[float]
    liquidity_bps: list[float]
    limit_queue_bps: list[float]
    factor_decay: list[FactorDecayItem]
    authority_manifest: AuthorityGateManifest
    fact_fingerprint: str


# ---------------------------------------------------------------------------
# Q7 · Unified MCP Envelope & Async Job Contracts
# ---------------------------------------------------------------------------


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str
    format: Literal["parquet", "arrow", "json", "html"]
    rows: int
    sha256: str


class ProvenanceMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    as_of: str
    availability_mode: AvailabilityMode = AvailabilityMode.STRICT_PUBLIC
    coverage_pct: float = 100.0
    warnings: list[str] = Field(default_factory=list)


class McpErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: McpErrorCode
    retryable: bool = False
    message: str


class McpEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"] = "2.0"
    request_id: str
    status: Literal["ok", "degraded", "accepted", "error"]
    snapshot_id: str | None = None
    as_of: str | None = None
    summary: dict[str, Any] | None = None
    artifact: ArtifactRef | None = None
    preview: list[dict[str, Any]] | None = None
    provenance: ProvenanceMeta | None = None
    job_id: str | None = None
    job_state: Literal["queued", "running", "completed", "failed", "cancelled"] | None = None
    error: McpErrorDetail | None = None


class AsyncJobRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    job_type: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    created_at: datetime
    updated_at: datetime
    snapshot_id: str
    progress_pct: int = 0
    summary: dict[str, Any] | None = None
    artifact_uri: str | None = None
    error: str | None = None
