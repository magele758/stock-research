"""Point-in-time alpha construction and cross-sectional factor diagnostics."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time
from itertools import combinations, pairwise
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
from scipy.optimize import least_squares
from scipy.stats import rankdata

from quant_core.factors.ast_guard import compile_factor_expression, validate_factor_ast
from quant_core.factors.preprocess import (
    PreprocessDiagnostics,
    eligible_mask,
    neutralize,
    numeric_column,
    preprocess_panel,
    tradability,
    winsorize_mad,
)
from quant_core.schemas import (
    FactorCorrRequest,
    FactorCorrResult,
    FactorEvalRequest,
    FactorEvalResult,
    PreprocessConfig,
)

BUILTIN_FACTORS: dict[str, dict] = {
    "alpha_vp_corr_20d": {
        "name": "量价背离相关性 20D",
        "expression": "-pl.rolling_corr(close.pct_change(), volume_shares.log1p().diff(), window_size=20)",
        "lookback": 20,
    },
    "alpha_mom_resid_60d": {
        "name": "行业市值中性残差动量 60D",
        "expression": "mean_5(resid_industry_size(adj_close[t-5]/adj_close[t-60]-1)/vol_60)",
        "lookback": 64,
    },
    "alpha_earn_sue_pit": {
        "name": "PIT 盈利超预期与质量",
        "expression": "0.70*z(SUE_8Q) + 0.20*z(ROE_TTM) + 0.10*z((OCF-NET_INCOME)/abs(EQUITY))",
        "lookback": 0,
    },
    "alpha_smart_money_vwap": {
        "name": "聪明钱 VWAP 偏离",
        "expression": "mean_10((tail_vwap/vwap-1)*sqrt(tail_volume_shares/volume_shares))",
        "lookback": 19,
    },
}
_HORIZONS = (1, 3, 5, 10, 20, 60)
_MIN_RANK_IC_NAMES = 50
_MIN_MATURE_DAYS = 120
_Q_LABELS = tuple(f"Q{n}" for n in range(1, 11))
_TRADING_DAYS = 252
_PRICE_FIELDS = (
    "close",
    "open",
    "pre_close",
    "high",
    "low",
    "vwap",
    "up_limit",
    "down_limit",
    "adj_factor",
    "volume_shares",
    "amount_ccy",
    "tail_vwap",
    "tail_volume_shares",
    "turnover_rate",
    "float_market_cap",
    "free_float_market_cap",
)
_CN_TZ = ZoneInfo("Asia/Shanghai")
_US_TZ = ZoneInfo("America/New_York")


def _prepare_bars(bars: pl.DataFrame) -> pl.DataFrame:
    fields = set(bars.columns)
    if bars.is_empty() and not fields:
        return pl.DataFrame(
            schema={"trade_date": pl.Date, "symbol": pl.String, "_id": pl.String, "close": pl.Float64},
        )
    if "trade_date" not in fields or not fields.intersection({"listing_id", "symbol"}):
        raise ValueError("Factor bars require trade_date and listing_id or symbol")
    source = "listing_id" if "listing_id" in fields else "symbol"
    aliases = {"volume": "volume_shares", "amount": "amount_ccy"}
    bars = bars.rename({old: new for old, new in aliases.items() if old in fields and new not in fields})
    bars = bars.with_columns(
        pl.col(source).cast(pl.String).alias("_id"),
        pl.col("trade_date").cast(pl.Date),
        *(pl.col(name).cast(pl.Float64, strict=False) for name in _PRICE_FIELDS if name in bars.columns),
    ).sort(["_id", "trade_date"])
    if bars.select(pl.any_horizontal(pl.col("_id", "trade_date").is_null()).any()).item():
        raise ValueError("Factor bar identity and trade_date must be non-null")
    if bars.select(pl.struct("_id", "trade_date").is_duplicated().any()).item():
        raise ValueError("Duplicate factor bars for a listing and trade date")
    return bars


def _number(bars: pl.DataFrame, name: str) -> pl.Expr:
    return pl.col(name) if name in bars.columns else pl.lit(None, dtype=pl.Float64)


def _adjusted_price(bars: pl.DataFrame, name: str) -> pl.Expr:
    adjustment = _number(bars, "adj_factor").fill_null(1.0)
    price = _number(bars, name)
    return pl.when(adjustment.is_finite() & (adjustment > 0) & price.is_finite() & (price > 0)).then(
        price * adjustment,
    )


def _date_sections(frame: pl.DataFrame) -> list[np.ndarray]:
    dates = frame["trade_date"].to_numpy()
    boundaries = np.flatnonzero(dates[1:] != dates[:-1]) + 1
    return np.split(np.arange(frame.height), boundaries) if frame.height else []


def _residual_momentum(bars: pl.DataFrame, config: PreprocessConfig) -> pl.DataFrame:
    close = _adjusted_price(bars, "close")
    momentum = close.shift(5).over("_id") / close.shift(60).over("_id") - 1
    vol = close.pct_change().rolling_std(60).over("_id")
    bars = bars.with_columns(momentum.alias("_momentum"), vol.alias("_mom_vol")).sort(["trade_date", "_id"])
    raw = numeric_column(bars, "_momentum")
    cap = numeric_column(bars, "float_market_cap")
    use_cap = "float_market_cap" in bars.columns
    industry = (
        bars["industry_l1"].cast(pl.String).fill_null("Unknown").to_numpy()
        if "industry_l1" in bars.columns
        else None
    )
    eligible = eligible_mask(bars, config) & np.isfinite(raw)
    if use_cap:
        eligible &= np.isfinite(cap) & (cap > 0)
    residual = np.full(bars.height, np.nan)
    for rows in _date_sections(bars):
        selected = rows[eligible[rows]]
        if len(selected) >= 2:
            residual[selected], _ = neutralize(
                raw[selected],
                industry[selected] if industry is not None else None,
                cap[selected] if use_cap else None,
            )
    return (
        bars.with_columns(pl.Series("_momentum_residual", residual, nan_to_null=True))
        .sort(["_id", "trade_date"])
        .with_columns(
            pl.when(pl.col("_mom_vol") > 0)
            .then(pl.col("_momentum_residual") / pl.col("_mom_vol"))
            .rolling_mean(5)
            .over("_id")
            .alias("factor_raw"),
        )
        .drop("_momentum", "_mom_vol", "_momentum_residual")
    )


def _timestamp(value: datetime | str, zone: ZoneInfo = _CN_TZ) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=zone).astimezone(UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _financial_state(
    observations: dict[str, dict[date, float]],
) -> tuple[float, float, float]:
    sue = np.nan
    for metric in ("eps", "net_profit_deducted"):
        periods = sorted(observations.get(metric, {}).items())
        # A seasonally differenced series compares reports with the same fiscal month.
        annual_lags = {(period.year, period.month): value for period, value in periods}
        changes = [
            (period, value - annual_lags[period.year - 1, period.month])
            for period, value in periods
            if (period.year - 1, period.month) in annual_lags
        ]
        if len(changes) < 5 or changes[-1][0] != periods[-1][0]:
            continue
        history = np.array([value for _, value in changes[-9:-1]])
        scale = history.std(ddof=1)
        if scale > 0:
            sue = float((changes[-1][1] - history.mean()) / scale)
            break

    roe_by_period = observations.get("roe_ttm", {})
    roe = roe_by_period[max(roe_by_period)] if roe_by_period else np.nan
    ocf = observations.get("ocf", {})
    equity = observations.get("equity", {})
    profit = observations.get("net_profit_deducted", {})
    common = ocf.keys() & equity.keys() & profit.keys()
    quality = np.nan
    if common:
        period = max(common)
        if equity[period] > 0:
            quality = (ocf[period] - profit[period]) / equity[period]
    return sue, roe, quality


def _pit_earnings(
    bars: pl.DataFrame,
    facts: pl.DataFrame | None,
    config: PreprocessConfig,
) -> tuple[pl.DataFrame, list[str]]:
    bars = bars.sort(["trade_date", "_id"])
    eligible = eligible_mask(bars, config)
    descriptors = np.full((bars.height, 3), np.nan)
    if facts is None or facts.is_empty():
        # A PIT-joined canonical panel may contain already computed financial descriptors.
        descriptors[:] = np.column_stack(
            [numeric_column(bars, name) for name in ("pit_sue", "roe_ttm", "pit_cash_quality")],
        )
        release_fields = tuple(
            name for name in ("financial_published_at", "financial_available_at") if name in bars.columns
        )
        if release_fields:
            for i, row in enumerate(bars.iter_rows(named=True)):
                zone, cutoff = _decision_time(row)
                if any(row[name] is None or _timestamp(row[name], zone) > cutoff for name in release_fields):
                    descriptors[i] = np.nan
        if not np.isfinite(descriptors[:, 0]).any():
            return bars.with_columns(pl.lit(None, dtype=pl.Float64).alias("factor_raw")), [
                "PIT earnings surprise has no admissible observations",
            ]
    else:
        required = {"report_period_end", "metric_code", "value", "published_at"}
        if not required.issubset(facts.columns):
            raise ValueError(
                "PIT financial data requires report_period_end, metric_code, value and published_at"
            )
        if "listing_id" in bars.columns and "listing_id" in facts.columns:
            keys = ("listing_id",)
        elif "issuer_id" in bars.columns and "issuer_id" in facts.columns:
            keys = ("issuer_id",)
        elif "market" in bars.columns and "market" in facts.columns:
            keys = ("market", "symbol")
        else:
            keys = ("symbol",)
        if any(key not in bars.columns or key not in facts.columns for key in keys):
            raise ValueError("PIT financial facts must share the listing, issuer or symbol keys of the bars")
        events = defaultdict(list)
        used_metrics = {"eps", "net_profit_deducted", "roe_ttm", "ocf", "equity"}
        for row in facts.iter_rows(named=True):
            if row["metric_code"] not in used_metrics or row["published_at"] is None:
                continue
            zone = _US_TZ if row.get("market") == "US_EQ" else _CN_TZ
            released = _timestamp(row["published_at"], zone)
            period = row["report_period_end"]
            if isinstance(period, datetime):
                period = period.date()
            elif isinstance(period, str):
                period = date.fromisoformat(period)
            if not isinstance(period, date):
                continue
            value = None if row.get("is_withdrawn") or row["value"] is None else float(row["value"])
            if value is not None and not np.isfinite(value):
                continue
            key = tuple(row[field] for field in keys)
            events[key].append(
                (released, int(row.get("revision_seq") or 1), row["metric_code"], period, value)
            )

        for key, filings in events.items():
            filings.sort(key=lambda item: item[:4])
            for previous, current in pairwise(filings):
                if previous[:4] == current[:4] and previous[-1] != current[-1]:
                    raise ValueError(f"Conflicting PIT financial revisions for {key!r}")
        columns = list(
            dict.fromkeys(
                [
                    *keys,
                    "trade_date",
                    *(name for name in ("market", "board", "decision_at") if name in bars.columns),
                ]
            )
        )
        decisions = defaultdict(list)
        for i, row in enumerate(bars.select(columns).iter_rows(named=True)):
            key = tuple(row[field] for field in keys)
            if key in events:
                _, cutoff = _decision_time(row)
                decisions[key].append((cutoff, i))

        for key, clock in decisions.items():
            filings = events[key]
            known: dict[str, dict[date, float]] = defaultdict(dict)
            revisions: dict[tuple[str, date], int] = {}
            state = (np.nan, np.nan, np.nan)
            end = 0
            for cutoff, i in sorted(clock):
                start = end
                while end < len(filings) and filings[end][0] <= cutoff:
                    _, revision, metric, period, value = filings[end]
                    identity = (metric, period)
                    if revision >= revisions.get(identity, 0):
                        revisions[identity] = revision
                        if value is not None:
                            known[metric][period] = value
                        else:
                            known[metric].pop(period, None)
                    end += 1
                if end != start:
                    state = _financial_state(known)
                descriptors[i] = state

    raw = np.full(bars.height, np.nan)
    for rows in _date_sections(bars):
        selected = rows[eligible[rows]]
        if not selected.size:
            continue
        scaled = np.full((selected.size, 3), np.nan)
        for j in range(3):
            values = descriptors[selected, j]
            if config.winsorize_mad:
                values = winsorize_mad(values, config.mad_k)
            finite = np.isfinite(values)
            if finite.any():
                scale = values[finite].std()
                scaled[finite, j] = (values[finite] - values[finite].mean()) / scale if scale > 0 else 0.0
        mix = np.array([0.7, 0.2, 0.1])
        valid = np.isfinite(scaled[:, 0])
        raw[selected[valid]] = np.nansum(scaled[valid] * mix, axis=1) / (np.isfinite(scaled[valid]) @ mix)
    warnings = [] if np.isfinite(raw).any() else ["Insufficient PIT earnings history for seasonal surprise"]
    return bars.with_columns(pl.Series("factor_raw", raw, nan_to_null=True)), warnings


def _decision_time(row: dict) -> tuple[ZoneInfo, datetime]:
    us = row.get("market") == "US_EQ" or row.get("board") in ("NYSE", "NASDAQ")
    zone = _US_TZ if us else _CN_TZ
    if row.get("decision_at") is not None:
        return zone, _timestamp(row["decision_at"], zone)
    close = time(16) if us else time(15)
    return zone, datetime.combine(row["trade_date"], close, zone).astimezone(UTC)


def _raw_factor(
    bars: pl.DataFrame,
    req: FactorEvalRequest,
    pit_fin_df: pl.DataFrame | None,
) -> tuple[pl.DataFrame, list[str]]:
    if req.expression is not None:
        expr = compile_factor_expression(req.expression)
        return bars.with_columns(expr.cast(pl.Float64, strict=False).alias("factor_raw")), []
    if req.factor_id not in BUILTIN_FACTORS:
        raise ValueError(f"Unknown factor {req.factor_id!r}")
    if req.factor_id == "alpha_mom_resid_60d":
        return _residual_momentum(bars, req.preprocess), []
    if req.factor_id == "alpha_earn_sue_pit":
        return _pit_earnings(bars, pit_fin_df, req.preprocess)
    if req.factor_id == "alpha_vp_corr_20d":
        rv = _adjusted_price(bars, "close").pct_change().over("_id")
        volume = _number(bars, "volume_shares")
        dv = pl.when(volume >= 0).then(volume.log1p()).diff().over("_id")
        expr = -pl.rolling_corr(rv, dv, window_size=20).over("_id")
        return bars.with_columns(expr.alias("factor_raw")), []

    volume = _number(bars, "volume_shares")
    vwap = pl.coalesce(_number(bars, "vwap"), _number(bars, "amount_ccy") / volume)
    tail = _number(bars, "tail_vwap")
    tail_volume = _number(bars, "tail_volume_shares")
    valid_tail = (tail > 0) & (tail_volume > 0) & (tail_volume <= volume)
    adv = volume.shift(1).rolling_mean(10).over("_id")
    if {"tail_vwap", "tail_volume_shares"}.issubset(bars.columns):
        expr = pl.when(valid_tail).then((tail / vwap - 1) * (tail_volume / volume).sqrt())
        warnings = ["Missing 14:30–15:00 VWAP inputs leave the corresponding smart-money scores unavailable"]
    else:
        expr = (_number(bars, "close") / vwap - 1) * pl.when(adv > 0).then((volume / adv).sqrt())
        warnings = ["Daily-bar smart-money proxy uses close versus VWAP weighted by relative volume"]
    return bars.with_columns(
        pl.when((vwap > 0) & (volume > 0)).then(expr).rolling_mean(10).over("_id").alias("factor_raw"),
    ), warnings


def _with_styles(bars: pl.DataFrame, config: PreprocessConfig) -> tuple[pl.DataFrame, tuple[str, ...]]:
    if not config.orthogonalize_barra or bars.is_empty():
        return bars, ()
    from quant_core.risk.barra_opt import BARRA_STYLES, build_barra_exposure_panel

    existing = tuple(name for name in BARRA_STYLES if name in bars.columns)
    if existing or not {"symbol", "close", "float_market_cap"}.issubset(bars.columns):
        return bars, existing
    # The risk model groups historical descriptors by symbol. Supply the stable
    # identity here and retain the user's symbol and original columns.
    exposure = build_barra_exposure_panel(bars.with_columns(pl.col("_id").alias("symbol")))
    return bars.join(
        exposure.select("_id", "trade_date", *BARRA_STYLES),
        on=["_id", "trade_date"],
        how="left",
        validate="1:1",
    ).sort(["_id", "trade_date"]), BARRA_STYLES


def _compute_panel(
    bars: pl.DataFrame,
    req: FactorEvalRequest,
    style_columns: tuple[str, ...],
    pit_fin_df: pl.DataFrame | None,
) -> tuple[pl.DataFrame, PreprocessDiagnostics, list[str]]:
    panel, notes = _raw_factor(bars, req, pit_fin_df)
    if req.start_date is not None:
        panel = panel.filter(pl.col("trade_date") >= req.start_date)
    panel, diagnostics = preprocess_panel(
        panel,
        req.preprocess,
        style_columns=style_columns,
        allow_imputation=req.expression is not None or req.factor_id != "alpha_earn_sue_pit",
    )
    return panel, diagnostics, notes


def _cutoff(bars: pl.DataFrame, req: FactorEvalRequest) -> pl.DataFrame:
    if req.start_date is not None and req.end_date is not None and req.start_date > req.end_date:
        raise ValueError("start_date must be no later than end_date")
    return bars.filter(pl.col("trade_date") <= req.end_date) if req.end_date is not None else bars


def factor_panel(
    bars_df: pl.DataFrame,
    req: FactorEvalRequest,
    pit_fin_df: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Compute causal raw and preprocessed factor values anchored to the signal date."""
    bars = _cutoff(_prepare_bars(bars_df), req)
    bars, style_columns = _with_styles(bars, req.preprocess)
    panel, _, _ = _compute_panel(bars, req, style_columns, pit_fin_df)
    return panel


def compute_factor_scores(
    bars_df: pl.DataFrame,
    factor_id: str,
    pit_fin_df: pl.DataFrame | None = None,
    *,
    expression: str | None = None,
) -> pl.DataFrame:
    """Return score rows at their decision date for the service signal builder."""
    panel = factor_panel(bars_df, FactorEvalRequest(factor_id=factor_id, expression=expression), pit_fin_df)
    symbol = pl.col("symbol") if "symbol" in panel.columns else pl.col("_id").alias("symbol")
    return panel.select("trade_date", symbol, pl.col("factor_value").alias("score"))


def _aligned_quotes(bars: pl.DataFrame) -> pl.DataFrame:
    sessions = bars.select("trade_date").unique().sort("trade_date").with_row_index("_session")
    suspended, up, down = tradability(bars)
    return bars.with_columns(
        pl.Series("_suspended", suspended),
        pl.Series("_one_price_up", up),
        pl.Series("_one_price_down", down),
        _adjusted_price(bars, "open").alias("_adjusted_open"),
        _adjusted_price(bars, "close").alias("_adjusted_close"),
    ).join(
        sessions.with_columns(pl.col("_session").cast(pl.Int64)), on="trade_date", how="left", validate="m:1"
    )


def _label_table(bars: pl.DataFrame, horizons: tuple[int, ...]) -> pl.DataFrame:
    quotes = _aligned_quotes(bars)
    entry = quotes.select(
        "_id",
        (pl.col("_session") - 1).alias("_session"),
        pl.when(~pl.col("_suspended") & ~pl.col("_one_price_up"))
        .then(pl.col("_adjusted_open"))
        .alias("_entry"),
    )
    labels = quotes.select("_id", "_session", "trade_date").join(
        entry,
        on=["_id", "_session"],
        how="left",
        validate="1:1",
    )
    for h in horizons:
        exit_quote = quotes.select(
            "_id",
            (pl.col("_session") - h).alias("_session"),
            (
                pl.col("_adjusted_close")
                if h == 1
                else pl.when(~pl.col("_suspended") & ~pl.col("_one_price_down")).then(
                    pl.col("_adjusted_close")
                )
            ).alias("_exit"),
        )
        labels = labels.join(exit_quote, on=["_id", "_session"], how="left", validate="1:1")
        expr = pl.col("_exit") / pl.col("_entry") - 1
        if h == 1:
            labels = labels.with_columns(expr.alias("_mark_1"))
            exit_available = quotes.select(
                "_id",
                (pl.col("_session") - 1).alias("_session"),
                (~pl.col("_suspended") & ~pl.col("_one_price_down")).alias("_sellable"),
            )
            labels = labels.join(exit_available, on=["_id", "_session"], how="left", validate="1:1")
            expr = pl.when(pl.col("_sellable")).then(expr)
        labels = labels.with_columns(expr.alias(f"_forward_{h}")).drop("_exit")
    return labels.drop("_session", "_entry", *(["_sellable"] if "_sellable" in labels.columns else []))


def forward_returns(bars_df: pl.DataFrame, horizon: int) -> pl.DataFrame:
    """Align a next-session entry and a mature, adjusted close label to the decision."""
    if type(horizon) is not int or horizon < 1:
        raise ValueError("A forward horizon must be a positive integer")
    bars = _prepare_bars(bars_df)
    labels = _label_table(bars, (horizon,))
    return bars.join(
        labels.select("_id", "trade_date", pl.col(f"_forward_{horizon}").alias("forward_return")),
        on=["_id", "trade_date"],
        how="left",
        validate="1:1",
    ).sort(["trade_date", "_id"])


def _correlation(a: np.ndarray, b: np.ndarray, method: str, min_names: int) -> float | None:
    common = np.isfinite(a) & np.isfinite(b)
    if common.sum() < min_names:
        return None
    x, y = a[common], b[common]
    if np.ptp(x) == 0 or np.ptp(y) == 0:
        return None
    if method == "spearman":
        x, y = rankdata(x, method="average"), rankdata(y, method="average")
    x = x - x.mean()
    y = y - y.mean()
    return float(np.clip(np.dot(x, y) / (np.linalg.norm(x) * np.linalg.norm(y)), -1.0, 1.0))


def spearman_rank_ic(
    values: np.ndarray,
    returns: np.ndarray,
    min_names: int = _MIN_RANK_IC_NAMES,
) -> float | None:
    return _correlation(
        np.asarray(values, dtype=float), np.asarray(returns, dtype=float), "spearman", min_names
    )


def newey_west_t_stat(ic: np.ndarray, horizon: int) -> tuple[float, int]:
    """Estimate the mean's Bartlett HAC t-stat using trading-session lags."""
    ic = np.asarray(ic, dtype=float)
    observed = np.isfinite(ic)
    n = int(observed.sum())
    if n < 2:
        return 0.0, 0
    bandwidth = min(len(ic) - 1, max(horizon - 1, int(4 * (n / 100) ** (2 / 9))))
    residual = np.where(observed, ic - ic[observed].mean(), 0.0)
    lrv = float(np.dot(residual, residual) / n)
    for lag in range(1, bandwidth + 1):
        lrv += 2 * (1 - lag / (bandwidth + 1)) * float(np.dot(residual[lag:], residual[:-lag])) / n
    if lrv <= 0:
        return 0.0, bandwidth
    return float(ic[observed].mean() / np.sqrt(lrv / n)), bandwidth


def _daily_ic(panel: pl.DataFrame, target: str) -> np.ndarray:
    factor = numeric_column(panel, "factor_value")
    labels = numeric_column(panel, target)
    results = np.full(len(_date_sections(panel)), np.nan)
    for i, rows in enumerate(_date_sections(panel)):
        ic = spearman_rank_ic(factor[rows], labels[rows])
        if ic is not None:
            results[i] = ic
    return results


def _average(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    return float(finite.mean()) if len(finite) else 0.0


def _half_life(decay: dict[int, float]) -> float:
    if len(decay) < 3 or 1 not in decay or decay[1] == 0:
        return 0.0
    x = np.array(list(decay)) - 1
    y = np.array(list(decay.values())) * np.sign(decay[1])
    fit = least_squares(
        lambda parameters: parameters[0] * np.exp(-parameters[1] * x) - y,
        x0=(max(y[0], 1e-6), 0.05),
        bounds=((0, 0), (np.inf, np.inf)),
    )
    if not fit.success or fit.x[1] < 1e-6 or fit.x[0] < 1e-10:
        return 0.0
    return float(np.log(2) / fit.x[1])


def _industry_ic(panel: pl.DataFrame, target: str) -> dict[str, float]:
    if "industry_l1" not in panel.columns:
        return {}
    result = defaultdict(list)
    groups = panel.group_by(["trade_date", "industry_l1"], maintain_order=True)
    for (_, industry), group in groups:
        if industry is not None:
            ic = spearman_rank_ic(
                numeric_column(group, "factor_value"),
                numeric_column(group, target),
                min_names=10,
            )
            if ic is not None:
                result[industry].append(ic)
    return {name: float(np.mean(values)) for name, values in sorted(result.items())}


def _paper_groups(
    panel: pl.DataFrame,
    last_session: date | None,
) -> tuple[dict[str, list[float]], list[float], float, float, float, float, int]:
    """Daily, next-open deciles. Unfilled intentions retain their allocation in cash."""
    values = numeric_column(panel, "factor_value")
    returns = numeric_column(panel, "_mark_1")
    eligible = panel["factor_eligible"].to_numpy()
    dates = panel["trade_date"].to_list()
    ids = panel["_id"].to_list()
    sections = _date_sections(panel)
    daily = np.zeros((len(sections), 12))
    active_days = np.zeros(len(sections), dtype=bool)
    turnovers = []
    pretrade = {"": 1.0}

    for i, rows in enumerate(sections):
        if dates[rows[0]] == last_session:
            continue
        names = rows[np.isfinite(values[rows])]
        if len(names) < 10:
            continue
        ordered = names[np.argsort(values[names], kind="stable")]
        deciles = np.array_split(ordered, 10)
        for q, members in enumerate(deciles):
            daily[i, q] = np.nan_to_num(returns[members], nan=0.0).mean()
        daily[i, 10] = (daily[i, 9] - daily[i, 0]) / 2
        daily[i, 11] = np.nan_to_num(returns[rows[eligible[rows]]], nan=0.0).mean()
        active_days[i] = True
        top = deciles[-1]
        target = {ids[row]: 1 / len(top) for row in top if np.isfinite(returns[row])}
        target[""] = 1 - sum(target.values())
        turnovers.append(
            0.5
            * sum(
                abs(target.get(key, 0.0) - pretrade.get(key, 0.0))
                for key in sorted(target.keys() | pretrade.keys())
            )
        )
        ending = {ids[row]: target[ids[row]] * (1 + returns[row]) for row in top if ids[row] in target}
        ending[""] = target[""]
        nav = sum(ending.values())
        pretrade = {key: value / nav for key, value in ending.items()} if nav > 0 else {"": 1.0}

    # Use a daily open-to-close window for the chart so h-day labels never overlap in NAV.
    compounded = np.cumprod(1 + daily, axis=0)
    navs = {key: compounded[:, i].tolist() for i, key in enumerate((*_Q_LABELS, "LS", "BENCH"))}
    periods = sum(dates[rows[0]] != last_session for rows in sections)
    last = compounded[-1] if len(compounded) else np.ones(12)
    annualized = np.maximum(0.0, last) ** (_TRADING_DAYS / max(1, periods)) - 1
    monotonicity = spearman_rank_ic(np.arange(10), annualized[:10], min_names=10)
    return (
        navs,
        annualized[:10].tolist(),
        float(annualized[10]),
        float(annualized[9] - annualized[11]),
        float(np.mean(turnovers)) if turnovers else 0.0,
        monotonicity or 0.0,
        int(active_days.sum()),
    )


def _library_matrix(
    panels: dict[str, pl.DataFrame],
    method: str,
) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray]:
    labels = list(panels)
    shape = (len(labels), len(labels))
    signed = np.zeros(shape)
    absolute = np.zeros(shape)
    coverage = np.zeros(shape)
    values = {
        key: panel.select("_id", "trade_date", "factor_eligible", "factor_value")
        for key, panel in panels.items()
    }
    for i, j in combinations(range(len(labels)), 2):
        pair = (
            values[labels[i]]
            .join(
                values[labels[j]].select("_id", "trade_date", pl.col("factor_value").alias("_peer")),
                on=["_id", "trade_date"],
                how="left",
                validate="1:1",
            )
            .sort(["trade_date", "_id"])
        )
        elig = pair["factor_eligible"].to_numpy()
        left, right = numeric_column(pair, "factor_value"), numeric_column(pair, "_peer")
        correlations = []
        joint_names = 0
        for rows in _date_sections(pair):
            usable = rows[elig[rows]]
            rho = _correlation(left[usable], right[usable], method, min_names=3)
            if rho is not None:
                correlations.append(rho)
                joint_names += int((np.isfinite(left[usable]) & np.isfinite(right[usable])).sum())
        if correlations:
            signed[i, j] = signed[j, i] = np.mean(correlations)
            absolute[i, j] = absolute[j, i] = np.mean(np.abs(correlations))
            coverage[i, j] = coverage[j, i] = joint_names / elig.sum()
    for i, panel in enumerate(panels.values()):
        observed = np.isfinite(numeric_column(panel, "factor_value"))
        if observed.any():
            signed[i, i] = absolute[i, i] = 1.0
            coverage[i, i] = float(observed.sum() / panel["factor_eligible"].sum())
    return labels, signed, absolute, coverage


def compute_factor_correlation(bars_df: pl.DataFrame, req: FactorCorrRequest) -> FactorCorrResult:
    """Estimate per-date common-sample correlations of preprocessed library signals."""
    if any(factor_id not in BUILTIN_FACTORS for factor_id in req.factor_ids):
        unknown = sorted(set(req.factor_ids).difference(BUILTIN_FACTORS))
        raise ValueError(f"Unknown factor IDs: {unknown}")
    config = PreprocessConfig()
    bars, style_columns = _with_styles(_prepare_bars(bars_df), config)
    panels = {}
    for factor_id in dict.fromkeys(req.factor_ids):
        eval_request = FactorEvalRequest(factor_id=factor_id, preprocess=config)
        panels[factor_id], _, _ = _compute_panel(bars, eval_request, style_columns, None)
    labels, signed, absolute, coverage = _library_matrix(panels, req.method)
    pairs = [
        {
            "factor_a": labels[i],
            "factor_b": labels[j],
            "corr": float(signed[i, j]),
            "mean_abs_corr": float(absolute[i, j]),
            "coverage_pct": float(coverage[i, j] * 100),
        }
        for i, j in combinations(range(len(labels)), 2)
        if absolute[i, j] >= 0.70
    ]
    return FactorCorrResult(
        universe_id=req.universe_id,
        snapshot_id=req.snapshot_id or "unversioned",
        method=req.method,
        labels=labels,
        matrix=signed.tolist(),
        high_corr_pairs=pairs,
        max_abs_corr=float(np.max(np.triu(np.abs(signed), k=1))) if labels else 0.0,
    )


def _admission_checks(
    *,
    rank_ic: float = 0.0,
    icir: float = 0.0,
    hac: float = 0.0,
    bandwidth: int = 0,
    turnover: float = 0.0,
    correlation: float = 0.0,
    monotonicity: float = 0.0,
    diagnostics: PreprocessDiagnostics | None = None,
    coverage_required: float = 0.80,
    mature_days: int = 0,
    group_days: int = 0,
    peer_coverage: dict[str, float] | None = None,
    spread: float = 0.0,
) -> list[dict]:
    coverage = diagnostics.coverage if diagnostics is not None else 0.0
    evidence = coverage >= max(0.80, coverage_required) and mature_days >= _MIN_MATURE_DAYS
    peers_available = bool(peer_coverage) and min(peer_coverage.values()) >= 0.80
    return [
        {
            "key": "rank_ic",
            "name": "RankIC",
            "value": rank_ic,
            "operator": ">=",
            "threshold": 0.03,
            "passed": evidence and rank_ic >= 0.03,
            "coverage_pct": coverage * 100,
            "mature_days": mature_days,
            "min_mature_days": _MIN_MATURE_DAYS,
            "min_names_per_day": _MIN_RANK_IC_NAMES,
        },
        {
            "key": "icir_ann",
            "name": "年化 ICIR",
            "value": icir,
            "operator": ">=",
            "threshold": 1.5,
            "passed": evidence and icir >= 1.5,
        },
        {
            "key": "nw_t_stat",
            "name": "Newey–West t",
            "value": hac,
            "operator": ">=",
            "threshold": 3.0,
            "passed": evidence and hac >= 3.0,
            "bandwidth": bandwidth,
        },
        {
            "key": "daily_turnover_oneway",
            "name": "日均单边换手",
            "value": turnover,
            "operator": "<=",
            "threshold": 0.15,
            "passed": group_days >= _MIN_MATURE_DAYS and turnover <= 0.15,
            "decile_days": group_days,
            "basis": "Top decile plus cash, marked pretrade weights",
        },
        {
            "key": "max_corr_in_library",
            "name": "库内最高相关",
            "value": correlation,
            "operator": "<",
            "threshold": 0.70,
            "passed": bool(peers_available and correlation < 0.70),
            "peer_common_coverage_pct": {
                label: value * 100 for label, value in (peer_coverage or {}).items()
            },
        },
        {
            "key": "monotonicity_spearman",
            "name": "分层单调性",
            "value": monotonicity,
            "operator": ">=",
            "threshold": 0.90,
            "passed": group_days >= _MIN_MATURE_DAYS and monotonicity >= 0.90 and spread > 0,
            "top_minus_bottom_ann": spread,
            "basis": "Paper deciles, before execution costs",
        },
    ]


def evaluate_factor(
    bars_df: pl.DataFrame,
    req: FactorEvalRequest,
    pit_fin_df: pl.DataFrame | None = None,
) -> FactorEvalResult:
    """Evaluate causal exposure, label alignment, cross-sectional IC and paper deciles."""
    if not req.horizons or any(h < 1 for h in req.horizons):
        raise ValueError("FactorEvalRequest.horizons must contain positive trading-session counts")
    if req.expression is None and req.factor_id not in BUILTIN_FACTORS:
        raise ValueError(f"Unknown factor {req.factor_id!r}")
    expression = (
        req.expression if req.expression is not None else BUILTIN_FACTORS[req.factor_id]["expression"]
    )
    safe, warnings = validate_factor_ast(expression) if req.expression is not None else (True, [])
    identity = {
        "factor_id": req.factor_id,
        "expression": expression,
        "universe_id": req.universe_id,
        "snapshot_id": req.snapshot_id or "unversioned",
        "ast_safe": safe,
    }
    if not safe:
        return FactorEvalResult(
            **identity,
            ast_warnings=warnings,
            rank_ic_mean=0,
            ic_std=0,
            icir_ann=0,
            nw_t_stat=0,
            ic_win_rate=0,
            long_short_ann=0,
            top_excess_ann=0,
            daily_turnover_oneway=0,
            half_life_days=0,
            monotonicity_spearman=0,
            max_corr_in_library=0,
            max_corr_peer="",
            coverage_pct=0,
            admission_passed=False,
            gate_checks=_admission_checks(),
            residual_exposures={},
            dates=[],
            decile_navs={key: [] for key in (*_Q_LABELS, "LS", "BENCH")},
            decile_ann_returns=[0.0] * 10,
            rolling_60d_ic=[],
            cum_ic=[],
            ic_decay={f"{h}D": 0.0 for h in _HORIZONS},
            industry_ic={},
            corr_matrix_labels=[],
            corr_matrix_values=[],
        )

    bars = _cutoff(_prepare_bars(bars_df), req)
    bars, styles = _with_styles(bars, req.preprocess)
    panel, diagnostics, notes = _compute_panel(bars, req, styles, pit_fin_df)
    warnings.extend(notes)
    horizons = tuple(sorted(set(_HORIZONS) | set(req.horizons)))
    horizon = req.horizons[0]
    panel = panel.join(
        _label_table(bars, horizons),
        on=["_id", "trade_date"],
        how="left",
        validate="1:1",
    ).sort(["trade_date", "_id"])
    dates = panel["trade_date"].unique(maintain_order=True).to_list()
    ic_by_horizon = {h: _daily_ic(panel, f"_forward_{h}") for h in horizons}
    daily_ic = ic_by_horizon[horizon]
    observed = daily_ic[np.isfinite(daily_ic)]
    avg = _average(daily_ic)
    stdev = float(observed.std(ddof=1)) if len(observed) > 1 else 0.0
    icir = float(avg / stdev * np.sqrt(_TRADING_DAYS)) if stdev > 0 else 0.0
    nw_stat, bandwidth = newey_west_t_stat(daily_ic, horizon)
    decay = {h: _average(ic_by_horizon[h]) for h in horizons if np.isfinite(ic_by_horizon[h]).any()}
    half_life = _half_life({h: decay[h] for h in _HORIZONS if h in decay})
    if half_life == 0:
        warnings.append("IC half-life is unavailable without a supported positive decay rate")
    if len(observed) < _MIN_MATURE_DAYS:
        warnings.append(f"Insufficient mature RankIC evidence: {len(observed)} usable decision dates")
    missing_decay = [f"{h}D" for h in horizons if h not in decay]
    if missing_decay:
        warnings.append(f"No valid RankIC observations at: {', '.join(missing_decay)}")
    if diagnostics.omitted_design:
        warnings.append(
            f"Unavailable neutralization exposures: {', '.join(sorted(diagnostics.omitted_design))}"
        )
    if diagnostics.industry_filled or diagnostics.market_filled:
        warnings.append(
            f"Imputed {diagnostics.industry_filled} observations by industry median and "
            f"{diagnostics.market_filled} by market median",
        )
    if diagnostics.low_coverage_days or diagnostics.constant_days:
        warnings.append(
            f"Excluded {diagnostics.low_coverage_days} low-coverage and {diagnostics.constant_days} constant days",
        )
    warnings.append("Decile NAVs measure next-session open-to-close paper returns before execution costs")

    navs, annual, ls, top, turnover, monotonicity, group_days = _paper_groups(
        panel,
        bars["trade_date"].max(),
    )
    panels = {req.factor_id: panel}
    peers = [
        factor_id for factor_id in BUILTIN_FACTORS if req.expression is not None or factor_id != req.factor_id
    ]
    for factor_id in peers:
        peer_req = req.model_copy(update={"factor_id": factor_id, "expression": None})
        peer, _, _ = _compute_panel(bars, peer_req, styles, pit_fin_df)
        label = factor_id if factor_id != req.factor_id else f"{factor_id} [built-in]"
        panels[label] = peer
    labels, correlations, absolute, coverage = _library_matrix(panels, method="spearman")
    joint_coverage = {label: float(coverage[0, i]) for i, label in enumerate(labels) if i > 0}
    available = [i for i in range(1, len(labels)) if coverage[0, i] > 0]
    max_idx = max(available, key=lambda i: absolute[0, i]) if available else None
    max_corr = float(absolute[0, max_idx]) if max_idx is not None else 0.0
    if any(value < 0.80 for value in joint_coverage.values()):
        warnings.append("Library correlation evidence is incomplete for peers below 80% common coverage")
    checks = _admission_checks(
        rank_ic=avg,
        icir=icir,
        hac=nw_stat,
        bandwidth=bandwidth,
        turnover=turnover,
        correlation=max_corr,
        monotonicity=monotonicity,
        diagnostics=diagnostics,
        coverage_required=req.preprocess.min_coverage,
        mature_days=len(observed),
        group_days=group_days,
        peer_coverage=joint_coverage,
        spread=annual[-1] - annual[0],
    )
    return FactorEvalResult(
        **identity,
        ast_warnings=warnings,
        rank_ic_mean=avg,
        ic_std=stdev,
        icir_ann=icir,
        nw_t_stat=nw_stat,
        ic_win_rate=float(np.mean(observed > 0)) if len(observed) else 0.0,
        long_short_ann=ls,
        top_excess_ann=top,
        daily_turnover_oneway=turnover,
        half_life_days=half_life,
        monotonicity_spearman=monotonicity,
        max_corr_in_library=max_corr,
        max_corr_peer=labels[max_idx] if max_idx is not None else "",
        coverage_pct=diagnostics.coverage * 100,
        admission_passed=all(check["passed"] for check in checks),
        gate_checks=checks,
        residual_exposures=diagnostics.residual_exposures,
        dates=[day.isoformat() for day in dates],
        decile_navs=navs,
        decile_ann_returns=annual,
        rolling_60d_ic=[_average(daily_ic[max(0, i - 59) : i + 1]) for i in range(len(daily_ic))],
        cum_ic=np.cumsum(np.nan_to_num(daily_ic, nan=0.0)).tolist(),
        ic_decay={f"{h}D": decay.get(h, 0.0) for h in horizons},
        industry_ic=_industry_ic(panel, f"_forward_{horizon}"),
        corr_matrix_labels=labels,
        corr_matrix_values=correlations.tolist(),
    )
