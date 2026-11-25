"""Historical equity limits and a daily-bar fill model on raw prices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from math import floor, isclose, isfinite, sqrt
from typing import Literal

from ..schemas import BoardType, MicrostructureConfig

Side = Literal["BUY", "SELL"]
_US_BOARDS = {BoardType.NASDAQ, BoardType.NYSE}
_LIMIT_SWITCH = date(2026, 7, 6)
_TICK = Decimal("0.01")


def as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(value[:10])


def positive_price(value: object) -> float:
    if value is None:
        return 0.0
    price = float(value)
    return price if isfinite(price) and price > 0 else 0.0


def get_price_limit_pct(board: BoardType, is_st: bool, trade_date: date) -> float | None:
    """Return the symmetric daily limit for the specified board and session."""
    board = BoardType(board)
    if board in _US_BOARDS:
        return None
    if board == BoardType.MAIN:
        return 0.05 if is_st and trade_date < _LIMIT_SWITCH else 0.10
    if board == BoardType.BSE:
        return 0.30
    return 0.20


def raw_price_limits(bar: dict) -> tuple[float | None, float | None]:
    board = BoardType(bar.get("board") or BoardType.MAIN)
    if board in _US_BOARDS or bar.get("limit_exempt") or bar.get("price_limit_status") == "LIMITLESS":
        return None, None
    up = positive_price(bar.get("up_limit"))
    down = positive_price(bar.get("down_limit"))
    reference = positive_price(bar.get("pre_close"))
    if (not up or not down) and reference and bar.get("trade_date") is not None:
        limit = get_price_limit_pct(board, bool(bar.get("is_st")), as_date(bar["trade_date"]))
        reference_dec, limit_dec = Decimal(str(reference)), Decimal(str(limit))
        up = up or float((reference_dec * (1 + limit_dec)).quantize(_TICK, rounding=ROUND_HALF_UP))
        down = down or float((reference_dec * (1 - limit_dec)).quantize(_TICK, rounding=ROUND_HALF_UP))
    return up or None, down or None


def bar_block_reason(bar: dict, side: Side, config: MicrostructureConfig) -> str | None:
    """Return the known trading restriction for a side of a daily bar."""
    if config.enforce_suspension and bar.get("is_suspended", False):
        return "SUSPENDED"
    opening = positive_price(bar.get("open"))
    if not opening:
        return "NO_QUOTE"
    if config.enforce_price_limits:
        if bar.get("price_limit_status") == "UNKNOWN":
            return "UNKNOWN_PRICE_LIMIT"
        up, down = raw_price_limits(bar)
        if up is not None and down is not None and down > up:
            return "UNKNOWN_PRICE_LIMIT"
        if (up is not None and opening > up + 1e-8) or (down is not None and opening < down - 1e-8):
            return "OUTSIDE_PRICE_LIMIT"
        limit = up if side == "BUY" else down
        if limit is not None and all(
            isclose(positive_price(bar.get(field)), limit, rel_tol=0.0, abs_tol=1e-8)
            for field in ("open", "high", "low")
        ):
            return "LIMIT_UP" if side == "BUY" else "LIMIT_DOWN"
    return None


def round_order_quantity(
    quantity: float,
    board: BoardType,
    side: Side,
    *,
    position: int = 0,
) -> int:
    """Round down standard lots and allow whole residual positions on sell."""
    shares = max(0, floor(quantity + 1e-10))
    board = BoardType(board)
    if board in _US_BOARDS:
        return shares
    minimum = 200 if board == BoardType.STAR else 100
    if side == "SELL" and 0 < position <= shares:
        return position
    if shares < minimum:
        return 0
    if board in {BoardType.STAR, BoardType.BSE}:
        return shares
    return shares // minimum * minimum


def fee_rates(board: BoardType, side: Side, config: MicrostructureConfig) -> tuple[float, float, float]:
    cn_equity = BoardType(board) not in _US_BOARDS
    commission = config.commission_bps / 10_000
    stamp = config.stamp_duty_sell_bps / 10_000 if cn_equity and side == "SELL" else 0.0
    transfer = config.transfer_fee_bps / 10_000 if cn_equity else 0.0
    return commission, stamp, transfer


def square_root_impact(
    quantity: float, adv_shares: float, daily_volatility: float, coefficient: float
) -> float:
    """Apply the model at parent order size using prior-session ADV and volatility."""
    if adv_shares <= 0 or daily_volatility <= 0:
        return 0.0
    return coefficient * daily_volatility * sqrt(abs(quantity) / adv_shares)


@dataclass(frozen=True)
class Fill:
    quantity: int = 0
    price: float = 0.0
    reference_price: float = 0.0
    commission: float = 0.0
    stamp_duty: float = 0.0
    transfer_fee: float = 0.0
    impact_cost: float = 0.0
    reason: str | None = None

    @property
    def explicit_fees(self) -> float:
        return self.commission + self.stamp_duty + self.transfer_fee


def match_order(
    bar: dict,
    side: Side,
    quantity: int,
    sellable: int,
    config: MicrostructureConfig,
    *,
    used_volume: int = 0,
    adv_shares: float = 0.0,
    daily_volatility: float = 0.0,
    available_cash: float | None = None,
    position: int | None = None,
) -> Fill:
    """Match one day-only parent order against bar volume and legal prices.

    The opening price is the arrival benchmark. Daily eligible volume is used
    to reconcile the simulated fill over the session. Pass the day's previously
    filled quantity in ``used_volume`` to share the participation budget.
    """
    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")
    if used_volume < 0 or not 0 <= config.max_pov <= 1:
        raise ValueError("used_volume must be nonnegative and max_pov must be in [0, 1]")
    opening = positive_price(bar.get("open"))
    if quantity <= 0:
        return Fill(reference_price=opening, reason="INVALID_QUANTITY")
    block = bar_block_reason(bar, side, config)
    if block:
        return Fill(reference_price=opening, reason=block)

    board = BoardType(bar.get("board") or BoardType.MAIN)
    position_provided = position is not None
    if not position_provided:
        position = sellable
    enforce_lock = config.enforce_t_plus_1 and board not in _US_BOARDS
    allowed = quantity
    reason = None
    if side == "SELL":
        held_limit = sellable if enforce_lock else position
        allowed = min(allowed, max(0, held_limit))
        if allowed < quantity:
            reason = "T+1_LOCK" if enforce_lock and sellable < position else "INSUFFICIENT_POSITION"
            # A low-level caller can supply the current available inventory alone.
            if enforce_lock and not position_provided:
                reason = "T+1_LOCK"
        if not allowed:
            return Fill(reference_price=opening, reason=reason)

    volume = positive_price(bar.get("volume_shares"))
    capacity = max(0, int(Decimal(str(config.max_pov)) * Decimal(str(volume))) - used_volume)
    bounded = min(allowed, capacity)
    if bounded < allowed:
        reason = "POV_LIMIT"
    filled = round_order_quantity(bounded, board, side, position=position)
    if filled < bounded and reason is None:
        reason = "ROUND_LOT"
    if not filled:
        return Fill(reference_price=opening, reason=reason or "ROUND_LOT")

    direction = 1 if side == "BUY" else -1
    impact = square_root_impact(quantity, adv_shares, daily_volatility, config.impact_coeff_y)
    minimum_price = opening * 1e-6 if board in _US_BOARDS else 0.01
    price = max(opening * (1 + direction * impact), minimum_price)
    if config.enforce_price_limits:
        up, down = raw_price_limits(bar)
        price = min(price, up) if up is not None else price
        price = max(price, down) if down is not None else price
    commission, stamp, transfer = fee_rates(board, side, config)
    if side == "BUY" and available_cash is not None:
        affordable = round_order_quantity(available_cash / (price * (1 + commission + transfer)), board, side)
        if affordable < filled:
            reason = "INSUFFICIENT_CASH"
            filled = affordable
        if not filled:
            return Fill(reference_price=opening, reason=reason)

    notional = filled * price
    return Fill(
        quantity=filled,
        price=price,
        reference_price=opening,
        commission=notional * commission,
        stamp_duty=notional * stamp,
        transfer_fee=notional * transfer,
        impact_cost=direction * filled * (price - opening),
        reason=reason,
    )
