"""Historical daily-bar capacity scenarios for research portfolios."""

from __future__ import annotations

from collections.abc import Callable
from itertools import pairwise
from math import exp, isfinite, log, sqrt

import numpy as np
import polars as pl

from ..schemas import (
    CapacityAnalysisRequest,
    CapacityFrontierResult,
    CapacityPoint,
    Market,
    MicrostructureConfig,
)

_TRADING_DAYS = 252
_WEEKLY_REBALANCES = 52


def _finite_column(bars: pl.DataFrame, name: str) -> pl.Expr:
    if name not in bars.columns:
        return pl.lit(None, dtype=pl.Float64)
    values = pl.col(name).cast(pl.Float64, strict=False)
    return pl.when(values.is_finite()).then(values)


def _positive(values: pl.Expr) -> pl.Expr:
    return pl.when(values > 0).then(values)


def _liquidity_history(bars: pl.DataFrame) -> pl.DataFrame:
    symbol = pl.col("symbol") if "symbol" in bars.columns else pl.col("listing_id")
    listing = pl.coalesce("listing_id", symbol) if "listing_id" in bars.columns else symbol
    suspended = pl.col("is_suspended").fill_null(False) if "is_suspended" in bars.columns else pl.lit(False)
    cny = pl.col("currency") == "CNY" if "currency" in bars.columns else pl.lit(True)
    if "market" in bars.columns:
        cny &= (pl.col("market").cast(pl.String) != Market.US_EQ.value).fill_null(True)
    close = _positive(_finite_column(bars, "close"))
    volume = _finite_column(bars, "volume_shares")
    amount = _finite_column(bars, "amount_ccy")
    history = bars.select(
        pl.col("trade_date").cast(pl.Date),
        listing.cast(pl.String).alias("listing_id"),
        symbol.cast(pl.String).alias("symbol"),
        (pl.col("board").cast(pl.String) if "board" in bars.columns else pl.lit("MAIN")).alias("board"),
        pl.coalesce(
            _positive(_finite_column(bars, "open")), _positive(_finite_column(bars, "pre_close"))
        ).alias("reference"),
        close.alias("close"),
        (close * _positive(_finite_column(bars, "adj_factor"))).alias("adjusted"),
        _positive(_finite_column(bars, "pre_close")).alias("pre_close"),
        pl.when(cny.fill_null(True) & ~suspended)
        .then(
            pl.coalesce(
                pl.when(volume >= 0).then(volume),
                pl.when(amount >= 0).then(
                    amount / pl.coalesce(_positive(_finite_column(bars, "vwap")), close)
                ),
            )
        )
        .when(cny.fill_null(True) & suspended)
        .then(0.0)
        .alias("volume"),
        _finite_column(bars, "benchmark_return").alias("benchmark_return"),
    ).sort("listing_id", "trade_date")
    if history.select(pl.any_horizontal(pl.col("listing_id", "trade_date").is_null()).any()).item():
        raise ValueError("capacity analysis requires a listing ID and date on every bar")
    if history.select(pl.struct("listing_id", "trade_date").is_duplicated().any()).item():
        raise ValueError("capacity analysis received duplicate daily bars")
    previous_close = pl.col("close").shift(1).over("listing_id")
    previous_adjusted = pl.col("adjusted").shift(1).over("listing_id")
    daily = pl.coalesce(
        pl.col("adjusted") / previous_adjusted - 1,
        pl.col("close") / pl.col("pre_close") - 1,
        pl.col("close") / previous_close - 1,
    )
    return history.with_columns(
        pl.coalesce("reference", previous_close).alias("reference"),
        pl.when(daily.is_finite()).then(daily).alias("daily_return"),
    ).with_columns(
        pl.col("volume").rolling_mean(20, min_samples=1).shift(1).over("listing_id").alias("adv"),
        pl.col("daily_return").rolling_std(20, min_samples=2).shift(1).over("listing_id").alias("vol"),
    )


def _matched_weights(history: pl.DataFrame, weights: dict[str, float]) -> dict[str, float]:
    listing_ids = set(history["listing_id"].unique())
    symbols: dict[str, set[str]] = {}
    for listing, symbol in history.select("listing_id", "symbol").unique().iter_rows():
        symbols.setdefault(symbol, set()).add(listing)
    matched: dict[str, float] = {}
    for name, weight in sorted(weights.items()):
        aliases = symbols.get(name, set())
        listing = next(iter(aliases)) if name not in listing_ids and len(aliases) == 1 else name
        matched[listing] = matched.get(listing, 0.0) + weight
    return matched


def _portfolio_inputs(history: pl.DataFrame, weights: dict[str, float]) -> tuple[float, float]:
    """Estimate a frozen-weight reference return and active volatility."""
    if not weights or history.is_empty():
        return 0.0, 0.0
    weight_panel = pl.DataFrame({"listing_id": list(weights), "weight": list(weights.values())})
    by_day = (
        history.join(weight_panel, on="listing_id", how="left")
        .group_by("trade_date")
        .agg(
            (pl.col("daily_return") * pl.col("weight")).sum().alias("portfolio"),
            pl.coalesce(pl.col("benchmark_return").mean(), pl.col("daily_return").mean(), pl.lit(0.0)).alias(
                "benchmark"
            ),
        )
        .sort("trade_date")
    )
    portfolio, benchmark = by_day["portfolio"].to_numpy(), by_day["benchmark"].to_numpy()
    active = portfolio - benchmark
    volatility = float(active.std(ddof=1) * sqrt(_TRADING_DAYS)) if len(active) > 1 else 0.0
    viable = (portfolio > -1) & (benchmark > -1)
    if not viable.all():
        return 0.0, volatility
    compounded = np.mean(np.log1p(portfolio) - np.log1p(benchmark)) * _TRADING_DAYS
    return float(np.expm1(compounded) * 100), volatility


def _capacity_root(
    points: list[tuple[float, float]],
    level: float,
    estimate: Callable[[float], float],
) -> float | None:
    for (low, low_value), (high, high_value) in pairwise(points):
        if low_value > level >= high_value:
            for _ in range(12):
                if high / low <= 1.05:
                    break
                mid = exp((log(low) + log(high)) / 2)
                mid_value = estimate(mid)
                if mid_value <= level:
                    high, high_value = mid, mid_value
                else:
                    low, low_value = mid, mid_value
            fraction = (low_value - level) / (low_value - high_value)
            return exp(log(low) + fraction * (log(high) - log(low)))
    return None


def _analyze_capacity(
    bars: pl.DataFrame,
    req: CapacityAnalysisRequest,
    *,
    target_weights: dict[str, float] | None = None,
    gross_excess_ann_pct: float | None = None,
    oneway_turnover_pct: float | None = None,
    signed_weights: dict[str, float] | None = None,
    orders: pl.DataFrame | None = None,
    annual_rebalances: float = _WEEKLY_REBALANCES,
    active_vol_ann: float | None = None,
    current_aum_cny: float = 1e8,
    config: MicrostructureConfig | None = None,
) -> CapacityFrontierResult:
    from ..backtest.microstructure import fee_rates

    if (
        not req.aum_grid_cny
        or any(not isfinite(aum) or aum <= 0 for aum in req.aum_grid_cny)
        or not isfinite(req.impact_coeff_y)
        or req.impact_coeff_y < 0
        or not 0 <= req.max_pov <= 1
    ):
        raise ValueError("capacity analysis requires a positive AUM grid and valid impact/PoV limits")
    if oneway_turnover_pct is not None and (not isfinite(oneway_turnover_pct) or oneway_turnover_pct < 0):
        raise ValueError("one-way turnover must be finite and nonnegative")
    if gross_excess_ann_pct is not None and not isfinite(gross_excess_ann_pct):
        raise ValueError("gross excess must be finite")
    if target_weights is not None and any(
        not isfinite(weight) or weight < 0 for weight in target_weights.values()
    ):
        raise ValueError("target weights must be finite and nonnegative")
    if target_weights and sum(target_weights.values()) > 1 + 1e-6:
        raise ValueError("long-only target weights may sum to at most 1")

    result = CapacityFrontierResult(
        experiment_id=req.experiment_id,
        universe_id=req.universe_id,
        snapshot_id=req.snapshot_id or "unspecified",
        current_aum_cny=current_aum_cny,
    )
    if bars.is_empty():
        result.binding_bottleneck = "liquidity_data_unavailable"
        return result
    if "trade_date" not in bars.columns or not {"symbol", "listing_id"}.intersection(bars.columns):
        raise ValueError("capacity analysis requires trade_date and a security identifier")
    history = _liquidity_history(bars)
    sessions = history.select("trade_date").unique().sort("trade_date")
    if target_weights is None and signed_weights is None and orders is None:
        latest = history.filter(pl.col("trade_date") == sessions.item(-1, 0))["listing_id"].to_list()
        target_weights = dict.fromkeys(latest, 1.0 / len(latest))
    weights = _matched_weights(history, target_weights or {})
    if gross_excess_ann_pct is None or active_vol_ann is None:
        reference_gross, reference_vol = _portfolio_inputs(history, weights)
        if gross_excess_ann_pct is None:
            gross_excess_ann_pct = reference_gross
        if active_vol_ann is None:
            active_vol_ann = reference_vol

    if orders is None:
        if signed_weights is None:
            turnover = 15.0 if oneway_turnover_pct is None else oneway_turnover_pct
            # With no order sides, model an equal mixture of buy and sell notionals.
            mass = sum(weights.values())
            changes = {name: weight / mass * turnover / 50 for name, weight in weights.items() if weight}
            sides = dict.fromkeys(changes, "BOTH")
        else:
            changes = _matched_weights(
                history, {name: weight for name, weight in signed_weights.items() if weight}
            )
            sides = {name: "BUY" if change > 0 else "SELL" for name, change in changes.items()}
        orders = (
            pl.DataFrame(
                {
                    "listing_id": list(changes),
                    "order_weight": [abs(value) for value in changes.values()],
                    "side": [sides[name] for name in changes],
                },
                schema={"listing_id": pl.String, "order_weight": pl.Float64, "side": pl.String},
            )
            .join(sessions, how="cross")
            .with_columns(pl.lit(False).alias("blocked"))
        )
    sample = (
        orders.filter(pl.col("order_weight") > 0)
        .join(
            history.select(
                "trade_date", "listing_id", "symbol", "board", "reference", "volume", "adv", "vol"
            ),
            on=["trade_date", "listing_id"],
            how="left",
        )
        .sort("trade_date", "listing_id")
    )
    order_weight = sample["order_weight"].to_numpy()
    order_mass = float(order_weight.sum())
    days = sample.get_column("trade_date").n_unique() or 1
    volume, reference, adv, vol = (sample[col].to_numpy() for col in ("volume", "reference", "adv", "vol"))
    valid = (
        np.isfinite(volume)
        & (volume >= 0)
        & np.isfinite(reference)
        & (reference > 0)
        & np.isfinite(adv)
        & (adv > 0)
        & np.isfinite(vol)
        & (vol >= 0)
    )
    blocked = sample["blocked"].to_numpy()
    eligible = valid & ~blocked
    cash_cap = np.zeros(len(sample))
    cash_cap[eligible] = req.max_pov * reference[eligible] * volume[eligible]
    impact_per_root = np.zeros(len(sample))
    impact_per_root[valid] = (
        req.impact_coeff_y * vol[valid] * np.sqrt(order_weight[valid] / (reference[valid] * adv[valid]))
    )
    config = config or MicrostructureConfig()
    rates = {
        (board, side): sum(fee_rates(board or "MAIN", side, config))
        for board in set(sample["board"])
        for side in ("BUY", "SELL")
    }
    cost_rates = np.array(
        [
            (rates[board, "BUY"] + rates[board, "SELL"]) / 2 if side == "BOTH" else rates[board, side]
            for board, side in sample.select("board", "side").iter_rows()
        ]
    )

    def components(aum: float) -> tuple[np.ndarray, float, float, float, float]:
        fills = np.minimum(order_weight, cash_cap / aum)
        impact = float(fills @ impact_per_root * sqrt(aum) / days)
        explicit = float(fills @ cost_rates / days)
        fill_rate = float(fills.sum() / order_mass) if order_mass else 1.0
        # Approximate foregone alpha in proportion to unfilled target notional.
        net = gross_excess_ann_pct * fill_rate - (impact + explicit) * annual_rebalances * 100
        return fills, impact, explicit, fill_rate, net

    def estimate(aum: float) -> CapacityPoint:
        fills, impact, explicit, fill_rate, net = components(aum)
        participation = np.divide(
            fills * aum * req.max_pov, cash_cap, out=np.zeros_like(fills), where=cash_cap > 0
        )
        return CapacityPoint(
            aum_cny=aum,
            gross_excess_pct=gross_excess_ann_pct,
            net_excess_pct=net,
            ir_ann=net / (active_vol_ann * 100) if active_vol_ann > 1e-12 else 0.0,
            impact_bps=impact * 10_000,
            explicit_cost_bps=explicit * 10_000,
            pov_pct=float(np.quantile(participation[fills > 0], 0.95) * 100) if fills.any() else 0.0,
            fill_rate_pct=fill_rate * 100,
        )

    result.points = [estimate(aum) for aum in sorted(set(req.aum_grid_cny))]
    first = result.points[0]
    can_cross = first.net_excess_pct > 0 and (bool(valid.any()) or order_mass == 0)
    if can_cross:
        net_curve = [(point.aum_cny, point.net_excess_pct) for point in result.points]
        result.half_alpha_aum_cny = _capacity_root(
            net_curve, first.net_excess_pct / 2, lambda aum: components(aum)[-1]
        )
        result.breakeven_aum_cny = _capacity_root(net_curve, 0.0, lambda aum: components(aum)[-1])
    fill_curve = [(point.aum_cny, point.fill_rate_pct) for point in result.points]
    pov_limit = (
        first.aum_cny
        if first.fill_rate_pct <= 95
        else _capacity_root(
            fill_curve,
            95.0,
            lambda aum: float(np.minimum(order_weight, cash_cap / aum).sum() / order_mass * 100),
        )
    )
    cost_limit = result.half_alpha_aum_cny
    if order_mass > 0 and float(order_weight[~valid].sum()) > 0.05 * order_mass:
        result.binding_bottleneck = "liquidity_data_unavailable"
    elif order_mass > 0 and float(order_weight[blocked].sum()) > 0.05 * order_mass:
        result.binding_bottleneck = "trading_restriction"
    elif pov_limit is not None and (cost_limit is None or pov_limit <= cost_limit):
        result.binding_bottleneck = "pov_limit"
    elif cost_limit is not None or (gross_excess_ann_pct > 0 and first.net_excess_pct <= 0):
        constrained = estimate(cost_limit) if cost_limit is not None else first
        result.binding_bottleneck = (
            "impact" if constrained.impact_bps >= constrained.explicit_cost_bps else "explicit_cost"
        )
    elif first.net_excess_pct <= 0:
        result.binding_bottleneck = "baseline_non_positive"

    if order_mass > 0:
        capacity = cash_cap / current_aum_cny
        constrained_names = (
            sample.with_columns(
                pl.Series("missing_liquidity", ~valid),
                pl.Series("unfilled", np.maximum(order_weight - capacity, 0)),
                pl.Series("adv_cny", np.where(valid, reference * adv, np.nan), nan_to_null=True),
            )
            .group_by("listing_id")
            .agg(
                pl.col("symbol").drop_nulls().last(),
                pl.col("order_weight").sum(),
                pl.col("unfilled").sum(),
                pl.col("adv_cny").median(),
                pl.col("missing_liquidity").mean(),
            )
            .with_columns(
                (100 * pl.col("unfilled") / pl.col("order_weight")).alias("unfilled_pct"),
                (100 * pl.col("missing_liquidity")).alias("missing_liquidity_pct"),
            )
            .sort(["unfilled", "adv_cny", "listing_id"], descending=[True, False, False], nulls_last=True)
            .head(10)
            .select(
                "listing_id",
                pl.coalesce("symbol", "listing_id").alias("symbol"),
                "adv_cny",
                "unfilled_pct",
                "missing_liquidity_pct",
            )
        )
        result.illiquid_names = constrained_names.to_dicts()
    return result


def analyze_capacity_frontier(
    bars_df: pl.DataFrame,
    req: CapacityAnalysisRequest,
    *,
    target_weights: dict[str, float] | None = None,
    gross_excess_ann_pct: float | None = None,
    oneway_turnover_pct: float | None = None,
) -> CapacityFrontierResult:
    """Estimate scale sensitivity over daily bars using frozen representative orders.

    Weights are fractions and the supplied turnover is a percentage per rebalance.
    In the absence of a turnover input, the weekly scenario uses 15%. Liquidity
    forecasts use the preceding 20 listing sessions. The scenario caps fills at
    observed daily participation and charges impact on filled notional using the
    intended parent order size. Cost bps divide by portfolio AUM per rebalance.
    """
    return _analyze_capacity(
        bars_df,
        req,
        target_weights=target_weights,
        gross_excess_ann_pct=gross_excess_ann_pct,
        oneway_turnover_pct=oneway_turnover_pct,
    )
