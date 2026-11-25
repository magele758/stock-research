"""Regression checks for the V3 evidence, regime, capacity and diagnosis contracts."""

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from quant_core.backtest.engine import run_backtest
from quant_core.factors.engine import compute_factor_correlation, evaluate_factor
from quant_core.factors.regime import analyze_factor_regimes, build_dimension_baselines
from quant_core.risk.barra_opt import optimize_portfolio
from quant_core.risk.capacity import analyze_capacity_frontier
from quant_core.schemas import (
    BacktestEngineMode,
    BacktestRequest,
    CapacityAnalysisRequest,
    CpcvRequest,
    FactorCorrRequest,
    FactorEvalRequest,
    FormalVerifyRequest,
    MicrostructureConfig,
    OptConstraints,
    OptObjective,
    PortfolioOptRequest,
    PreprocessConfig,
    RegimeAnalysisRequest,
    ResearchDispatchRequest,
)
from quant_core.validation.cpcv import run_cpcv_gate
from quant_core.validation.diagnosis import generate_diagnosis_and_guidance
from quant_core.validation.formal import (
    benjamini_hochberg_fdr,
    check_prefix_invariance,
    hansen_spa_test,
    run_formal_verification,
    run_parameter_cliff_test,
    stationary_block_bootstrap,
)

_SIGNAL = "pl.col('close') / pl.col('open') - 1"
_WINDOWED = f"({_SIGNAL}).rolling_mean(10).over('symbol')"


@pytest.fixture(scope="module")
def bars() -> pl.DataFrame:
    rng = np.random.default_rng(164)
    columns = 60
    sessions = pl.date_range(date(2023, 1, 2), date(2023, 10, 2), eager=True)
    sessions = [day for day in sessions if day.weekday() < 5]
    symbols = [f"{600000 + i}.SH" for i in range(columns)]
    shares = np.geomspace(1e7, 2e8, columns)
    previous = np.full(columns, 20.0)
    tilt = rng.normal(0, 0.004, columns)
    records = []
    for day in sessions:
        tilt = 0.85 * tilt + rng.normal(0, 0.002, columns)
        benchmark = rng.normal(0.003, 0.002)
        daily = benchmark + tilt + rng.normal(0, 0.003, columns)
        close = previous * (1 + daily)
        volume = rng.integers(2_000_000, 5_000_000, columns)
        for i, symbol in enumerate(symbols):
            records.append(
                {
                    "trade_date": day,
                    "listing_id": f"listing-{i:03}",
                    "symbol": symbol,
                    "market": "CN_A",
                    "board": "MAIN",
                    "industry_l1": f"industry-{i % 3}",
                    "open": previous[i],
                    "pre_close": previous[i],
                    "close": close[i],
                    "high": max(previous[i], close[i]) * 1.001,
                    "low": min(previous[i], close[i]) * 0.999,
                    "volume_shares": int(volume[i]),
                    "float_market_cap": shares[i] * close[i],
                    "amount_ccy": volume[i] * (previous[i] + close[i]) / 2,
                    "adj_factor": 1.0,
                    "is_st": False,
                    "is_suspended": False,
                    "benchmark_return": benchmark,
                }
            )
        previous = close
    return pl.DataFrame(records)


def test_stationary_bootstrap_retains_serial_dependence():
    rng = np.random.default_rng(321)
    series = 0.7 + np.convolve(rng.normal(size=350), np.ones(10) / 10, mode="valid")
    series[44:49] = np.nan
    blocks = stationary_block_bootstrap(series, block_len=12, n_boot=500, seed=91)
    iid = stationary_block_bootstrap(series, block_len=1, n_boot=500, seed=91)
    assert blocks == stationary_block_bootstrap(series, block_len=12, n_boot=500, seed=91)
    assert blocks["point"] == pytest.approx(np.nanmean(series))
    assert blocks["ci95"][0] > 0
    assert np.ptp(blocks["ci95"]) > 1.5 * np.ptp(iid["ci95"])
    assert blocks["p_value"] == pytest.approx(1 / 501)


def test_bh_by_accounts_for_unobserved_historical_trials():
    result = benjamini_hochberg_fdr([0.04, 0.001, 0.015], total_trials_m=5)
    expected = np.array([5 * 0.04 / 3, 0.005, 0.0375])
    np.testing.assert_allclose(result["q_values"], expected)
    np.testing.assert_allclose(result["by_q_values"], expected * sum(1 / i for i in range(1, 6)))
    assert result["rejected"] == [False, True, True]
    assert result["m"] == 5


def test_spa_and_white_rc_share_benchmark_and_candidate_dates():
    rng = np.random.default_rng(1654)
    benchmark = rng.normal(0.0005, 0.01, 250)
    noise = rng.normal(0, 0.005, (len(benchmark), 3))
    candidates = benchmark[:, None] + noise + [0.004, -0.003, -0.006]
    superior = hansen_spa_test(candidates, benchmark, n_boot=500, seed=51)
    inferior = hansen_spa_test(candidates - 0.01, benchmark, n_boot=500, seed=51)
    assert superior == hansen_spa_test(candidates, benchmark, n_boot=500, seed=51)
    assert superior["spa_p_value"] < 0.05
    assert superior["wrc_p_value"] < 0.05
    assert superior["t_stat"] > 0
    assert min(inferior["spa_p_value"], inferior["wrc_p_value"]) > 0.95


def test_prefix_invariance_detects_full_sample_standardization_and_mask_drift(bars):
    frame = bars.filter(pl.col("symbol").is_in(["600000.SH", "600001.SH", "600002.SH"]))
    req = FactorEvalRequest(
        factor_id="causal",
        expression=_SIGNAL,
        preprocess=PreprocessConfig(
            neutralize_industry=False, neutralize_size=False, orthogonalize_barra=False
        ),
    )
    genuine = check_prefix_invariance(frame, req)
    assert genuine["passed"] and genuine["n_compared"] > 0

    seen: list[date] = []

    def leaky(raw, _req):
        seen.append(raw["trade_date"].max())
        return raw.select(
            "trade_date",
            "listing_id",
            ((pl.col("close") - pl.col("close").mean()) / pl.col("close").std()).alias("score"),
        )

    result = check_prefix_invariance(frame, req, compute_fn=leaky)
    assert seen[0] == frame["trade_date"].max()
    assert result["cutoffs_tested"] == [day.isoformat() for day in seen[1:]]
    assert len(seen) == 4
    assert not result["passed"] and result["max_drift"] > 0.01 and result["witness_keys"]

    def mask_only(raw, _req):
        return raw.select(
            "trade_date",
            "listing_id",
            pl.when(pl.col("trade_date") == raw["trade_date"].min())
            .then(pl.lit(float("nan") if raw.height == frame.height else None))
            .otherwise(pl.col("close"))
            .alias("factor_value"),
        )

    masked = check_prefix_invariance(frame, req, compute_fn=mask_only)
    assert not masked["passed"] and masked["max_drift"] == 0 and masked["witness_keys"]


def test_parameter_replay_perturbs_actual_custom_and_builtin_windows(bars):
    custom = run_parameter_cliff_test(bars, FactorEvalRequest(factor_id="rolling", expression=_WINDOWED))
    curve = custom["curves"][0]
    assert curve["values"] == [8, 9, 10, 11, 12] and curve["selected_index"] == 2
    assert curve["n_observed"] > 80 and len(set(curve["icir"])) > 1
    built_in = run_parameter_cliff_test(bars, FactorEvalRequest())
    assert built_in["curves"][0]["values"] == [16, 18, 20, 22, 24]
    assert len(set(built_in["curves"][0]["icir"])) > 1


def test_factor_evaluation_and_custom_correlation_expose_v3_evidence(bars):
    req = FactorEvalRequest(factor_id="causal", expression=_SIGNAL)
    result = evaluate_factor(bars, req)
    assert result.bootstrap_b == 250
    assert result.prefix_invariance_passed is True and result.max_prefix_drift <= 1e-10
    assert all(
        interval and len(interval) == 2 and interval[0] <= interval[1]
        for interval in (result.rank_ic_ci95, result.icir_ci95, result.long_short_ci95)
    )
    assert {item["dim"] for item in result.dimension_slices} == {
        "market_regime",
        "volatility_regime",
        "market_cap_tier",
    }
    off = evaluate_factor(bars, req, n_boot=0, check_prefix=False, include_dimension_slices=False)
    assert off.rank_ic_ci95 is None and off.prefix_invariance_passed is None and not off.dimension_slices
    assert off.rank_ic_mean == result.rank_ic_mean
    corr = compute_factor_correlation(
        bars,
        FactorCorrRequest(factor_ids=["causal", "scaled"]),
        custom_expressions={"causal": _SIGNAL, "scaled": f"2 * ({_SIGNAL})"},
    )
    assert corr.max_abs_corr == pytest.approx(1)
    assert len(corr.high_corr_pairs) == 1
    correlated = result.model_copy(
        update={
            "max_corr_in_library": 0.85,
            "max_corr_peer": "scaled",
            "gate_checks": [{"key": "max_corr_in_library", "peer_common_coverage_pct": {"scaled": 94}}],
        }
    )
    diagnoses, _ = generate_diagnosis_and_guidance(factor_eval=correlated)
    assert [item.rule_id for item in diagnoses] == ["D07"]
    assert diagnoses[0].evidence[0]["peer"] == "scaled"


def test_formal_proof_runs_all_tiers_and_hashes_frozen_evidence(bars):
    rows = bars["trade_date"].n_unique()
    rng = np.random.default_rng(772)
    candidates = rng.normal(0, 0.005, (rows, 3)) + [0.004, 0.001, -0.003]
    req = FormalVerifyRequest(
        factor_id="rolling", expression=_WINDOWED, snapshot_id="fixture", trial_count_m=3
    )
    first = run_formal_verification(bars, req, candidate_matrix=candidates)
    second = run_formal_verification(bars.reverse(), req, candidate_matrix=candidates)
    assert first.proof_sha256 == second.proof_sha256
    assert len(first.proof_sha256) == 64
    assert first.proofs_total == 10
    checks = {item.check_id: item for item in first.checks}
    for key in ("T1.AST_CAUSAL", "T1.PREFIX_INVARIANCE", "T2.SCALE_INVARIANCE", "T2.COVERAGE"):
        assert checks[key].status == "pass", checks[key]
    assert checks["T3.PIT_MONOTONICITY"].status == "not_applicable"
    assert checks["T3.CONSERVATION_PSD"].status == "pass", checks["T3.CONSERVATION_PSD"]
    assert checks["T4.CPCV_PBO_DSR"].n_observed > 0
    assert first.bh_fdr_q_value < 0.05 and first.spa_p_value < 0.05

    unsafe = run_formal_verification(bars, req.model_copy(update={"expression": "pl.col('close').shift(-1)"}))
    assert not unsafe.passed
    diagnoses, plan = generate_diagnosis_and_guidance(proof=unsafe)
    assert diagnoses[0].rule_id == "D01"
    assert diagnoses[0].evidence[0]["proof_check_id"] == "T1.AST_CAUSAL"
    assert ResearchDispatchRequest.model_validate(plan[0]["child_study_patch"])

    family_failure = checks["T4.BH_FDR_SPA"].model_copy(update={"status": "fail", "statistic": "0.13"})
    rejected = first.model_copy(update={"checks": [family_failure]})
    diagnoses, _ = generate_diagnosis_and_guidance(proof=rejected)
    assert [item.rule_id for item in diagnoses] == ["D03"]
    assert diagnoses[0].evidence[0]["statistic"] == "0.13"


def test_cpcv_exposes_fdr_for_each_candidate():
    rng = np.random.default_rng(194)
    candidates = rng.normal(0, 0.007, (168, 3)) + [0.005, 0.0001, -0.005]
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(len(candidates))]
    result = run_cpcv_gate(candidates, dates, CpcvRequest(experiment_id="fixture"))
    assert len(result.fdr_items) == 3
    assert result.fdr_q_value < 0.05 and result.spa_p_value < 0.05
    assert result.fdr_items[2]["q_value"] > 0.1


def test_regime_baselines_are_candidate_independent_and_lag_the_latest_return(bars):
    baselines = build_dimension_baselines(bars, "fixture", "pool")
    other_order = build_dimension_baselines(bars.reverse(), "fixture", "pool")
    assert len(baselines) == 5
    assert [entry.baseline_sha256 for entry in baselines] == [entry.baseline_sha256 for entry in other_order]
    assert all("spark" in item for entry in baselines for item in entry.bucket_stats)
    latest = bars["trade_date"].max()
    shocked = bars.with_columns(
        pl.when(pl.col("trade_date") == latest)
        .then(-0.50)
        .otherwise(pl.col("benchmark_return"))
        .alias("benchmark_return")
    )
    changed = build_dimension_baselines(shocked, "fixture", "pool")
    assert changed[0].current_state == baselines[0].current_state == "BULL"
    assert changed[1].current_state == baselines[1].current_state
    result = analyze_factor_regimes(
        bars,
        RegimeAnalysisRequest(
            factor_id="causal", expression=_SIGNAL, snapshot_id="fixture", universe_id="pool"
        ),
        baselines=baselines,
    )
    assert {item.dim for item in result.slices} == {entry.dimension_type for entry in baselines}
    assert all(item.n_days <= bars["trade_date"].n_unique() for item in result.slices)
    assert all(item.contribution_pct > 60 for item in result.slices if item.dominant_flag)
    assert result.single_dim_dominance_flag == any(item.dominant_flag for item in result.slices)
    concentrated = result.model_copy(
        update={
            "slices": [result.slices[0].model_copy(update={"contribution_pct": 82, "dominant_flag": True})],
            "single_dim_dominance_flag": True,
        }
    )
    diagnoses, _ = generate_diagnosis_and_guidance(regime=concentrated)
    assert [item.rule_id for item in diagnoses] == ["D05"]
    assert diagnoses[0].evidence[0]["contribution_pct"] == 82


def test_capacity_square_root_scaling_and_measured_pov_limit(bars):
    liquid = bars.filter(pl.col("symbol") == "600000.SH").with_columns(
        pl.lit(100_000_000).alias("volume_shares")
    )
    req = CapacityAnalysisRequest(aum_grid_cny=[1e7, 4e7], max_pov=0.10)
    frontier = analyze_capacity_frontier(
        liquid, req, target_weights={"600000.SH": 1}, gross_excess_ann_pct=8, oneway_turnover_pct=10
    )
    low, high = frontier.points
    assert high.impact_bps == pytest.approx(2 * low.impact_bps)
    assert high.explicit_cost_bps == pytest.approx(low.explicit_cost_bps)
    assert high.net_excess_pct < low.net_excess_pct

    scarce = liquid.with_columns(pl.lit(250_000).alias("volume_shares"))
    constrained = analyze_capacity_frontier(
        scarce, CapacityAnalysisRequest(), target_weights={"600000.SH": 1}, gross_excess_ann_pct=8
    )
    assert len(constrained.points) == 6 and constrained.binding_bottleneck == "pov_limit"
    assert constrained.illiquid_names[0]["symbol"] == "600000.SH"
    assert constrained.points[-1].fill_rate_pct < constrained.points[0].fill_rate_pct
    assert max(point.pov_pct for point in constrained.points) <= 5 + 1e-10
    assert constrained.half_alpha_aum_cny is not None
    diagnoses, plan = generate_diagnosis_and_guidance(capacity=constrained)
    assert "D06" in {item.rule_id for item in diagnoses}
    for action in plan:
        assert ResearchDispatchRequest.model_validate(action["child_study_patch"])
        if action["constraints_patch"]:
            assert OptConstraints.model_validate(action["constraints_patch"])


def test_optimizer_and_backtest_emit_numerical_invariants(bars):
    symbols = sorted(bars["symbol"].unique())
    weights = dict.fromkeys(symbols, 1 / len(symbols))
    alpha = dict(zip(symbols, np.linspace(-0.05, 0.05, len(symbols)), strict=True))
    request = PortfolioOptRequest(constraints=OptConstraints(objective=OptObjective.MEAN_VARIANCE))
    optimized = optimize_portfolio(bars, alpha, weights, weights, request)
    assert optimized.solve_status != "infeasible"
    assert {check["key"] for check in optimized.invariants} == {
        "weight_simplex",
        "industry_net_zero",
        "constraint_violation",
        "te_recompute",
        "cov_psd",
    }
    assert all(check["passed"] for check in optimized.invariants), optimized.invariants
    assert len(optimized.capacity_frontier["points"]) == 6
    challenged = optimized.model_copy(
        update={
            "style_exposures": [optimized.style_exposures[0].model_copy(update={"post_opt": 0.51})],
            "expected_gross_excess_pct": 4,
            "expected_cost_pct": 3,
        }
    )
    diagnoses, plan = generate_diagnosis_and_guidance(opt=challenged)
    assert {"D02", "D04"} <= {item.rule_id for item in diagnoses}
    assert all(ResearchDispatchRequest.model_validate(action["child_study_patch"]) for action in plan)

    days = sorted(bars["trade_date"].unique())
    signals = pl.DataFrame({"trade_date": [days[0]], "symbol": [symbols[-1]], "target_weight": [1.0]})
    benchmark = bars.select("trade_date", pl.col("benchmark_return").alias("return")).unique("trade_date")
    tested = run_backtest(
        bars,
        None,
        signals,
        benchmark,
        BacktestRequest(
            engine=BacktestEngineMode.EVENT, microstructure=MicrostructureConfig(rebalance_freq="daily")
        ),
    )
    assert tested.total_fills > 0
    assert {check["key"] for check in tested.invariants} == {
        "brinson_closure",
        "barra_closure",
        "nav_conservation",
        "friction_closure",
        "t_plus_1_zero_violation",
    }
    assert all(check["passed"] for check in tested.invariants), tested.invariants
    assert len(tested.capacity_frontier["points"]) == 6
