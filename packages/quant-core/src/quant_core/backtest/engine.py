"""Daily A-share simulation with event replay and vectorized screening.

Targets are close decisions and first become eligible on a later session.
Daily bars support open-reference execution with a realized session volume cap.
Only prior-session volume and returns estimate ADV and volatility.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from itertools import pairwise
from math import ceil, isfinite, sqrt
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from ..risk.barra_opt import BARRA_STYLES, build_barra_exposure_panel, fit_barra_factor_returns
from ..schemas import (
    AttributionResult,
    BacktestEngineMode,
    BacktestRequest,
    BacktestResult,
    BoardType,
    FrictionBreakdown,
    MicrostructureConfig,
    RejectedOrderRecord,
)
from .attribution import barra_decomposition, brinson_fachler, relative_link_weights
from .microstructure import (
    as_date,
    bar_block_reason,
    fee_rates,
    get_price_limit_pct,
    match_order,
    positive_price,
    raw_price_limits,
)

__all__ = ["get_price_limit_pct", "run_backtest"]

_EMPTY_BAR: dict = {}
_CHINA_TIME = ZoneInfo("Asia/Shanghai")


def _prepare_bars(bars: pl.DataFrame) -> pl.DataFrame:
    if not {"trade_date", "close"}.issubset(bars.columns) or not {"symbol", "listing_id"}.intersection(
        bars.columns
    ):
        raise ValueError("bars require trade_date, close and a security identifier")
    defaults = {
        "open": None,
        "pre_close": None,
        "volume_shares": None,
        "adj_factor": None,
        "industry_l1": "UNKNOWN",
        "float_market_cap": None,
    }
    bars = bars.with_columns(
        pl.lit(value).alias(col) for col, value in defaults.items() if col not in bars.columns
    )
    if "symbol" not in bars.columns:
        bars = bars.with_columns(pl.col("listing_id").alias("symbol"))
    listing = (
        pl.col("listing_id").fill_null(pl.col("symbol")) if "listing_id" in bars.columns else pl.col("symbol")
    )
    bars = bars.with_columns(
        pl.col("trade_date").cast(pl.Date),
        listing.cast(pl.String).alias("listing_id"),
        pl.col("close", "open", "pre_close", "volume_shares", "adj_factor", "float_market_cap").cast(
            pl.Float64
        ),
    ).sort("listing_id", "trade_date")
    if bars["listing_id"].null_count() or bars["trade_date"].null_count():
        raise ValueError("bar security identifiers and trade dates cannot be null")
    if bars.select(pl.struct("trade_date", "listing_id").is_duplicated().any()).item():
        raise ValueError("duplicate daily bars for the same listing")
    adjusted = pl.col("close") * pl.col("adj_factor")
    pre_close_returns = pl.col("close") / pl.col("pre_close").replace(0, None) - 1
    raw_lag = pl.col("close").shift(1).over("listing_id").replace(0, None)
    daily = pl.coalesce(
        adjusted / adjusted.shift(1).over("listing_id") - 1,
        pre_close_returns,
        pl.col("close") / raw_lag - 1,
    )
    bars = bars.with_columns(daily.fill_nan(None).alias("_hist_return"))
    return bars.with_columns(
        pl.col("volume_shares")
        .clip(lower_bound=0)
        .rolling_mean(20, min_samples=1)
        .shift(1)
        .over("listing_id")
        .alias("_prior_adv"),
        pl.when(pl.col("_hist_return").is_finite())
        .then(pl.col("_hist_return"))
        .rolling_std(20, min_samples=2)
        .shift(1)
        .over("listing_id")
        .alias("_prior_vol"),
    ).sort("trade_date", "listing_id")


def _identifier(row: dict, lookup: dict[str, set[str]], known_ids: Collection[str]) -> str:
    listing = row.get("listing_id")
    if listing is not None:
        return str(listing)
    symbol = str(row.get("symbol", ""))
    if symbol in known_ids:
        return symbol
    candidates = lookup.get(symbol, set())
    if len(candidates) > 1:
        raise ValueError(f"ambiguous listing for {symbol}; supply listing_id")
    return next(iter(candidates), symbol)


def _weight_date(row: dict) -> date:
    base = row.get("trade_date") or row.get("as_of_date") or row.get("date")
    dates = [as_date(base)] if base is not None else []
    for name in ("decision_at", "available_at"):
        value = row.get(name)
        if value is None:
            continue
        if isinstance(value, str) and len(value) > 10:
            value = datetime.fromisoformat(value)
        if isinstance(value, datetime) and value.utcoffset() is not None:
            value = value.astimezone(_CHINA_TIME)
        dates.append(as_date(value))
    if not dates:
        raise ValueError("weight history requires trade_date or decision_at")
    return max(dates)


def _weight_history(
    frame: pl.DataFrame, weight_col: str, lookup: dict[str, set[str]], known_ids: set[str]
) -> dict[date, dict[str, float]]:
    records: dict[date, dict[str, float]] = defaultdict(dict)
    for row in frame.iter_rows(named=True):
        day = _weight_date(row)
        key = _identifier(row, lookup, known_ids)
        if not key:
            raise ValueError("weight history requires symbol or listing_id")
        if key in records[day]:
            raise ValueError(f"duplicate target/benchmark weight for {key} on {day}")
        weight = float(row[weight_col])
        if weight_col.endswith("_pct"):
            weight /= 100
        if not isfinite(weight) or weight < 0:
            raise ValueError(f"non-finite or negative weight for {key} on {day}")
        records[day][key] = weight
    for day, weights in records.items():
        if sum(weights.values()) > 1 + 1e-8:
            raise ValueError(f"long-only weights exceed one on {day}")
    return dict(records)


def _bench_history(
    bench: pl.DataFrame, lookup: dict[str, set[str]], known_ids: set[str]
) -> tuple[dict[date, float], dict[date, dict[str, float]]]:
    if bench.is_empty():
        return {}, {}
    weight_col = next(
        (c for c in ("benchmark_weight", "bench_weight", "weight", "weight_pct") if c in bench.columns), None
    )
    weights = _weight_history(bench, weight_col, lookup, known_ids) if weight_col else {}
    aliases = ("benchmark_return", "bench_return", "bench_ret")
    if not weights:
        aliases += ("daily_return", "return", "ret")
    return_col = next((c for c in aliases if c in bench.columns), None)
    level_col = next((c for c in ("benchmark_nav", "bench_nav", "nav", "close") if c in bench.columns), None)
    if weights and level_col == "close":
        level_col = None  # Close belongs to the constituent rows.
    if return_col is None and level_col is None:
        if weights:
            return {}, weights
        raise ValueError("benchmark requires daily returns, an index level, or constituent weights")
    values: dict[date, float] = {}
    for row in bench.iter_rows(named=True):
        raw = row.get(return_col or level_col)
        if raw is None:
            continue
        day = as_date(row.get("trade_date") or row.get("as_of_date") or row.get("date"))
        value = float(raw)
        if not isfinite(value) or value <= (-1 if return_col else 0):
            raise ValueError(f"invalid benchmark return/level on {day}")
        if day in values and values[day] != value:
            raise ValueError(f"conflicting benchmark observations on {day}")
        values[day] = value
    if return_col:
        return values, weights
    observations = sorted(values)
    return {day: values[day] / values[previous] - 1 for previous, day in pairwise(observations)} | (
        {observations[0]: 0.0} if observations else {}
    ), weights


def _score_targets(
    frame: pl.DataFrame, lookup: dict[str, set[str]], known_ids: set[str]
) -> dict[date, dict[str, float]]:
    ranked: dict[date, dict[str, float]] = defaultdict(dict)
    for row in frame.iter_rows(named=True):
        day = _weight_date(row)
        daily_scores = ranked[day]
        score = row["score"]
        if score is None or not isfinite(float(score)):
            continue
        key = _identifier(row, lookup, known_ids)
        if not key or key in daily_scores:
            raise ValueError(f"missing or duplicate score identifier on {day}")
        daily_scores[key] = float(score)
    targets = {}
    for day, scores in ranked.items():
        ordered = sorted(scores, key=lambda key: (-scores[key], key))
        count = ceil(len(ordered) / 5)
        targets[day] = dict.fromkeys(ordered[:count], 1 / count) if count else {}
    return targets


def _rebalance_targets(targets: dict[date, dict[str, float]], frequency: str) -> dict[date, dict[str, float]]:
    # Use the first published target per calendar period, consistently for dense and sparse signals.
    if frequency == "daily":
        return targets
    selected = {}
    seen = set()
    for day in sorted(targets):
        period = day.isocalendar()[:2] if frequency == "weekly" else (day.year, day.month)
        if period not in seen:
            selected[day] = targets[day]
            seen.add(period)
    return selected


@dataclass(frozen=True, eq=False)
class _CorporateEvent:
    asset: int
    pay_date: date
    cash_per_share: float
    extra_shares: float


def _corporate_events(
    actions: pl.DataFrame | None,
    sessions: list[date],
    index: dict[str, int],
    lookup: dict[str, set[str]],
) -> tuple[dict[date, list[_CorporateEvent]], dict[date, list[_CorporateEvent]]]:
    ex_events: dict[date, list[_CorporateEvent]] = defaultdict(list)
    record_events: dict[date, list[_CorporateEvent]] = defaultdict(list)
    if actions is None:
        return ex_events, record_events
    seen = {}
    for row in actions.iter_rows(named=True):
        asset = index.get(_identifier(row, lookup, index))
        if asset is None:
            continue
        action_id = row.get("action_id")
        if action_id is not None and action_id in seen:
            if seen[action_id] != row:
                raise ValueError(f"conflicting corporate action {action_id}")
            continue
        if action_id is not None:
            seen[action_id] = row
        ex_date = as_date(row["ex_date"])
        if ex_date not in sessions:
            continue
        pay_date = as_date(row.get("pay_date") or ex_date)
        record_date = as_date(row.get("record_date") or ex_date)
        cash = float(row.get("cash_dividend_per_share") or 0.0)
        split = 1.0 if row.get("split_ratio") is None else float(row["split_ratio"])
        bonus = float(row.get("bonus_share_ratio") or 0.0)
        if not isfinite(cash + split + bonus) or cash < 0 or split <= 0 or bonus < 0 or pay_date < ex_date:
            raise ValueError(f"invalid corporate action for {row.get('symbol', asset)} on {ex_date}")
        event = _CorporateEvent(asset, pay_date, cash, split + bonus - 1)
        ex_events[ex_date].append(event)
        eligible = min(bisect_right(sessions, record_date), bisect_right(sessions, ex_date) - 1) - 1
        if eligible >= 0:
            record_events[sessions[eligible]].append(event)
    return ex_events, record_events


def _rejections(
    day: date,
    assets: list[str],
    bars: list[dict],
    desired: np.ndarray,
    fills: np.ndarray,
    reasons: dict[int, str],
    reference: np.ndarray,
    marks: np.ndarray,
    nav: float,
) -> tuple[list[RejectedOrderRecord], float]:
    records = []
    opportunity = 0.0
    for i, reason in sorted(reasons.items()):
        unfilled = abs(desired[i]) - abs(fills[i])
        if unfilled <= 1e-9:
            continue
        side = "BUY" if desired[i] > 0 else "SELL"
        direction = 1 if side == "BUY" else -1
        missed = direction * unfilled * (marks[i] - reference[i]) if reference[i] > 0 else 0.0
        if reason in {"LIMIT_UP", "LIMIT_DOWN", "SUSPENDED"}:
            opportunity += missed
        records.append(
            RejectedOrderRecord(
                trade_date=day.isoformat(),
                symbol=str(bars[i].get("symbol") or assets[i]),
                name=str(bars[i].get("name") or bars[i].get("symbol") or assets[i]),
                side=side,
                reason=reason,
                loss_bps=missed / nav * 10_000,
            )
        )
    return records, opportunity


def _event_trades(
    bars: list[dict],
    positions: np.ndarray,
    desired: np.ndarray,
    cash: float,
    config: MicrostructureConfig,
) -> tuple[np.ndarray, float, np.ndarray, float, dict[int, str]]:
    filled = np.zeros_like(desired)
    fees = np.zeros(5)
    traded = 0.0
    reasons = {}
    # Day-only orders with stable listing order; sale proceeds become available to buys.
    for i in np.concatenate((np.flatnonzero(desired < 0), np.flatnonzero(desired > 0))):
        side = "BUY" if desired[i] > 0 else "SELL"
        direction = 1 if side == "BUY" else -1
        shares = int(abs(desired[i]) + 1e-9)
        if not shares:
            continue
        position = int(positions[i] + 1e-9)
        fill = match_order(
            bars[i],
            side,
            shares,
            position,
            config,
            position=position,
            available_cash=cash,
            adv_shares=positive_price(bars[i].get("_prior_adv")),
            daily_volatility=positive_price(bars[i].get("_prior_vol")),
        )
        filled[i] = direction * fill.quantity
        nominal = fill.price * fill.quantity
        traded += nominal
        costs = np.array([fill.commission, fill.stamp_duty, fill.transfer_fee, fill.impact_cost, 0.0])
        fees += costs
        cash -= direction * nominal + fill.explicit_fees
        if fill.reason is not None:
            reasons[int(i)] = fill.reason
    return filled, max(cash, 0.0), fees, traded, reasons


def _screening_trades(
    bars: list[dict],
    positions: np.ndarray,
    desired: np.ndarray,
    cash: float,
    config: MicrostructureConfig,
) -> tuple[np.ndarray, float, np.ndarray, float, dict[int, str]]:
    """Vectorized float-share execution; all eligible buys share available cash."""
    filled = np.zeros_like(desired)
    active = np.flatnonzero(np.abs(desired) > 1e-10)
    if active.size == 0:
        return filled, cash, np.zeros(5), 0.0, {}
    orders = [bars[i] for i in active]
    boards = [BoardType(bar.get("board") or BoardType.MAIN) for bar in orders]
    direction = np.sign(desired[active])
    sides = ["BUY" if sign > 0 else "SELL" for sign in direction]
    opening = np.array([positive_price(bar.get("open")) for bar in orders])
    quantity = np.abs(desired[active])
    statuses = [bar_block_reason(bar, side, config) for bar, side in zip(orders, sides)]
    reasons = {int(i): reason for i, reason in zip(active, statuses) if reason is not None}
    limits = [raw_price_limits(bar) if config.enforce_price_limits else (None, None) for bar in orders]
    upper = np.array([up if up is not None else np.inf for up, _ in limits])
    minimum = np.array(
        [
            opening[i] * 1e-6 if board in {BoardType.NASDAQ, BoardType.NYSE} else 0.01
            for i, board in enumerate(boards)
        ]
    )
    lower = np.array([down if down is not None else minimum[i] for i, (_, down) in enumerate(limits)])
    volume = np.array([positive_price(bar.get("volume_shares")) for bar in orders])
    adv = np.array([positive_price(bar.get("_prior_adv")) for bar in orders])
    volatility = np.array([positive_price(bar.get("_prior_vol")) for bar in orders])
    pressure = np.divide(quantity, adv, out=np.zeros_like(quantity), where=adv > 0)
    impact = config.impact_coeff_y * volatility * np.sqrt(pressure)
    price = np.clip(opening * (1 + direction * impact), lower, upper)
    rates = np.array([fee_rates(board, side, config) for board, side in zip(boards, sides)])
    eligible = np.array([int(i) not in reasons for i in active])
    volume_cap = config.max_pov * volume
    bounded = np.minimum(quantity, volume_cap) * eligible
    for i in active[eligible & (volume_cap < quantity)]:
        reasons.setdefault(int(i), "POV_LIMIT")
    sellers = direction < 0
    buyers = ~sellers
    bounded[sellers] = np.minimum(bounded[sellers], positions[active[sellers]])
    per_share_fees = price * rates.sum(axis=1)
    available = cash + bounded[sellers] @ (price - per_share_fees)[sellers]
    cost = bounded[buyers] @ (price + per_share_fees)[buyers]
    if cost > available:
        for i in active[buyers & (bounded > 0)]:
            reasons[int(i)] = "INSUFFICIENT_CASH"
        bounded[buyers] *= max(available, 0.0) / cost
    filled[active] = bounded * direction
    nominal = bounded * price
    explicit_fees = nominal @ rates
    fees = np.r_[explicit_fees, (bounded * direction) @ (price - opening), 0.0]
    cash -= nominal @ direction + explicit_fees.sum()
    return filled, max(cash, 0.0), fees, float(nominal.sum()), reasons


@dataclass
class _DayAttribution:
    brinson: np.ndarray
    styles: np.ndarray
    industry: float
    specific: float
    brinson_error: float
    barra_error: float


def _day_attribution(
    wp: np.ndarray,
    wb: np.ndarray,
    returns: np.ndarray,
    industries: np.ndarray,
    styles: np.ndarray,
    market_cap: np.ndarray,
    observed: np.ndarray,
    timing: float,
    fees: float,
    strategy_return: float,
    benchmark_return: float,
) -> _DayAttribution:
    allocation, selection, interaction = brinson_fachler(
        np.r_[wp, 1 - wp.sum()],
        np.r_[wb, 1 - wb.sum()],
        np.r_[returns, 0.0],
        np.r_[industries, np.array(["__CASH__"])],
    )
    # Map the trade-timing and benchmark-index reconciliation bridge to selection.
    bridge = timing + float(wb @ returns) - benchmark_return
    brinson = np.array([allocation.sum(), selection.sum() + bridge, interaction.sum()])

    _, groups = np.unique(industries, return_inverse=True)
    dummies = np.eye(groups.max() + 1)[groups]
    industry_returns = np.zeros(dummies.shape[1])
    style_returns = np.zeros(styles.shape[1])
    residuals = returns.copy()
    if observed.any():
        style_returns, industry_returns, residual = fit_barra_factor_returns(
            styles[observed],
            dummies[observed],
            returns[observed],
            market_cap[observed],
            wb[observed],
        )
        # Apply the fitted coefficients to stale/missing observations too and carry
        # the valuation gap explicitly in their residuals.
        residuals -= np.nan_to_num(styles, nan=0.0) @ style_returns + dummies @ industry_returns
        residuals[observed] = residual
    style, industry, specific = barra_decomposition(
        wp - wb,
        styles,
        dummies,
        np.r_[style_returns, industry_returns],
        residuals,
    )
    specific += bridge
    daily_active = strategy_return - benchmark_return
    return _DayAttribution(
        brinson,
        style,
        industry,
        specific,
        abs(float(brinson.sum()) - fees - daily_active),
        abs(float(style.sum()) + industry + specific - fees - daily_active),
    )


def _attribution_and_friction(
    run_id: str,
    p: np.ndarray,
    b: np.ndarray,
    daily_fees: np.ndarray,
    daily_attribution: list[_DayAttribution],
) -> tuple[AttributionResult, FrictionBreakdown]:
    link = relative_link_weights(p, b)
    allocation, selection, interaction = link @ np.array([row.brinson for row in daily_attribution])
    styles = link @ np.array([row.styles for row in daily_attribution])
    style = float(styles.sum())
    industry = float(link @ np.array([row.industry for row in daily_attribution]))
    specific = float(link @ np.array([row.specific for row in daily_attribution]))
    linked_fees = link @ daily_fees
    costs = float(linked_fees.sum())
    active = float(np.expm1(np.log1p(p).sum() - np.log1p(b).sum()))
    brinson_error = max(
        abs(allocation + selection + interaction - costs - active),
        max(row.brinson_error for row in daily_attribution),
    )
    barra_error = max(
        abs(style + industry + specific - costs - active),
        max(row.barra_error for row in daily_attribution),
    )
    attribution = AttributionResult(
        run_id=run_id,
        brinson_allocation_pct=100 * allocation,
        brinson_selection_pct=100 * selection,
        brinson_interaction_pct=100 * interaction,
        brinson_closure_error_bp=10_000 * brinson_error,
        barra_style_pct=100 * style,
        barra_industry_pct=100 * industry,
        barra_specific_alpha_pct=100 * specific,
        barra_cost_pct=-100 * costs,
        barra_closure_error_bp=10_000 * barra_error,
        barra_style_detail={label: 100 * float(value) for label, value in zip(BARRA_STYLES, styles)},
    )
    commission, stamp, transfer, impact, queue = 100 * linked_fees
    friction = FrictionBreakdown(
        gross_excess_pct=100 * (active + costs),
        commission_pct=commission,
        stamp_duty_pct=stamp,
        transfer_fee_pct=transfer,
        impact_slippage_pct=impact,
        limit_suspend_pct=queue,
        net_excess_pct=100 * active,
        total_friction_bps=10_000 * costs,
    )
    return attribution, friction


def _underwater(nav: np.ndarray) -> np.ndarray:
    peak = np.maximum.accumulate(np.r_[1, nav])[1:]
    return nav / peak - 1


def run_backtest(
    bars_df: pl.DataFrame,
    corp_actions_df: pl.DataFrame | None,
    signals_df: pl.DataFrame,
    bench_df: pl.DataFrame,
    req: BacktestRequest,
) -> BacktestResult:
    """Replay a long-only close target panel over raw equity market bars.

    Express targets as unit-fraction `target_weight` (or `target_weight_pct`).
    A score panel forms an equal-weight top quintile with ties resolved by listing.
    A benchmark may supply index returns/levels, constituent weights, or both.
    Missing constituent weights use the lagged available equal-weight universe
    for attribution. Any resulting index return difference remains in the
    reconciliation bridge.
    """
    config = req.microstructure
    if not isfinite(req.initial_cash) or req.initial_cash <= 0:
        raise ValueError("initial_cash must be finite and positive")
    if config.max_pov < 0 or config.max_pov > 1 or not isfinite(config.max_pov):
        raise ValueError("max_pov must be in [0, 1]")
    fees = (config.commission_bps, config.stamp_duty_sell_bps, config.transfer_fee_bps, config.impact_coeff_y)
    if any(not isfinite(value) or value < 0 for value in fees):
        raise ValueError("fee and impact parameters must be finite and nonnegative")
    if req.start_date is not None and req.end_date is not None and req.start_date > req.end_date:
        raise ValueError("start_date cannot follow end_date")

    panel = _prepare_bars(bars_df)
    if req.end_date is not None:
        panel = panel.filter(pl.col("trade_date") <= req.end_date)
    if panel.is_empty():
        raise ValueError("no historical bars in the backtest window")
    sessions = panel["trade_date"].unique(maintain_order=True).to_list()
    if req.start_date is not None and sessions[-1] < req.start_date:
        raise ValueError("no historical bars in the backtest window")
    ids = set(panel["listing_id"].to_list())
    symbol_lookup: dict[str, set[str]] = defaultdict(set)
    for listing, symbol in panel.select("listing_id", "symbol").unique().iter_rows():
        symbol_lookup[str(symbol)].add(listing)

    target_col = next(
        (
            col
            for col in ("target_weight", "weight", "target_weight_pct", "weight_pct")
            if col in signals_df.columns
        ),
        None,
    )
    if target_col is not None:
        targets = _weight_history(signals_df, target_col, symbol_lookup, ids)
    elif "score" in signals_df.columns:
        targets = _score_targets(signals_df, symbol_lookup, ids)
    elif signals_df.is_empty():
        targets = {}
    else:
        raise ValueError("signals require target_weight, target_weight_pct or score")
    targets = _rebalance_targets(targets, config.rebalance_freq)
    index_returns, prior_benchmark_targets = _bench_history(bench_df, symbol_lookup, ids)
    assets = sorted(
        ids
        | {key for weights in targets.values() for key in weights}
        | {key for weights in prior_benchmark_targets.values() for key in weights}
    )
    asset_index = {key: i for i, key in enumerate(assets)}
    ex_events, record_events = _corporate_events(corp_actions_df, sessions, asset_index, symbol_lookup)

    risk_panel = panel.with_columns(pl.col("listing_id").alias("symbol"))
    if index_returns:
        index_panel = pl.DataFrame(
            {"trade_date": list(index_returns), "_index_return": list(index_returns.values())},
        )
        risk_panel = risk_panel.join(index_panel, on="trade_date", how="left")
        if "benchmark_return" in risk_panel.columns:
            risk_panel = risk_panel.with_columns(
                pl.coalesce("_index_return", "benchmark_return").alias("_index_return"),
            ).drop("benchmark_return")
        risk_panel = risk_panel.rename({"_index_return": "benchmark_return"})
    exposure_panel = build_barra_exposure_panel(risk_panel)
    style_lookup = {
        (day, listing): values
        for day, listing, *values in exposure_panel.select(
            "trade_date", "listing_id", *BARRA_STYLES
        ).iter_rows()
    }
    target_dates = sorted(targets)
    bench_dates = sorted(prior_benchmark_targets)
    order_cursor = 0
    n_assets = len(assets)
    positions = np.zeros(n_assets)
    marks = np.zeros(n_assets)
    prior_industries = np.full(n_assets, "UNKNOWN", dtype=object)
    prior_caps = np.zeros(n_assets)
    prior_styles = np.zeros((n_assets, len(BARRA_STYLES)))
    prior_observed = np.zeros(n_assets, dtype=bool)
    receipts: dict[date, float] = defaultdict(float)
    entitlement: dict[_CorporateEvent, float] = {}
    cash = req.initial_cash
    nav = req.initial_cash
    dates: list[str] = []
    rejections: list[RejectedOrderRecord] = []
    portfolio_returns: list[float] = []
    benchmark_returns: list[float] = []
    daily_fees: list[np.ndarray] = []
    daily_attribution: list[_DayAttribution] = []
    n_fills = 0
    turnover = 0.0
    execute = _screening_trades if req.engine == BacktestEngineMode.SCREENING else _event_trades

    for day_panel in panel.partition_by("trade_date", maintain_order=True):
        day = day_panel["trade_date"][0]
        bars = [_EMPTY_BAR] * n_assets
        for row in day_panel.iter_rows(named=True):
            bars[asset_index[row["listing_id"]]] = row
        new_marks = np.array([positive_price(bar.get("close")) for bar in bars])
        close_available = new_marks > 0
        pre_close = np.array([positive_price(bar.get("pre_close")) for bar in bars])
        opening = np.array([positive_price(bar.get("open")) for bar in bars])

        start_positions = positions.copy()
        distribution = np.zeros(n_assets)
        extra_shares = np.zeros(n_assets)
        extra_cash_entitlement = np.zeros(n_assets)
        extra_share_entitlement = np.zeros(n_assets)
        for event in ex_events.get(day, ()):
            i = event.asset
            eligible_pos = entitlement.pop(event, 0.0)
            dist_cash = event.cash_per_share * eligible_pos
            distrib_shares = event.extra_shares * eligible_pos
            receipts[event.pay_date] += dist_cash
            positions[i] += distrib_shares
            distribution[i] += event.cash_per_share
            extra_shares[i] += event.extra_shares
            extra_cash_entitlement[i] += event.cash_per_share * (eligible_pos - start_positions[i])
            extra_share_entitlement[i] += event.extra_shares * (eligible_pos - start_positions[i])
        if np.any(positions < -1e-9):
            raise ValueError(f"corporate actions yield a negative share balance on {day}")
        for due in [due for due in receipts if due <= day]:
            cash += receipts.pop(due)
        adjusted_mark = np.divide(
            marks - distribution,
            1 + extra_shares,
            out=marks.copy(),
            where=(1 + extra_shares > 0) & (marks > 0),
        )
        current_marks = np.where(close_available, new_marks, np.maximum(adjusted_mark, 0))
        if np.any((positions > 0) & (current_marks <= 0)):
            raise ValueError(f"unable to value a held stock on {day}")
        asset_returns = np.divide(
            (1 + extra_shares) * current_marks + distribution - marks,
            marks,
            out=np.zeros(n_assets),
            where=marks > 0,
        )

        target = None
        while order_cursor < len(target_dates) and target_dates[order_cursor] < day:
            target = targets[target_dates[order_cursor]]
            order_cursor += 1
        in_window = req.start_date is None or day >= req.start_date
        daily_cost_values = np.zeros(5)
        filled = np.zeros(n_assets)
        if in_window and target is not None:
            weights = np.zeros(n_assets)
            for asset, weight in target.items():
                weights[asset_index[asset]] = weight
            sizing = np.where(pre_close > 0, pre_close, np.where(adjusted_mark > 0, adjusted_mark, opening))
            target_qty = np.divide(nav * weights, sizing, out=np.zeros(n_assets), where=sizing > 0)
            delta = target_qty - positions
            filled, cash, daily_cost_values, notional, reasons = execute(bars, positions, delta, cash, config)
            for i in np.flatnonzero((weights > 0) & (sizing <= 0)):
                rejections.append(
                    RejectedOrderRecord(
                        trade_date=day.isoformat(),
                        symbol=str(bars[i].get("symbol") or assets[i]),
                        name=str(bars[i].get("name") or bars[i].get("symbol") or assets[i]),
                        side="BUY",
                        reason="NO_QUOTE",
                        loss_bps=0.0,
                    )
                )
            rejection_reference = np.where(pre_close > 0, pre_close, sizing)
            rejects, queue_cost = _rejections(
                day, assets, bars, delta, filled, reasons, rejection_reference, current_marks, nav
            )
            rejections.extend(rejects)
            daily_cost_values[-1] = queue_cost
            positions += filled
            turnover += notional / (2 * nav)
            n_fills += np.count_nonzero(filled)

        new_nav = cash + float(positions @ current_marks) + sum(receipts.values())
        if new_nav <= 0 or not isfinite(new_nav):
            raise ValueError(f"portfolio value exhausted or invalid on {day}")
        if in_window:
            wp = start_positions * marks / nav
            wb = np.zeros(n_assets)
            bench_index = bisect_left(bench_dates, day)
            if bench_index:
                for asset, weight in prior_benchmark_targets[bench_dates[bench_index - 1]].items():
                    wb[asset_index[asset]] = weight
            elif prior_observed.any():
                wb[prior_observed] = 1 / np.count_nonzero(prior_observed)
            b = index_returns.get(day, float(wb @ asset_returns))
            p = new_nav / nav - 1
            # Open-reference timing, record-date entitlement mismatches and the
            # theoretical opportunity bridge stay separate from price/fee ledger.
            timing_pnl = filled @ (current_marks - opening) + extra_cash_entitlement.sum()
            timing_pnl += extra_share_entitlement @ current_marks + daily_cost_values[-1]
            cost_rates = daily_cost_values / nav
            attrib = _day_attribution(
                wp,
                wb,
                asset_returns,
                prior_industries,
                prior_styles,
                prior_caps,
                prior_observed & close_available,
                float(timing_pnl / nav),
                float(cost_rates.sum()),
                p,
                b,
            )
            dates.append(day.isoformat())
            portfolio_returns.append(p)
            benchmark_returns.append(b)
            daily_fees.append(cost_rates)
            daily_attribution.append(attrib)

        for event in record_events.get(day, ()):
            entitlement[event] = positions[event.asset]
        for i, bar in enumerate(bars):
            if bar:
                prior_industries[i] = str(bar.get("industry_l1") or "UNKNOWN")
                prior_caps[i] = positive_price(bar.get("float_market_cap"))
                prior_styles[i, :] = style_lookup[(day, assets[i])]
        prior_observed = close_available
        marks = current_marks
        nav = new_nav

    p = np.asarray(portfolio_returns)
    b = np.asarray(benchmark_returns)
    portfolio_nav = np.cumprod(1 + p)
    benchmark_nav = np.cumprod(1 + b)
    excess_nav = portfolio_nav / benchmark_nav
    active_returns = p - b
    strategy_dd = _underwater(portfolio_nav)
    excess_dd = _underwater(excess_nav)
    annualization = 252 / len(dates)
    portfolio_ann = float(np.expm1(np.log1p(p).sum() * annualization))
    benchmark_ann = float(np.expm1(np.log1p(b).sum() * annualization))
    relative_ann = float(np.expm1(np.log(excess_nav[-1]) * annualization))
    active_volatility = active_returns.std(ddof=1) if len(p) > 1 else 0
    ir = float(active_returns.mean() * sqrt(252) / active_volatility) if active_volatility > 1e-16 else 0.0
    mdd, active_mdd = float(strategy_dd.min()), float(excess_dd.min())
    gains, losses = p[p > 0], p[p < 0]
    profit_loss = float(gains.mean() / -losses.mean()) if gains.size and losses.size else 0.0

    signature = sha256(req.model_dump_json().encode())
    signature.update("|".join(dates).encode())
    signature.update(np.stack((p, b)).astype("<f8").tobytes())
    signature.update(np.asarray(daily_fees, dtype="<f8").tobytes())
    signature.update(np.asarray([turnover, n_fills], dtype="<f8").tobytes())
    for day_attrib in daily_attribution:
        effects = np.r_[day_attrib.brinson, day_attrib.styles, day_attrib.industry, day_attrib.specific]
        signature.update(effects.astype("<f8").tobytes())
    for rejection in rejections:
        signature.update(rejection.model_dump_json().encode())
    run_id = "BT-" + signature.hexdigest()[:20]
    attribution, friction = _attribution_and_friction(run_id, p, b, np.asarray(daily_fees), daily_attribution)
    return BacktestResult(
        run_id=run_id,
        experiment_id=req.experiment_id,
        universe_id=req.universe_id,
        engine=req.engine,
        snapshot_id=req.snapshot_id or "unspecified",
        ann_return_pct=100 * portfolio_ann,
        bench_ann_return_pct=100 * benchmark_ann,
        net_excess_ann_pct=100 * relative_ann,
        info_ratio=ir,
        max_drawdown_pct=100 * mdd,
        excess_max_drawdown_pct=100 * active_mdd,
        calmar=portfolio_ann / -mdd if mdd < -1e-16 else 0.0,
        daily_win_rate_pct=float((p > 0).mean() * 100),
        profit_loss_ratio=profit_loss,
        ann_turnover_pct=100 * turnover * annualization,
        total_fills=int(n_fills),
        total_rejections=len(rejections),
        dates=dates,
        strategy_nav=portfolio_nav.tolist(),
        bench_nav=benchmark_nav.tolist(),
        excess_nav=excess_nav.tolist(),
        strategy_dd=strategy_dd.tolist(),
        excess_dd=excess_dd.tolist(),
        friction=friction,
        recent_rejections=rejections[-50:],
        attribution=attribution,
    )
