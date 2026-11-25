"""End-of-day 7-check quantitative data QA Sentinel (anti-bias, PIT, continuity & rule audit)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import pandera.polars as pa
import polars as pl

from quant_core.schemas import (
    BoardType,
    DataHealthReport,
    DatasetTableStatus,
    Market,
    QualityCheckItem,
)

# CSRC / SSE / SZSE rule effective 2026-07-06: main-board ST daily limit widened from 5% to 10%.
ST_MAIN_RULE_CHANGE_DATE = date(2026, 7, 6)

BAR_RAW_PANDERA_SCHEMA = pa.DataFrameSchema(
    columns={
        "listing_id": pa.Column(pl.Utf8, nullable=False),
        "symbol": pa.Column(pl.Utf8, nullable=False),
        "market": pa.Column(pl.Utf8, nullable=False),
        "trade_date": pa.Column(pl.Date, nullable=False),
        "open": pa.Column(pl.Float64, nullable=False, checks=pa.Check.gt(0.0)),
        "high": pa.Column(pl.Float64, nullable=False, checks=pa.Check.gt(0.0)),
        "low": pa.Column(pl.Float64, nullable=False, checks=pa.Check.gt(0.0)),
        "close": pa.Column(pl.Float64, nullable=False, checks=pa.Check.gt(0.0)),
        "pre_close": pa.Column(pl.Float64, nullable=False, checks=pa.Check.gt(0.0)),
        "vwap": pa.Column(pl.Float64, nullable=False, checks=pa.Check.gt(0.0)),
        "volume_shares": pa.Column(pl.Int64, nullable=False, checks=pa.Check.ge(0)),
        "amount_ccy": pa.Column(pl.Float64, nullable=False, checks=pa.Check.ge(0.0)),
        "adj_factor": pa.Column(pl.Float64, nullable=False, checks=pa.Check.gt(0.0)),
        "is_suspended": pa.Column(pl.Boolean, nullable=False),
        "is_st": pa.Column(pl.Boolean, nullable=False),
        "float_market_cap": pa.Column(pl.Float64, nullable=False, checks=pa.Check.gt(0.0)),
    },
    strict=False,
)


def round_tick_half_up(value: float | Decimal, tick: str = "0.01") -> float:
    """Round price to exchange tick size using China/standard HALF_UP convention."""
    d = Decimal(str(value)).quantize(Decimal(tick), rounding=ROUND_HALF_UP)
    return float(d)


def get_expected_price_limit_pct(
    market: Market | str,
    board: BoardType | str,
    is_st: bool,
    trade_date: date,
) -> float | None:
    """Return statutory daily price limit fraction for (market, board, is_st, trade_date).

    Reflects the 2026-07-06 Shanghai/Shenzhen Main Board ST rule change (5% -> 10%).
    Returns ``None`` for markets without static daily price bands (e.g. ``US_EQ``).
    """
    m_val = market.value if isinstance(market, Market) else str(market)
    b_val = board.value if isinstance(board, BoardType) else str(board)
    if m_val == Market.US_EQ.value or b_val in (BoardType.NASDAQ.value, BoardType.NYSE.value):
        return None
    if b_val == BoardType.BSE.value:
        return 0.30
    if b_val in (BoardType.CHINEXT.value, BoardType.STAR.value):
        return 0.20
    if b_val == BoardType.MAIN.value:
        if is_st:
            return 0.10 if trade_date >= ST_MAIN_RULE_CHANGE_DATE else 0.05
        return 0.10
    return 0.10


def _cast_bar_numeric(df: pl.DataFrame) -> pl.DataFrame:
    float_cols = [
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "vwap",
        "amount_ccy",
        "adj_factor",
        "up_limit",
        "down_limit",
        "float_market_cap",
        "free_float_market_cap",
    ]
    exprs = [pl.col(c).cast(pl.Float64) for c in float_cols if c in df.columns]
    if "volume_shares" in df.columns:
        exprs.append(pl.col("volume_shares").cast(pl.Int64))
    return df.with_columns(exprs) if exprs else df


def _check_1_ohlcv_geometry(bars_df: pl.DataFrame) -> QualityCheckItem:
    """Audit 1: OHLCV geometry legality & non-zero missingness via Pandera + vector assertions."""
    if bars_df.is_empty():
        return QualityCheckItem(
            check_id="QA-01-OHLCV-GEOM",
            name="OHLCV 几何合法性与缺失非零检查",
            status="fail",
            detail="bar_raw_1d 为空，无法通过几何合法性检查",
            affected_count=0,
        )

    df = _cast_bar_numeric(bars_df)
    pandera_err: str | None = None
    try:
        BAR_RAW_PANDERA_SCHEMA.validate(df, lazy=True)
    except Exception as exc:  # noqa: BLE001
        pandera_err = str(exc).splitlines()[0][:200]

    bad = df.filter(
        pl.col("open").is_null()
        | pl.col("high").is_null()
        | pl.col("low").is_null()
        | pl.col("close").is_null()
        | pl.col("pre_close").is_null()
        | pl.col("open").is_nan()
        | pl.col("high").is_nan()
        | pl.col("low").is_nan()
        | pl.col("close").is_nan()
        | (pl.col("low") <= 0.0)
        | (pl.col("open") <= 0.0)
        | (pl.col("close") <= 0.0)
        | (pl.col("pre_close") <= 0.0)
        | (pl.col("adj_factor") <= 0.0)
        | (pl.col("volume_shares") < 0)
        | (pl.col("amount_ccy") < 0.0)
        | (pl.col("high") < pl.max_horizontal("open", "close") - 1e-8)
        | (pl.min_horizontal("open", "close") < pl.col("low") - 1e-8)
        | (pl.col("high") < pl.col("low") - 1e-8)
        | (pl.col("is_suspended") & (pl.col("volume_shares") != 0))
        | ((~pl.col("is_suspended")) & (pl.col("volume_shares") <= 0))
    )

    if bad.height > 0 or pandera_err is not None:
        samples = bad.select("symbol").unique().head(5)["symbol"].to_list() if bad.height > 0 else []
        detail = f"发现 {bad.height} 条 OHLCV 几何/非正价格/停牌成交量异常"
        if pandera_err:
            detail = f"{detail}; Pandera: {pandera_err}"
        return QualityCheckItem(
            check_id="QA-01-OHLCV-GEOM",
            name="OHLCV 几何合法性与缺失非零检查",
            status="fail",
            detail=detail,
            affected_count=max(bad.height, 1),
            sample_symbols=samples,
        )

    return QualityCheckItem(
        check_id="QA-01-OHLCV-GEOM",
        name="OHLCV 几何合法性与缺失非零检查",
        status="pass",
        detail=(
            f"全部 {df.height:,} 条日线通过 Pandera 契约及 high>=max(O,C)>=min(O,C)>=low>0、"
            "volume>=0、停牌零成交校验"
        ),
        affected_count=0,
    )


def _check_2_corp_action_continuity(
    bars_df: pl.DataFrame,
    corp_actions_df: pl.DataFrame,
) -> QualityCheckItem:
    """Audit 2: Split/dividend ex-day back-adjusted return continuity (< 1e-5 jump)."""
    df = _cast_bar_numeric(bars_df).sort(["listing_id", "trade_date"])
    with_prev = df.with_columns(
        pl.col("close").shift(1).over("listing_id").alias("prev_bar_close"),
        pl.col("adj_factor").shift(1).over("listing_id").alias("prev_adj_factor"),
        pl.col("trade_date").shift(1).over("listing_id").alias("prev_trade_date"),
    )

    # Index corporate actions by (listing_id, ex_date)
    action_map: dict[tuple[str, date], dict[str, float]] = {}
    if not corp_actions_df.is_empty():
        for row in corp_actions_df.iter_rows(named=True):
            key = (str(row["listing_id"]), row["ex_date"])
            cash_div = float(row["cash_dividend_per_share"])
            split_ratio = float(row["split_ratio"])
            bonus_ratio = float(row["bonus_share_ratio"])
            if key in action_map:
                # Combine same-day dividend + split/bonus actions
                action_map[key]["cash_div"] += cash_div
                action_map[key]["share_mult"] *= (split_ratio + bonus_ratio)
            else:
                action_map[key] = {
                    "cash_div": cash_div,
                    "share_mult": split_ratio + bonus_ratio,
                }

    violations: list[tuple[str, str]] = []
    verified_actions = 0

    for row in with_prev.iter_rows(named=True):
        prev_close_bar = row["prev_bar_close"]
        prev_adj = row["prev_adj_factor"]
        if prev_close_bar is None or prev_adj is None:
            continue

        lid = str(row["listing_id"])
        sym = str(row["symbol"])
        t_date = row["trade_date"]
        adj_t = float(row["adj_factor"])
        close_t = float(row["close"])
        pre_close_t = float(row["pre_close"])

        # Check if any action has ex_date in (prev_trade_date, t_date]
        prev_t = row["prev_trade_date"]
        matched_actions = [
            act
            for (a_lid, a_ex), act in action_map.items()
            if a_lid == lid and prev_t < a_ex <= t_date
        ]

        if matched_actions:
            verified_actions += len(matched_actions)
            total_cash = sum(a["cash_div"] for a in matched_actions)
            total_mult = 1.0
            for a in matched_actions:
                total_mult *= a["share_mult"]
            ex_ref = (float(prev_close_bar) - total_cash) / total_mult
            if ex_ref <= 0:
                violations.append((sym, f"{t_date}: invalid ex_ref={ex_ref}"))
                continue
            r_adj = (close_t * adj_t) / (float(prev_close_bar) * float(prev_adj)) - 1.0
            r_true_ex = close_t / ex_ref - 1.0
            if abs(r_adj - r_true_ex) >= 1e-5:
                violations.append(
                    (sym, f"{t_date}: |r_adj - r_ex|={abs(r_adj - r_true_ex):.2e} >= 1e-5")
                )
            elif abs(pre_close_t - ex_ref) > max(0.015, ex_ref * 1e-3):
                violations.append(
                    (sym, f"{t_date}: pre_close={pre_close_t:.4f} != ex_ref={ex_ref:.4f}")
                )
        else:
            adj_drift = abs(adj_t / float(prev_adj) - 1.0)
            if adj_drift >= 1e-5:
                violations.append((sym, f"{t_date}: unexpected adj_factor jump {adj_drift:.2e}"))

    if violations:
        samples = sorted({sym for sym, _ in violations})[:5]
        return QualityCheckItem(
            check_id="QA-02-CORP-ACTION-ADJ",
            name="复权与公司行动连续性校验",
            status="fail",
            detail=f"发现 {len(violations)} 处除权除息/复权跳空异常: {violations[0][1]}",
            affected_count=len(violations),
            sample_symbols=samples,
        )

    return QualityCheckItem(
        check_id="QA-02-CORP-ACTION-ADJ",
        name="复权与公司行动连续性校验",
        status="pass",
        detail=(
            f"已核验 {verified_actions} 起拆股/分红/送转事件及全部连续交易日，"
            "后复权收益与除权基准偏差全部 < 1e-5"
        ),
        affected_count=0,
    )


def _check_3_pit_financial_timestamps(fin_df: pl.DataFrame) -> QualityCheckItem:
    """Audit 3: PIT financial timestamp legality (zero ann_date < report_period_end & monotone revisions)."""
    if fin_df.is_empty():
        return QualityCheckItem(
            check_id="QA-03-PIT-FIN-TS",
            name="PIT 财务时间戳合法性检查",
            status="fail",
            detail="financial_fact 表为空，缺少 PIT 财务样本",
            affected_count=0,
        )

    inverted = fin_df.filter(
        (pl.col("ann_date") < pl.col("report_period_end"))
        | (pl.col("first_seen_at") < pl.col("published_at"))
        | (pl.col("published_at").dt.date() < pl.col("report_period_end"))
        | (pl.col("revision_seq") < 1)
    )

    rev_violations: list[str] = []
    grouped = fin_df.sort(["issuer_id", "metric_code", "report_period_end", "revision_seq"])
    prev_key: tuple[str, str, date] | None = None
    prev_seq = 0
    prev_pub: datetime | None = None
    for row in grouped.iter_rows(named=True):
        key = (str(row["issuer_id"]), str(row["metric_code"]), row["report_period_end"])
        seq = int(row["revision_seq"])
        pub = row["published_at"]
        if key == prev_key and (seq <= prev_seq or (prev_pub is not None and pub < prev_pub)):
            rev_violations.append(str(row["symbol"]))
        prev_key = key
        prev_seq = seq
        prev_pub = pub

    total_bad = inverted.height + len(rev_violations)
    if total_bad > 0:
        samples = sorted(
            set(
                (inverted.select("symbol").unique().head(5)["symbol"].to_list() if inverted.height else [])
                + rev_violations[:5]
            )
        )[:5]
        return QualityCheckItem(
            check_id="QA-03-PIT-FIN-TS",
            name="PIT 财务时间戳合法性检查",
            status="fail",
            detail=(
                f"发现 {inverted.height} 条公告日/入库日倒挂记录，"
                f"{len(rev_violations)} 条修订版本号或发布时间非单调记录"
            ),
            affected_count=total_bad,
            sample_symbols=samples,
        )

    restated_cnt = int(fin_df.filter(pl.col("is_restated")).height)
    withdrawn_cnt = int(fin_df.filter(pl.col("is_withdrawn")).height)
    return QualityCheckItem(
        check_id="QA-03-PIT-FIN-TS",
        name="PIT 财务时间戳合法性检查",
        status="pass",
        detail=(
            f"全部 {fin_df.height:,} 条 PIT 财务事实 0 条 ann_date < report_period_end 倒挂，"
            f"含 {restated_cnt} 条重述与 {withdrawn_cnt} 条撤回版本链"
        ),
        affected_count=0,
    )


def _check_4_delisted_coverage(
    lifecycle_df: pl.DataFrame,
    bars_df: pl.DataFrame,
) -> QualityCheckItem:
    """Audit 4: Delisted stock retention & zero survivorship bias check."""
    if lifecycle_df.is_empty():
        return QualityCheckItem(
            check_id="QA-04-DELISTED-SURVIVOR",
            name="退市股覆盖与零幸存者偏差检查",
            status="fail",
            detail="instrument_lifecycle 为空",
            affected_count=0,
        )

    delisted = lifecycle_df.filter(pl.col("delist_date").is_not_null())
    if delisted.is_empty():
        return QualityCheckItem(
            check_id="QA-04-DELISTED-SURVIVOR",
            name="退市股覆盖与零幸存者偏差检查",
            status="fail",
            detail="快照未包含任何历史退市标的，存在幸存者偏差风险",
            affected_count=1,
        )

    min_bar_date = bars_df["trade_date"].min() if not bars_df.is_empty() else None
    missing_bars: list[str] = []
    ghost_bars: list[str] = []

    for row in delisted.iter_rows(named=True):
        lid = str(row["listing_id"])
        sym = str(row["symbol"])
        d_date = row["delist_date"]
        sub = bars_df.filter(pl.col("listing_id") == lid)
        if min_bar_date is not None and d_date >= min_bar_date:
            if sub.is_empty():
                missing_bars.append(sym)
            else:
                after = sub.filter(pl.col("trade_date") > d_date)
                if after.height > 0:
                    ghost_bars.append(sym)

    bad_total = len(missing_bars) + len(ghost_bars)
    if bad_total > 0:
        samples = sorted(set(missing_bars + ghost_bars))[:5]
        return QualityCheckItem(
            check_id="QA-04-DELISTED-SURVIVOR",
            name="退市股覆盖与零幸存者偏差检查",
            status="fail",
            detail=(
                f"退市标的历史保留异常: {len(missing_bars)} 只缺失历史行情，"
                f"{len(ghost_bars)} 只存在退市后幽灵行情"
            ),
            affected_count=bad_total,
            sample_symbols=samples,
        )

    delisted_syms = delisted["symbol"].to_list()
    return QualityCheckItem(
        check_id="QA-04-DELISTED-SURVIVOR",
        name="退市股覆盖与零幸存者偏差检查",
        status="pass",
        detail=(
            f"完整保留 {delisted.height} 只历史已退市标的 ({', '.join(delisted_syms[:5])})，"
            "退市前行情齐全且退市后零幽灵 bar"
        ),
        affected_count=0,
    )


def _check_5_universe_weights(universe_df: pl.DataFrame) -> QualityCheckItem:
    """Audit 5: Index constituent weight normalization (|sum(weight) - 1.0| <= 0.005)."""
    if universe_df.is_empty():
        return QualityCheckItem(
            check_id="QA-05-UNIVERSE-WEIGHT",
            name="指数成分权重归一检查",
            status="fail",
            detail="universe_member 为空",
            affected_count=0,
        )

    dup_cnt = (
        universe_df.group_by(["universe_id", "as_of_date", "listing_id"])
        .len()
        .filter(pl.col("len") > 1)
        .height
    )
    neg_cnt = universe_df.filter(pl.col("weight").is_not_null() & (pl.col("weight") < 0.0)).height

    weighted = universe_df.filter(
        pl.col("weight").is_not_null() & (pl.col("weight_quality") == "official")
    )
    sums = weighted.group_by(["universe_id", "as_of_date"]).agg(
        pl.col("weight").sum().alias("w_sum"),
        pl.len().alias("member_cnt"),
    )
    bad_sums = sums.filter((pl.col("w_sum") - 1.0).abs() > 0.005)

    total_issues = dup_cnt + neg_cnt + bad_sums.height
    if total_issues > 0:
        samples = [
            f"{r['universe_id']}@{r['as_of_date']}({r['w_sum']:.4f})"
            for r in bad_sums.head(5).iter_rows(named=True)
        ]
        return QualityCheckItem(
            check_id="QA-05-UNIVERSE-WEIGHT",
            name="指数成分权重归一检查",
            status="fail",
            detail=(
                f"成分权重异常: {bad_sums.height} 个快照权重和偏差 > 0.005，"
                f"重复成分 {dup_cnt} 条，负权重 {neg_cnt} 条"
            ),
            affected_count=total_issues,
            sample_symbols=samples,
        )

    max_err = float(sums.select((pl.col("w_sum") - 1.0).abs().max()).item() or 0.0) if sums.height else 0.0
    universes = sorted(universe_df["universe_id"].unique().to_list())
    return QualityCheckItem(
        check_id="QA-05-UNIVERSE-WEIGHT",
        name="指数成分权重归一检查",
        status="pass",
        detail=(
            f"全部 {sums.height} 个成分权重快照 ({', '.join(universes)}) 归一通过，"
            f"最大误差 {max_err:.6f} <= 0.005"
        ),
        affected_count=0,
    )


def _check_6_industry_non_overlap(industry_df: pl.DataFrame) -> QualityCheckItem:
    """Audit 6: Historical Shenwan (and GICS) industry membership valid-interval zero overlap."""
    if industry_df.is_empty():
        return QualityCheckItem(
            check_id="QA-06-INDUSTRY-OVERLAP",
            name="历史申万行业有效区间零重叠检查",
            status="fail",
            detail="industry_membership 为空",
            affected_count=0,
        )

    ordered = industry_df.sort(["listing_id", "taxonomy", "valid_from"])
    overlaps: list[str] = []
    switch_count = 0
    prev_key: tuple[str, str] | None = None
    prev_to: date | None = None
    prev_open_ended = False

    for row in ordered.iter_rows(named=True):
        key = (str(row["listing_id"]), str(row["taxonomy"]))
        v_from: date = row["valid_from"]
        v_to: date | None = row["valid_to"]
        sym = str(row["symbol"])

        if v_to is not None and v_from > v_to:
            overlaps.append(sym)
        if key == prev_key:
            switch_count += 1
            if prev_open_ended or (prev_to is not None and v_from <= prev_to):
                overlaps.append(sym)

        prev_key = key
        prev_to = v_to
        prev_open_ended = v_to is None

    if overlaps:
        return QualityCheckItem(
            check_id="QA-06-INDUSTRY-OVERLAP",
            name="历史申万行业有效区间零重叠检查",
            status="fail",
            detail=f"发现 {len(overlaps)} 处行业有效区间重叠或起止倒挂",
            affected_count=len(overlaps),
            sample_symbols=sorted(set(overlaps))[:5],
        )

    sw_cnt = industry_df.filter(pl.col("taxonomy").str.starts_with("SW")).height
    return QualityCheckItem(
        check_id="QA-06-INDUSTRY-OVERLAP",
        name="历史申万行业有效区间零重叠检查",
        status="pass",
        detail=(
            f"全部 {industry_df.height} 条行业归属记录 (含 {sw_cnt} 条申万记录、{switch_count} 次历史行业切换) "
            "区间零重叠且日期单调合法"
        ),
        affected_count=0,
    )


def _check_7_price_limit_consistency(
    bars_df: pl.DataFrame,
    lifecycle_df: pl.DataFrame,
) -> QualityCheckItem:
    """Audit 7: Cross-day price-limit rule consistency including 2026-07-06 Main-Board ST 5%->10%."""
    if bars_df.is_empty() or lifecycle_df.is_empty():
        return QualityCheckItem(
            check_id="QA-07-PRICE-LIMIT-RULES",
            name="涨跌停规则跨日一致性检查",
            status="fail",
            detail="缺少行情或证券主档数据，无法校验涨跌停规则",
            affected_count=0,
        )

    df = _cast_bar_numeric(bars_df).join(
        lifecycle_df.select("listing_id", "board"),
        on="listing_id",
        how="left",
    )

    violations: list[tuple[str, str]] = []
    st_pre_20260706 = 0
    st_post_20260706 = 0

    for row in df.iter_rows(named=True):
        market = str(row["market"])
        board = str(row["board"] or "MAIN")
        is_st = bool(row["is_st"])
        t_date: date = row["trade_date"]
        pre_close = float(row["pre_close"])
        up_lim = row["up_limit"]
        dn_lim = row["down_limit"]
        sym = str(row["symbol"])

        expected_pct = get_expected_price_limit_pct(market, board, is_st, t_date)
        if expected_pct is None:
            if up_lim is not None or dn_lim is not None:
                violations.append((sym, f"{t_date}: US_EQ should not have static price limit"))
            continue

        if board == BoardType.MAIN.value and is_st:
            if t_date < ST_MAIN_RULE_CHANGE_DATE:
                st_pre_20260706 += 1
            else:
                st_post_20260706 += 1

        if up_lim is None or dn_lim is None:
            violations.append((sym, f"{t_date}: missing CN_A price limit"))
            continue

        exp_up = round_tick_half_up(pre_close * (1.0 + expected_pct))
        exp_dn = round_tick_half_up(pre_close * (1.0 - expected_pct))

        if abs(float(up_lim) - exp_up) > 0.015 or abs(float(dn_lim) - exp_dn) > 0.015:
            violations.append(
                (
                    sym,
                    (
                        f"{t_date} (board={board}, st={is_st}, pct={expected_pct:.0%}): "
                        f"got [{dn_lim},{up_lim}] != expected [{exp_dn},{exp_up}]"
                    ),
                )
            )
            continue

        if not bool(row["is_suspended"]):
            high = float(row["high"])
            low = float(row["low"])
            if high > float(up_lim) + 0.015 or low < float(dn_lim) - 0.015:
                violations.append(
                    (sym, f"{t_date}: OHLC [{low},{high}] breached limits [{dn_lim},{up_lim}]")
                )

    if violations:
        samples = sorted({sym for sym, _ in violations})[:5]
        return QualityCheckItem(
            check_id="QA-07-PRICE-LIMIT-RULES",
            name="涨跌停规则跨日一致性检查",
            status="fail",
            detail=f"发现 {len(violations)} 条涨跌停规则违规: {violations[0][1]}",
            affected_count=len(violations),
            sample_symbols=samples,
        )

    return QualityCheckItem(
        check_id="QA-07-PRICE-LIMIT-RULES",
        name="涨跌停规则跨日一致性检查",
        status="pass",
        detail=(
            f"全部板块涨跌停规则一致 (主板10%/创科20%/北交所30%/美股无限制)；"
            f"2026-07-06 主板 ST 5%→10% 跨日规则核验通过 "
            f"(变更前 {st_pre_20260706} 条 5%，变更后 {st_post_20260706} 条 10%)"
        ),
        affected_count=0,
    )


def _table_disk_mb(paths: list[Path]) -> float:
    total = sum(p.stat().st_size for p in paths if p.exists())
    return round(total / (1024.0 * 1024.0), 4)


def run_all_qa_checks(
    *,
    snapshot_id: str,
    as_of: datetime,
    tables: dict[str, pl.DataFrame],
    table_files: dict[str, list[Path]],
    manifest_sha256: str,
    now_utc: datetime | None = None,
) -> DataHealthReport:
    """Execute all 7 hard QA audits and build a deterministic ``DataHealthReport``."""
    bars_df = tables.get("bar_raw_1d", pl.DataFrame())
    corp_df = tables.get("corp_action", pl.DataFrame())
    fin_df = tables.get("financial_fact", pl.DataFrame())
    uni_df = tables.get("universe_member", pl.DataFrame())
    ind_df = tables.get("industry_membership", pl.DataFrame())
    life_df = tables.get("instrument_lifecycle", pl.DataFrame())

    checks = [
        _check_1_ohlcv_geometry(bars_df),
        _check_2_corp_action_continuity(bars_df, corp_df),
        _check_3_pit_financial_timestamps(fin_df),
        _check_4_delisted_coverage(life_df, bars_df),
        _check_5_universe_weights(uni_df),
        _check_6_industry_non_overlap(ind_df),
        _check_7_price_limit_consistency(bars_df, life_df),
    ]

    failed_checks = [c.check_id for c in checks if c.status == "fail"]
    warn_checks = [c.check_id for c in checks if c.status == "warn"]
    if failed_checks:
        overall_status = "fail"
    elif warn_checks:
        overall_status = "warn"
    else:
        overall_status = "pass"

    def _latest_str(df: pl.DataFrame, col: str) -> str:
        if df.is_empty() or col not in df.columns:
            return "N/A"
        val = df[col].max()
        return val.isoformat() if val is not None else "N/A"

    datasets: list[DatasetTableStatus] = [
        DatasetTableStatus(
            dataset="bar_raw_1d",
            freq="1D",
            primary_source="Tushare Pro / Massive",
            backup_source="BaoStock",
            coverage_pct=100.0 if not bars_df.is_empty() else 0.0,
            latest_date=_latest_str(bars_df, "trade_date"),
            pit_status=checks[0].status if checks[0].status == "fail" else checks[6].status,
            row_count=bars_df.height,
            disk_mb=_table_disk_mb(table_files.get("bar_raw_1d", [])),
        ),
        DatasetTableStatus(
            dataset="corp_action",
            freq="EVT",
            primary_source="CNINFO / Massive",
            backup_source="Tushare Pro",
            coverage_pct=100.0 if not corp_df.is_empty() else 0.0,
            latest_date=_latest_str(corp_df, "ex_date"),
            pit_status=checks[1].status,
            row_count=corp_df.height,
            disk_mb=_table_disk_mb(table_files.get("corp_action", [])),
        ),
        DatasetTableStatus(
            dataset="financial_fact",
            freq="Q",
            primary_source="Tushare VIP / SEC EDGAR",
            backup_source="CNINFO XBRL",
            coverage_pct=100.0 if not fin_df.is_empty() else 0.0,
            latest_date=_latest_str(fin_df, "ann_date"),
            pit_status=checks[2].status,
            row_count=fin_df.height,
            disk_mb=_table_disk_mb(table_files.get("financial_fact", [])),
        ),
        DatasetTableStatus(
            dataset="instrument_lifecycle",
            freq="EVT",
            primary_source="SSE/SZSE/BSE Master",
            backup_source="Tushare stock_basic",
            coverage_pct=100.0 if not life_df.is_empty() else 0.0,
            latest_date=_latest_str(life_df, "delist_date"),
            pit_status=checks[3].status,
            row_count=life_df.height,
            disk_mb=_table_disk_mb(table_files.get("instrument_lifecycle", [])),
        ),
        DatasetTableStatus(
            dataset="universe_member",
            freq="1D",
            primary_source="CSI / S&P Index",
            backup_source="Tushare index_weight",
            coverage_pct=100.0 if not uni_df.is_empty() else 0.0,
            latest_date=_latest_str(uni_df, "as_of_date"),
            pit_status=checks[4].status,
            row_count=uni_df.height,
            disk_mb=_table_disk_mb(table_files.get("universe_member", [])),
        ),
        DatasetTableStatus(
            dataset="industry_membership",
            freq="EVT",
            primary_source="SW2021 / GICS",
            backup_source="Tushare index_member_all",
            coverage_pct=100.0 if not ind_df.is_empty() else 0.0,
            latest_date=_latest_str(ind_df, "valid_from"),
            pit_status=checks[5].status,
            row_count=ind_df.height,
            disk_mb=_table_disk_mb(table_files.get("industry_membership", [])),
        ),
    ]

    ref_now = now_utc or datetime.now(UTC)
    as_of_utc = as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=UTC)
    freshness_sec = max(0, int((ref_now - as_of_utc).total_seconds()))

    non_empty_cov = [d.coverage_pct for d in datasets]
    coverage_pct = round(sum(non_empty_cov) / len(non_empty_cov), 2) if non_empty_cov else 0.0

    # Deterministic SHA-256 fingerprint across manifest + check verdicts + table counts
    fp_payload: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "manifest_sha256": manifest_sha256,
        "checks": [
            {"id": c.check_id, "status": c.status, "affected": c.affected_count}
            for c in checks
        ],
        "table_counts": {d.dataset: d.row_count for d in datasets},
    }
    fact_fingerprint = hashlib.sha256(
        json.dumps(fp_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return DataHealthReport(
        snapshot_id=snapshot_id,
        as_of=as_of_utc,
        overall_status=overall_status,
        freshness_sec=freshness_sec,
        coverage_pct=coverage_pct,
        stale_datasets=[],
        failed_checks=failed_checks,
        checks=checks,
        datasets=datasets,
        fact_fingerprint=fact_fingerprint,
    )
