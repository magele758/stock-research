"""Deterministic Q2–Q6 numerical gold checks on in-memory data."""

import json
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from itertools import combinations
from math import e, sqrt

import cvxpy as cp
import numpy as np
import polars as pl
import pytest
import statsmodels.api as sm
from scipy.stats import norm

from quant_core.backtest.attribution import barra_decomposition, brinson_fachler, relative_link_weights
from quant_core.backtest.engine import get_price_limit_pct, run_backtest
from quant_core.backtest.microstructure import match_order
from quant_core.factors.engine import (
    BUILTIN_FACTORS,
    compute_factor_correlation,
    evaluate_factor,
    factor_panel,
    newey_west_t_stat,
    validate_factor_ast,
)
from quant_core.factors.preprocess import neutralize, winsorize_mad
from quant_core.risk.barra_opt import BARRA_STYLES, fit_barra_risk_model, optimize_portfolio
from quant_core.schemas import (
    BacktestEngineMode,
    BacktestRequest,
    BoardType,
    CpcvRequest,
    FactorCorrRequest,
    FactorEvalRequest,
    GateVerdict,
    MicrostructureConfig,
    OptConstraints,
    OptObjective,
    PortfolioOptRequest,
    PreprocessConfig,
    ShadowDriftRequest,
)
from quant_core.shadow.monitor import _decay_item, compute_implementation_shortfall, compute_shadow_drift
from quant_core.validation.cpcv import (
    build_cpcv_splits,
    compute_cscv_pbo,
    compute_deflated_sharpe,
    run_cpcv_gate,
)


def trading_days(n: int, start: date = date(2025, 1, 2)) -> list[date]:
    calendar = []
    while len(calendar) < n:
        if start.weekday() < 5:
            calendar.append(start)
        start += timedelta(days=1)
    return calendar


@pytest.fixture(scope="module")
def equity_panel() -> pl.DataFrame:
    rng = np.random.default_rng(710205)
    n = 54
    calendar = trading_days(160)
    cap = np.exp(rng.uniform(20, 24, n))
    float_shares = cap / 20
    industry = np.arange(n) % 3
    value, growth, lev = rng.uniform(0.1, 0.7, (3, n))
    previous = np.full(n, 20.0)
    records = []
    for day in calendar:
        returns = rng.normal(0, 0.011, n) + rng.normal(0, 0.007, 3)[industry]
        close = previous * (1 + returns)
        opening = previous * (1 + rng.normal(0, 0.004, n))
        vwap = (opening + close) / 2 + rng.normal(0, 0.018, n)
        volume = np.round(float_shares * rng.uniform(0.003, 0.03, n)).astype(int)
        for i in range(n):
            symbol = f"{600000 + i}.SH"
            records.append(
                {
                    "listing_id": symbol,
                    "symbol": symbol,
                    "name": symbol,
                    "market": "CN_A",
                    "board": "MAIN",
                    "trade_date": day,
                    "open": opening[i],
                    "close": close[i],
                    "pre_close": previous[i],
                    "high": max(opening[i], close[i]) * 1.002,
                    "low": min(opening[i], close[i]) * 0.998,
                    "adj_factor": 1.0,
                    "vwap": vwap[i],
                    "volume_shares": int(volume[i]),
                    "amount_ccy": volume[i] * vwap[i],
                    "float_market_cap": float_shares[i] * close[i],
                    "industry_l1": f"industry-{industry[i]}",
                    "is_suspended": False,
                    "is_st": False,
                    "equity": cap[i] * value[i],
                    "total_assets": cap[i] * (value[i] + lev[i]),
                    "net_profit_ttm": cap[i] * growth[i] * 0.15,
                    "total_debt": cap[i] * lev[i],
                    "revenue_growth": growth[i],
                    "earnings_growth": growth[i] * (1 + value[i]),
                }
            )
        previous = close
    return pl.DataFrame(records)


@pytest.mark.parametrize(
    "expression",
    [
        "pl.col('close').shift(-1)",
        "shift(close, n=-5)",
        "lead(close)",
        "close[-1]",
        "close.shift(1-2)",
        "pl.col('close').reverse()",
        "os.system('id')",
        "open('/tmp/log')",
        "eval('1+1')",
        "__import__('socket')",
        "pl.col('forward_return')",
    ],
)
def test_ast_rejects_future_or_unapproved_operator(expression):
    safe, warnings = validate_factor_ast(expression)
    assert safe is False
    assert warnings


def test_factor_values_are_unchanged_by_later_sessions(equity_panel):
    expression = "pl.col('close') / pl.col('close').shift(5).over('symbol') - 1"
    assert validate_factor_ast(expression) == (True, [])
    cutoff = trading_days(160)[115]
    against = equity_panel.filter(pl.col("trade_date") <= cutoff)
    perturbed = equity_panel.with_columns(
        pl.when(pl.col("trade_date") > cutoff)
        .then(pl.col("close") * 100)
        .otherwise(pl.col("close"))
        .alias("close"),
        pl.when(pl.col("trade_date") > cutoff)
        .then(pl.col("volume_shares") * 1000)
        .otherwise(pl.col("volume_shares"))
        .alias("volume_shares"),
    )
    req = FactorEvalRequest(factor_id="safe_test", expression=expression)
    builtins = [
        FactorEvalRequest(factor_id=factor) for factor in BUILTIN_FACTORS if factor != "alpha_earn_sue_pit"
    ]
    for request in [req, *builtins]:
        baseline = factor_panel(against, request).filter(pl.col("trade_date") == cutoff).sort("_id")
        actual = factor_panel(perturbed, request).filter(pl.col("trade_date") == cutoff).sort("_id")
        assert baseline["factor_value"].null_count() == 0, request.factor_id
        assert np.max(np.abs(baseline["factor_value"].to_numpy() - actual["factor_value"].to_numpy())) < 1e-12


def test_factor_tearsheet_aligns_nav_hac_and_library(equity_panel):
    req = FactorEvalRequest(
        factor_id="safe_test",
        expression="pl.col('close') / pl.col('open') - 1",
        horizons=[3],
        preprocess=PreprocessConfig(
            neutralize_industry=False, neutralize_size=False, orthogonalize_barra=False
        ),
    )
    result = evaluate_factor(equity_panel, req)
    assert result.ast_safe
    assert len(result.gate_checks) == 6
    assert set(result.decile_navs) == {f"Q{i}" for i in range(1, 11)} | {"LS", "BENCH"}
    assert {len(nav) for nav in result.decile_navs.values()} == {len(result.dates)}
    assert len(result.decile_ann_returns) == 10
    assert set(result.ic_decay) == {"1D", "3D", "5D", "10D", "20D", "60D"}
    assert abs(result.rank_ic_mean) < 1 and np.isfinite(result.nw_t_stat)
    assert len(result.corr_matrix_values) == len(result.corr_matrix_labels)
    correlation = compute_factor_correlation(
        equity_panel,
        FactorCorrRequest(
            factor_ids=["alpha_vp_corr_20d", "alpha_smart_money_vwap"],
        ),
    )
    matrix = np.asarray(correlation.matrix)
    np.testing.assert_allclose(matrix, matrix.T, atol=1e-12)
    np.testing.assert_allclose(np.diag(matrix), 1, atol=1e-12)
    # The matrix is the signed daily mean. The gate uses the mean magnitude
    # to catch peers whose correlation changes direction over time.
    assert abs(matrix[0, 1]) <= correlation.max_abs_corr <= 1
    rng = np.random.default_rng(251)
    ic = 0.04 + np.convolve(rng.normal(0, 0.12, 160), np.ones(5) / 5, mode="valid")
    t_stat, bandwidth = newey_west_t_stat(ic, 10)
    reference = sm.OLS(ic, np.ones(len(ic))).fit(cov_type="HAC", cov_kwds={"maxlags": bandwidth})
    assert bandwidth >= 9
    assert t_stat == pytest.approx(reference.tvalues[0], abs=1e-12)


def test_pit_earnings_ignores_a_revision_published_after_the_decision_close(equity_panel):
    first, second = trading_days(160)[-2:]
    bars = equity_panel.filter(pl.col("trade_date").is_in([first, second]))
    periods = [date(year, month, 28) for year in (2021, 2022, 2023) for month in (3, 6, 9, 12)]
    symbols = sorted(bars["symbol"].unique().to_list())
    records = [
        {
            "symbol": symbol,
            "report_period_end": period,
            "metric_code": "eps",
            "published_at": datetime.combine(period + timedelta(days=40), time(2), UTC),
            "value": 0.05 * i + 0.2 * np.sin(i + j / 9),
            "revision_seq": 1,
        }
        for j, symbol in enumerate(symbols)
        for i, period in enumerate(periods)
    ]
    # 08:00 UTC is after the first signal's 15:00 Shanghai close.
    revised = {
        **records[len(periods) - 1],
        "published_at": datetime.combine(first, time(8), UTC),
        "value": 10.0,
        "revision_seq": 2,
    }
    request = FactorEvalRequest(
        factor_id="alpha_earn_sue_pit",
        preprocess=PreprocessConfig(orthogonalize_barra=False),
    )
    previous = factor_panel(bars, request, pl.DataFrame(records))
    observed = factor_panel(bars, request, pl.DataFrame([*records, revised]))
    for day, invariant in [(first, True), (second, False)]:
        left, right = (
            panel.filter(pl.col("trade_date") == day).sort("_id")["factor_value"].to_numpy()
            for panel in (previous, observed)
        )
        assert np.isfinite(left).all() and np.isfinite(right).all()
        assert bool(np.max(np.abs(left - right)) < 1e-12) == invariant


@pytest.mark.parametrize("weighted", [False, True])
def test_cross_sectional_regression_residual_is_orthogonal(weighted):
    rng = np.random.default_rng(19)
    n = 72
    groups = np.array(["Tech", "Banks", "Industrials"])[np.arange(n) % 3]
    caps = np.exp(rng.uniform(16, 28, n))
    beta = rng.normal(size=n)
    design = np.column_stack([np.ones(n), groups == "Tech", groups == "Banks", np.log(caps), beta])
    values = design @ [0.3, -1.0, 0.1, 0.8, -0.2] + rng.normal(scale=0.7, size=n)
    weights = np.sqrt(caps) if weighted else np.ones(n)
    residual, diagnostics = neutralize(
        values, groups, caps, styles={"Beta": beta}, weights=weights if weighted else None
    )
    projection = (weights * residual) @ design
    normalizers = np.linalg.norm(design * np.sqrt(weights[:, None]), axis=0) * np.linalg.norm(
        residual * np.sqrt(weights),
    )
    assert np.max(np.abs(projection) / normalizers) < 1e-8
    assert max(diagnostics.values()) < 1e-8
    expected = sm.WLS(values, design, weights=weights).fit().resid
    np.testing.assert_allclose(residual, expected, atol=1e-10)
    clipped = winsorize_mad(np.array([0.0, 1.0, 2.0, 3.0, 100.0]), 3.0)
    assert clipped[-1] == pytest.approx(2 + 3 * 1.4826)
    assert winsorize_mad(np.array([1.0, 1.0, 1.0, 10.0]), 2.0)[-1] < 10


def test_cpcv_paths_purge_and_embargo_are_independently_verified():
    folds, splits, paths = build_cpcv_splits(240)
    assert [split.test_folds for split in splits] == list(combinations(range(6), 2))
    assert len(paths) == 5
    for fold_number, fold in enumerate(folds):
        assigned = sorted(path[fold_number] for path in paths)
        assert assigned == [i for i, split in enumerate(splits) if fold_number in split.test_folds]
        assert all(np.isin(fold, splits[i].test).all() for i in assigned)
    for split in splits:
        assert not np.any(np.abs(split.train[:, None] - split.test[None, :]) <= 5)
        ends = split.test[np.r_[np.diff(split.test) != 1, True]]
        assert not np.any((split.train[:, None] > ends + 5) & (split.train[:, None] <= ends + 15))
        assert len(split.train) + len(split.test) + len(split.purged) + len(split.embargoed) == 240


def test_pbo_and_dsr_reject_a_frozen_gaussian_trial_family():
    dates = trading_days(480)
    returns = np.random.default_rng(42).normal(0, 0.01, (len(dates), 64))
    result = run_cpcv_gate(
        returns, dates, CpcvRequest(experiment_id="noise", trial_count_m=64), returns[:, 0]
    )
    assert (result.n_combinations, result.n_oos_paths, result.purge_conflicts) == (15, 5, 0)
    assert len(result.path_sharpes) == len(result.baseline_path_sharpes) == 5
    assert len(result.walk_forward_folds) == 4
    assert result.pbo == pytest.approx(31 / 70)
    assert result.pbo > 0.25 and result.dsr < 0.95
    assert result.verdict is GateVerdict.REJECT
    for fold in result.walk_forward_folds:
        assert fold["train_end"] < fold["validation_start"] < fold["test_start"]
    assert compute_cscv_pbo(returns) == pytest.approx(result.pbo)
    with pytest.raises(ValueError, match="distinct"):
        compute_cscv_pbo(np.repeat(returns[:, :1], 2, axis=1))

    sr, n_obs, trials, sd, skew, kurtosis = 0.12, 420, 64, 0.045, -0.38, 4.6
    expected_max = sd * (
        (1 - np.euler_gamma) * norm.ppf(1 - 1 / trials) + np.euler_gamma * norm.ppf(1 - 1 / (trials * e))
    )
    expected = norm.cdf(
        (sr - expected_max) * sqrt(n_obs - 1) / sqrt(1 - skew * sr + (kurtosis - 1) * sr**2 / 4)
    )
    assert compute_deflated_sharpe(sr, n_obs, trials, sd, skew, kurtosis) == pytest.approx(
        expected, abs=1e-14
    )


def test_cpcv_accepts_a_stable_out_of_sample_edge():
    dates = trading_days(480)
    returns = np.random.default_rng(50).normal(0, 0.01, (len(dates), 64))
    returns[:, 0] += 0.004
    result = run_cpcv_gate(returns, dates, CpcvRequest(experiment_id="edge", trial_count_m=64))
    assert result.verdict is GateVerdict.PASS
    assert result.pbo == 0
    assert result.dsr > 0.95
    assert all(fold["candidate_index"] == 0 for fold in result.walk_forward_folds)


def test_barra_covariance_and_portfolio_hard_constraints(equity_panel):
    names = sorted(equity_panel["symbol"].unique().to_list())
    benchmark = dict.fromkeys(names, 1 / len(names))
    covariance = fit_barra_risk_model(equity_panel, benchmark)
    assert covariance.style_exposures.shape == (len(names), len(BARRA_STYLES)) == (54, 10)
    assert np.linalg.eigvalsh(covariance.factor_covariance).min() > 0
    assert np.linalg.eigvalsh(covariance.covariance).min() > 0
    constraints = OptConstraints(
        objective=OptObjective.MEAN_VARIANCE,
        max_stock_weight=0.05,
        max_industry_dev=0.04,
        turnover_lambda=0.001,
        max_turnover_oneway=0.20,
        target_te=0.04,
        max_size_z=0.20,
        max_style_z=0.25,
    )
    alpha = dict(zip(names, np.linspace(-0.12, 0.12, len(names)), strict=True))
    result = optimize_portfolio(
        equity_panel, alpha, benchmark, benchmark, PortfolioOptRequest(constraints=constraints)
    )
    assert result.solve_status in ("optimal", "optimal_inaccurate")
    assert result.solver == "CLARABEL"
    assert result.max_constraint_violation < 1e-5
    assert result.predicted_te_pct <= constraints.target_te * 100 + 1e-5
    assert result.oneway_turnover_pct <= constraints.max_turnover_oneway * 100 + 1e-5
    assert sum(h.target_weight_pct for h in result.top_holdings) == pytest.approx(100, abs=1e-5)
    assert all(
        -1e-5 <= h.target_weight_pct <= constraints.max_stock_weight * 100 + 1e-5 for h in result.top_holdings
    )
    assert all(abs(entry.active_weight_pct) <= entry.limit_pct + 1e-5 for entry in result.industry_deviations)
    assert all(abs(entry.post_opt) <= entry.limit + 1e-5 for entry in result.style_exposures)
    assert "CLARABEL" in result.cvxpy_snippet and "cp.norm" in result.cvxpy_snippet

    previous = dict.fromkeys(names, 0.70 / (len(names) - 1))
    previous[names[0]] = 0.30
    strict = constraints.model_copy(update={"max_stock_weight": 0.20, "max_turnover_oneway": 0.0})
    relaxed = optimize_portfolio(
        equity_panel, alpha, previous, benchmark, PortfolioOptRequest(constraints=strict)
    )
    assert relaxed.solve_status == "relaxed_optimal"
    assert any("turnover" in note.lower() for note in relaxed.relaxation_notes)
    assert relaxed.max_constraint_violation < 1e-5
    assert all(h.target_weight_pct <= 20 + 1e-5 for h in relaxed.top_holdings)
    impossible = strict.model_copy(update={"max_stock_weight": 0.01})
    refused = optimize_portfolio(
        equity_panel, alpha, previous, benchmark, PortfolioOptRequest(constraints=impossible)
    )
    assert refused.solve_status == "infeasible"
    assert refused.top_holdings == []


def test_barra_relaxes_style_only_after_turnover_floor(equity_panel):
    names = sorted(equity_panel["symbol"].unique().to_list())[:2]
    panel = equity_panel.filter(pl.col("symbol").is_in(names))
    benchmark = dict(zip(names, [0.90, 0.10], strict=True))
    constraints = OptConstraints(
        objective=OptObjective.MEAN_VARIANCE,
        max_stock_weight=0.55,
        max_industry_dev=1.0,
        max_turnover_oneway=0.0,
        max_style_z=0.001,
        max_size_z=0.001,
        target_te=1.0,
    )
    result = optimize_portfolio(
        panel, {names[0]: 0.05}, benchmark, benchmark, PortfolioOptRequest(constraints=constraints)
    )
    assert result.solve_status == "relaxed_optimal"
    assert "turnover" in result.relaxation_notes[0].lower()
    assert "style" in result.relaxation_notes[1].lower()
    assert result.max_constraint_violation < 1e-5
    assert max(h.target_weight_pct for h in result.top_holdings) <= 55 + 1e-5


def test_osqp_fallback_rechecks_the_original_tracking_error_cone(equity_panel, monkeypatch):
    names = sorted(equity_panel["symbol"].unique().to_list())[:6]
    panel = equity_panel.filter(pl.col("symbol").is_in(names))
    benchmark = dict.fromkeys(names, 1 / len(names))
    solve = cp.Problem.solve

    def without_clarabel(problem, *args, **kwargs):
        if kwargs.get("solver") == "CLARABEL":
            raise cp.error.SolverError("CLARABEL unavailable in fallback test")
        return solve(problem, *args, **kwargs)

    monkeypatch.setattr(cp.Problem, "solve", without_clarabel)
    constraints = OptConstraints(
        objective=OptObjective.MEAN_VARIANCE,
        max_stock_weight=0.45,
        max_industry_dev=0.5,
        turnover_lambda=0.001,
        max_turnover_oneway=0.50,
        max_size_z=0.8,
        max_style_z=0.8,
        target_te=0.008,
    )
    result = optimize_portfolio(
        panel,
        {names[0]: 0.8, names[-1]: -0.8},
        benchmark,
        benchmark,
        PortfolioOptRequest(constraints=constraints),
    )
    assert result.solve_status in ("optimal", "optimal_inaccurate")
    assert result.solver == "OSQP"
    assert result.predicted_te_pct == pytest.approx(constraints.target_te * 100, abs=1e-5)
    assert result.max_constraint_violation < 1e-5


def _bar(day, opening=10.0, closing=None, **kwargs):
    return {
        "trade_date": day,
        "symbol": "600001.SH",
        "listing_id": "SH600001",
        "name": "Fixture",
        "board": "MAIN",
        "market": "CN_A",
        "industry_l1": "Industrials",
        "pre_close": 10.0,
        "open": opening,
        "high": opening,
        "low": opening,
        "close": opening if closing is None else closing,
        "volume_shares": 80_000,
        "is_st": False,
        "is_suspended": False,
        "adj_factor": 1.0,
        **kwargs,
    }


def test_cn_microstructure_historical_limits_t_plus_one_and_pov():
    old, new = date(2026, 7, 5), date(2026, 7, 6)
    assert get_price_limit_pct(BoardType.MAIN, True, old) == 0.05
    assert get_price_limit_pct(BoardType.MAIN, True, new) == 0.10
    assert get_price_limit_pct(BoardType.MAIN, False, old) == 0.10
    assert get_price_limit_pct(BoardType.STAR, False, old) == 0.20
    assert get_price_limit_pct(BoardType.CHINEXT, True, new) == 0.20
    assert get_price_limit_pct(BoardType.BSE, False, new) == 0.30
    assert get_price_limit_pct(BoardType.NYSE, False, old) is None
    assert get_price_limit_pct(BoardType.NASDAQ, True, old) is None
    config = MicrostructureConfig()
    buy = match_order(_bar(new), "BUY", 500, 0, config)
    assert buy.quantity == 500
    locked = match_order(_bar(new), "SELL", buy.quantity, 0, config, position=buy.quantity)
    assert locked.quantity == 0 and locked.reason == "T+1_LOCK"
    assert match_order(_bar(new + timedelta(days=1)), "SELL", 500, 500, config, position=500).quantity == 500
    assert match_order(_bar(old, 10.50, is_st=True), "BUY", 100, 0, config).reason == "LIMIT_UP"
    assert match_order(_bar(new, 10.50, is_st=True), "BUY", 100, 0, config).quantity == 100
    assert match_order(_bar(new, 11.00, is_st=True), "BUY", 100, 0, config).reason == "LIMIT_UP"
    assert match_order(_bar(new, 9.00), "SELL", 100, 100, config).reason == "LIMIT_DOWN"
    assert match_order(_bar(new, is_suspended=True), "SELL", 100, 100, config).reason == "SUSPENDED"
    cap = match_order(
        _bar(new, volume_shares=9_999),
        "BUY",
        2_000,
        0,
        config,
        used_volume=100,
        adv_shares=50_000,
        daily_volatility=0.02,
    )
    assert cap.quantity == 300 and cap.reason == "POV_LIMIT"
    assert cap.price == pytest.approx(10 * (1 + 0.45 * 0.02 * sqrt(2_000 / 50_000)), abs=0.01)
    assert cap.commission == pytest.approx(cap.quantity * cap.price * 2.5 / 10_000, abs=0.01)


@pytest.mark.parametrize("engine", [BacktestEngineMode.EVENT, BacktestEngineMode.SCREENING])
def test_cash_dividend_and_bonus_share_total_wealth_is_conserved(engine):
    days = trading_days(6)
    bars = pl.DataFrame(
        [
            _bar(days[0]),
            _bar(days[1]),
            _bar(days[2], 9.0, pre_close=9.0, adj_factor=10 / 9),
            _bar(days[3], 9.0, pre_close=9.0, adj_factor=10 / 9),
            _bar(days[4], 4.5, pre_close=4.5, adj_factor=20 / 9),
            _bar(days[5], 4.5, pre_close=4.5, adj_factor=20 / 9),
        ]
    )
    actions = pl.DataFrame(
        [
            {
                "action_id": "div",
                "listing_id": "SH600001",
                "symbol": "600001.SH",
                "ex_date": days[2],
                "record_date": days[1],
                "pay_date": days[3],
                "cash_dividend_per_share": 1.0,
                "split_ratio": 1.0,
                "bonus_share_ratio": 0.0,
            },
            {
                "action_id": "bonus",
                "listing_id": "SH600001",
                "symbol": "600001.SH",
                "ex_date": days[4],
                "record_date": days[3],
                "pay_date": days[4],
                "cash_dividend_per_share": 0.0,
                "split_ratio": 1.0,
                "bonus_share_ratio": 1.0,
            },
        ]
    )
    signal = pl.DataFrame({"trade_date": [days[0]], "symbol": ["600001.SH"], "target_weight": [1.0]})
    bench = pl.DataFrame({"trade_date": days, "return": [0.0] * len(days)})
    micro = MicrostructureConfig(
        rebalance_freq="daily",
        commission_bps=0,
        stamp_duty_sell_bps=0,
        transfer_fee_bps=0,
        impact_coeff_y=0,
    )
    result = run_backtest(
        bars,
        actions,
        signal,
        bench,
        BacktestRequest(
            engine=engine,
            initial_cash=1000,
            microstructure=micro,
        ),
    )
    assert result.total_fills == 1
    value = np.asarray(result.strategy_nav)
    # The public NAV series may be quoted in units of initial equity.
    if value[0] == pytest.approx(1):
        value = value * 1000
    np.testing.assert_allclose(value, 1000.0, atol=1e-10)
    assert result.ann_return_pct == pytest.approx(0, abs=1e-10)
    assert abs(result.attribution.brinson_closure_error_bp) < 0.01
    assert abs(result.attribution.barra_closure_error_bp) < 0.01


def test_independent_brinson_and_barra_attribution_and_geometric_linking():
    portfolio = np.array([0.25, 0.30, 0.15, 0.15, 0.15])
    benchmark = np.array([0.20, 0.10, 0.35, 0.25, 0.10])
    industry = np.array(["Banking", "Banking", "Tech", "Tech", "Cash"])
    group = (industry[:, None] == np.unique(industry)[None, :]).astype(float)
    styles = np.array([[0.1, 0.8], [-0.4, -0.2], [1.0, 0.1], [-0.1, 0.4], [0, 0]])
    factors = np.array([0.02, -0.01, 0.005, 0, -0.03])
    residual = np.array([0.01, -0.01, 0.002, 0.03, 0])
    returns = np.column_stack([styles, group]) @ factors + residual
    active = portfolio - benchmark
    allocation, selection, interaction = brinson_fachler(portfolio, benchmark, returns, industry)
    assert abs((allocation + selection + interaction).sum() - active @ returns) * 10_000 < 0.01
    style, industry_effect, specific = barra_decomposition(active, styles, group, factors, residual)
    assert abs(style.sum() + industry_effect + specific - active @ returns) * 10_000 < 0.01
    p = np.array([0.04, -0.08, 0.025, 0.0])
    b = np.array([0.01, -0.01, 0.025, 0.02])
    assert relative_link_weights(p, b) @ (p - b) == pytest.approx(
        np.prod(1 + p) / np.prod(1 + b) - 1, abs=1e-12
    )


@pytest.mark.parametrize("engine", [BacktestEngineMode.EVENT, BacktestEngineMode.SCREENING])
def test_suspended_position_freezes_and_execution_costs_close(engine):
    days = trading_days(4)
    bars = pl.DataFrame(
        [
            _bar(days[0]),
            _bar(days[1], 10.0, 10.2, high=10.2),
            _bar(days[2], 10.2, pre_close=10.2, is_suspended=True, volume_shares=0),
            _bar(days[3], 10.3, 10.2, pre_close=10.2, low=10.2),
        ]
    )
    signal = pl.DataFrame(
        {
            "trade_date": days[:3],
            "symbol": ["600001.SH"] * 3,
            "target_weight": [0.55, 0.0, 0.0],
        }
    )
    benchmark = pl.DataFrame({"trade_date": days, "bench_ret": [0.0, 0.001, -0.001, 0.003]})
    result = run_backtest(
        bars,
        None,
        signal,
        benchmark,
        BacktestRequest(
            engine=engine,
            initial_cash=10_000.0,
            microstructure=MicrostructureConfig(rebalance_freq="daily", impact_coeff_y=0),
        ),
    )
    assert result.total_fills == 2
    assert result.strategy_nav[2] == pytest.approx(result.strategy_nav[1], abs=1e-12)
    assert any(
        row.reason == "SUSPENDED" and row.side == "SELL" and row.trade_date == days[2].isoformat()
        for row in result.recent_rejections
    )
    if engine is BacktestEngineMode.EVENT:
        final_value = result.strategy_nav[-1] / result.strategy_nav[0] * 10_000
        assert final_value == pytest.approx(10144.786, abs=1e-8)
    friction = result.friction
    assert friction.commission_pct > 0 and friction.stamp_duty_pct > 0 and friction.transfer_fee_pct > 0
    assert (friction.gross_excess_pct - friction.net_excess_pct) * 100 == pytest.approx(
        friction.total_friction_bps,
        abs=1e-9,
    )
    assert abs(result.attribution.brinson_closure_error_bp) < 0.01
    assert abs(result.attribution.barra_closure_error_bp) < 0.01


def test_shadow_declares_sustained_rank_ic_decay_against_prior_mature_windows():
    steady = [0.07] * 204
    decayed = [0.07] * 140 + [-0.04] * 64
    healthy, healthy_alert = _decay_item("alpha", "Test alpha", steady, 0.2, False)
    current, current_alert = _decay_item("alpha", "Test alpha", decayed, 0.2, False)
    crowded, crowded_alert = _decay_item("alpha", "Test alpha", decayed, 0.98, False)
    assert healthy.status == "ACTIVE" and not healthy_alert
    assert current.rank_ic_60d == pytest.approx(-0.04)
    assert current.hist_percentile == 0
    assert current.status == "DEGRADING" and current_alert
    assert crowded.status == "KILL-SWITCH" and crowded_alert
    sparse, sparse_alert = _decay_item("alpha", "Test alpha", steady[-40:], None, False)
    assert "Insufficient" in sparse.note and not sparse_alert


def test_perold_monitor_and_authority_gate_are_read_only():
    days = trading_days(21)
    shortfall = compute_implementation_shortfall("BUY", 100, 10, 10.1, 10.2, [(60, 10.15)], 0.2)
    assert shortfall.delay == pytest.approx(10.0)
    assert shortfall.liquidity == pytest.approx(3.0)
    assert shortfall.unfilled == pytest.approx(4.0)
    assert shortfall.total_bps == pytest.approx(172.0)
    short_sell = compute_implementation_shortfall("SELL", 100, 10, 9.9, 9.8, [(60, 9.85)], 0.2)
    assert short_sell.total_bps == pytest.approx(172.0)
    available = datetime.combine(days[-1], time(7), UTC)
    rows = [
        _bar(
            day,
            shadow_order_id=f"paper-{i}",
            shadow_side="BUY",
            shadow_target_qty=100,
            shadow_decision_price=10.0,
            shadow_arrival_price=10.1,
            shadow_end_price=10.2,
            shadow_filled_qty=60,
            shadow_fill_price=10.15,
            shadow_reject_reason="LIMIT_QUEUE",
            shadow_fees=0.2,
            backtest_is_bps=165.0,
            signal_side="BUY",
            signal_delta_weight=0.02,
            signal_est_shares=100,
            available_at=datetime.combine(day, time(7), UTC),
            authority_approved=True,
            signal_risk_passed=True,
            authority_reviewed_at=available - timedelta(hours=1),
            authority_expires_at=available + timedelta(hours=1),
        )
        for i, day in enumerate(days)
    ]
    panel = pl.DataFrame(rows)
    req = ShadowDriftRequest(snapshot_id="fixture")
    result = compute_shadow_drift(panel, req)
    assert result.risk_state == "ok"
    assert result.total_is_bps == pytest.approx(172.0)
    assert result.median_is_delta_bps == pytest.approx(7.0)
    assert result.fill_rate_pct == pytest.approx(60)
    assert result.limit_queue_bps[-1] == pytest.approx(40.0)
    manifest = result.authority_manifest
    assert manifest.broker_connected is False
    assert manifest.execution_mode == "READ_ONLY_SIGNAL_EXPORT_ONLY"
    assert manifest.risk_passed is True and len(manifest.signals) == 1
    payload = manifest.model_dump(mode="json", exclude={"sha256"})
    expected = sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()
    assert manifest.sha256 == expected
    assert result == compute_shadow_drift(panel.reverse(), req)

    killed = compute_shadow_drift(panel.with_columns(data_stale=pl.col("trade_date") == days[-1]), req)
    assert killed.risk_state == "kill"
    assert killed.authority_manifest.risk_passed is False
    assert killed.authority_manifest.broker_connected is False
    assert killed.authority_manifest.signals == []
    overfilled = panel.with_columns(shadow_filled_qty=pl.lit(101))
    assert compute_shadow_drift(overfilled, req).risk_state == "kill"
    plain = compute_shadow_drift(pl.DataFrame([_bar(days[-1])]), req)
    assert plain.risk_state == "unknown"
    assert plain.authority_manifest.broker_connected is False
    assert plain.authority_manifest.signals == []
