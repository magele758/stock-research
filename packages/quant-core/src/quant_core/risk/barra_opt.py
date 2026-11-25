"""Causal Barra-style proxy risk model and long-only portfolio construction."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import cvxpy as cp
import numpy as np
import polars as pl
from sklearn.covariance import LedoitWolf

from quant_core.schemas import (
    BarraExposureRequest,
    BarraExposureResult,
    BarraStyleExposure,
    CapacityAnalysisRequest,
    IndustryDeviation,
    InvariantCheckItem,
    OptConstraints,
    OptObjective,
    PortfolioOptRequest,
    PortfolioOptResult,
    TargetWeightItem,
)

from .capacity import _analyze_capacity

BARRA_STYLES = (
    "Size",
    "Beta",
    "Momentum",
    "Residual Vol",
    "Nonlinear Size",
    "BTOP",
    "Liquidity",
    "Earnings Yield",
    "Growth",
    "Leverage",
)
_TRADING_DAYS = 252
_VARIANCE_FLOOR = 1e-9
_FEASIBILITY_TOLERANCE = 1e-7


def _number(panel: pl.DataFrame, *names: str) -> pl.Expr:
    columns = [pl.col(name).cast(pl.Float64, strict=False) for name in names if name in panel.columns]
    if not columns:
        return pl.lit(None, dtype=pl.Float64)
    return pl.coalesce([pl.when(column.is_finite()).then(column) for column in columns])


def _ratio(numerator: pl.Expr, denominator: pl.Expr) -> pl.Expr:
    return pl.when(denominator > 0).then(numerator / denominator)


def _standardized_styles(panel: pl.DataFrame, fields: tuple[str, ...]) -> pl.DataFrame:
    """Winsorize and standardize descriptors independently on each session."""
    raw = panel.select("trade_date", *(_number(panel, name).alias(name) for name in fields))
    centers = raw.with_columns(
        pl.col(name).median().over("trade_date").alias(f"_center_{name}") for name in fields
    )
    scales = []
    for name in fields:
        column = pl.col(name)
        mad = (column - pl.col(f"_center_{name}")).abs().median().over("trade_date") * 1.4826
        scales.append(
            pl.when(mad == 0)
            .then(column.std(ddof=0).over("trade_date"))
            .otherwise(mad)
            .alias(f"_scale_{name}")
        )
    moments = centers.with_columns(scales)
    clipped = moments.select(
        "trade_date",
        *(
            pl.col(name)
            .clip(
                pl.col(f"_center_{name}") - 3 * pl.col(f"_scale_{name}"),
                pl.col(f"_center_{name}") + 3 * pl.col(f"_scale_{name}"),
            )
            .alias(name)
            for name in fields
        ),
    )
    normalized = []
    for name in fields:
        column = pl.col(name)
        std = column.std(ddof=0).over("trade_date")
        normalized.append(
            pl.when(std.is_null())
            .then(None)
            .when(std > np.finfo(float).eps)
            .then(((column - column.mean().over("trade_date")) / std).fill_null(0.0))
            .otherwise(0.0)
            .alias(name)
        )
    return clipped.select("trade_date", *normalized)


def _utc_timestamp(panel: pl.DataFrame, name: str) -> pl.Expr:
    column = pl.col(name)
    if panel.schema[name] == pl.String:
        return pl.coalesce(
            [
                column.str.to_datetime(format=pattern, time_zone="UTC", strict=False)
                for pattern in ("%+", "%Y-%m-%dT%H:%M:%S%.f", "%Y-%m-%d %H:%M:%S%.f")
            ]
        )
    return column.cast(pl.Datetime("us", "UTC"), strict=False)


def _financial_available(panel: pl.DataFrame) -> pl.Expr:
    fields = [name for name in ("financial_published_at", "financial_available_at") if name in panel.columns]
    if not fields:
        return pl.lit(True)
    day = pl.col("trade_date")
    us = pl.lit(False)
    if "market" in panel.columns:
        us |= pl.col("market").cast(pl.String) == "US_EQ"
    if "board" in panel.columns:
        us |= pl.col("board").cast(pl.String).is_in(["NASDAQ", "NYSE"])
    midnight = day.cast(pl.Datetime("us"))
    us_close = (midnight + pl.duration(hours=16)).dt.replace_time_zone("America/New_York")
    cn_close = (midnight + pl.duration(hours=15)).dt.replace_time_zone("Asia/Shanghai")
    cutoff = (
        pl.when(us.fill_null(False))
        .then(us_close.dt.convert_time_zone("UTC"))
        .otherwise(
            cn_close.dt.convert_time_zone("UTC"),
        )
    )
    if "decision_at" in panel.columns and panel.schema["decision_at"] != pl.Date:
        cutoff = pl.min_horizontal(cutoff, _utc_timestamp(panel, "decision_at"))
    available = pl.lit(True)
    for name in fields:
        field = pl.col(name)
        if panel.schema[name] == pl.Date:
            eligible = field < day
        else:
            eligible = _utc_timestamp(panel, name) <= cutoff
            if panel.schema[name] == pl.String:
                date_only = field.str.to_date(format="%Y-%m-%d", strict=False)
                eligible = pl.coalesce(eligible, date_only < day)
        available &= eligible.fill_null(False)
    return available


def build_barra_exposure_panel(
    bars_df: pl.DataFrame, bench_weights: dict[str, float] | None = None
) -> pl.DataFrame:
    """Build as-of-close exposures using prices and PIT financial values on each row.

    Historical benchmark returns come from an explicit index-return column or PIT
    beginning-of-period weights. Lagged float-cap weights proxy the broad market
    when those inputs are unavailable. A latest-day benchmark map is for the
    risk model's holdings and has no effect on historical beta or momentum.
    """
    required = {"trade_date", "symbol", "close"}
    if not required.issubset(bars_df.columns):
        raise ValueError(f"Barra panel is missing columns: {sorted(required - set(bars_df.columns))}")
    if bars_df.is_empty():
        return bars_df.with_columns(pl.lit(None, dtype=pl.Float64).alias(style) for style in BARRA_STYLES)

    panel = bars_df.with_columns(
        pl.col("trade_date").cast(pl.Date),
        pl.col("symbol").cast(pl.String),
    ).sort(["trade_date", "symbol"])
    if panel.select(pl.any_horizontal(pl.col("trade_date", "symbol").is_null()).any()).item():
        raise ValueError("Barra panel requires a symbol and trade date on every row")
    if panel.select(pl.struct("trade_date", "symbol").is_duplicated().any()).item():
        raise ValueError("Barra panel has duplicate dates and symbols")

    cap = _number(panel, "float_market_cap", "free_float_market_cap", "market_cap")
    total_cap = _number(panel, "market_cap", "total_market_cap", "float_market_cap")
    price = _number(panel, "close")
    equity = _number(panel, "equity", "book_equity", "shareholders_equity")
    earnings = _number(panel, "net_profit_ttm", "net_income_ttm", "net_profit_deducted_ttm")
    debt = _number(panel, "total_debt", "interest_bearing_debt")
    revenue_growth = pl.coalesce(
        _number(panel, "revenue_growth", "revenue_yoy"),
        _ratio(
            _number(panel, "revenue_ttm") - _number(panel, "revenue_ttm_prior_year"),
            _number(panel, "revenue_ttm_prior_year").abs(),
        ),
    )
    earnings_growth = pl.coalesce(
        _number(panel, "earnings_growth", "net_profit_growth", "net_profit_yoy"),
        _ratio(
            earnings - _number(panel, "net_profit_ttm_prior_year"),
            _number(panel, "net_profit_ttm_prior_year").abs(),
        ),
    )
    financial_available = _financial_available(panel)

    panel = panel.with_columns(
        cap.alias("_barra_cap"),
        (price * _number(panel, "adj_factor")).alias("_barra_adjusted"),
        _number(panel, "pre_close").alias("_barra_pre_close"),
        _ratio(_number(panel, "volume_shares") * price, cap).alias("_barra_turnover"),
        pl.when(cap > 0).then(cap.log()).alias("_barra_size"),
        pl.when(financial_available).then(_ratio(equity, total_cap)).alias("_barra_btop"),
        pl.when(financial_available)
        .then(
            pl.coalesce(
                _number(panel, "earnings_yield"),
                _ratio(earnings, total_cap),
                _ratio(_number(panel, "eps_ttm", "eps"), price),
            )
        )
        .alias("_barra_ey"),
        pl.when(financial_available).then(revenue_growth).alias("_barra_revgrowth"),
        pl.when(financial_available).then(earnings_growth).alias("_barra_earngrowth"),
        pl.when(financial_available).then(_ratio(debt, equity)).alias("_barra_debteq"),
        pl.when(financial_available)
        .then(_ratio(debt, _number(panel, "total_assets")))
        .alias("_barra_debtassets"),
        pl.col("trade_date").rank(method="dense").alias("_barra_session"),
    )
    lag = pl.col("_barra_session") - pl.col("_barra_session").shift(1).over("symbol")
    same_step = lag == 1
    adjusted_return = pl.when(same_step).then(
        _ratio(pl.col("_barra_adjusted"), pl.col("_barra_adjusted").shift(1).over("symbol")) - 1
    )
    raw_return = (
        _ratio(
            price,
            pl.coalesce(
                pl.col("_barra_pre_close"),
                pl.when(same_step).then(price.shift(1).over("symbol")),
            ),
        )
        - 1
    )
    daily_return = pl.coalesce(adjusted_return, raw_return)
    suspension = pl.col("is_suspended").fill_null(False) if "is_suspended" in panel.columns else pl.lit(False)
    panel = panel.with_columns(
        pl.when(~suspension & (daily_return > -1) & daily_return.is_finite())
        .then(daily_return)
        .alias("_barra_return"),
        pl.coalesce(
            _number(panel, "benchmark_weight", "bench_weight").shift(1).over("symbol"),
            pl.col("_barra_cap").shift(1).over("symbol"),
        ).alias("_barra_market_weight"),
        _number(panel, "volume_shares")
        .shift(1)
        .rolling_mean(20, min_samples=1)
        .over("symbol")
        .alias("_barra_adv"),
    )
    # A partially populated index-membership column treats nonmembers as zero.
    if any(name in panel.columns for name in ("benchmark_weight", "bench_weight")):
        panel = panel.with_columns(
            _number(panel, "benchmark_weight", "bench_weight")
            .fill_null(0)
            .shift(1)
            .over("symbol")
            .alias("_barra_market_weight")
        )
    lagged_weight = pl.col("_barra_market_weight")
    observed_weight = pl.when(pl.col("_barra_return").is_not_null() & (lagged_weight > 0)).then(lagged_weight)
    panel = panel.with_columns(
        pl.coalesce(
            _number(panel, "benchmark_return", "bench_return").drop_nulls().first().over("trade_date"),
            _ratio(
                (pl.col("_barra_return") * observed_weight).sum().over("trade_date"),
                observed_weight.sum().over("trade_date"),
            ),
            pl.col("_barra_return").mean().over("trade_date"),
        ).alias("_barra_market")
    )
    excess = pl.col("_barra_return").log1p() - pl.col("_barra_market").log1p()
    panel = panel.with_columns(
        pl.when(pl.col("_barra_return").is_not_null())
        .then(pl.col("_barra_market"))
        .alias("_barra_beta_market"),
        excess.alias("_barra_log_excess"),
    )
    stock = pl.col("_barra_return")
    market = pl.col("_barra_beta_market")
    covariance = pl.rolling_cov(stock, market, window_size=252, min_samples=20).over("symbol")
    market_variance = market.rolling_var(252, min_samples=20).over("symbol")
    stock_variance = stock.rolling_var(252, min_samples=20).over("symbol")
    panel = panel.with_columns(
        _ratio(covariance, market_variance).alias("_barra_beta"),
        pl.when(market_variance > 0)
        .then((stock_variance - covariance.pow(2) / market_variance).clip(lower_bound=0).sqrt())
        .alias("_barra_residual_vol"),
        stock_variance.sqrt().alias("_barra_total_vol"),
        pl.col("_barra_log_excess")
        .shift(21)
        .rolling_sum(231, min_samples=40)
        .over("symbol")
        .alias("_barra_momentum"),
        *[
            pl.col("_barra_turnover")
            .rolling_mean(window, min_samples=minimum)
            .over("symbol")
            .log1p()
            .alias(f"_barra_liq{window}")
            for window, minimum in ((5, 3), (21, 10), (63, 20))
        ],
    )
    if "industry_l1" not in panel.columns:
        panel = panel.with_columns(pl.lit("Unknown").alias("industry_l1"))
    else:
        panel = panel.with_columns(pl.col("industry_l1").cast(pl.String).fill_null("Unknown"))

    single = {
        "Size": "_barra_size",
        "Beta": "_barra_beta",
        "Momentum": "_barra_momentum",
        "BTOP": "_barra_btop",
        "Earnings Yield": "_barra_ey",
    }
    composite = {
        "Residual Vol": ("_barra_residual_vol", "_barra_total_vol"),
        "Liquidity": ("_barra_liq5", "_barra_liq21", "_barra_liq63"),
        "Growth": ("_barra_revgrowth", "_barra_earngrowth"),
        "Leverage": ("_barra_debteq", "_barra_debtassets"),
    }
    descriptors = (*single.values(), *(name for fields in composite.values() for name in fields))
    standardized = _standardized_styles(panel, descriptors)
    combined = _standardized_styles(
        standardized.select(
            "trade_date", *(pl.mean_horizontal(fields).alias(name) for name, fields in composite.items())
        ),
        tuple(composite),
    )
    sizes = standardized["_barra_size"].to_numpy()
    dates = standardized["trade_date"].to_numpy()
    boundaries = np.flatnonzero(dates[1:] != dates[:-1]) + 1
    nonlinear = np.full(panel.height, np.nan)
    for rows in np.split(np.arange(panel.height), boundaries):
        size = sizes[rows]
        if np.isfinite(size).any():
            regressors = np.column_stack([np.ones(len(rows)), size])
            cubic = size**3
            coefficients = np.linalg.lstsq(regressors, cubic, rcond=None)[0]
            residual = cubic - regressors @ coefficients
            # A common scale preserves the orthogonality of the cubic residual.
            deviation = residual.std()
            nonlinear[rows] = residual / deviation if deviation > 1e-12 else 0.0
    exposures = combined.with_columns(
        *(standardized[field].alias(name) for name, field in single.items()),
        pl.Series("Nonlinear Size", nonlinear, nan_to_null=True),
    )
    return panel.with_columns(exposures.select(*BARRA_STYLES).get_columns())


def fit_barra_factor_returns(
    styles: np.ndarray,
    industries: np.ndarray,
    returns: np.ndarray,
    market_cap: np.ndarray,
    benchmark_weights: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Estimate factor returns with sqrt-cap WLS and identified industry effects.

    Industry coefficients include the market intercept. In a fully-invested
    active book their sum therefore contributes exactly the industry effect.
    """
    styles = np.nan_to_num(np.asarray(styles, dtype=float), nan=0.0)
    industries = np.asarray(industries, dtype=float)
    returns = np.asarray(returns, dtype=float)
    market_cap = np.asarray(market_cap, dtype=float)
    valid = np.isfinite(returns)
    n_styles = styles.shape[1]
    if not valid.any():
        return np.zeros(n_styles), np.zeros(industries.shape[1]), returns.copy()

    observed_cap = market_cap[valid]
    valid_cap = np.isfinite(observed_cap) & (observed_cap > 0)
    replacement = np.median(observed_cap[valid_cap]) if valid_cap.any() else 1.0
    observed_cap = np.where(valid_cap, observed_cap, replacement)
    weights = np.sqrt(observed_cap)
    weights = np.minimum(weights, np.median(weights) * 10)
    root_weights = np.sqrt(weights / weights.mean())

    observed_industries = industries[valid]
    reference_weights = observed_cap
    if benchmark_weights is not None:
        observed_benchmark = np.asarray(benchmark_weights, dtype=float)[valid]
        if observed_benchmark.sum() > 0:
            reference_weights = observed_benchmark
    industry_mass = observed_industries.T @ reference_weights
    if industry_mass.size and industry_mass.max() > 0:
        pivot = int(np.argmax(industry_mass))
        free = np.arange(len(industry_mass)) != pivot
        ratios = industry_mass[free] / industry_mass[pivot]
        contrasts = industries[:, free] - industries[:, [pivot]] * ratios
    else:
        pivot = None
        contrasts = np.empty((len(returns), 0))

    design = np.column_stack([np.ones(len(returns)), styles, contrasts])
    coefficients = np.linalg.lstsq(
        design[valid] * root_weights[:, None],
        returns[valid] * root_weights,
        rcond=1e-10,
    )[0]
    style_returns = coefficients[1 : n_styles + 1]
    industry_returns = np.full(industries.shape[1], coefficients[0])
    if pivot is not None:
        contrast_returns = coefficients[n_styles + 1 :]
        industry_returns[free] += contrast_returns
        industry_returns[pivot] -= float(ratios @ contrast_returns)
    fitted = styles @ style_returns + industries @ industry_returns
    if industries.shape[1] == 0:
        fitted = styles @ style_returns
    return style_returns, industry_returns, returns - fitted


@dataclass(frozen=True)
class BarraRiskModel:
    symbols: tuple[str, ...]
    industry_names: tuple[str, ...]
    as_of: str
    latest: pl.DataFrame
    style_exposures: np.ndarray
    industry_exposures: np.ndarray
    factor_covariance: np.ndarray
    specific_variance: np.ndarray

    @property
    def design(self) -> np.ndarray:
        return np.column_stack([self.style_exposures, self.industry_exposures])

    @property
    def covariance(self) -> np.ndarray:
        covariance = self.design @ self.factor_covariance @ self.design.T
        return (covariance + covariance.T) / 2 + np.diag(self.specific_variance)

    @property
    def factor_root(self) -> np.ndarray:
        return np.linalg.cholesky(self.factor_covariance).T @ self.design.T

    def tracking_error(self, active: np.ndarray) -> float:
        systematic = self.factor_root @ active
        return float(np.sqrt(systematic @ systematic + self.specific_variance @ active**2))


def _clip_psd(covariance: np.ndarray) -> np.ndarray:
    eigenvalues, eigenvectors = np.linalg.eigh((covariance + covariance.T) / 2)
    return (eigenvectors * np.maximum(eigenvalues, _VARIANCE_FLOOR)) @ eigenvectors.T


def _industry_matrix(block: pl.DataFrame, names: tuple[str, ...]) -> np.ndarray:
    return block.get_column("industry_l1").to_numpy()[:, None] == np.array(names)[None, :]


def fit_barra_risk_model(
    bars_df: pl.DataFrame, bench_weights: dict[str, float] | None = None
) -> BarraRiskModel:
    """Fit trailing one-day factor returns and annualize shrunk covariance."""
    if bars_df.is_empty():
        raise ValueError("Barra risk estimation requires at least one bar")
    panel = build_barra_exposure_panel(bars_df, bench_weights)
    blocks = panel.partition_by("trade_date", maintain_order=True)[-253:]
    latest = blocks[-1]
    symbols = tuple(latest.get_column("symbol").to_list())
    histories: dict[str, list[float]] = {symbol: [] for symbol in symbols}
    raw_histories: dict[str, list[float]] = {symbol: [] for symbol in symbols}
    industry_names = tuple(sorted({name for block in blocks for name in block["industry_l1"].unique()}))
    factor_history = []

    for beginning, ending in pairwise(blocks):
        beginning = beginning.join(ending.select("symbol", "_barra_return"), on="symbol", suffix="_next")
        if beginning.is_empty():
            continue
        returns = beginning.get_column("_barra_return_next").to_numpy()
        if not np.isfinite(returns).any():
            continue
        explicit_benchmark = None
        for name in ("benchmark_weight", "bench_weight"):
            if name in beginning.columns:
                explicit_benchmark = np.nan_to_num(beginning.get_column(name).cast(pl.Float64).to_numpy())
                break
        styles, industries, residual = fit_barra_factor_returns(
            beginning.select(BARRA_STYLES).to_numpy(),
            _industry_matrix(beginning, industry_names),
            returns,
            beginning.get_column("_barra_cap").to_numpy(),
            explicit_benchmark,
        )
        factor_history.append(np.concatenate([styles, industries]))
        for symbol, raw_return, specific in zip(beginning["symbol"], returns, residual, strict=True):
            if symbol in histories and np.isfinite(specific):
                histories[symbol].append(float(specific))
                raw_histories[symbol].append(float(raw_return))

    observations = np.asarray(factor_history)
    n_features = len(BARRA_STYLES) + len(industry_names)
    if len(observations) >= 2:
        factor_covariance = LedoitWolf().fit(observations).covariance_ * _TRADING_DAYS
    else:
        factor_covariance = np.zeros((n_features, n_features))
    factor_covariance = _clip_psd(factor_covariance)

    residual_variances = {
        symbol: np.var(values, ddof=1) for symbol, values in histories.items() if len(values) >= 2
    }
    raw_variances = [np.var(values, ddof=1) for values in raw_histories.values() if len(values) >= 2]
    observed_prior = np.array([value for value in residual_variances.values() if value > 1e-12])
    if observed_prior.size:
        global_prior = float(np.median(observed_prior))
    elif raw_variances:
        global_prior = float(np.median(raw_variances)) * 0.25
    else:
        global_prior = 0.02**2
    labels = latest.get_column("industry_l1").to_list()
    industry_priors: dict[str, list[float]] = {}
    for symbol, industry in zip(symbols, labels, strict=True):
        if residual_variances.get(symbol, 0) > 1e-12:
            industry_priors.setdefault(industry, []).append(residual_variances[symbol])
    specific_variance = []
    for symbol, industry in zip(symbols, labels, strict=True):
        peers = industry_priors.get(industry, ())
        prior = float(np.median(peers)) if len(peers) >= 3 else global_prior
        count = len(histories[symbol])
        weight = count / (count + 40) if count >= 2 else 0.0
        daily_variance = weight * residual_variances.get(symbol, 0.0) + (1 - weight) * prior
        specific_variance.append(max(float(daily_variance) * _TRADING_DAYS, _VARIANCE_FLOOR))

    return BarraRiskModel(
        symbols=symbols,
        industry_names=industry_names,
        as_of=str(latest.get_column("trade_date").item(0)),
        latest=latest,
        style_exposures=np.nan_to_num(latest.select(BARRA_STYLES).to_numpy()),
        industry_exposures=_industry_matrix(latest, industry_names).astype(float),
        factor_covariance=factor_covariance,
        specific_variance=np.array(specific_variance),
    )


def _weight_vector(values: dict[str, float], symbols: tuple[str, ...]) -> np.ndarray:
    vector = np.asarray([values.get(symbol, 0.0) for symbol in symbols], dtype=float)
    if not np.isfinite(vector).all():
        raise ValueError("Portfolio inputs must be finite")
    return vector


def _validate_weights(values: dict[str, float], label: str) -> None:
    vector = np.asarray(list(values.values()), dtype=float)
    if not np.isfinite(vector).all() or np.any(vector < 0) or vector.sum() > 1 + 1e-6:
        raise ValueError(f"{label} must contain finite, long-only weights summing to at most one")


def _risk_report(
    model: BarraRiskModel,
    pre_active: np.ndarray,
    post_active: np.ndarray,
    constraints: OptConstraints,
    style_limits: np.ndarray,
) -> tuple[list[BarraStyleExposure], list[IndustryDeviation]]:
    before = model.style_exposures.T @ pre_active
    after = model.style_exposures.T @ post_active
    deviations = model.industry_exposures.T @ post_active
    return [
        BarraStyleExposure(
            factor=name,
            pre_opt=float(before[index]),
            post_opt=float(after[index]),
            limit=float(style_limits[index]),
            breached=bool(abs(after[index]) > style_limits[index] + _FEASIBILITY_TOLERANCE),
        )
        for index, name in enumerate(BARRA_STYLES)
    ], [
        IndustryDeviation(
            industry=name,
            active_weight_pct=float(deviations[index]) * 100,
            limit_pct=constraints.max_industry_dev * 100,
        )
        for index, name in enumerate(model.industry_names)
        if model.industry_exposures[:, index].any()
    ]


def _style_limits(constraints: OptConstraints) -> np.ndarray:
    limits = np.full(len(BARRA_STYLES), constraints.max_style_z, dtype=float)
    limits[0] = constraints.max_size_z
    return limits


def compute_barra_exposure(
    bars_df: pl.DataFrame,
    weights: dict[str, float],
    bench_weights: dict[str, float],
    req: BarraExposureRequest,
) -> BarraExposureResult:
    """Report active exposures and Euler shares of annualized active variance."""
    _validate_weights(weights, "Portfolio weights")
    _validate_weights(bench_weights, "Benchmark weights")
    model = fit_barra_risk_model(bars_df, bench_weights)
    missing = sorted(set(weights) - set(model.symbols))
    if any(weights[symbol] > 0 for symbol in missing):
        raise ValueError(f"Cannot estimate risk for unpriced holdings: {missing}")
    active = _weight_vector(weights, model.symbols) - _weight_vector(bench_weights, model.symbols)
    constraints = OptConstraints()
    styles, industries = _risk_report(model, active, active, constraints, _style_limits(constraints))
    exposures = model.design.T @ active
    covariance_times_exposure = model.factor_covariance @ exposures
    contribution = exposures * covariance_times_exposure
    style_risk = float(contribution[: len(BARRA_STYLES)].sum())
    industry_risk = float(contribution[len(BARRA_STYLES) :].sum())
    specific_risk = float(model.specific_variance @ active**2)
    total = style_risk + industry_risk + specific_risk
    denominator = total if total > 0 else 1.0
    return BarraExposureResult(
        experiment_id=req.experiment_id,
        universe_id=req.universe_id,
        snapshot_id=req.snapshot_id or "unspecified",
        as_of=model.as_of,
        styles=styles,
        industries=industries,
        predicted_te_pct=100 * model.tracking_error(active),
        style_risk_share=style_risk / denominator,
        industry_risk_share=industry_risk / denominator,
        specific_risk_share=specific_risk / denominator,
        psd_min_eigenvalue=float(np.linalg.eigvalsh(model.covariance)[0]),
    )


def _positive(value: Any) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if np.isfinite(value) and value > 0 else None


def _trade_bounds(
    model: BarraRiskModel, previous: np.ndarray, max_weight: float, nav: float | None
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Derive hard frozen-position, side-limit and forecast POV bounds."""
    from quant_core.backtest.microstructure import raw_price_limits

    lower = np.zeros(len(previous))
    upper = np.full(len(previous), max_weight)
    statuses = []
    for index, row in enumerate(model.latest.iter_rows(named=True)):
        prior = previous[index]
        price = _positive(row["close"])
        status = "OK"
        if row.get("is_suspended") or price is None:
            lower[index] = prior
            upper[index] = min(upper[index], prior)
            statuses.append("SUSPENDED")
            continue
        lock = _positive(row.get("locked_weight"))
        if row.get("is_t_plus_1_locked"):
            lock = prior
        if lock is not None:
            lower[index] = max(lower[index], min(lock, prior))
            status = "T+1_LOCK"

        prices = [_positive(row.get(field)) for field in ("open", "high", "low")]
        if prices[0] is not None and all(value == prices[0] for value in prices[1:]):
            limit_up, limit_down = raw_price_limits(row)
            if limit_up is not None and abs(prices[0] - limit_up) <= 1e-8:
                upper[index] = min(upper[index], prior)
                status = "LIMIT_CAP"
            if limit_down is not None and abs(prices[0] - limit_down) <= 1e-8:
                lower[index] = max(lower[index], prior)
                status = "LIMIT_CAP"
        adv = _positive(row.get("_barra_adv"))
        if nav is not None and adv is not None:
            pov = float(row.get("max_pov") if row.get("max_pov") is not None else 0.05)
            if not 0 <= pov <= 1:
                raise ValueError("Latest max_pov must be in [0, 1]")
            capacity = price * adv * pov / nav
            lower[index] = max(lower[index], prior - capacity)
            upper[index] = min(upper[index], prior + capacity)
        statuses.append(status)
    return lower, upper, statuses


@dataclass(frozen=True)
class _Portfolio:
    model: BarraRiskModel
    alpha: np.ndarray
    prior: np.ndarray
    benchmark: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    constraints: OptConstraints

    def violations(
        self,
        weights: np.ndarray,
        turnover: float,
        style_limits: np.ndarray,
        cash: float = 0,
        traded_weights: np.ndarray | None = None,
    ) -> dict[str, float]:
        active = weights - self.benchmark
        traded = weights if traded_weights is None else traded_weights
        return {
            "full investment / cash": abs(weights.sum() + cash - 1),
            "buy, sell and capacity bounds": max(
                float(np.max(self.lower - traded)),
                float(np.max(traded - self.upper)),
                0,
            ),
            "concentration": max(float(np.max(weights - self.constraints.max_stock_weight)), 0),
            "industry": max(
                float(
                    np.max(
                        np.abs(self.model.industry_exposures.T @ active) - self.constraints.max_industry_dev
                    )
                ),
                0,
            ),
            "style": max(float(np.max(np.abs(self.model.style_exposures.T @ active) - style_limits)), 0),
            "turnover": max(float(np.abs(traded - self.prior).sum() / 2 - turnover), 0),
            "tracking error": max(self.model.tracking_error(active) - self.constraints.target_te, 0),
        }


@dataclass(frozen=True)
class _ContinuousSolution:
    weights: np.ndarray | None
    solver: str
    status: str


def _solve_problem(
    objective: cp.Expression,
    constraints: list[cp.Constraint],
    risk_vector: cp.Expression,
    risk_ceiling: cp.Expression | float,
    solver: str,
    risk_penalty: float = 0,
) -> str:
    if solver == "CLARABEL":
        risk_norm = cp.norm(risk_vector)
        problem = cp.Problem(
            cp.Maximize(objective - risk_penalty * risk_norm),
            [*constraints, risk_norm <= risk_ceiling],
        )
        problem.solve(solver=solver, tol_gap_abs=1e-10, tol_feas=1e-10, tol_gap_rel=1e-9)
        return problem.status

    # The squared TE bound has a nonnegative Lagrange multiplier. Solving
    # the resulting QPs and bisecting the multiplier preserves the TE cone.
    # For a norm penalty eta, the first-order coefficient includes eta/(2*TE).
    # TE decreases with the multiplier and multiplier*TE increases.
    ceiling = float(risk_ceiling)
    variance = cp.sum_squares(risk_vector)
    multiplier = cp.Parameter(nonneg=True)
    problem = cp.Problem(cp.Maximize(objective - multiplier * variance), constraints)
    acceptable = (cp.OPTIMAL, cp.OPTIMAL_INACCURATE)

    def run(value: float) -> tuple[str, float]:
        multiplier.value = value
        problem.solve(
            solver="OSQP",
            eps_abs=1e-9,
            eps_rel=1e-9,
            max_iter=100_000,
            polishing=True,
        )
        norm = float(np.linalg.norm(risk_vector.value)) if problem.status in acceptable else np.inf
        return problem.status, norm

    def adequate(value: float, norm: float) -> bool:
        return norm <= ceiling + 1e-9 and 2 * value * norm >= risk_penalty - 1e-8

    status, norm = run(0.0)
    if status not in acceptable or adequate(0.0, norm):
        return status

    lower, upper = 0.0, 1.0
    for _ in range(30):
        status, norm = run(upper)
        if status not in acceptable:
            break
        if adequate(upper, norm):
            break
        lower, upper = upper, 2 * upper
    else:
        status = "te_multiplier_unbracketed"

    if status not in acceptable:
        feasible = cp.Problem(cp.Minimize(variance), constraints)
        feasible.solve(
            solver="OSQP",
            eps_abs=1e-9,
            eps_rel=1e-9,
            max_iter=100_000,
            polishing=True,
        )
        if feasible.status not in acceptable:
            return feasible.status
        minimum_risk = float(np.linalg.norm(risk_vector.value))
        if minimum_risk > ceiling + _FEASIBILITY_TOLERANCE / 2:
            return cp.INFEASIBLE
        return cp.OPTIMAL_INACCURATE

    for _ in range(32):
        midpoint = (lower + upper) / 2
        status, norm = run(midpoint)
        if status not in acceptable:
            break
        if adequate(midpoint, norm):
            upper = midpoint
        else:
            lower = midpoint
        if upper - lower <= 1e-8 * max(1.0, upper):
            break
    return run(upper)[0]


def _solve_with(
    inputs: _Portfolio, turnover: float, style_limits: np.ndarray, solver: str
) -> _ContinuousSolution:
    constraint = inputs.constraints
    parity = constraint.objective == OptObjective.RISK_PARITY
    weights = cp.Variable(len(inputs.alpha))
    scale = cp.sum(weights) if parity else 1
    active = weights - inputs.benchmark * scale
    delta = weights - inputs.prior * scale
    risk = cp.hstack(
        [
            inputs.model.factor_root @ active,
            cp.multiply(
                np.sqrt(inputs.model.specific_variance),
                active,
            ),
        ]
    )
    hard = [
        weights >= inputs.lower * scale,
        weights <= inputs.upper * scale,
        cp.abs(inputs.model.industry_exposures.T @ active) <= constraint.max_industry_dev * scale,
        cp.abs(inputs.model.style_exposures.T @ active) <= style_limits * scale,
        cp.norm1(delta) <= 2 * turnover * scale,
    ]
    if not parity:
        hard.append(cp.sum(weights) == 1)

    ceiling = float(constraint.target_te) * scale
    transaction_penalty = constraint.turnover_lambda * cp.norm1(delta)
    net_alpha = inputs.alpha @ active - transaction_penalty
    if parity:
        absolute_risk = cp.hstack(
            [
                inputs.model.factor_root @ weights,
                cp.multiply(np.sqrt(inputs.model.specific_variance), weights),
            ]
        )
        risk_budget = (inputs.upper > 0).astype(float)
        risk_budget /= risk_budget.sum()
        risk_budget_members = risk_budget > 0
        hard.append(weights[risk_budget_members] >= 1e-10)
        objective = -0.5 * cp.sum_squares(absolute_risk) + risk_budget[risk_budget_members] @ cp.log(
            weights[risk_budget_members],
        )
    elif constraint.objective == OptObjective.MIN_VAR:
        objective = -cp.sum_squares(risk)
    else:
        objective = net_alpha - cp.sum_squares(risk)

    if constraint.objective != OptObjective.MAX_SHARPE:
        status = _solve_problem(objective, hard, risk, ceiling, solver)
        return _ContinuousSolution(_solution_value(weights, status), solver, status)

    # Dinkelbach's iteration solves the active net-return / TE ratio by
    # maximizing its concave numerator minus a positive multiple of the norm.
    # Start at zero and retain the best positive-ratio feasible portfolio.
    best: _ContinuousSolution | None = None
    estimate = 0.0
    for _ in range(30):
        status = _solve_problem(net_alpha, hard, risk, ceiling, solver, estimate)
        candidate = _solution_value(weights, status)
        if candidate is None:
            if best is None:
                return _ContinuousSolution(None, solver, status)
            break
        numerator = float(
            inputs.alpha @ (candidate - inputs.benchmark)
            - constraint.turnover_lambda * np.abs(candidate - inputs.prior).sum()
        )
        risk_value = inputs.model.tracking_error(candidate - inputs.benchmark)
        if risk_value <= 1e-10 or numerator <= 1e-10:
            break
        ratio = numerator / risk_value
        if ratio <= estimate + 1e-7 * max(1, estimate):
            break
        estimate = ratio
        best = _ContinuousSolution(candidate, solver, status)
    if best is not None:
        return best

    # With no positive numerator the information-ratio transformation lacks
    # a positive-root solution. Use the registered variance-penalized utility.
    status = _solve_problem(objective, hard, risk, ceiling, solver)
    return _ContinuousSolution(_solution_value(weights, status), solver, status)


def _solution_value(weights: cp.Variable, status: str) -> np.ndarray | None:
    if status not in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE) or weights.value is None:
        return None
    candidate = np.maximum(np.asarray(weights.value).reshape(-1), 0)
    if not np.isfinite(candidate).all() or candidate.sum() <= 0:
        return None
    return candidate / candidate.sum()


def _continuous_solve(inputs: _Portfolio, turnover: float, style_limits: np.ndarray) -> _ContinuousSolution:
    result = _ContinuousSolution(None, "CLARABEL", "solver_error")
    for solver in ("CLARABEL", "OSQP"):
        if solver == "OSQP" and inputs.constraints.objective == OptObjective.RISK_PARITY:
            break  # The constrained risk-budgeting objective has a logarithm.
        try:
            result = _solve_with(inputs, turnover, style_limits, solver)
        except cp.error.SolverError:
            result = _ContinuousSolution(None, solver, "solver_error")
        if result.status == cp.INFEASIBLE:
            return result
        if (
            result.weights is not None
            and max(
                inputs.violations(
                    result.weights,
                    turnover,
                    style_limits,
                ).values()
            )
            <= _FEASIBILITY_TOLERANCE
        ):
            return result
    return _ContinuousSolution(None, result.solver, result.status)


def _portfolio_nav(latest: pl.DataFrame) -> float | None:
    if "portfolio_nav" not in latest.columns:
        return None
    values = latest.get_column("portfolio_nav").drop_nulls().unique().to_list()
    if not values:
        return None
    if len(values) != 1 or _positive(values[0]) is None:
        raise ValueError("Latest portfolio_nav must be one consistent positive value")
    return float(values[0])


def _estimated_cost(
    model: BarraRiskModel,
    delta: np.ndarray,
    nav: float | None,
) -> tuple[float, np.ndarray]:
    from quant_core.backtest.microstructure import fee_rates
    from quant_core.schemas import MicrostructureConfig

    impact = np.zeros(len(delta))
    if nav is not None:
        adv_notional = (
            model.latest.select((pl.col("close").cast(pl.Float64) * pl.col("_barra_adv")).alias("adv"))
            .to_series()
            .to_numpy()
        )
        volatility = model.latest.get_column("_barra_total_vol").to_numpy()
        valid = np.isfinite(adv_notional) & (adv_notional > 0) & np.isfinite(volatility)
        impact[valid] = 0.45 * volatility[valid] * np.sqrt(np.abs(delta[valid]) * nav / adv_notional[valid])
    config = MicrostructureConfig()
    boards = (
        model.latest.get_column("board").to_list() if "board" in model.latest.columns else [None] * len(delta)
    )
    fees = sum(
        abs(change) * sum(fee_rates(board or "MAIN", "BUY" if change >= 0 else "SELL", config))
        for board, change in zip(boards, delta, strict=True)
    )
    return float(fees + np.abs(delta) @ impact), impact


def _round_to_lots(
    inputs: _Portfolio,
    weights: np.ndarray,
    nav: float,
    turnover: float,
    style_limits: np.ndarray,
) -> tuple[np.ndarray | None, float, np.ndarray | None, str]:
    """Round the actual order deltas and recheck the post-fee stock and cash book."""
    from quant_core.backtest.microstructure import round_order_quantity

    rows = inputs.model.latest.to_dicts()
    prices = np.asarray([(_positive(row["close"]) or 0) for row in rows])
    if np.any(prices <= 0):
        return None, 0, None, "Lot audit lacks a valid reference price"
    boards = [row.get("board") or "MAIN" for row in rows]
    implied = inputs.prior * nav / prices
    held = np.array(
        [
            int(row["position_shares"]) if row.get("position_shares") is not None else round(implied[index])
            for index, row in enumerate(rows)
        ],
        dtype=np.int64,
    )
    if np.any(held < 0) or np.any(np.abs(held - implied) > 0.01):
        return None, 0, None, "Lot audit cannot reconcile current shares, NAV, and weights"

    target = held.copy()
    for index, change in enumerate(weights * nav / prices - held):
        side = "BUY" if change >= 0 else "SELL"
        requested = abs(float(change))
        if abs(requested - round(requested)) <= 1e-6:
            requested = float(round(requested))
        quantity = round_order_quantity(requested, boards[index], side, position=int(held[index]))
        target[index] += quantity if side == "BUY" else -quantity

    def cash_and_cost() -> tuple[float, float]:
        cost, _ = _estimated_cost(inputs.model, (target - held) * prices / nav, nav)
        cash = nav - float(prices @ target) - cost * nav
        return cash, cost

    cash, cost = cash_and_cost()
    for index in np.argsort(inputs.alpha, kind="stable"):
        if cash >= -1e-8:
            break
        buy_quantity = int(target[index] - held[index])
        if buy_quantity <= 0:
            continue
        permitted = round_order_quantity(
            max(buy_quantity - (-cash / prices[index]) - 1e-6, 0.0),
            boards[index],
            "BUY",
        )
        target[index] = held[index] + permitted
        cash, cost = cash_and_cost()
    if cash < -1e-8 or cost >= 1:
        return None, 0, None, "Lot audit found insufficient post-fee cash"
    post_trade_nav = nav * (1 - cost)
    cash_weight = max(cash, 0.0) / post_trade_nav
    rounded = prices * target / post_trade_nav
    traded = prices * target / nav
    violations = inputs.violations(rounded, turnover, style_limits, cash_weight, traded)
    failed = [
        f"{name} ({amount:.8g})" for name, amount in violations.items() if amount > _FEASIBILITY_TOLERANCE
    ]
    if failed:
        return None, 0, None, f"Lot audit blocked release because of {', '.join(failed)}"
    return rounded, cash_weight, traded, f"Lot audit passed with {cash_weight:.6%} post-fee cash"


def _cvxpy_snippet(
    constraints: OptConstraints,
    turnover: float,
    style_limits: np.ndarray,
) -> str:
    return f'''import cvxpy as cp
import numpy as np

def solve(alpha, w0, benchmark, X, D, factor_root, specific_vol, lower, upper):
    """Aligned NumPy arrays. X/D are N×K exposures. Risk and alpha are annualized."""
    n = len(alpha)
    x = cp.Variable(n)
    objective_name = {constraints.objective.value!r}
    penalty = {constraints.turnover_lambda!r}
    parity = objective_name == "risk_parity"
    scale = cp.sum(x) if parity else 1
    a = x - benchmark * scale
    dw = x - w0 * scale
    risk = cp.hstack([factor_root @ a, cp.multiply(specific_vol, a)])
    te = cp.norm(risk)
    hard = [
        x >= lower * scale, x <= upper * scale,
        cp.abs(D.T @ a) <= {constraints.max_industry_dev!r} * scale,
        cp.abs(X.T @ a) <= np.array({style_limits.tolist()!r}) * scale,
        cp.norm1(dw) <= {2 * turnover!r} * scale,
        te <= {constraints.target_te!r} * scale,
    ]
    if not parity:
        hard.append(cp.sum(x) == 1)

    def run(objective):
        p = cp.Problem(cp.Maximize(objective), hard)
        p.solve(solver="CLARABEL", tol_gap_abs=1e-10, tol_feas=1e-10, tol_gap_rel=1e-9)
        if p.status not in ("optimal", "optimal_inaccurate"):
            return None
        result = np.maximum(x.value, 0)
        return result / result.sum()

    net = alpha @ a - penalty * cp.norm1(dw)
    utility = net - cp.sum_squares(risk)
    if objective_name == "min_var":
        return run(-cp.sum_squares(risk))
    if parity:
        absolute_risk = cp.hstack([factor_root @ x, cp.multiply(specific_vol, x)])
        members = upper > 0
        hard.append(x[members] >= 1e-10)
        return run(-0.5 * cp.sum_squares(absolute_risk) + cp.sum(cp.log(x[members])) / members.sum())
    if objective_name == "max_sharpe":
        estimate, best = 0.0, None
        for _ in range(30):
            candidate = run(net - estimate * te)
            if candidate is None:
                return best
            active = candidate - benchmark
            excess = alpha @ active - penalty * np.abs(candidate - w0).sum()
            risk_value = np.linalg.norm(np.r_[factor_root @ active, specific_vol * active])
            if risk_value <= 1e-10 or excess <= 1e-10:
                break
            ratio = excess / risk_value
            if ratio <= estimate + 1e-7 * max(1.0, estimate):
                break
            estimate, best = ratio, candidate
        if best is not None:
            return best
    return run(utility)
'''


def _portfolio_invariants(
    inputs: _Portfolio,
    target: np.ndarray | None,
    cash: float,
    traded: np.ndarray | None,
    reported_te: float,
    effective_violation: float,
) -> list[dict[str, Any]]:
    model = inputs.model
    covariance = model.factor_covariance
    symmetry = float(np.max(np.abs(covariance - covariance.T)))
    factor_min = float(np.linalg.eigvalsh((covariance + covariance.T) / 2).min())
    specific_min = float(model.specific_variance.min())
    design = model.design
    # For F PSD, X F X' is PSD. A negative factor eigenvalue supplies a
    # conservative asset-covariance bound via the squared design operator norm.
    asset_psd_floor = specific_min
    if factor_min < 0:
        asset_psd_floor += factor_min * float(np.linalg.norm(design, ord=2) ** 2)
    psd_tolerance = 1e-10
    min_eigen_bound = min(factor_min, specific_min, asset_psd_floor)
    covariance_check = InvariantCheckItem(
        key="cov_psd",
        label="Covariance PSD",
        value=f"{asset_psd_floor:.12g}",
        tolerance=f">= {-psd_tolerance:g}",
        passed=bool(min_eigen_bound >= -psd_tolerance and symmetry <= psd_tolerance),
        detail=(
            f"Asset covariance eigenvalue lower bound from X F X' + diag(s). "
            f"Factor minimum {factor_min:.12g}, specific minimum {specific_min:.12g}, "
            f"factor symmetry residual {symmetry:.12g}."
        ),
    )
    checks = [
        ("weight_simplex", "Weight simplex", 1e-6),
        ("industry_net_zero", "Industry active weights plus cash close to zero", 1e-6),
        ("constraint_violation", "Requested constraint violation", _FEASIBILITY_TOLERANCE),
        ("te_recompute", "Tracking error recomputation", 1e-10),
    ]
    if target is None:
        output = [
            InvariantCheckItem(
                key=key,
                label=label,
                value="unavailable",
                tolerance=f"<= {tol:g}",
                passed=False,
                detail="The solver did not produce a releasable target.",
            )
            for key, label, tol in checks
        ]
    else:
        active = target - inputs.benchmark
        industry_active = model.industry_exposures.T @ active
        membership_error = np.max(np.abs(model.industry_exposures.sum(axis=1) - 1))
        requested_violation = max(
            inputs.violations(
                target,
                inputs.constraints.max_turnover_oneway,
                _style_limits(inputs.constraints),
                cash,
                traded,
            ).values()
        )
        factor_active = design.T @ active
        recomputed_te = np.sqrt(
            max(float(factor_active @ covariance @ factor_active + model.specific_variance @ active**2), 0.0)
        )
        evidence = [
            (
                max(abs(target.sum() + cash - 1), max(-float(target.min()), -cash, 0)),
                f"Stock and cash weight total {target.sum() + cash:.12g}, cash weight {cash:.12g}.",
            ),
            (
                max(abs(industry_active.sum() + cash), membership_error),
                "Each listing belongs to one industry. Cash is included in active weight totals.",
            ),
            (requested_violation, f"Effective constraint residual {effective_violation:.12g}."),
            (
                abs(recomputed_te - reported_te),
                f"Recomputed TE {recomputed_te:.12g}, reported {reported_te:.12g}.",
            ),
        ]
        output = [
            InvariantCheckItem(
                key=key,
                label=label,
                value=f"{value:.12g}",
                tolerance=f"<= {tol:g}",
                passed=bool(value <= tol),
                detail=detail,
            )
            for (key, label, tol), (value, detail) in zip(checks, evidence, strict=True)
        ]
    return [item.model_dump() for item in [*output, covariance_check]]


def optimize_portfolio(
    bars_df: pl.DataFrame,
    alpha_scores: dict[str, float],
    prev_weights: dict[str, float],
    bench_weights: dict[str, float],
    req: PortfolioOptRequest,
) -> PortfolioOptResult:
    """Solve with fixed hard trading constraints and ordered turnover/style relaxation."""
    start = time.perf_counter()
    c = req.constraints
    limits = _style_limits(c)
    if any(
        not np.isfinite(value) or value < 0
        for value in (
            *limits,
            c.max_stock_weight,
            c.max_industry_dev,
            c.turnover_lambda,
            c.max_turnover_oneway,
            c.target_te,
            c.min_benchmark_weight_sum,
        )
    ):
        raise ValueError("Optimization limits must be finite and nonnegative")
    _validate_weights(prev_weights, "Previous weights")
    _validate_weights(bench_weights, "Benchmark weights")
    model = fit_barra_risk_model(bars_df, bench_weights)
    alpha = _weight_vector(alpha_scores, model.symbols)
    prior = _weight_vector(prev_weights, model.symbols)
    benchmark = _weight_vector(bench_weights, model.symbols)
    benchmark_mass = float(benchmark.sum())
    if benchmark_mass > 0:
        benchmark /= benchmark_mass
    nav = _portfolio_nav(model.latest)
    lower, upper, trading_statuses = _trade_bounds(model, prior, c.max_stock_weight, nav)
    inputs = _Portfolio(model, alpha, prior, benchmark, lower, upper, c)
    identity = hashlib.sha256(
        json.dumps(
            [
                req.model_dump(mode="json"),
                model.as_of,
                model.symbols,
                alpha.tolist(),
                prior.tolist(),
                benchmark.tolist(),
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    for array in (model.design, model.factor_covariance, model.specific_variance, lower, upper):
        identity.update(array.astype("<f8").tobytes())
    run_id = f"OPT-{identity.hexdigest()[:20]}"

    turnover = c.max_turnover_oneway
    notes = []
    solution = _ContinuousSolution(None, "CLARABEL", cp.INFEASIBLE)
    missing_previous = max(sum(prev_weights.values()) - prior.sum(), 0)
    if missing_previous > 1e-10:
        notes.append(f"Cannot price or release {missing_previous:.4%} of prior holdings")
    elif benchmark_mass + 1e-10 < c.min_benchmark_weight_sum or benchmark_mass <= 0:
        notes.append(f"Matched benchmark weight {benchmark_mass:.4%} is below required coverage")
    elif upper.sum() < 1 - _FEASIBILITY_TOLERANCE or lower.sum() > 1 + _FEASIBILITY_TOLERANCE:
        notes.append("Hard stock caps and trading bounds cannot fund a fully invested long-only portfolio")
    elif np.any(lower > upper + _FEASIBILITY_TOLERANCE):
        notes.append("A hard concentration limit conflicts with a frozen/sell-locked holding")
    else:
        solution = _continuous_solve(inputs, turnover, limits)
        if solution.weights is None and solution.status in (cp.INFEASIBLE, cp.INFEASIBLE_INACCURATE):
            if turnover < 1:
                notes.append(f"Relaxed one-way turnover limit from {turnover:.4%} to 100.0000%")
                turnover = 1.0
                solution = _continuous_solve(inputs, turnover, limits)
            if solution.weights is None and solution.status in (cp.INFEASIBLE, cp.INFEASIBLE_INACCURATE):
                feasible_range = np.max(
                    np.abs(model.style_exposures - benchmark @ model.style_exposures),
                    axis=0,
                )
                widened = np.maximum(limits, feasible_range + _FEASIBILITY_TOLERANCE)
                notes.append(f"Relaxed active style bounds from {limits.tolist()} to {widened.tolist()}")
                limits = widened
                solution = _continuous_solve(inputs, turnover, limits)

    relaxed = bool(notes)
    target = solution.weights
    cash = 0.0
    traded = None
    if target is not None and nav is not None:
        target, cash, traded, audit = _round_to_lots(inputs, target, nav, turnover, limits)
        notes.append(audit)
    elif target is not None:
        notes.append(
            "Continuous target weights. Lot audit requires portfolio_nav and reconciled current shares."
        )
    if target is None and (not notes or (relaxed and solution.weights is None and len(notes) > 1)):
        notes.append(f"{solution.solver} returned {solution.status} with the hard constraints")

    statuses = {"optimal", "optimal_inaccurate"}
    solve_status = "infeasible"
    if target is not None and solution.status in statuses:
        solve_status = "relaxed_optimal" if relaxed else solution.status

    evaluated = target if target is not None else prior
    active = evaluated - benchmark
    styles, industries = _risk_report(model, prior - benchmark, active, c, limits)
    variance = model.tracking_error(active) if target is not None else 0.0
    delta = (traded if traded is not None else evaluated) - prior
    excess = float(alpha @ active) if target is not None else 0.0
    cost, impacts = _estimated_cost(model, delta, nav)
    one_way = float(np.abs(delta).sum() / 2)
    holdings = []
    if target is not None:
        rows = model.latest.to_dicts()
        ranked = sorted(range(len(target)), key=lambda index: (-target[index], model.symbols[index]))
        for index in ranked:
            if target[index] <= 1e-9:
                continue
            row = rows[index]
            holdings.append(
                TargetWeightItem(
                    symbol=model.symbols[index],
                    name=row.get("name") or model.symbols[index],
                    industry=row["industry_l1"],
                    target_weight_pct=float(target[index]) * 100,
                    bench_weight_pct=float(benchmark[index]) * 100,
                    active_weight_pct=float(active[index]) * 100,
                    est_impact_bps=float(impacts[index]) * 10_000,
                    tradable_status=trading_statuses[index],
                )
            )

    elapsed_ms = (time.perf_counter() - start) * 1000
    max_violation = float(max(inputs.violations(evaluated, turnover, limits, cash, traded).values()))
    invariants = _portfolio_invariants(inputs, target, cash, traded, variance, max_violation)
    capacity = None
    if target is not None:
        latest_ids = (
            model.latest["listing_id"].to_list() if "listing_id" in model.latest.columns else model.symbols
        )
        frontier = _analyze_capacity(
            bars_df,
            CapacityAnalysisRequest(
                experiment_id=req.experiment_id,
                universe_id=req.universe_id,
                snapshot_id=req.snapshot_id,
            ),
            signed_weights={
                listing or symbol: float(change)
                for listing, symbol, change in zip(latest_ids, model.symbols, delta, strict=True)
            },
            gross_excess_ann_pct=excess * 100,
            active_vol_ann=variance,
            current_aum_cny=nav if nav is not None else 1e8,
        )
        capacity = frontier.model_dump(mode="json", exclude={"illiquid_names", "artifact_uri"})
    return PortfolioOptResult(
        opt_run_id=run_id,
        experiment_id=req.experiment_id,
        universe_id=req.universe_id,
        snapshot_id=req.snapshot_id or "unspecified",
        solver=solution.solver,
        solve_status=solve_status,
        solve_ms=elapsed_ms,
        relaxation_notes=notes,
        expected_gross_excess_pct=excess * 100,
        expected_cost_pct=cost * 100,
        expected_net_excess_pct=(excess - cost) * 100,
        predicted_te_pct=variance * 100,
        expected_ir=(excess - cost) / variance if variance else 0.0,
        oneway_turnover_pct=one_way * 100,
        annualized_turnover=one_way * 52,
        effective_n_stocks=len(holdings),
        max_constraint_violation=max_violation,
        style_exposures=styles,
        industry_deviations=industries,
        top_holdings=holdings,
        cvxpy_snippet=_cvxpy_snippet(c, turnover, limits),
        invariants=invariants,
        capacity_frontier=capacity,
    )
