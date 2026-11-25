"""Causal market baselines and conditional paper-factor diagnostics."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import numpy as np
import polars as pl

from quant_core.backtest.attribution import relative_link_weights
from quant_core.factors.engine import (
    BUILTIN_FACTORS,
    _adjusted_price,
    _date_sections,
    _decision_time,
    _label_table,
    _prepare_bars,
    _timestamp,
    factor_panel,
    spearman_rank_ic,
)
from quant_core.factors.preprocess import eligible_mask, numeric_column
from quant_core.risk.barra_opt import BARRA_STYLES, build_barra_exposure_panel, fit_barra_factor_returns
from quant_core.schemas import (
    INSTITUTIONAL_GATES,
    DimensionBaselineRecord,
    DimensionSliceItem,
    FactorEvalRequest,
    FactorEvalResult,
    PreprocessConfig,
    RegimeAnalysisRequest,
    RegimeAnalysisResult,
)

_UNKNOWN = "Unknown"
_TRADING_DAYS = 252
_MARKET_BUCKETS = ("BULL", "BEAR", "SIDEWAYS")
_VOL_BUCKETS = ("LOW", "MID", "HIGH")
_CAP_BUCKETS = ("LARGE", "MID", "SMALL")
_BUCKETS = {
    "market_regime": _MARKET_BUCKETS,
    "volatility_regime": _VOL_BUCKETS,
    "market_cap_tier": _CAP_BUCKETS,
    "sw_industry": (),
    "barra_style": BARRA_STYLES,
}
_LIGHT_DIMENSIONS = tuple(_BUCKETS)[:3]
_TIME_DIMENSIONS = _LIGHT_DIMENSIONS[:2]
_DEFINITIONS: dict[str, dict[str, Any]] = {
    "market_regime": {
        "basis": "Previous close. Explicit index return or prior-weight equity proxy",
        "trend_sessions": 60,
        "drawdown_sessions": 120,
        "bear_entry": {"trend_max": -0.08, "drawdown_max": -0.15},
        "bull_entry": {"trend_min": 0.08, "drawdown_above": -0.10},
        "bear_hold": {"trend_below": -0.03, "drawdown_max": -0.10},
        "bull_hold": {"trend_above": 0.03, "drawdown_above": -0.12},
    },
    "volatility_regime": {
        "basis": "Previous close. Trailing realized volatility versus prior observations only",
        "volatility_sessions": 20,
        "reference_sessions": 252,
        "min_reference_samples": 126,
        "quantiles": [1 / 3, 2 / 3],
        "ties": "MID",
    },
    "market_cap_tier": {
        "basis": "Decision-day eligible free-float cap cross-section. Returns are next-session equal weight",
        "quantiles": [1 / 3, 2 / 3],
        "ties": "MID",
        "current_state_basis": "Largest aggregate free-float capitalization",
    },
    "sw_industry": {
        "basis": "PIT industry membership. Returns are next-session equal weight",
        "taxonomy": "SW2021",
        "current_state_basis": "Top and bottom industries by latest observed daily return",
    },
    "barra_style": {
        "basis": "CNE6 proxies. Decision-date exposures and next-session returns, sqrt-cap WLS",
        "current_state_basis": "Largest latest absolute style return",
        "conditional_ic": "Names with positive exposure to each style",
    },
}


@dataclass
class _DimensionData:
    bars: pl.DataFrame
    sections: list[np.ndarray]
    dates: list[str]
    market_returns: np.ndarray
    asset_returns: np.ndarray
    caps: np.ndarray
    eligible: np.ndarray
    groups: dict[str, np.ndarray]
    bounds: dict[str, np.ndarray]
    styles: np.ndarray | None


def _observed_number(frame: pl.DataFrame, *names: str, lower: float | None = None) -> pl.Expr:
    expressions = [pl.col(name).cast(pl.Float64, strict=False) for name in names if name in frame.columns]
    if not expressions:
        return pl.lit(None, dtype=pl.Float64)
    return pl.coalesce(
        pl.when(value.is_finite() & (value > lower) if lower is not None else value.is_finite()).then(value)
        for value in expressions
    )


def _market_states(returns: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = len(returns)
    market = np.full(n, _UNKNOWN, dtype=object)
    volatility = np.full(n, _UNKNOWN, dtype=object)
    state = "SIDEWAYS"
    if not n:
        return market, volatility, np.empty((0, 2)), np.empty((0, 2))
    log_returns = np.log1p(returns)
    # Each decision uses the previous close. The current return is absent from
    # every window in this loop, including the volatility reference sample.
    realized = pl.Series(returns).fill_nan(None).rolling_std(20, min_samples=20).to_numpy()
    market_features = np.full((n, 2), np.nan)
    volatility_bounds = np.full((n, 2), np.nan)
    for i in range(1, n):
        if i >= 120 and np.isfinite(log_returns[i - 120 : i]).all():
            history = log_returns[i - 120 : i]
            nav = np.concatenate(([0.0], np.cumsum(history)))
            trend = float(np.expm1(history[-60:].sum()))
            drawdown = float(np.expm1(nav[-1] - nav.max()))
            market_features[i] = trend, drawdown
            if trend <= -0.08 or drawdown <= -0.15:
                state = "BEAR"
            elif trend >= 0.08 and drawdown > -0.10:
                state = "BULL"
            elif not (
                (state == "BEAR" and (trend < -0.03 or drawdown <= -0.10))
                or (state == "BULL" and trend > 0.03 and drawdown > -0.12)
            ):
                state = "SIDEWAYS"
            market[i] = state
        else:
            state = "SIDEWAYS"
        if np.isfinite(realized[i - 1]):
            reference = realized[max(0, i - 253) : i - 1]
            reference = reference[np.isfinite(reference)]
            if reference.size >= 126:
                low, high = np.quantile(reference, (1 / 3, 2 / 3))
                volatility_bounds[i] = low, high
                if np.isclose(low, high, rtol=0, atol=1e-12):
                    volatility[i] = "MID"
                else:
                    volatility[i] = (
                        "LOW" if realized[i - 1] < low else "HIGH" if realized[i - 1] > high else "MID"
                    )
    return market, volatility, market_features, volatility_bounds


def _industry_labels(bars: pl.DataFrame) -> np.ndarray:
    source = next(
        (column for column in ("sw_l1_code", "l1_code", "industry_l1") if column in bars.columns), None
    )
    if not source:
        return np.full(bars.height, _UNKNOWN, dtype=object)
    label = pl.col(source).cast(pl.String).str.strip_chars()
    known = ~label.str.to_lowercase().is_in(["", "unknown", "unclassified"])
    taxonomy = next((name for name in ("industry_taxonomy", "taxonomy") if name in bars.columns), None)
    if taxonomy:
        known &= pl.col(taxonomy).cast(pl.String).str.to_uppercase() == "SW2021"
    if "industry_valid_from" in bars.columns:
        known &= pl.col("industry_valid_from").cast(pl.Date, strict=False) <= pl.col("trade_date")
    if "industry_valid_to" in bars.columns:
        ending = pl.col("industry_valid_to").cast(pl.Date, strict=False)
        known &= ending.is_null() | (ending >= pl.col("trade_date"))
    timestamps = [name for name in ("industry_published_at", "industry_available_at") if name in bars.columns]
    if timestamps:
        fields = [name for name in ("trade_date", "decision_at", "market", "board") if name in bars.columns]
        available = []
        for row in bars.select(*fields, *timestamps).iter_rows(named=True):
            zone, cutoff = _decision_time(row)
            observed = []
            for name in timestamps:
                value = row[name]
                try:
                    if type(value) is date or (isinstance(value, str) and len(value) == 10):
                        observed.append(date.fromisoformat(str(value)) < row["trade_date"])
                    else:
                        observed.append(value is not None and _timestamp(value, zone) <= cutoff)
                except ValueError:
                    observed.append(False)
            available.append(all(observed))
        known &= pl.Series(available, dtype=pl.Boolean)
    return bars.select(pl.when(known).then(label).otherwise(pl.lit(_UNKNOWN)))[source].to_numpy()


def _dimension_data(prepared: pl.DataFrame, dimensions: Sequence[str]) -> _DimensionData:
    if "close" not in prepared.columns:
        prepared = prepared.with_columns(pl.lit(None, dtype=pl.Float64).alias("close"))
    bars = prepared.sort(["trade_date", "_id"])
    sections = _date_sections(bars) if bars.height else []
    caps_expr = _observed_number(bars, "free_float_market_cap", "float_market_cap", "market_cap", lower=0)
    adjusted = _adjusted_price(bars, "close")
    close = pl.col("close")
    previous_close = _observed_number(bars, "pre_close", lower=0)
    explicit_weights = any(column in bars.columns for column in ("benchmark_weight", "bench_weight"))
    weight = (
        _observed_number(bars, "benchmark_weight", "bench_weight").fill_null(0)
        if explicit_weights
        else caps_expr
    )
    quotes = bars.with_columns(
        pl.col("trade_date").rank(method="dense").alias("_baseline_session"),
        caps_expr.alias("_baseline_cap"),
        adjusted.alias("_baseline_close"),
        weight.alias("_baseline_weight"),
    )
    previous_session = (pl.col("_baseline_session") - pl.col("_baseline_session").shift(1).over("_id")) == 1
    adjusted_return = adjusted / pl.col("_baseline_close").shift(1).over("_id") - 1
    unadjusted_return = (
        close / pl.coalesce(previous_close, pl.when(previous_session).then(close.shift(1).over("_id"))) - 1
    )
    # Prefer the explicit corporate-action-aware pre-close when adjustment data is absent.
    use_adjustment = (
        pl.col("adj_factor").is_finite() & pl.col("adj_factor").shift(1).over("_id").is_finite()
        if "adj_factor" in bars.columns
        else pl.lit(False)
    )
    returns = pl.coalesce(pl.when(previous_session & use_adjustment).then(adjusted_return), unadjusted_return)
    suspended = (
        pl.col("is_suspended").cast(pl.Boolean, strict=False).fill_null(False)
        if "is_suspended" in bars.columns
        else pl.lit(False)
    )
    quotes = quotes.with_columns(
        pl.when(returns.is_finite() & (returns > -1) & ~suspended).then(returns).alias("_baseline_return"),
        pl.when(previous_session)
        .then(pl.col("_baseline_weight").shift(1).over("_id"))
        .alias("_lagged_weight"),
        _observed_number(bars, "benchmark_return", "bench_return", lower=-1).alias("_explicit_market"),
    )
    caps = numeric_column(quotes, "_baseline_cap")
    returns = numeric_column(quotes, "_baseline_return")
    weights = numeric_column(quotes, "_lagged_weight")
    explicit_market = numeric_column(quotes, "_explicit_market")
    market_returns = np.full(len(sections), np.nan)
    for i, rows in enumerate(sections):
        explicit = explicit_market[rows]
        explicit = explicit[np.isfinite(explicit)]
        if explicit.size:
            if not np.allclose(explicit, explicit[0], rtol=0, atol=1e-10):
                raise ValueError("Conflicting benchmark returns within a trading session")
            market_returns[i] = explicit[0]
            continue
        valid = np.isfinite(returns[rows])
        good_weight = valid & np.isfinite(weights[rows]) & (weights[rows] > 0)
        if good_weight.any():
            selected = rows[good_weight]
            market_returns[i] = np.average(returns[selected], weights=weights[selected])
        elif valid.any() and (not explicit_weights or i == 0):
            market_returns[i] = returns[rows[valid]].mean()

    eligible = eligible_mask(bars, PreprocessConfig())
    groups: dict[str, np.ndarray] = {}
    bounds: dict[str, np.ndarray] = {}
    if set(dimensions).intersection(_TIME_DIMENSIONS):
        market, volatility, market_features, volatility_bounds = _market_states(market_returns)
        counts = [len(rows) for rows in sections]
        groups.update(
            market_regime=np.repeat(market, counts), volatility_regime=np.repeat(volatility, counts)
        )
        bounds.update(market_regime=market_features, volatility_regime=volatility_bounds)
    if "market_cap_tier" in dimensions:
        tiers = np.full(bars.height, _UNKNOWN, dtype=object)
        cap_bounds = np.full((len(sections), 2), np.nan)
        for i, rows in enumerate(sections):
            observed = rows[eligible[rows] & np.isfinite(caps[rows])]
            if observed.size:
                low, high = np.quantile(caps[observed], (1 / 3, 2 / 3))
                cap_bounds[i] = low, high
                tiers[observed] = np.select(
                    [caps[observed] > high, caps[observed] < low], ["LARGE", "SMALL"], default="MID"
                )
        groups["market_cap_tier"] = tiers
        bounds["market_cap_tier"] = cap_bounds
    if "sw_industry" in dimensions or "barra_style" in dimensions:
        groups["sw_industry"] = _industry_labels(bars)
    styles = None
    if "barra_style" in dimensions:
        missing = [style for style in BARRA_STYLES if style not in bars.columns]
        if missing and bars.height:
            exposure = build_barra_exposure_panel(bars.with_columns(pl.col("_id").alias("symbol")))
            bars = bars.join(
                exposure.select("_id", "trade_date", *missing),
                on=["_id", "trade_date"],
                how="left",
                validate="1:1",
                maintain_order="left",
            )
        styles = np.column_stack([numeric_column(bars, style) for style in BARRA_STYLES])

    dates = [day.isoformat() for day in bars["trade_date"].unique(maintain_order=True)]
    return _DimensionData(
        bars, sections, dates, market_returns, returns, caps, eligible, groups, bounds, styles
    )


def _annual_return(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    if not finite.size:
        return 0.0
    if (finite <= -1).any():
        return -100.0
    return 100 * float(np.expm1(np.clip(_TRADING_DAYS * np.log1p(finite).mean(), -745, 700)))


def _style_returns(
    styles: np.ndarray, industries: np.ndarray, returns: np.ndarray, caps: np.ndarray
) -> np.ndarray:
    valid = np.isfinite(returns)
    coefficients = np.full(len(BARRA_STYLES), np.nan)
    if not valid.any():
        return coefficients
    observed_styles = np.nan_to_num(styles[valid], nan=0.0)
    available = np.ptp(observed_styles, axis=0) > 1e-12
    if not available.any():
        return coefficients
    dummies = industries[valid, None] == np.unique(industries[valid])
    if valid.sum() <= available.sum() + dummies.shape[1]:
        return coefficients
    estimated, _, _ = fit_barra_factor_returns(
        observed_styles[:, available], dummies, returns[valid], caps[valid]
    )
    coefficients[available] = estimated
    return coefficients


def _forward_baseline_marks(data: _DimensionData) -> np.ndarray:
    if not data.bars.height:
        return np.empty(0)
    # The series uses signal-date keys for joining PIT characteristics to the
    # following session, including when a listing leaves or has a gap.
    if "open" in data.bars.columns:
        return numeric_column(_label_table(data.bars, (1,)).sort(["trade_date", "_id"]), "_mark_1")
    realized = data.bars.with_columns(pl.Series("_realized", data.asset_returns)).select(
        "_id", (pl.col("trade_date").rank(method="dense") - 1).alias("_decision"), "_realized"
    )
    return numeric_column(
        data.bars.select("_id", pl.col("trade_date").rank(method="dense").alias("_decision")).join(
            realized,
            on=["_id", "_decision"],
            how="left",
            validate="1:1",
            maintain_order="left",
        ),
        "_realized",
    )


def _baseline_series(
    data: _DimensionData,
    dimension: str,
    forward_marks: np.ndarray | None,
) -> tuple[dict[str, np.ndarray], str]:
    n = len(data.sections)
    if dimension in _TIME_DIMENSIONS:
        labels = np.array([data.groups[dimension][rows[0]] for rows in data.sections], dtype=object)
        names = (*_BUCKETS[dimension], _UNKNOWN)
        return {name: np.where(labels == name, data.market_returns, np.nan) for name in names}, (
            str(labels[-1]) if n else _UNKNOWN
        )

    if dimension == "barra_style":
        matrix = np.full((n, len(BARRA_STYLES)), np.nan)
        assert data.styles is not None and forward_marks is not None
        for i, rows in enumerate(data.sections[:-1]):
            matrix[i + 1] = _style_returns(
                data.styles[rows], data.groups["sw_industry"][rows], forward_marks[rows], data.caps[rows]
            )
        recent = matrix[-1] if n else np.full(len(BARRA_STYLES), np.nan)
        state = BARRA_STYLES[int(np.nanargmax(abs(recent)))] if np.isfinite(recent).any() else _UNKNOWN
        return dict(zip(BARRA_STYLES, matrix.T)), state

    assert forward_marks is not None
    groups = data.groups[dimension]
    names = (*_BUCKETS[dimension], _UNKNOWN) if dimension in _LIGHT_DIMENSIONS else tuple(sorted(set(groups)))
    result = {name: np.full(n, np.nan) for name in names}
    for i, rows in enumerate(data.sections[:-1]):
        keep = data.eligible[rows] & np.isfinite(forward_marks[rows])
        for name in np.unique(groups[rows[keep]]):
            selected = rows[keep & (groups[rows] == name)]
            result[name][i + 1] = forward_marks[selected].mean()
    state = _UNKNOWN
    if n and dimension == "market_cap_tier":
        last = data.sections[-1]
        sizes = [(float(data.caps[last[groups[last] == name]].sum()), name) for name in _CAP_BUCKETS]
        largest, tier = max(sizes)
        state = tier if largest > 0 else _UNKNOWN
    elif n and dimension == "sw_industry":
        ranked = sorted(
            (
                (float(values[-1]), name)
                for name, values in result.items()
                if name != _UNKNOWN and np.isfinite(values[-1])
            ),
            key=lambda item: (-item[0], item[1]),
        )
        if ranked:
            state = f"TOP {ranked[0][1]} / BOTTOM {ranked[-1][1]}"
    return result, state


def _bucket_stats(bucket: str, series: np.ndarray) -> dict[str, Any]:
    observed = series[np.isfinite(series)]
    growth = 100 * np.expm1(np.clip(np.log1p(np.maximum(observed, -1 + 1e-12)).cumsum(), -745, 700))
    preview = (
        growth[np.linspace(0, len(growth) - 1, min(24, len(growth)), dtype=int)] if growth.size else growth
    )
    return {
        "bucket": bucket,
        "n_days": int(observed.size),
        "sample_pct": 100 * observed.size / max(1, len(series)),
        "annual_return_pct": _annual_return(observed),
        "annual_volatility_pct": 100 * float(observed.std(ddof=1) * np.sqrt(_TRADING_DAYS))
        if observed.size > 1
        else 0.0,
        "latest_return_pct": float(100 * series[-1]) if series.size and np.isfinite(series[-1]) else None,
        "spark": preview.tolist(),
    }


def _masked_numbers(values: np.ndarray) -> list[Any]:
    return np.where(np.isfinite(values), values, None).tolist()


def _build_records(
    data: _DimensionData, snapshot_id: str, universe_id: str, dimensions: Sequence[str]
) -> list[DimensionBaselineRecord]:
    needs_marks = any(dimension not in _TIME_DIMENSIONS for dimension in dimensions)
    marks = _forward_baseline_marks(data) if needs_marks else None
    records = []
    created = datetime.now(UTC).isoformat()
    for dimension in dimensions:
        series, state = _baseline_series(data, dimension, marks)
        stats = [_bucket_stats(name, values) for name, values in series.items()]
        if dimension in ("market_cap_tier", "sw_industry"):
            memberships = data.groups[dimension][data.eligible]
            for item in stats:
                count = int(np.count_nonzero(memberships == item["bucket"]))
                item["name_session_count"] = count
                item["name_session_pct"] = 100 * count / max(1, memberships.size)
        if dimension == "sw_industry":
            ranked = sorted(
                (
                    item
                    for item in stats
                    if item["bucket"] != _UNKNOWN and item["latest_return_pct"] is not None
                ),
                key=lambda item: (-item["latest_return_pct"], item["bucket"]),
            )
            for rank, item in enumerate(ranked, start=1):
                item["current_rank"] = rank
                item["current_state"] = "TOP" if rank == 1 else "BOTTOM" if rank == len(ranked) else "MID"
        bounds = data.bounds.get(dimension)
        definitions = {
            **_DEFINITIONS[dimension],
            "buckets": list(series),
            "latest_features": _masked_numbers(bounds[-1]) if bounds is not None and len(bounds) else [],
        }
        payload = {
            "snapshot_id": snapshot_id,
            "universe_id": universe_id,
            "dimension": dimension,
            "baseline_version": 1,
            "definitions": definitions,
            "dates": data.dates,
            "daily_returns": {name: _masked_numbers(values) for name, values in series.items()},
            "state": state,
            "bucket_stats": stats,
        }
        if dimension in data.groups:
            payload["memberships"] = hashlib.sha256(
                data.bars.select("trade_date", "_id")
                .with_columns(pl.Series("bucket", data.groups[dimension].tolist(), dtype=pl.String))
                .write_csv()
                .encode()
            ).hexdigest()
        baseline_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        ).hexdigest()
        end = data.dates[-1] if data.dates else ""
        records.append(
            DimensionBaselineRecord(
                baseline_id=f"bl_{snapshot_id}_{universe_id}_{dimension}",
                snapshot_id=snapshot_id,
                universe_id=universe_id,
                dimension_type=dimension,
                date_range_start=data.dates[0] if data.dates else "",
                date_range_end=end,
                current_state=state,
                bucket_definitions=definitions,
                bucket_stats=stats,
                baseline_sha256=baseline_hash,
                created_at=created,
            )
        )
    return records


def build_dimension_baselines(
    bars_df: pl.DataFrame, snapshot_id: str, universe_id: str
) -> list[DimensionBaselineRecord]:
    """Build deterministic snapshot summaries independently of the factor under test."""
    dimensions = tuple(_BUCKETS)
    return _build_records(
        _dimension_data(_prepare_bars(bars_df), dimensions), snapshot_id, universe_id, dimensions
    )


@dataclass
class _SliceSeries:
    portfolio: np.ndarray
    benchmark: np.ndarray
    ic: np.ndarray
    contributions: np.ndarray
    matched: np.ndarray

    @classmethod
    def empty(cls, length: int) -> _SliceSeries:
        return cls(
            np.full(length, np.nan),
            np.full(length, np.nan),
            np.full(length, np.nan),
            np.zeros(length),
            np.zeros(length, dtype=bool),
        )


def _aligned_memberships(
    data: _DimensionData, panel: pl.DataFrame, dimensions: Sequence[str]
) -> tuple[dict[str, np.ndarray], np.ndarray | None, np.ndarray]:
    groups = {dimension: data.groups[dimension] for dimension in dimensions if dimension in data.groups}
    if "barra_style" in dimensions and "sw_industry" not in groups:
        groups["sw_industry"] = data.groups["sw_industry"]
    baseline_keys = data.bars.select("trade_date", "_id")
    panel_keys = panel.select("trade_date", "_id")
    if panel_keys.equals(baseline_keys):
        return groups, data.styles, data.caps
    aligned = panel_keys.join(
        baseline_keys.with_row_index("_baseline_row"),
        on=["trade_date", "_id"],
        how="left",
        validate="1:1",
        maintain_order="left",
    )
    row_index = aligned["_baseline_row"].to_numpy()
    return (
        {dimension: values[row_index] for dimension, values in groups.items()},
        data.styles[row_index] if data.styles is not None else None,
        data.caps[row_index],
    )


def _daily_slices(
    data: _DimensionData, panel: pl.DataFrame, horizon: int, dimensions: Sequence[str]
) -> tuple[dict[str, dict[str, _SliceSeries]], np.ndarray]:
    by_date = {day: i for i, day in enumerate(data.dates)}
    groups, styles, caps = _aligned_memberships(data, panel, dimensions)
    labels = numeric_column(panel, f"_forward_{horizon}")
    marks = numeric_column(panel, "_mark_1")
    scores = numeric_column(panel, "factor_value")
    eligible = panel["factor_eligible"].to_numpy()
    dates = panel["trade_date"].to_list()
    size = len(data.dates)
    series: dict[str, dict[str, _SliceSeries]] = {}
    for dimension in dimensions:
        names = dict.fromkeys((*_BUCKETS[dimension], *sorted(set(groups.get(dimension, ())))))
        series[dimension] = {name: _SliceSeries.empty(size) for name in names}
    portfolio = np.zeros(size)
    benchmark = np.zeros(size)
    for rows in _date_sections(panel) if panel.height else []:
        day = by_date[dates[rows[0]].isoformat()]
        score = scores[rows]
        target = labels[rows]
        mark = marks[rows]
        usable = eligible[rows]
        ranked = np.flatnonzero(usable & np.isfinite(score))
        active = len(ranked) >= 10 and np.any(usable & np.isfinite(mark))
        wp = np.zeros(len(rows))
        wb = np.zeros(len(rows))
        realized = np.nan_to_num(mark, nan=0.0)
        if active:
            top_count = len(ranked) // 10
            top = ranked[np.argsort(score[ranked], kind="stable")[-top_count:]]
            wp[top] = 1 / top_count
            wb[usable] = 1 / usable.sum()
            if np.any(realized <= -1):
                active = False
            else:
                portfolio[day] = wp @ realized
                benchmark[day] = wb @ realized
        active_returns = (wp - wb) * realized

        for dimension, items in series.items():
            if dimension == "barra_style":
                assert styles is not None
                x = styles[rows]
                coefficient = (
                    _style_returns(x, groups["sw_industry"][rows], mark, caps[rows]) if active else None
                )
                for j, item in enumerate(items.values()):
                    member = usable & (x[:, j] > 0)
                    item.matched[day] = member.any()
                    ic = spearman_rank_ic(score[member], target[member], min_names=20)
                    if ic is not None:
                        item.ic[day] = ic
                    if coefficient is not None and np.isfinite(coefficient[j]):
                        exposures = np.nan_to_num(x[:, j], nan=0.0)
                        item.portfolio[day] = (wp @ exposures) * coefficient[j]
                        item.benchmark[day] = (wb @ exposures) * coefficient[j]
                        item.contributions[day] = item.portfolio[day] - item.benchmark[day]
                continue

            names = groups[dimension][rows]
            for name in np.unique(names):
                item = items[name]
                member = (names == name) & usable
                item.matched[day] = member.any()
                ic = spearman_rank_ic(score[member], target[member], min_names=20)
                if ic is not None:
                    item.ic[day] = ic
                if active:
                    item.contributions[day] = active_returns[member].sum()
                    strategy_capital, benchmark_capital = wp[member].sum(), wb[member].sum()
                    if strategy_capital > 0 and benchmark_capital > 0:
                        item.portfolio[day] = (wp[member] @ realized[member]) / strategy_capital
                        item.benchmark[day] = (wb[member] @ realized[member]) / benchmark_capital
    return series, relative_link_weights(portfolio, benchmark) if size else np.empty(0)


def _interval(series: _SliceSeries, samples: np.ndarray | None, *, linear: bool) -> list[float] | None:
    valid = np.isfinite(series.portfolio) & np.isfinite(series.benchmark)
    if samples is None or valid.sum() < 2:
        return None
    usable = valid[samples]
    counts = usable.sum(axis=1)
    estimates = []
    for returns in (series.portfolio, series.benchmark):
        values = returns if linear else np.log1p(returns)
        sums = np.where(usable, values[samples], 0.0).sum(axis=1)
        annualized = _TRADING_DAYS * sums[counts > 0] / counts[counts > 0]
        estimates.append(100 * (annualized if linear else np.expm1(np.clip(annualized, -745, 700))))
    if len(estimates[0]) < len(samples) // 2:
        return None
    return np.quantile(estimates[0] - estimates[1], (0.025, 0.975)).tolist()


def _summarize_slices(
    series: dict[str, dict[str, _SliceSeries]],
    links: np.ndarray,
    samples: np.ndarray | None = None,
) -> tuple[list[DimensionSliceItem], float | None]:
    slices = []
    qualified = 0
    supported = 0
    consistency_dimension = next((dim for dim in _TIME_DIMENSIONS if dim in series), None)
    for dimension, items in series.items():
        contributions = {name: float(item.contributions @ links) for name, item in items.items()}
        total_positive = sum(max(0.0, amount) for amount in contributions.values())
        linear = dimension == "barra_style"
        for name, item in items.items():
            share = max(0.0, contributions[name]) / total_positive if total_positive > 0 else 0.0
            daily_excess = item.portfolio - item.benchmark
            observed = daily_excess[np.isfinite(daily_excess)]
            ic = item.ic[np.isfinite(item.ic)]
            volatility = float(ic.std(ddof=1)) if ic.size > 1 else 0.0
            interval = _interval(item, samples, linear=linear)
            if dimension == consistency_dimension and name != _UNKNOWN:
                episodes = (
                    np.count_nonzero(item.matched & ~np.r_[False, item.matched[:-1]])
                    if item.matched.size
                    else 0
                )
                if (
                    interval is not None
                    and observed.size >= 60
                    and observed.size >= 0.60 * item.matched.sum()
                    and episodes >= 3
                ):
                    qualified += 1
                    supported += int(interval[0] > 0)
            if linear:
                active = 100 * _TRADING_DAYS * float(observed.mean()) if observed.size else 0.0
                benchmark = 100 * _TRADING_DAYS * float(np.nanmean(item.benchmark)) if observed.size else 0.0
            else:
                benchmark = _annual_return(item.benchmark)
                active = _annual_return(item.portfolio) - benchmark
            slices.append(
                DimensionSliceItem(
                    dim=dimension,
                    bucket=name,
                    strategy_excess_pct=active,
                    baseline_return_pct=benchmark,
                    alpha_vs_baseline_pct=active,
                    rank_ic=float(ic.mean()) if ic.size else None,
                    icir=float(np.sqrt(_TRADING_DAYS) * ic.mean() / volatility)
                    if volatility > 1e-12
                    else None,
                    win_rate_pct=float(100 * np.mean(observed > 0)) if observed.size else 0.0,
                    n_days=int(observed.size),
                    contribution_pct=100 * share,
                    ci95=interval,
                    dominant_flag=name != _UNKNOWN
                    and share > INSTITUTIONAL_GATES["single_dim_dominance_max"],
                )
            )
    score = supported / len(_BUCKETS[consistency_dimension]) if qualified else None
    return slices, score


def _factor_dimension_slices(
    bars: pl.DataFrame, panel: pl.DataFrame, horizon: int
) -> list[DimensionSliceItem]:
    """Compute lightweight V2 additions using its existing factor scores and labels."""
    if not panel.height:
        return []
    data = _dimension_data(bars, _LIGHT_DIMENSIONS)
    series, links = _daily_slices(data, panel, horizon, _LIGHT_DIMENSIONS)
    slices, _ = _summarize_slices(series, links)
    return slices


def analyze_factor_regimes(
    bars_df: pl.DataFrame,
    req: RegimeAnalysisRequest,
    *,
    baselines: list[DimensionBaselineRecord] | None = None,
    eval_result: FactorEvalResult | None = None,
) -> RegimeAnalysisResult:
    """Apply causal dimensions to a factor and attribute next-session paper excess."""
    dimensions = tuple(dict.fromkeys(req.dimensions))
    if invalid := set(dimensions) - set(_BUCKETS):
        raise ValueError(f"Unknown regime dimensions: {sorted(invalid)}")
    matching_eval = eval_result is not None and (
        eval_result.factor_id == req.factor_id
        and eval_result.universe_id == req.universe_id
        and eval_result.expression
        == (req.expression or BUILTIN_FACTORS.get(req.factor_id, {}).get("expression"))
        and (req.snapshot_id is None or req.snapshot_id == eval_result.snapshot_id)
    )
    matching_baselines = [record for record in baselines or [] if record.universe_id == req.universe_id]
    snapshots = {record.snapshot_id for record in matching_baselines}
    snapshot = req.snapshot_id or (eval_result.snapshot_id if matching_eval else None)
    snapshot = snapshot or (next(iter(snapshots)) if len(snapshots) == 1 else "unversioned")
    identity = {"factor_id": req.factor_id, "universe_id": req.universe_id, "snapshot_id": snapshot}
    if not dimensions:
        return RegimeAnalysisResult(**identity)

    data = _dimension_data(_prepare_bars(bars_df), dimensions)
    date_range = (data.dates[0], data.dates[-1]) if data.dates else ("", "")
    records = {
        record.dimension_type: record
        for record in matching_baselines
        if record.snapshot_id == snapshot and (record.date_range_start, record.date_range_end) == date_range
    }
    missing = [dimension for dimension in dimensions if dimension not in records]
    for record in _build_records(data, snapshot, req.universe_id, missing):
        records[record.dimension_type] = record
    ordered = [records[dimension] for dimension in dimensions]
    if not data.dates or (matching_eval and not eval_result.ast_safe):
        return RegimeAnalysisResult(**identity, baselines=ordered)

    selected_dates = eval_result.dates if matching_eval else []
    request = FactorEvalRequest(
        **identity,
        expression=req.expression,
        horizons=[1],
        start_date=date.fromisoformat(selected_dates[0]) if selected_dates else None,
        end_date=date.fromisoformat(selected_dates[-1]) if selected_dates else None,
    )
    panel = factor_panel(data.bars, request)
    labels = _label_table(data.bars, (1,))
    panel = panel.join(labels, on=["trade_date", "_id"], how="left", validate="1:1").sort(
        ["trade_date", "_id"]
    )
    if request.end_date is not None:
        panel = panel.with_columns(
            pl.when(pl.col("trade_date") < request.end_date).then(pl.col(column)).alias(column)
            for column in ("_mark_1", "_forward_1")
        )
    series, links = _daily_slices(data, panel, 1, dimensions)
    from quant_core.validation.formal import _stationary_indices

    size = len(data.dates)
    samples = np.stack(list(_stationary_indices(size, int(np.ceil(size ** (1 / 3))), 500, 42)))
    slices, consistency = _summarize_slices(series, links, samples)
    if matching_eval and not np.isfinite(numeric_column(panel, "factor_value")).any():
        # The frozen regime request cannot carry PIT fundamentals. Preserve
        # conditional evidence from an already-computed matching PIT evaluation.
        persisted = {
            (item.dim, item.bucket): item
            for item in (DimensionSliceItem.model_validate(value) for value in eval_result.dimension_slices)
        }
        slices = [persisted.get((item.dim, item.bucket), item) for item in slices]

    dominant = max(
        (item for item in slices if item.dominant_flag),
        key=lambda item: item.contribution_pct,
        default=None,
    )
    return RegimeAnalysisResult(
        **identity,
        baselines=ordered,
        slices=slices,
        regime_consistency_score=consistency,
        single_dim_dominance_flag=dominant is not None,
        dominant_bucket=dominant.bucket if dominant is not None else None,
        dominant_share_pct=dominant.contribution_pct if dominant is not None else None,
        warning_summary=(
            f"DIMENSION_DOMINANCE: {dominant.dim}/{dominant.bucket} contributes "
            f"{dominant.contribution_pct:.1f}% of positive paper excess"
            if dominant is not None
            else None
        ),
    )
