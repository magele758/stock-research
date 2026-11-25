"""Hermetic TEST-ONLY lake + engine stubs that satisfy IMPLEMENTATION-CONTRACT §2.

They exist so the REST/MCP contract tests do not depend on `make seed` or on the engines'
runtime. Production code never imports this module and never fabricates market data.
The real `quant_core.validation.cpcv` is used for the CPCV gate (it already exists).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import polars as pl
from quant_core.schemas import (
    ArtifactRef,
    AttributionResult,
    AuthorityGateManifest,
    BacktestResult,
    BarraExposureResult,
    BarraStyleExposure,
    DataHealthReport,
    DatasetSnapshot,
    DatasetTableStatus,
    ExperimentRecord,
    FactorCorrResult,
    FactorDecayItem,
    FactorEvalResult,
    FrictionBreakdown,
    GateVerdict,
    IndustryDeviation,
    Market,
    PortfolioOptResult,
    QualityCheckItem,
    RebalanceSignalItem,
    ShadowDriftResult,
    TargetWeightItem,
    UniverseMember,
)

SNAP_ID = "snap-test-0001"
SYMBOLS = [f"60{i:04d}.SH" for i in range(1, 41)]
INDUSTRIES = ["银行", "食品饮料", "电子", "医药生物", "电力设备"]


def trading_days(n: int, start: date = date(2025, 1, 2)) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


DAYS = trading_days(330)
SNAPSHOT = DatasetSnapshot(
    snapshot_id=SNAP_ID,
    as_of=datetime.combine(DAYS[-1], datetime.min.time(), tzinfo=UTC),
    markets=[Market.CN_A],
    created_at=datetime(2026, 11, 21, tzinfo=UTC),
    manifest_sha256="a" * 64,
    table_counts={"bar_raw_1d": len(SYMBOLS) * len(DAYS)},
    qa_status="pass",
)


def _rng(key: str) -> np.random.Generator:
    return np.random.default_rng(int(hashlib.sha256(key.encode()).hexdigest()[:8], 16))


class FakeLake:
    def __init__(self, root: Path, with_snapshot: bool = True):
        self.root = root
        self.with_snapshot = with_snapshot
        self.experiments: dict[str, ExperimentRecord] = {}
        self.jobs: dict[str, Any] = {}
        self.artifacts: dict[str, dict[str, Any]] = {}
        if with_snapshot:
            self.save_experiment(
                ExperimentRecord(
                    experiment_id="EXP-0427", family_id="alpha_vp_corr_20d", name="VP corr", model_type="single_factor",
                    feature_summary="alpha_vp_corr_20d + alpha_smart_money_vwap", universe_id="CSI500",
                    snapshot_id=SNAP_ID, trial_count_m=20, is_sharpe=1.5, oos_sharpe=1.1, oos_is_ratio=0.73,
                    cpcv_pbo=0.2, dsr=0.96, oos_excess_ann=5.0, verdict=GateVerdict.PASS, status_badge="PASS",
                    created_at=datetime(2026, 11, 21, tzinfo=UTC),
                )
            )
        self.registry = SimpleNamespace(get_artifact_meta=self._artifact_meta)

    # -- snapshots
    def get_latest_snapshot(self) -> DatasetSnapshot:
        if not self.with_snapshot:
            raise FileNotFoundError("No snapshots published in QuantLake. Run scripts/seed_golden_dataset.py")
        return SNAPSHOT

    def list_snapshots(self) -> list[DatasetSnapshot]:
        return [SNAPSHOT] if self.with_snapshot else []

    # -- data
    def load_bars(self, universe_id, start_date=None, end_date=None, snapshot_id=None) -> pl.DataFrame:
        rows = []
        for si, sym in enumerate(SYMBOLS):
            rng = _rng(sym)
            px = 10.0 + si
            for d in DAYS:
                if (start_date and d < start_date) or (end_date and d > end_date):
                    continue
                px *= 1 + float(rng.normal(0, 0.01))
                rows.append(
                    {
                        "listing_id": f"L{si:03d}", "symbol": sym, "name": f"N{si}", "market": "CN_A", "board": "MAIN",
                        "trade_date": d, "open": px, "high": px * 1.01, "low": px * 0.99, "close": px,
                        "pre_close": px / 1.001, "vwap": px, "volume_shares": 1_000_000 + si, "amount_ccy": px * 1e6,
                        "adj_factor": 1.0, "up_limit": px * 1.1, "down_limit": px * 0.9, "is_suspended": False,
                        "is_st": False, "float_market_cap": 1e10 + si * 1e8, "industry_l1": INDUSTRIES[si % 5],
                    }
                )
        return pl.DataFrame(rows)

    def load_pit_financials(self, as_of, snapshot_id=None, availability_mode=None) -> pl.DataFrame:
        return pl.DataFrame({"symbol": [], "metric_code": []})

    def load_corp_actions(self, snapshot_id=None) -> pl.DataFrame:
        return pl.DataFrame({"symbol": [], "ex_date": []})

    def load_universe_constituents(self, universe_id, as_of_date, snapshot_id=None) -> list[UniverseMember]:
        if universe_id not in {"CSI500", "CSI300"}:
            return []
        n = len(SYMBOLS)
        return [
            UniverseMember(universe_id=universe_id, as_of_date=as_of_date, listing_id=f"L{i:03d}", symbol=s,
                           weight=1.0 / n, effective_from=date(2025, 1, 2))
            for i, s in enumerate(SYMBOLS)
        ]

    def run_qa_sentinel(self, snapshot_id=None) -> DataHealthReport:
        checks = [
            QualityCheckItem(check_id=f"qa_{i}", name=f"check {i}", status="pass", detail="ok " * 40)
            for i in range(7)
        ]
        return DataHealthReport(
            snapshot_id=SNAP_ID, as_of=SNAPSHOT.as_of, overall_status="pass", freshness_sec=3600, coverage_pct=99.5,
            checks=checks,
            datasets=[
                DatasetTableStatus(dataset=f"t{i}", freq="1d", primary_source="tushare", backup_source="baostock",
                                   coverage_pct=99.0, latest_date="2026-11-20", pit_status="pass", row_count=100, disk_mb=1.0)
                for i in range(8)
            ],
            fact_fingerprint="f" * 16,
        )

    # -- registry
    def list_experiments(self, limit: int = 20) -> list[ExperimentRecord]:
        return sorted(self.experiments.values(), key=lambda r: r.created_at, reverse=True)[:limit]

    def save_experiment(self, rec: ExperimentRecord) -> None:
        self.experiments[rec.experiment_id] = rec

    def save_artifact(self, run_id: str, name: str, payload) -> ArtifactRef:
        if isinstance(payload, pl.DataFrame):
            path = self.root / f"{run_id}_{name}.parquet"
            payload.write_parquet(path)
            fmt, rows = "parquet", payload.height
        else:
            path = self.root / f"{run_id}_{name}.json"
            path.write_text(json.dumps(payload, default=str))
            fmt, rows = "json", len(payload)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in f"{run_id}_{name}")
        ref = ArtifactRef(uri=f"quant-artifact://{safe}_{digest[:10]}", format=fmt, rows=rows, sha256=digest)
        self.artifacts[ref.uri] = {"format": fmt, "rows": rows, "sha256": digest, "file_path": str(path)}
        return ref

    def _artifact_meta(self, uri: str):
        return self.artifacts.get(uri)

    def save_job(self, job) -> None:
        self.jobs[job.job_id] = job

    def get_job(self, job_id):
        return self.jobs.get(job_id)


# ---------------------------------------------------------------------------
# engines
# ---------------------------------------------------------------------------

BUILTIN = {
    "alpha_vp_corr_20d": {"name": "vp", "expression": "-rolling_corr(ret, dvol, 20)", "lookback": 20},
    "alpha_mom_resid_60d": {"name": "mom", "expression": "resid_mom", "lookback": 64},
    "alpha_earn_sue_pit": {"name": "sue", "expression": "sue", "lookback": 0},
    "alpha_smart_money_vwap": {"name": "smart", "expression": "smart", "lookback": 19},
}


def _validate_ast(expression: str) -> tuple[bool, list[str]]:
    bad = [tok for tok in ("shift(-", "__import__", "os.", "eval(", "open(") if tok in expression]
    return (not bad), [f"forbidden construct: {t}" for t in bad]


def _evaluate_factor(bars, req, pit=None) -> FactorEvalResult:
    key = req.expression or req.factor_id
    rng = _rng(key)
    n = len(DAYS)
    dates = [d.isoformat() for d in DAYS]

    def walk(mu: float, sd: float) -> list[float]:
        return list(np.cumprod(1 + rng.normal(mu, sd, n)))

    navs = {f"Q{i}": walk(0.0002 * i - 0.0004, 0.008) for i in range(1, 11)}
    navs["LS"] = walk(0.0006, 0.006)
    navs["BENCH"] = walk(0.0002, 0.008)
    expr = req.expression or BUILTIN[req.factor_id]["expression"]
    return FactorEvalResult(
        factor_id=req.factor_id, expression=expr, universe_id=req.universe_id, snapshot_id=req.snapshot_id or SNAP_ID,
        ast_safe=True, rank_ic_mean=0.031, ic_std=0.12, icir_ann=1.8, nw_t_stat=3.1, ic_win_rate=0.58,
        long_short_ann=0.12, top_excess_ann=0.06, daily_turnover_oneway=0.18, half_life_days=9.0,
        monotonicity_spearman=0.88, max_corr_in_library=0.21, max_corr_peer="alpha_mom_resid_60d", coverage_pct=97.0,
        admission_passed=True, gate_checks=[{"name": "ic", "passed": True, "value": 0.031, "threshold": 0.02}],
        residual_exposures={"size": 0.01}, dates=dates, decile_navs=navs,
        decile_ann_returns=[0.01 * i for i in range(10)], rolling_60d_ic=[0.03] * n, cum_ic=[0.03 * i for i in range(n)],
        ic_decay={"1D": 0.03, "5D": 0.025}, industry_ic={"银行": 0.02},
        corr_matrix_labels=["a", "b"], corr_matrix_values=[[1.0, 0.2], [0.2, 1.0]],
        # deliberately large to exercise the 8 KiB envelope guard in the MCP layer
    )


def _factor_panel(bars, req, pit=None) -> pl.DataFrame:
    df = bars.select("trade_date", "symbol", "listing_id").with_columns(pl.col("listing_id").alias("_id"))
    rng = _rng(req.expression or req.factor_id)
    return df.with_columns(
        pl.Series("factor_value", rng.normal(size=df.height)), pl.lit(True).alias("factor_eligible")
    )


def _barra(bars, weights, bench, req) -> BarraExposureResult:
    styles = [BarraStyleExposure(factor=f, pre_opt=0.4, post_opt=0.1, limit=0.5, breached=False) for f in ("size", "beta", "momentum")]
    inds = [IndustryDeviation(industry=i, active_weight_pct=0.5 * (k + 1), limit_pct=3.0) for k, i in enumerate(INDUSTRIES)]
    assert abs(sum(weights.values()) - 1.0) < 1e-6 and abs(sum(bench.values()) - 1.0) < 1e-6
    return BarraExposureResult(
        experiment_id=req.experiment_id, universe_id=req.universe_id, snapshot_id=req.snapshot_id or SNAP_ID,
        as_of=DAYS[-1].isoformat(), styles=styles, industries=inds, predicted_te_pct=4.8, style_risk_share=0.3,
        industry_risk_share=0.2, specific_risk_share=0.5, psd_min_eigenvalue=1e-6,
    )


def _optimize(bars, alpha, prev, bench, req) -> PortfolioOptResult:
    assert alpha and bench
    top = [
        TargetWeightItem(symbol=s, name=s, industry="银行", target_weight_pct=2.0, bench_weight_pct=2.5,
                         active_weight_pct=-0.5, est_impact_bps=3.0)
        for s in SYMBOLS[:30]
    ]
    return PortfolioOptResult(
        opt_run_id="OPT-1", experiment_id=req.experiment_id, universe_id=req.universe_id,
        snapshot_id=req.snapshot_id or SNAP_ID, solver="CLARABEL", solve_status="optimal", solve_ms=12.0,
        expected_gross_excess_pct=6.0, expected_cost_pct=0.8, expected_net_excess_pct=5.2, predicted_te_pct=4.9,
        expected_ir=1.1, oneway_turnover_pct=20.0, annualized_turnover=5.0, effective_n_stocks=40,
        max_constraint_violation=0.0,
        style_exposures=[BarraStyleExposure(factor="size", pre_opt=0.6, post_opt=0.2, limit=0.5, breached=False)],
        industry_deviations=[IndustryDeviation(industry="银行", active_weight_pct=1.0, limit_pct=3.0)],
        top_holdings=top, cvxpy_snippet="# cvxpy snippet " + "x" * 200,
    )


def _backtest(bars, corp, signals, bench, req) -> BacktestResult:
    assert {"trade_date", "symbol", "target_weight"} <= set(signals.columns), signals.columns
    sums = signals.group_by("trade_date").agg(pl.col("target_weight").sum().alias("w"))
    assert sums["w"].max() <= 1.0 + 1e-9, "long-only weights must not exceed 1"
    assert "benchmark_return" in bench.columns and bench.height > 10
    n = 100
    dates = [d.isoformat() for d in DAYS[-n:]]
    nav = list(np.linspace(1.0, 1.1, n))
    return BacktestResult(
        run_id="BT-TEST-1", experiment_id=req.experiment_id, universe_id=req.universe_id, engine=req.engine,
        snapshot_id=req.snapshot_id or SNAP_ID, ann_return_pct=12.0, bench_ann_return_pct=6.0, net_excess_ann_pct=5.5,
        info_ratio=1.2, max_drawdown_pct=-8.0, excess_max_drawdown_pct=-3.0, calmar=1.5, daily_win_rate_pct=53.0,
        profit_loss_ratio=1.3, ann_turnover_pct=480.0, total_fills=1200, total_rejections=17, dates=dates,
        strategy_nav=nav, bench_nav=nav, excess_nav=nav, strategy_dd=[0.0] * n, excess_dd=[-0.01 * (i % 7) for i in range(n)],
        friction=FrictionBreakdown(gross_excess_pct=7.0, commission_pct=0.3, stamp_duty_pct=0.4, transfer_fee_pct=0.02,
                                   impact_slippage_pct=0.6, limit_suspend_pct=0.18, net_excess_pct=5.5, total_friction_bps=150.0),
        recent_rejections=[], attribution=AttributionResult(
            run_id="BT-TEST-1", brinson_allocation_pct=0.5, brinson_selection_pct=4.0, brinson_interaction_pct=0.2,
            brinson_closure_error_bp=0.0, barra_style_pct=1.0, barra_industry_pct=0.5, barra_specific_alpha_pct=4.5,
            barra_cost_pct=-1.0, barra_closure_error_bp=0.0, barra_style_detail={"size": 0.4}),
    )


def _shadow(bars, req) -> ShadowDriftResult:
    sig = [RebalanceSignalItem(symbol=s, name=s, side="BUY", delta_weight_pct=0.2, est_shares=100, risk_check="PASS") for s in SYMBOLS[:15]]
    return ShadowDriftResult(
        shadow_portfolio_id=req.shadow_portfolio_id, universe_id=req.universe_id, snapshot_id=req.snapshot_id or SNAP_ID,
        risk_state="ok", median_is_delta_bps=4.0, total_is_bps=40.0, fill_rate_pct=98.0,
        dates=[d.isoformat() for d in DAYS[-60:]], theoretical_excess_bps=[1.0] * 60, shadow_excess_bps=[0.9] * 60,
        gap_bps=[0.1] * 60, liquidity_bps=[0.05] * 60, limit_queue_bps=[0.02] * 60,
        factor_decay=[FactorDecayItem(factor_id="alpha_vp_corr_20d", name="vp", rank_ic_60d=0.03, hist_percentile=60.0,
                                      crowding_index=0.3, status="ACTIVE", note="ok")],
        authority_manifest=AuthorityGateManifest(manifest_id="M-1", generated_at=datetime(2026, 11, 21, tzinfo=UTC),
                                                 snapshot_id=SNAP_ID, risk_passed=True, sha256="b" * 64, signals=sig),
        fact_fingerprint="c" * 16,
    )


def make_engines() -> dict[str, Any]:
    from quant_core.validation import cpcv

    return {
        "factors": SimpleNamespace(
            BUILTIN_FACTORS=BUILTIN, validate_factor_ast=_validate_ast, evaluate_factor=_evaluate_factor,
            compute_factor_correlation=lambda bars, req: FactorCorrResult(
                universe_id=req.universe_id, snapshot_id=req.snapshot_id or SNAP_ID, method=req.method,
                labels=req.factor_ids, matrix=[[1.0 if i == j else 0.3 for j in range(len(req.factor_ids))] for i in range(len(req.factor_ids))],
                high_corr_pairs=[{"a": "x", "b": "y", "corr": 0.7}], max_abs_corr=0.7),
            factor_panel=_factor_panel,
        ),
        "validation": cpcv,
        "risk": SimpleNamespace(compute_barra_exposure=_barra, optimize_portfolio=_optimize),
        "backtest": SimpleNamespace(run_backtest=_backtest),
        "shadow": SimpleNamespace(compute_shadow_drift=_shadow),
    }
