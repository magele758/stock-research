"""Perold shortfall, matured cross-sectional IC and paper signal manifests.

The input is a frozen daily bar panel. Optional same-row shadow audit fields
carry paper or imported fills and authority decisions. Each shadow_order_id
identifies one parent order with a volume-weighted fill or a list of fills.
Plain price bars support factor diagnostics but cannot attest to executions.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from hashlib import sha256
from math import ceil, isfinite
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from quant_core.factors.engine import (
    BUILTIN_FACTORS,
    _prepare_bars,
    _with_styles,
    factor_panel,
    forward_returns,
    spearman_rank_ic,
)
from quant_core.schemas import (
    AuthorityGateManifest,
    FactorDecayItem,
    FactorEvalRequest,
    PreprocessConfig,
    RebalanceSignalItem,
    ShadowDriftRequest,
    ShadowDriftResult,
)

_IC_WINDOW = 60
_IS_WINDOW = 20
_MIN_COMPARISON_COVERAGE = 0.95
_ALERT_DELTA_BPS = 20
_SCALE_BPS = 10_000
_CRITICAL_FLAGS = (
    "data_stale",
    "authority_stale",
    "rules_mismatch",
    "illegal_fill",
    "reconciliation_failed",
    "kill_latched",
    "authority_replay",
    "authority_revoked",
)


class _ExecutionBreach(ValueError):
    """An observed order or fill conflicts with its parent execution record."""


@dataclass(frozen=True)
class ImplementationShortfall:
    decision_notional: float
    delay: float
    liquidity: float
    unfilled: float
    fees: float
    theoretical_pnl: float
    fill_notional_at_decision: float

    @property
    def total(self) -> float:
        return self.delay + self.liquidity + self.unfilled + self.fees

    @property
    def total_bps(self) -> float:
        return self.total / self.decision_notional * _SCALE_BPS


def compute_implementation_shortfall(
    side: str,
    target_qty: float,
    decision_price: float,
    arrival_price: float,
    end_price: float,
    fills: list[tuple[float, float]],
    fees: float = 0.0,
) -> ImplementationShortfall:
    """Decompose one parent order into signed currency costs at a common timestamp."""
    if side not in {"BUY", "SELL"}:
        raise _ExecutionBreach("Order side must be BUY or SELL")
    values = [target_qty, decision_price, arrival_price, end_price, fees]
    if not all(isfinite(value) for value in values) or min(values[:4]) <= 0 or fees < 0:
        raise ValueError("Shortfall requires positive quantity and prices, and nonnegative fees")
    if any(not (isfinite(qty) and isfinite(price) and qty > 0 and price > 0) for qty, price in fills):
        raise _ExecutionBreach("Fills require positive, finite quantities and prices")
    filled_qty = sum(qty for qty, _ in fills)
    if filled_qty > target_qty + 1e-9:
        raise _ExecutionBreach("Filled quantity exceeds target quantity")
    sign = 1 if side == "BUY" else -1
    return ImplementationShortfall(
        decision_notional=target_qty * decision_price,
        delay=sign * target_qty * (arrival_price - decision_price),
        liquidity=sign * sum(qty * (price - arrival_price) for qty, price in fills),
        unfilled=sign * max(0.0, target_qty - filled_qty) * (end_price - arrival_price),
        fees=fees,
        theoretical_pnl=sign * target_qty * (end_price - decision_price),
        fill_notional_at_decision=min(filled_qty, target_qty) * decision_price,
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def _date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(value[:10]) if isinstance(value, str) else value


def _datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    instant = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(instant, datetime) or instant.tzinfo is None:
        raise ValueError("Authority and availability timestamps require a time zone")
    return instant.astimezone(UTC)


def _due_to_limit(row: dict) -> bool:
    reason = str(row.get("shadow_reject_reason") or "").upper()
    return "LIMIT" in reason or "QUEUE" in reason


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def _shortfall(row: dict) -> ImplementationShortfall:
    fills = row.get("shadow_fills")
    if fills is None:
        filled = float(row["shadow_filled_qty"])
        if filled < 0 or not isfinite(filled):
            raise _ExecutionBreach("Filled quantity must be nonnegative and finite")
        prices = [(filled, float(row["shadow_fill_price"]))] if filled else []
    else:
        prices = [(float(fill["quantity"]), float(fill["price"])) for fill in fills]
    return compute_implementation_shortfall(
        str(row["shadow_side"]).upper(),
        float(row["shadow_target_qty"]),
        float(row["shadow_decision_price"]),
        float(row["shadow_arrival_price"]),
        float(row["shadow_end_price"]),
        prices,
        float(row["shadow_fees"]),
    )


def _daily_shortfall(
    orders: dict[str, dict],
) -> tuple[dict[date, dict[str, float]], bool]:
    totals: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    invalid_fill = False
    for row in orders.values():
        session = _date(row["trade_date"])
        bucket = totals[session]
        bucket["attempts"] += 1
        try:
            shortfall = _shortfall(row)
        except _ExecutionBreach:
            invalid_fill = True
            continue
        except (KeyError, TypeError, ValueError, OverflowError):
            # Missing or unusable marks reduce matched coverage.
            continue
        bucket["matches"] += 1
        bucket["decision_notional"] += shortfall.decision_notional
        bucket["fill_notional"] += shortfall.fill_notional_at_decision
        bucket["theoretical"] += shortfall.theoretical_pnl
        bucket["delay"] += shortfall.delay
        bucket["liquidity"] += shortfall.liquidity
        bucket["unfilled"] += shortfall.unfilled
        bucket["fees"] += shortfall.fees
        if _due_to_limit(row):
            bucket["limit_queue"] += shortfall.unfilled
        expected_is = _number(row.get("backtest_is_bps"))
        if expected_is is not None:
            bucket["paired_notional"] += shortfall.decision_notional
            bucket["is_delta"] += shortfall.total - expected_is / _SCALE_BPS * shortfall.decision_notional
    return totals, invalid_fill


def _decay_item(
    factor_id: str, name: str, daily: list[float | None], crowding: float | None, hard_kill: bool
) -> tuple[FactorDecayItem, bool]:
    # Missing daily cross-sections remain on the exchange calendar. Require a
    # complete rolling window to prevent missing days or tiny cross-sections
    # from turning into observations with zero IC.
    values = np.asarray([value if value is not None else np.nan for value in daily])
    means = np.array([np.mean(values[end - _IC_WINDOW : end]) for end in range(_IC_WINDOW, len(values) + 1)])
    # Historical comparisons are frozen before the current 60-session window.
    reference = means[:-_IC_WINDOW]
    reference = reference[np.isfinite(reference)]
    latest = means[-1] if len(means) else np.nan
    known = bool(len(reference) >= 20 and isfinite(latest))
    crowd = crowding if crowding is not None else 0.0
    historical_percentile = 0.0
    degrading = False
    if known:
        below = np.count_nonzero(reference < latest)
        tied = np.count_nonzero(reference == latest)
        historical_percentile = float((below + 0.5 * tied) / len(reference) * 100)
        floor = float(np.quantile(reference, 0.10))
        degrading = bool(np.all((means[-5:] <= 0) & (means[-5:] < floor)))
    alert = degrading or crowd >= 0.80
    status = "DEGRADING"
    if hard_kill or (degrading and crowd >= 0.95):
        status = "KILL-SWITCH"
    elif known and not alert:
        status = "ACTIVE"
    if hard_kill:
        note = "Execution, freshness, authorization or reconciliation check failed."
    elif degrading:
        note = "RankIC is nonpositive and below the historical 10th percentile for five sessions."
    elif crowd >= 0.80:
        note = "Crowding alert on the latest available cross-section."
    elif not known:
        note = "Insufficient mature 60-session RankIC or disjoint historical comparison windows."
    else:
        note = "RankIC is within the registered historical tolerance."
    if crowding is None:
        note += " Crowding evidence unavailable."
    item = FactorDecayItem(
        factor_id=factor_id,
        name=name,
        rank_ic_60d=float(latest) if isfinite(latest) else 0.0,
        hist_percentile=historical_percentile,
        crowding_index=crowd,
        status=status,
        note=note,
    )
    return item, alert


def _factor_decays(
    bars: pl.DataFrame,
    sessions: list[date],
    hard_kill: bool,
) -> tuple[list[FactorDecayItem], bool]:
    if bars.is_empty():
        return [], False
    # The newest end-of-session decision has no mature next-session label.
    mature_sessions = sessions[:-1]
    preprocess = PreprocessConfig()
    try:
        bars, _ = _with_styles(_prepare_bars(bars), preprocess)
        labels = forward_returns(bars, 1).select("trade_date", "_id", "forward_return")
    except (ValueError, pl.exceptions.ColumnNotFoundError):
        labels = None
    evidence = []
    latest_scores = {}
    latest = bars.filter(pl.col("trade_date") == sessions[-1])
    for factor_id, definition in BUILTIN_FACTORS.items():
        daily = {}
        if labels is not None and mature_sessions:
            try:
                values = factor_panel(bars, FactorEvalRequest(factor_id=factor_id, preprocess=preprocess))
                panel = values.select("trade_date", "_id", "factor_value").join(
                    labels,
                    on=["trade_date", "_id"],
                    how="inner",
                )
                for (session,), section in panel.group_by("trade_date"):
                    daily[session] = spearman_rank_ic(
                        section["factor_value"].to_numpy(),
                        section["forward_return"].to_numpy(),
                    )
                section = values.filter(
                    (pl.col("trade_date") == sessions[-1]) & pl.col("factor_value").is_finite(),
                ).sort("factor_value", "_id", descending=[True, False])
                if section.height >= 50:
                    latest_scores[factor_id] = section["_id"].to_list()
            except (ValueError, pl.exceptions.ColumnNotFoundError):
                # Missing descriptors leave this factor's evidence unresolved.
                pass
        column = f"{factor_id}_crowding"
        crowd = None
        if column in latest.columns:
            observed = np.asarray(latest[column].cast(pl.Float64, strict=False).to_numpy(), dtype=float)
            valid = observed[np.isfinite(observed) & (observed >= 0) & (observed <= 1)]
            if len(valid):
                crowd = float(np.median(valid))
        evidence.append((factor_id, str(definition.get("name", factor_id)), daily, crowd))
    result = []
    alert = False
    for factor_id, name, daily, crowd in evidence:
        overlap_proxy = False
        if crowd is None and factor_id in latest_scores:
            overlaps = []
            scores = latest_scores[factor_id]
            for peer, peer_scores in latest_scores.items():
                common = set(scores).intersection(peer_scores)
                if peer == factor_id or len(common) < 50:
                    continue
                top_n = ceil(len(common) / 10)
                top = [symbol for symbol in scores if symbol in common][:top_n]
                peer_top = [symbol for symbol in peer_scores if symbol in common][:top_n]
                overlaps.append(len(set(top).intersection(peer_top)) / top_n)
            if overlaps:
                crowd = max(overlaps)
                overlap_proxy = True
        item, factor_alert = _decay_item(
            factor_id,
            name,
            [daily.get(day) for day in mature_sessions],
            crowd,
            hard_kill,
        )
        if overlap_proxy:
            item.note += " Crowding proxy is the maximum peer top-decile overlap."
        result.append(item)
        alert |= factor_alert
    return result, alert


def _approved_signals(rows: list[dict], instant: datetime, snapshot: str) -> list[RebalanceSignalItem]:
    approved = []
    seen = set()
    for row in rows:
        if row.get("authority_approved") is not True or row.get("signal_risk_passed") is not True:
            continue
        if row.get("is_suspended") or row.get("authority_snapshot_id") not in (None, snapshot):
            continue
        try:
            available = _datetime(row.get("available_at"))
            reviewed = _datetime(row.get("authority_reviewed_at"))
            expires = _datetime(row.get("authority_expires_at"))
        except ValueError:
            continue
        if available is None or reviewed is None or expires is None:
            continue
        if not available <= instant or not reviewed <= instant < expires:
            continue
        symbol = row.get("symbol") or row.get("listing_id")
        side = str(row.get("signal_side") or "").upper()
        weight = _number(row.get("signal_delta_weight"))
        quantity = _number(row.get("signal_est_shares"))
        if not symbol or symbol in seen or side not in {"BUY", "SELL", "HOLD"}:
            continue
        if weight is None or abs(weight) > 1 or quantity is None or quantity < 0 or int(quantity) != quantity:
            continue
        directions = {
            "BUY": weight > 0 and quantity > 0,
            "SELL": weight < 0 and quantity > 0,
            "HOLD": weight == 0 and quantity == 0,
        }
        if not directions[side]:
            continue
        seen.add(symbol)
        approved.append(
            RebalanceSignalItem(
                symbol=str(symbol),
                name=str(row.get("name") or symbol),
                side=side,
                delta_weight_pct=100 * weight,
                est_shares=int(quantity),
                risk_check="PASS",
            )
        )
    return approved


def compute_shadow_drift(bars_df: pl.DataFrame, req: ShadowDriftRequest) -> ShadowDriftResult:
    """Monitor matched order records and emit a deterministic read-only manifest.

    Order fields are shadow_order_id, shadow_side, shadow_target_qty,
    shadow_decision_price, shadow_arrival_price, shadow_end_price, shadow_fees
    and shadow_fills or shadow_filled_qty/shadow_fill_price. backtest_is_bps
    supplies the registered per-order comparator. <factor_id>_crowding is an
    optional observed score in [0, 1]. Positive shortfall denotes a cost.
    """
    if req.window_days <= 0:
        raise ValueError("window_days must be positive")
    snapshot = req.snapshot_id or "unspecified"
    if bars_df.is_empty():
        panel, sessions = bars_df, []
    else:
        identity = "listing_id" if "listing_id" in bars_df.columns else "symbol"
        panel = bars_df.with_columns(pl.col("trade_date").cast(pl.Date)).sort("trade_date", identity)
        sessions = panel["trade_date"].unique(maintain_order=True).to_list()
    last = sessions[-1] if sessions else date(1970, 1, 1)
    latest = panel.filter(pl.col("trade_date") == last) if sessions else panel
    flag_columns = [flag for flag in _CRITICAL_FLAGS if flag in panel.columns]
    audit_columns = [
        name
        for name in panel.columns
        if name.startswith(("shadow_", "signal_", "authority_"))
        or name
        in {
            "trade_date",
            "symbol",
            "listing_id",
            "name",
            "backtest_is_bps",
            "available_at",
            "market",
            "is_suspended",
        }
    ]
    rows = list(latest.select(audit_columns).iter_rows(named=True)) if sessions else []
    instants = []
    hard_kill = any(latest.select(pl.col(flag).eq(True).any()).item() for flag in flag_columns)
    for row in rows:
        try:
            instant = _datetime(row.get("available_at"))
        except ValueError:
            hard_kill = True
        else:
            if instant is not None:
                instants.append(instant)
    local = (
        ZoneInfo("America/New_York")
        if rows and rows[0].get("market") == "US_EQ"
        else ZoneInfo("Asia/Shanghai")
    )
    close = datetime.combine(last, time(16 if local.key == "America/New_York" else 15), tzinfo=local)
    as_of = max(instants).astimezone(UTC) if instants else close.astimezone(UTC)

    orders = {}
    observation_sessions = sessions[-req.window_days :]
    if "shadow_order_id" in panel.columns and observation_sessions:
        order_rows = panel.filter(
            (pl.col("trade_date") >= observation_sessions[0]) & pl.col("shadow_order_id").is_not_null(),
        ).select(audit_columns)
        for row in order_rows.iter_rows(named=True):
            order_id = str(row["shadow_order_id"])
            if order_id in orders and orders[order_id] != row:
                hard_kill = True
                continue
            orders[order_id] = row

    day_stats, invalid = _daily_shortfall(orders)
    hard_kill |= invalid
    decay, decay_alert = _factor_decays(panel, sessions, hard_kill)
    days = sorted(day for day, bucket in day_stats.items() if bucket["decision_notional"] > 0)
    series = {name: [] for name in ("theoretical", "shadow", "delay", "liquidity", "limit_queue")}
    deltas = []
    total_notional = total_fill = total_cost = paired_notional = 0.0
    for day in days:
        bucket = day_stats[day]
        notional = bucket["decision_notional"]
        cost = sum(bucket[name] for name in ("delay", "liquidity", "unfilled", "fees"))
        total_notional += notional
        total_cost += cost
        total_fill += bucket["fill_notional"]
        paired_notional += bucket["paired_notional"]
        scale = _SCALE_BPS / notional
        for key in ("theoretical", "delay", "liquidity", "limit_queue"):
            series[key].append(bucket[key] * scale)
        series["shadow"].append((bucket["theoretical"] - cost) * scale)
        if day in sessions[-_IS_WINDOW:] and bucket["paired_notional"] > 0:
            deltas.append(bucket["is_delta"] / bucket["paired_notional"] * _SCALE_BPS)
    median_delta = float(np.median(deltas)) if deltas else 0.0
    match_count = sum(bucket["matches"] for bucket in day_stats.values())
    comparison_ok = (
        len(deltas) >= _IS_WINDOW
        and len(orders) > 0
        and total_notional > 0
        and match_count / len(orders) >= _MIN_COMPARISON_COVERAGE
        and paired_notional / total_notional >= _MIN_COMPARISON_COVERAGE
    )
    risk_state = "unknown"
    if hard_kill or any(item.status == "KILL-SWITCH" for item in decay):
        risk_state = "kill"
    elif (comparison_ok and median_delta > _ALERT_DELTA_BPS) or decay_alert:
        risk_state = "alert"
    elif comparison_ok:
        risk_state = "ok"

    signals = (
        _approved_signals(rows, as_of, snapshot)
        if risk_state == "ok" and instants and req.snapshot_id
        else []
    )
    unsigned = {
        "generated_at": as_of.isoformat(),
        "snapshot_id": snapshot,
        "execution_mode": "READ_ONLY_SIGNAL_EXPORT_ONLY",
        "broker_connected": False,
        "risk_passed": bool(signals),
        "signals": [signal.model_dump(mode="json") for signal in signals],
    }
    manifest_key = sha256(_canonical_json(unsigned)).hexdigest()[:16]
    manifest = AuthorityGateManifest(
        **unsigned,
        manifest_id=f"{req.shadow_portfolio_id}-{manifest_key}",
        sha256="",
    )
    digest = sha256(_canonical_json(manifest.model_dump(mode="json", exclude={"sha256"}))).hexdigest()
    manifest = manifest.model_copy(update={"sha256": digest})
    facts = {
        "request": req.model_dump(mode="json"),
        "manifest_sha256": digest,
        "risk_state": risk_state,
        "dates": [day.isoformat() for day in days],
        "daily": {day.isoformat(): day_stats[day] for day in sorted(day_stats)},
        "decay": [item.model_dump(mode="json") for item in decay],
    }
    return ShadowDriftResult(
        shadow_portfolio_id=req.shadow_portfolio_id,
        universe_id=req.universe_id,
        snapshot_id=snapshot,
        risk_state=risk_state,
        median_is_delta_bps=median_delta,
        total_is_bps=total_cost / total_notional * _SCALE_BPS if total_notional else 0.0,
        fill_rate_pct=total_fill / total_notional * 100 if total_notional else 0.0,
        dates=facts["dates"],
        theoretical_excess_bps=series["theoretical"],
        shadow_excess_bps=series["shadow"],
        gap_bps=series["delay"],
        liquidity_bps=series["liquidity"],
        limit_queue_bps=series["limit_queue"],
        factor_decay=decay,
        authority_manifest=manifest,
        fact_fingerprint=sha256(_canonical_json(facts)).hexdigest(),
    )
