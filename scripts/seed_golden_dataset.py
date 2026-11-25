#!/usr/bin/env python3
"""Explicit CLI seeder for the Pro-Quant V2.0 deterministic CN_A + US_EQ Golden PIT Dataset.

IMPORTANT:
This script is ONLY for explicit offline seeding / verification via CLI (or unit tests).
Production ``DataProviderAdapter`` implementations MUST NEVER call this script or fall back
to synthetic data.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

# Ensure quant_core is importable when invoked directly from repo root
_REPO_ROOT = Path(__file__).resolve().parents[1]
_QUANT_CORE_SRC = _REPO_ROOT / "packages" / "quant-core" / "src"
if str(_QUANT_CORE_SRC) not in sys.path:
    sys.path.insert(0, str(_QUANT_CORE_SRC))

from quant_core.lake.qa_sentinel import (
    ST_MAIN_RULE_CHANGE_DATE,
    get_expected_price_limit_pct,
    round_tick_half_up,
)
from quant_core.lake.store import QuantLake
from quant_core.schemas import (
    BoardType,
    ExperimentRecord,
    GateVerdict,
    Market,
    QualityState,
)

GOLDEN_SEED = 20261125
GOLDEN_SNAPSHOT_ID = "snap-golden-20261125-v1"
START_DATE = date(2024, 1, 2)
END_DATE = date(2026, 11, 25)
SNAPSHOT_AS_OF = datetime(2026, 11, 25, 8, 30, 0, tzinfo=UTC)  # 16:30 Asia/Shanghai


# ---------------------------------------------------------------------------
# Realistic CN_A & US_EQ Trading Calendars (2024-01-02 .. 2026-11-25)
# ---------------------------------------------------------------------------

CN_HOLIDAYS: set[date] = {
    # 2024
    date(2024, 1, 1),
    *(date(2024, 2, d) for d in range(9, 19)),  # Spring Festival
    date(2024, 4, 4), date(2024, 4, 5),         # Qingming
    *(date(2024, 5, d) for d in range(1, 6)),   # Labor Day
    date(2024, 6, 10),                          # Dragon Boat
    date(2024, 9, 16), date(2024, 9, 17),       # Mid-Autumn
    *(date(2024, 10, d) for d in range(1, 8)),  # National Day
    # 2025
    date(2025, 1, 1),
    *(date(2025, 1, d) for d in range(28, 32)),
    *(date(2025, 2, d) for d in range(1, 5)),   # Spring Festival
    date(2025, 4, 4),                           # Qingming
    *(date(2025, 5, d) for d in range(1, 6)),   # Labor Day
    date(2025, 6, 2),                           # Dragon Boat
    *(date(2025, 10, d) for d in range(1, 9)),  # National Day + Mid-Autumn
    # 2026
    date(2026, 1, 1), date(2026, 1, 2),
    *(date(2026, 2, d) for d in range(16, 24)), # Spring Festival
    date(2026, 4, 6),                           # Qingming
    *(date(2026, 5, d) for d in range(1, 6)),   # Labor Day
    date(2026, 6, 19),                          # Dragon Boat
    date(2026, 9, 25),                          # Mid-Autumn
    *(date(2026, 10, d) for d in range(1, 8)),  # National Day
}

US_HOLIDAYS: set[date] = {
    # 2024
    date(2024, 1, 1), date(2024, 1, 15), date(2024, 2, 19), date(2024, 3, 29),
    date(2024, 5, 27), date(2024, 6, 19), date(2024, 7, 4), date(2024, 9, 2),
    date(2024, 11, 28), date(2024, 12, 25),
    # 2025
    date(2025, 1, 1), date(2025, 1, 9), date(2025, 1, 20), date(2025, 2, 17),
    date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4),
    date(2025, 9, 1), date(2025, 11, 27), date(2025, 12, 25),
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
}


def build_trading_calendar(start: date, end: date, holidays: set[date]) -> list[date]:
    days: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5 and cur not in holidays:
            days.append(cur)
        cur += timedelta(days=1)
    return days


# ---------------------------------------------------------------------------
# Instrument Master Definitions (60 CN_A + 12 US_EQ)
# ---------------------------------------------------------------------------

SW_CODE_MAP: dict[str, str] = {
    "电子": "801080",
    "计算机": "801750",
    "医药生物": "801150",
    "电力设备": "801730",
    "机械设备": "801890",
    "基础化工": "801030",
    "汽车": "801880",
    "有色金属": "801050",
    "食品饮料": "801120",
    "银行": "801780",
    "非银金融": "801790",
    "国防军工": "801740",
    "通信": "801770",
    "传媒": "801760",
    "公用事业": "801160",
    "轻工制造": "801140",
    "煤炭": "801950",
}

GICS_CODE_MAP: dict[str, str] = {
    "Information Technology": "45",
    "Consumer Discretionary": "25",
    "Communication Services": "50",
    "Financials": "40",
    "Health Care": "35",
    "Energy": "10",
}


@dataclass(frozen=True)
class StockSpec:
    symbol: str
    name: str
    market: Market
    board: BoardType
    exchange: str
    industry_l1: str
    init_price: float
    float_shares_m: float  # Millions of float shares (controls market cap dispersion)
    list_date: date = date(2018, 1, 2)
    delist_date: date | None = None
    st_intervals: tuple[tuple[date, date | None], ...] = ()
    industry_switch: tuple[str, date] | None = None  # (old_l1, switch_date)
    csi300: bool = False
    csi500: bool = False
    csi1000: bool = False
    currency: str = "CNY"


def _build_cn_specs() -> list[StockSpec]:
    """Exactly 60 CN_A stocks across SH/SZ/BJ & MAIN/CHINEXT/STAR/BSE.

    Includes:
    - 3 explicitly delisted listings (2024-11-15, 2025-04-30, 2026-03-20)
    - 2 MAIN-board ST stocks crossing 2026-07-06 (5% -> 10% rule transition)
    - 4 historical Shenwan L1 industry switch stocks
    - 54+ active stocks in CSI500 so cross-sections stay >= 50 every trading day
    """
    specs: list[StockSpec] = [
        # --- 30 Large/Core & CSI300 + CSI500 overlap ---
        StockSpec("600519.SH", "贵州茅台", Market.CN_A, BoardType.MAIN, "SSE", "食品饮料", 1680.0, 1256.0, csi300=True, csi500=False, csi1000=False),
        StockSpec("600036.SH", "招商银行", Market.CN_A, BoardType.MAIN, "SSE", "银行", 32.5, 20600.0, csi300=True, csi500=False, csi1000=False),
        StockSpec("601318.SH", "中国平安", Market.CN_A, BoardType.MAIN, "SSE", "非银金融", 44.2, 10800.0, csi300=True, csi500=False, csi1000=False),
        StockSpec("300750.SZ", "宁德时代", Market.CN_A, BoardType.CHINEXT, "SZSE", "电力设备", 178.0, 3900.0, csi300=True, csi500=True, csi1000=False),
        StockSpec("000858.SZ", "五粮液", Market.CN_A, BoardType.MAIN, "SZSE", "食品饮料", 136.0, 3880.0, csi300=True, csi500=True, csi1000=False),
        StockSpec("601398.SH", "工商银行", Market.CN_A, BoardType.MAIN, "SSE", "银行", 5.15, 65000.0, csi300=True, csi500=False, csi1000=False),
        StockSpec("002594.SZ", "比亚迪", Market.CN_A, BoardType.MAIN, "SZSE", "汽车", 215.0, 1810.0, csi300=True, csi500=True, csi1000=False),
        StockSpec("600900.SH", "长江电力", Market.CN_A, BoardType.MAIN, "SSE", "公用事业", 24.8, 24000.0, csi300=True, csi500=True, csi1000=False),
        StockSpec("601899.SH", "紫金矿业", Market.CN_A, BoardType.MAIN, "SSE", "有色金属", 13.8, 20500.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("600276.SH", "恒瑞医药", Market.CN_A, BoardType.MAIN, "SSE", "医药生物", 43.5, 6370.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("000333.SZ", "美的集团", Market.CN_A, BoardType.MAIN, "SZSE", "机械设备", 58.6, 6850.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("600030.SH", "中信证券", Market.CN_A, BoardType.MAIN, "SSE", "非银金融", 21.4, 12100.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("600309.SH", "万华化学", Market.CN_A, BoardType.MAIN, "SSE", "基础化工", 78.2, 3140.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("601088.SH", "中国神华", Market.CN_A, BoardType.MAIN, "SSE", "煤炭", 34.5, 16500.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("002475.SZ", "立讯精密", Market.CN_A, BoardType.MAIN, "SZSE", "电子", 32.8, 7150.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("688981.SH", "中芯国际", Market.CN_A, BoardType.STAR, "SSE", "电子", 49.5, 1980.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("688111.SH", "金山办公", Market.CN_A, BoardType.STAR, "SSE", "计算机", 288.0, 462.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("688041.SH", "海光信息", Market.CN_A, BoardType.STAR, "SSE", "电子", 76.4, 1450.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("300059.SZ", "东方财富", Market.CN_A, BoardType.CHINEXT, "SZSE", "非银金融", 14.2, 13200.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("300760.SZ", "迈瑞医疗", Market.CN_A, BoardType.CHINEXT, "SZSE", "医药生物", 275.0, 1210.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("000063.SZ", "中兴通讯", Market.CN_A, BoardType.MAIN, "SZSE", "通信", 28.4, 4020.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("600893.SH", "航发动力", Market.CN_A, BoardType.MAIN, "SSE", "国防军工", 36.8, 2660.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("600104.SH", "上汽集团", Market.CN_A, BoardType.MAIN, "SSE", "汽车", 14.5, 11500.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("600031.SH", "三一重工", Market.CN_A, BoardType.MAIN, "SSE", "机械设备", 15.6, 8450.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("300124.SZ", "汇川技术", Market.CN_A, BoardType.CHINEXT, "SZSE", "机械设备", 61.5, 2320.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("300274.SZ", "阳光电源", Market.CN_A, BoardType.CHINEXT, "SZSE", "电力设备", 82.0, 1480.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("002371.SZ", "北方华创", Market.CN_A, BoardType.MAIN, "SZSE", "电子", 268.0, 530.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("600438.SH", "通威股份", Market.CN_A, BoardType.MAIN, "SSE", "电力设备", 24.6, 4500.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("601012.SH", "隆基绿能", Market.CN_A, BoardType.MAIN, "SSE", "电力设备", 21.8, 7580.0, csi300=True, csi500=True, csi1000=True),
        StockSpec("000001.SZ", "平安银行", Market.CN_A, BoardType.MAIN, "SZSE", "银行", 10.4, 19400.0, csi300=True, csi500=True, csi1000=True),

        # --- 25 Mid/Small-cap Active Stocks (including prototype holdings + STAR + BSE + ST + industry switches) ---
        StockSpec("002463.SZ", "沪电股份", Market.CN_A, BoardType.MAIN, "SZSE", "电子", 26.4, 1910.0, csi500=True, csi1000=True),
        StockSpec("600183.SH", "生益科技", Market.CN_A, BoardType.MAIN, "SSE", "电子", 18.8, 2350.0, csi500=True, csi1000=True),
        StockSpec("000988.SZ", "华工科技", Market.CN_A, BoardType.MAIN, "SZSE", "电子", 31.2, 1005.0, industry_switch=("机械设备", date(2025, 7, 1)), csi500=True, csi1000=True),
        StockSpec("601168.SH", "西部矿业", Market.CN_A, BoardType.MAIN, "SSE", "有色金属", 16.5, 2380.0, csi500=True, csi1000=True),
        StockSpec("000630.SZ", "铜陵有色", Market.CN_A, BoardType.MAIN, "SZSE", "有色金属", 3.45, 10500.0, industry_switch=("基础化工", date(2024, 7, 1)), csi500=True, csi1000=True),
        StockSpec("600536.SH", "中国软件", Market.CN_A, BoardType.MAIN, "SSE", "计算机", 39.5, 860.0, industry_switch=("通信", date(2025, 4, 1)), csi500=True, csi1000=True),
        StockSpec("002558.SZ", "巨人网络", Market.CN_A, BoardType.MAIN, "SZSE", "传媒", 12.6, 1920.0, csi500=True, csi1000=True),
        StockSpec("600966.SH", "博汇纸业", Market.CN_A, BoardType.MAIN, "SSE", "轻工制造", 6.20, 1336.0, csi500=True, csi1000=True),
        StockSpec("000831.SZ", "中国稀土", Market.CN_A, BoardType.MAIN, "SZSE", "有色金属", 27.4, 980.0, csi500=True, csi1000=True),
        StockSpec("601233.SH", "桐昆股份", Market.CN_A, BoardType.MAIN, "SSE", "基础化工", 14.8, 2410.0, csi500=True, csi1000=True),
        StockSpec("300759.SZ", "康龙化成", Market.CN_A, BoardType.CHINEXT, "SZSE", "医药生物", 25.4, 1420.0, csi500=True, csi1000=True),
        StockSpec("600216.SH", "浙江医药", Market.CN_A, BoardType.MAIN, "SSE", "医药生物", 11.8, 960.0, csi500=True, csi1000=True),
        StockSpec("002384.SZ", "东山精密", Market.CN_A, BoardType.MAIN, "SZSE", "电子", 19.6, 1410.0, industry_switch=("通信", date(2025, 1, 1)), csi500=True, csi1000=True),
        StockSpec("300308.SZ", "中际旭创", Market.CN_A, BoardType.CHINEXT, "SZSE", "通信", 118.0, 1110.0, csi500=True, csi1000=True),
        StockSpec("300502.SZ", "新易盛", Market.CN_A, BoardType.CHINEXT, "SZSE", "通信", 62.5, 708.0, csi500=True, csi1000=True),
        StockSpec("688256.SH", "寒武纪", Market.CN_A, BoardType.STAR, "SSE", "电子", 142.0, 416.0, csi500=True, csi1000=True),
        StockSpec("688012.SH", "中微公司", Market.CN_A, BoardType.STAR, "SSE", "电子", 152.0, 620.0, csi500=True, csi1000=True),
        StockSpec("688271.SH", "联影医疗", Market.CN_A, BoardType.STAR, "SSE", "医药生物", 131.0, 560.0, csi500=True, csi1000=True),
        StockSpec("002049.SZ", "紫光国微", Market.CN_A, BoardType.MAIN, "SZSE", "电子", 66.5, 848.0, csi500=True, csi1000=True),
        StockSpec("002230.SZ", "科大讯飞", Market.CN_A, BoardType.MAIN, "SZSE", "计算机", 46.8, 2180.0, csi500=True, csi1000=True),
        StockSpec("000768.SZ", "中航西飞", Market.CN_A, BoardType.MAIN, "SZSE", "国防军工", 23.4, 2770.0, csi500=True, csi1000=True),
        StockSpec("600418.SH", "江淮汽车", Market.CN_A, BoardType.MAIN, "SSE", "汽车", 17.5, 2184.0, csi500=True, csi1000=True),
        StockSpec("002600.SZ", "领益智造", Market.CN_A, BoardType.MAIN, "SZSE", "电子", 6.85, 6950.0, csi500=True, csi1000=True),
        StockSpec("835185.BJ", "贝特瑞", Market.CN_A, BoardType.BSE, "BSE", "电力设备", 24.5, 1110.0, csi500=True, csi1000=True),
        StockSpec("832982.BJ", "锦波生物", Market.CN_A, BoardType.BSE, "BSE", "医药生物", 188.0, 88.0, csi500=True, csi1000=True),

        # --- 2 MAIN-board ST Stocks crossing 2026-07-06 (5% -> 10%) ---
        StockSpec(
            "600157.SH", "*ST永泰", Market.CN_A, BoardType.MAIN, "SSE", "公用事业",
            4.20, 2220.0,
            st_intervals=((date(2024, 1, 2), None),),
            csi500=True, csi1000=True,
        ),
        StockSpec(
            "000669.SZ", "ST金鸿", Market.CN_A, BoardType.MAIN, "SZSE", "公用事业",
            5.40, 680.0,
            st_intervals=((date(2025, 4, 28), None),),
            csi500=True, csi1000=True,
        ),

        # --- 3 Explicitly Marked Historical Delisted Stocks (zero survivorship bias) ---
        StockSpec(
            "600677.SH", "航天通信退", Market.CN_A, BoardType.MAIN, "SSE", "国防军工",
            6.80, 520.0,
            delist_date=date(2024, 11, 15),
            st_intervals=((date(2024, 1, 2), date(2024, 11, 15)),),
            csi500=True, csi1000=True,
        ),
        StockSpec(
            "000040.SZ", "东旭蓝天退", Market.CN_A, BoardType.MAIN, "SZSE", "公用事业",
            5.10, 1060.0,
            delist_date=date(2025, 4, 30),
            st_intervals=((date(2024, 5, 6), date(2025, 4, 30)),),
            csi500=True, csi1000=True,
        ),
        StockSpec(
            "300208.SZ", "青岛中程退", Market.CN_A, BoardType.CHINEXT, "SZSE", "机械设备",
            4.90, 745.0,
            delist_date=date(2026, 3, 20),
            st_intervals=((date(2025, 4, 30), date(2026, 3, 20)),),
            csi500=True, csi1000=True,
        ),
    ]
    assert len(specs) == 60, f"Expected 60 CN_A stocks, got {len(specs)}"
    return specs


def _build_us_specs() -> list[StockSpec]:
    """12 core US equities for US_EQ / SP500_SAMPLE."""
    specs = [
        StockSpec("NVDA", "NVIDIA Corp", Market.US_EQ, BoardType.NASDAQ, "NASDAQ", "Information Technology", 490.0, 2460.0, currency="USD"),
        StockSpec("AAPL", "Apple Inc", Market.US_EQ, BoardType.NASDAQ, "NASDAQ", "Information Technology", 185.0, 15400.0, currency="USD"),
        StockSpec("MSFT", "Microsoft Corp", Market.US_EQ, BoardType.NASDAQ, "NASDAQ", "Information Technology", 374.0, 7430.0, currency="USD"),
        StockSpec("AMZN", "Amazon.com Inc", Market.US_EQ, BoardType.NASDAQ, "NASDAQ", "Consumer Discretionary", 151.0, 10380.0, currency="USD"),
        StockSpec("META", "Meta Platforms Inc", Market.US_EQ, BoardType.NASDAQ, "NASDAQ", "Communication Services", 352.0, 2540.0, currency="USD"),
        StockSpec("GOOGL", "Alphabet Inc", Market.US_EQ, BoardType.NASDAQ, "NASDAQ", "Communication Services", 139.0, 12300.0, currency="USD"),
        StockSpec("TSLA", "Tesla Inc", Market.US_EQ, BoardType.NASDAQ, "NASDAQ", "Consumer Discretionary", 248.0, 3180.0, currency="USD"),
        StockSpec("AVGO", "Broadcom Inc", Market.US_EQ, BoardType.NASDAQ, "NASDAQ", "Information Technology", 1110.0, 463.0, currency="USD"),
        StockSpec("JPM", "JPMorgan Chase & Co", Market.US_EQ, BoardType.NYSE, "NYSE", "Financials", 171.0, 2880.0, currency="USD"),
        StockSpec("LLY", "Eli Lilly and Co", Market.US_EQ, BoardType.NYSE, "NYSE", "Health Care", 588.0, 950.0, currency="USD"),
        StockSpec("XOM", "Exxon Mobil Corp", Market.US_EQ, BoardType.NYSE, "NYSE", "Energy", 102.0, 4450.0, currency="USD"),
        StockSpec("UNH", "UnitedHealth Group", Market.US_EQ, BoardType.NYSE, "NYSE", "Health Care", 528.0, 920.0, currency="USD"),
    ]
    assert len(specs) == 12
    return specs


def _is_st_on_date(spec: StockSpec, d: date) -> bool:
    for start_d, end_d in spec.st_intervals:
        if d >= start_d and (end_d is None or d <= end_d):
            return True
    return False


def _snap_to_calendar(target: date, cal: list[date]) -> date:
    for d in cal:
        if d >= target:
            return d
    return cal[-1]


# ---------------------------------------------------------------------------
# Corporate Actions, Microstructure Events, Bars, Financials, Universes
# ---------------------------------------------------------------------------


def _build_corp_actions(cn_cal: list[date], us_cal: list[date]) -> list[dict[str, Any]]:
    """Construct verified ex-dividend, 2-for-1 split, 10送5 bonus, and US 10-for-1 split events."""
    raw_events = [
        # CN_A events
        ("CN:600519.SH", "600519.SH", _snap_to_calendar(date(2024, 6, 20), cn_cal), 25.00, 1.0, 0.0, "cninfo_div"),
        ("CN:600519.SH", "600519.SH", _snap_to_calendar(date(2025, 6, 19), cn_cal), 30.00, 1.0, 0.0, "cninfo_div"),
        ("CN:002463.SZ", "002463.SZ", _snap_to_calendar(date(2025, 5, 22), cn_cal), 0.00, 2.0, 0.0, "cninfo_split"),
        ("CN:300750.SZ", "300750.SZ", _snap_to_calendar(date(2025, 4, 24), cn_cal), 5.00, 1.0, 0.5, "cninfo_bonus_div"),
        ("CN:600036.SH", "600036.SH", _snap_to_calendar(date(2024, 7, 11), cn_cal), 2.00, 1.0, 0.0, "cninfo_div"),
        ("CN:600036.SH", "600036.SH", _snap_to_calendar(date(2025, 7, 10), cn_cal), 2.10, 1.0, 0.0, "cninfo_div"),
        # US_EQ events (NVDA & AVGO 10-for-1 splits + AAPL/MSFT dividends)
        ("US:NVDA", "NVDA", _snap_to_calendar(date(2024, 6, 10), us_cal), 0.00, 10.0, 0.0, "massive_splits"),
        ("US:AVGO", "AVGO", _snap_to_calendar(date(2024, 7, 15), us_cal), 0.00, 10.0, 0.0, "massive_splits"),
        ("US:AAPL", "AAPL", _snap_to_calendar(date(2024, 8, 12), us_cal), 0.25, 1.0, 0.0, "massive_dividends"),
        ("US:AAPL", "AAPL", _snap_to_calendar(date(2025, 8, 11), us_cal), 0.26, 1.0, 0.0, "massive_dividends"),
        ("US:MSFT", "MSFT", _snap_to_calendar(date(2025, 5, 15), us_cal), 0.83, 1.0, 0.0, "massive_dividends"),
    ]
    rows: list[dict[str, Any]] = []
    for idx, (lid, sym, ex_d, cash_div, split_r, bonus_r, src) in enumerate(raw_events, start=1):
        rec_d = ex_d - timedelta(days=1)
        pay_d = ex_d + timedelta(days=5)
        ann_d = ex_d - timedelta(days=14)
        rows.append(
            {
                "action_id": f"ca-golden-{idx:03d}",
                "listing_id": lid,
                "symbol": sym,
                "ex_date": ex_d,
                "record_date": rec_d,
                "pay_date": pay_d,
                "cash_dividend_per_share": float(cash_div),
                "split_ratio": float(split_r),
                "bonus_share_ratio": float(bonus_r),
                "announced_at": datetime(ann_d.year, ann_d.month, ann_d.day, 10, 0, tzinfo=UTC),
                "source_id": src,
            }
        )
    return rows


def _build_microstructure_events(cn_cal: list[date]) -> dict[tuple[str, date], str]:
    """Explicit one-tick limit-up, limit-down, and temporary suspension windows for A-share realism."""
    events: dict[tuple[str, date], str] = {}
    # 1. 一字涨停 (one-tick limit-up)
    events[("002558.SZ", _snap_to_calendar(date(2025, 2, 18), cn_cal))] = "LIMIT_UP"
    events[("002558.SZ", _snap_to_calendar(date(2026, 11, 20), cn_cal))] = "LIMIT_UP"
    events[("002384.SZ", _snap_to_calendar(date(2025, 9, 12), cn_cal))] = "LIMIT_UP"
    events[("000831.SZ", _snap_to_calendar(date(2026, 8, 14), cn_cal))] = "LIMIT_UP"
    events[("688981.SH", _snap_to_calendar(date(2024, 10, 8), cn_cal))] = "LIMIT_UP"

    # 2. 一字跌停 (one-tick limit-down)
    events[("300759.SZ", _snap_to_calendar(date(2025, 4, 7), cn_cal))] = "LIMIT_DOWN"
    events[("300759.SZ", _snap_to_calendar(date(2026, 11, 18), cn_cal))] = "LIMIT_DOWN"
    events[("000831.SZ", _snap_to_calendar(date(2026, 8, 18), cn_cal))] = "LIMIT_DOWN"

    # 3. Temporary suspension windows (3-day & 2-day)
    susp1_start = _snap_to_calendar(date(2026, 11, 4), cn_cal)
    idx1 = cn_cal.index(susp1_start)
    for k in range(3):
        if idx1 + k < len(cn_cal):
            events[("600216.SH", cn_cal[idx1 + k])] = "SUSPENDED"

    susp2_start = _snap_to_calendar(date(2025, 9, 15), cn_cal)
    idx2 = cn_cal.index(susp2_start)
    for k in range(2):
        if idx2 + k < len(cn_cal):
            events[("002384.SZ", cn_cal[idx2 + k])] = "SUSPENDED"

    return events


def _generate_bars(
    *,
    specs: list[StockSpec],
    cal: list[date],
    corp_actions: list[dict[str, Any]],
    micro_events: dict[tuple[str, date], str],
    rng: np.random.Generator,
) -> list[dict[str, Any]]:
    """Generate deterministic, factor-structured raw OHLCV bars obeying every market rule."""
    n_days = len(cal)
    # Market & industry daily common shocks for realistic cross-sectional covariance
    mkt_ret = rng.normal(0.00035, 0.011, size=n_days)
    # Inject recognizable CN A-share stress windows (2024-01..02 small-cap quake & 2025-04 tariff dip)
    for i, d in enumerate(cal):
        if date(2024, 1, 22) <= d <= date(2024, 2, 6):
            mkt_ret[i] -= 0.008
        elif date(2024, 9, 24) <= d <= date(2024, 10, 8):
            mkt_ret[i] += 0.018
        elif date(2025, 4, 2) <= d <= date(2025, 4, 9):
            mkt_ret[i] -= 0.009

    all_industries = sorted({s.industry_l1 for s in specs})
    ind_ret = {ind: rng.normal(0.0, 0.006, size=n_days) for ind in all_industries}

    ca_lookup: dict[tuple[str, date], list[dict[str, Any]]] = {}
    for ca in corp_actions:
        ca_lookup.setdefault((ca["listing_id"], ca["ex_date"]), []).append(ca)

    bars: list[dict[str, Any]] = []
    for s_idx, spec in enumerate(specs):
        prefix = "CN" if spec.market == Market.CN_A else "US"
        lid = f"{prefix}:{spec.symbol}"
        beta = float(rng.uniform(0.75, 1.35))
        idio_vol = float(rng.uniform(0.012, 0.025))
        # Subtle persistent alpha drift differentiated across names so factor labs see signal
        alpha_daily = float(rng.normal(0.00015, 0.00035))
        if spec.delist_date is not None:
            alpha_daily = -0.0022  # Realistic distress drift prior to delisting

        close_raw = float(spec.init_price)
        adj_factor = 1.0
        shares_m = float(spec.float_shares_m)
        base_turnover = float(rng.uniform(0.008, 0.032))

        for t_idx, d in enumerate(cal):
            if spec.delist_date is not None and d > spec.delist_date:
                break

            is_st = _is_st_on_date(spec, d)
            actions_today = ca_lookup.get((lid, d), [])

            # Corporate action adjustment on ex_date (exact total-return continuity)
            if t_idx == 0:
                pre_close = round_tick_half_up(close_raw)
            elif actions_today:
                prev_raw = close_raw
                total_cash = sum(float(a["cash_dividend_per_share"]) for a in actions_today)
                total_mult = 1.0
                for a in actions_today:
                    total_mult *= float(a["split_ratio"]) + float(a["bonus_share_ratio"])
                ex_ref_exact = (prev_raw - total_cash) / total_mult
                # Exact back-adjustment factor update ensures |r_adj - r_ex| < 1e-12
                adj_factor = adj_factor * (prev_raw / ex_ref_exact)
                shares_m = shares_m * total_mult
                pre_close = round_tick_half_up(ex_ref_exact)
                close_raw = ex_ref_exact
            else:
                pre_close = round_tick_half_up(close_raw)

            limit_pct = get_expected_price_limit_pct(spec.market, spec.board, is_st, d)
            if limit_pct is not None:
                up_limit: float | None = round_tick_half_up(pre_close * (1.0 + limit_pct))
                down_limit: float | None = round_tick_half_up(pre_close * (1.0 - limit_pct))
            else:
                up_limit = None
                down_limit = None

            ev = micro_events.get((spec.symbol, d))
            # Avoid conflicting suspension on a corp-action ex_date
            if actions_today and ev == "SUSPENDED":
                ev = None

            if ev == "SUSPENDED":
                open_px = high_px = low_px = close_px = vwap_px = pre_close
                vol_shares = 0
                amt_ccy = 0.0
                is_susp = True
            elif ev == "LIMIT_UP" and up_limit is not None:
                open_px = high_px = low_px = close_px = vwap_px = up_limit
                vol_shares = int(max(10_000, round(shares_m * 1e6 * 0.0018 / 100.0) * 100))
                amt_ccy = round(vwap_px * vol_shares, 2)
                is_susp = False
            elif ev == "LIMIT_DOWN" and down_limit is not None:
                open_px = high_px = low_px = close_px = vwap_px = down_limit
                vol_shares = int(max(10_000, round(shares_m * 1e6 * 0.0015 / 100.0) * 100))
                amt_ccy = round(vwap_px * vol_shares, 2)
                is_susp = False
            else:
                raw_r = (
                    alpha_daily
                    + beta * float(mkt_ret[t_idx])
                    + float(ind_ret[spec.industry_l1][t_idx])
                    + float(rng.normal(0.0, idio_vol))
                )
                if limit_pct is not None:
                    raw_r = float(np.clip(raw_r, -limit_pct * 0.96, limit_pct * 0.96))
                else:
                    raw_r = float(np.clip(raw_r, -0.18, 0.22))

                target_close = max(1.20, pre_close * (1.0 + raw_r))
                open_gap = float(rng.normal(0.0, 0.0045))
                target_open = max(1.20, pre_close * (1.0 + open_gap))
                spread = abs(float(rng.normal(0.009, 0.005)))
                target_high = max(target_open, target_close) * (1.0 + spread * 0.6)
                target_low = max(1.10, min(target_open, target_close) * (1.0 - spread * 0.6))

                if up_limit is not None and down_limit is not None:
                    target_open = float(np.clip(target_open, down_limit, up_limit))
                    target_close = float(np.clip(target_close, down_limit, up_limit))
                    target_high = float(np.clip(target_high, down_limit, up_limit))
                    target_low = float(np.clip(target_low, down_limit, up_limit))

                open_px = round_tick_half_up(target_open)
                close_px = round_tick_half_up(target_close)
                high_px = round_tick_half_up(max(target_high, open_px, close_px))
                low_px = round_tick_half_up(min(target_low, open_px, close_px))
                if up_limit is not None and down_limit is not None:
                    high_px = min(up_limit, max(high_px, open_px, close_px))
                    low_px = max(down_limit, min(low_px, open_px, close_px))
                vwap_px = round_tick_half_up(
                    float(np.clip((open_px + high_px + low_px + 2.0 * close_px) / 5.0, low_px, high_px))
                )

                # Volume responds moderately to return magnitude (gives volume-price factors real structure)
                turnover = max(
                    0.0015,
                    base_turnover * (1.0 + 12.0 * abs(raw_r)) * float(rng.lognormal(0.0, 0.25)),
                )
                raw_vol = shares_m * 1e6 * turnover
                vol_shares = int(max(5000, round(raw_vol / 100.0) * 100))
                amt_ccy = round(vwap_px * vol_shares, 2)
                is_susp = False

            close_raw = close_px
            float_mcap = round(close_px * shares_m * 1e6, 2)
            free_float_mcap = round(float_mcap * 0.72, 2)
            avail_ts = datetime(d.year, d.month, d.day, 8, 0 if spec.market == Market.CN_A else 21, tzinfo=UTC)

            bars.append(
                {
                    "listing_id": lid,
                    "symbol": spec.symbol,
                    "market": spec.market.value,
                    "trade_date": d,
                    "open": float(open_px),
                    "high": float(high_px),
                    "low": float(low_px),
                    "close": float(close_px),
                    "pre_close": float(pre_close),
                    "vwap": float(vwap_px),
                    "volume_shares": int(vol_shares),
                    "amount_ccy": float(amt_ccy),
                    "adj_factor": float(adj_factor),
                    "up_limit": float(up_limit) if up_limit is not None else None,
                    "down_limit": float(down_limit) if down_limit is not None else None,
                    "is_suspended": is_susp,
                    "is_st": is_st,
                    "float_market_cap": float(float_mcap),
                    "free_float_market_cap": float(free_float_mcap),
                    "source_id": "tushare_daily_golden" if spec.market == Market.CN_A else "massive_eod_golden",
                    "available_at": avail_ts,
                    "quality_state": QualityState.OBSERVED.value,
                }
            )
    return bars


def _build_pit_financials(
    cn_specs: list[StockSpec],
    us_specs: list[StockSpec],
    rng: np.random.Generator,
) -> list[dict[str, Any]]:
    """Generate quarterly PIT financial facts + 10 explicit initial->restated->withdrawn CN_A test pairs."""
    rows: list[dict[str, Any]] = []
    quarters = [
        (date(2023, 12, 31), date(2024, 4, 22)),
        (date(2024, 3, 31), date(2024, 4, 28)),
        (date(2024, 6, 30), date(2024, 8, 25)),
        (date(2024, 9, 30), date(2024, 10, 28)),
        (date(2024, 12, 31), date(2025, 4, 21)),
        (date(2025, 3, 31), date(2025, 4, 29)),
        (date(2025, 6, 30), date(2025, 8, 26)),
        (date(2025, 9, 30), date(2025, 10, 29)),
        (date(2025, 12, 31), date(2026, 4, 20)),
        (date(2026, 3, 31), date(2026, 4, 28)),
        (date(2026, 6, 30), date(2026, 8, 24)),
        (date(2026, 9, 30), date(2026, 10, 27)),
    ]
    metrics = ("revenue", "net_profit_deducted", "roe_ttm", "ocf", "equity", "total_debt", "eps")

    for spec in cn_specs + us_specs:
        is_cn = spec.market == Market.CN_A
        issuer_id = f"ISSUER:{spec.symbol}"
        mcap_scale = spec.init_price * spec.float_shares_m * 1e6
        base_rev = mcap_scale * float(rng.uniform(0.12, 0.45))
        base_margin = float(rng.uniform(0.06, 0.28))
        base_roe = float(rng.uniform(0.05, 0.24))
        base_eq = mcap_scale * float(rng.uniform(0.25, 0.65))
        base_debt = base_eq * float(rng.uniform(0.20, 0.90))

        for q_idx, (period_end, base_ann) in enumerate(quarters):
            if spec.delist_date is not None and base_ann > spec.delist_date:
                continue
            ann_shift = int(rng.integers(-3, 4))
            ann_d = max(period_end + timedelta(days=15), base_ann + timedelta(days=ann_shift))
            pub_ts = datetime(ann_d.year, ann_d.month, ann_d.day, 10, 0, tzinfo=UTC)
            seen_ts = pub_ts + timedelta(hours=2)
            growth = (1.0 + float(rng.normal(0.025, 0.04))) ** (q_idx / 4.0)

            vals = {
                "revenue": round(base_rev * growth / 4.0, 2),
                "net_profit_deducted": round(base_rev * growth * base_margin / 4.0, 2),
                "roe_ttm": round(max(0.01, base_roe + float(rng.normal(0.0, 0.015))), 4),
                "ocf": round(base_rev * growth * base_margin * 1.15 / 4.0, 2),
                "equity": round(base_eq * growth, 2),
                "total_debt": round(base_debt * (1.0 + 0.01 * q_idx), 2),
                "eps": round((base_rev * growth * base_margin / 4.0) / max(spec.float_shares_m * 1e6, 1.0), 4),
            }
            for m_code in metrics:
                rows.append(
                    {
                        "fact_id": f"pit:{spec.symbol}:{m_code}:{period_end.isoformat()}:v1",
                        "issuer_id": issuer_id,
                        "symbol": spec.symbol,
                        "market": spec.market.value,
                        "metric_code": m_code,
                        "report_period_end": period_end,
                        "ann_date": ann_d,
                        "published_at": pub_ts,
                        "first_seen_at": seen_ts,
                        "revision_seq": 1,
                        "is_restated": False,
                        "is_withdrawn": False,
                        "value": float(vals[m_code]),
                        "currency": spec.currency,
                        "source_id": "tushare_income_vip_golden" if is_cn else "sec_edgar_xbrl",
                        "quality_state": QualityState.OBSERVED.value,
                    }
                )

    # Construct 10 explicit, deterministic CN_A PIT chains (Initial -> Restated -> Optional Withdrawn)
    # on report_period_end = 2024-12-31 / metric_code = "net_profit_deducted"
    pit_targets = [
        ("002463.SZ", True, False),   # pair 1: initial -> restated -> withdrawn
        ("600183.SH", False, False),  # pair 2: initial -> restated
        ("000988.SZ", False, False),  # pair 3: initial -> restated
        ("601168.SH", True, False),   # pair 4: initial -> restated -> withdrawn
        ("000630.SZ", False, False),  # pair 5: initial -> restated
        ("600536.SH", False, False),  # pair 6: initial -> restated
        ("002558.SZ", True, False),   # pair 7: initial -> restated -> withdrawn
        ("600966.SH", False, False),  # pair 8: initial -> restated
        ("000831.SZ", False, True),   # pair 9: delayed feed ingestion (first_seen_at = T1 + 3d) + restated
        ("601233.SH", True, True),    # pair 10: delayed feed ingestion + restated + withdrawn
    ]
    # Replace the base v1 row for these 10 targets on 2024-12-31 net_profit_deducted with exact sentinel chains
    target_syms = {sym for sym, _, _ in pit_targets}
    rows = [
        r
        for r in rows
        if not (
            r["symbol"] in target_syms
            and r["metric_code"] == "net_profit_deducted"
            and r["report_period_end"] == date(2024, 12, 31)
        )
    ]

    for idx, (sym, has_withdrawal, delayed_ingest) in enumerate(pit_targets, start=1):
        issuer_id = f"ISSUER:{sym}"
        period_end = date(2024, 12, 31)
        t1_date = date(2025, 3, 28)
        t1_pub = datetime(2025, 3, 28, 10, 0, tzinfo=UTC)
        t1_seen = t1_pub + (timedelta(days=3) if delayed_ingest else timedelta(hours=2))
        v1_val = float(100_000_000.0 * idx)

        rows.append(
            {
                "fact_id": f"pit-chain:{sym}:net_profit_deducted:2024-12-31:v1",
                "issuer_id": issuer_id,
                "symbol": sym,
                "market": Market.CN_A.value,
                "metric_code": "net_profit_deducted",
                "report_period_end": period_end,
                "ann_date": t1_date,
                "published_at": t1_pub,
                "first_seen_at": t1_seen,
                "revision_seq": 1,
                "is_restated": False,
                "is_withdrawn": False,
                "value": v1_val,
                "currency": "CNY",
                "source_id": "cninfo_pit_golden",
                "quality_state": QualityState.OBSERVED.value,
            }
        )

        # Revision 2: Restatement published 2025-05-15 (materially lower restated earnings)
        t2_date = date(2025, 5, 15)
        t2_pub = datetime(2025, 5, 15, 10, 0, tzinfo=UTC)
        rows.append(
            {
                "fact_id": f"pit-chain:{sym}:net_profit_deducted:2024-12-31:v2",
                "issuer_id": issuer_id,
                "symbol": sym,
                "market": Market.CN_A.value,
                "metric_code": "net_profit_deducted",
                "report_period_end": period_end,
                "ann_date": t2_date,
                "published_at": t2_pub,
                "first_seen_at": t2_pub + timedelta(hours=2),
                "revision_seq": 2,
                "is_restated": True,
                "is_withdrawn": False,
                "value": round(v1_val * 0.78, 2),
                "currency": "CNY",
                "source_id": "cninfo_pit_golden",
                "quality_state": QualityState.OBSERVED.value,
            }
        )

        # Revision 3: Withdrawal published 2025-07-10
        if has_withdrawal:
            t3_date = date(2025, 7, 10)
            t3_pub = datetime(2025, 7, 10, 10, 0, tzinfo=UTC)
            rows.append(
                {
                    "fact_id": f"pit-chain:{sym}:net_profit_deducted:2024-12-31:v3",
                    "issuer_id": issuer_id,
                    "symbol": sym,
                    "market": Market.CN_A.value,
                    "metric_code": "net_profit_deducted",
                    "report_period_end": period_end,
                    "ann_date": t3_date,
                    "published_at": t3_pub,
                    "first_seen_at": t3_pub + timedelta(hours=1),
                    "revision_seq": 3,
                    "is_restated": False,
                    "is_withdrawn": True,
                    "value": 0.0,
                    "currency": "CNY",
                    "source_id": "cninfo_pit_golden",
                    "quality_state": QualityState.OBSERVED.value,
                }
            )

    # Also add 2 US_EQ SEC EDGAR restatement chains (NVDA & AAPL 2024-12-31 revenue)
    for us_sym, v1_usd, v2_usd in [("NVDA", 35_100_000_000.0, 35_082_000_000.0), ("AAPL", 124_300_000_000.0, 124_180_000_000.0)]:
        rows = [
            r
            for r in rows
            if not (
                r["symbol"] == us_sym
                and r["metric_code"] == "revenue"
                and r["report_period_end"] == date(2024, 12, 31)
            )
        ]
        t1 = datetime(2025, 2, 26, 21, 30, tzinfo=UTC)
        t2 = datetime(2025, 4, 18, 21, 30, tzinfo=UTC)
        rows.append(
            {
                "fact_id": f"edgar-chain:{us_sym}:revenue:2024-12-31:v1",
                "issuer_id": f"ISSUER:{us_sym}",
                "symbol": us_sym,
                "market": Market.US_EQ.value,
                "metric_code": "revenue",
                "report_period_end": date(2024, 12, 31),
                "ann_date": date(2025, 2, 26),
                "published_at": t1,
                "first_seen_at": t1 + timedelta(hours=1),
                "revision_seq": 1,
                "is_restated": False,
                "is_withdrawn": False,
                "value": v1_usd,
                "currency": "USD",
                "source_id": "sec_edgar_xbrl",
                "quality_state": QualityState.OBSERVED.value,
            }
        )
        rows.append(
            {
                "fact_id": f"edgar-chain:{us_sym}:revenue:2024-12-31:v2",
                "issuer_id": f"ISSUER:{us_sym}",
                "symbol": us_sym,
                "market": Market.US_EQ.value,
                "metric_code": "revenue",
                "report_period_end": date(2024, 12, 31),
                "ann_date": date(2025, 4, 18),
                "published_at": t2,
                "first_seen_at": t2 + timedelta(hours=1),
                "revision_seq": 2,
                "is_restated": True,
                "is_withdrawn": False,
                "value": v2_usd,
                "currency": "USD",
                "source_id": "sec_edgar_xbrl",
                "quality_state": QualityState.OBSERVED.value,
            }
        )

    return rows


def _build_universe_members(
    cn_specs: list[StockSpec],
    us_specs: list[StockSpec],
    bars_df: pl.DataFrame,
    cn_cal: list[date],
    us_cal: list[date],
) -> list[dict[str, Any]]:
    """Build PIT semi-annual universe constituent snapshots with normalized official weights."""
    cn_rebal_dates = [
        _snap_to_calendar(d, cn_cal)
        for d in (
            date(2024, 1, 2),
            date(2024, 6, 17),
            date(2024, 12, 16),
            date(2025, 6, 16),
            date(2025, 12, 15),
            date(2026, 6, 15),
        )
    ]
    us_rebal_dates = [
        _snap_to_calendar(d, us_cal)
        for d in (
            date(2024, 1, 2),
            date(2024, 6, 17),
            date(2024, 12, 16),
            date(2025, 6, 16),
            date(2025, 12, 15),
            date(2026, 6, 15),
        )
    ]

    # Map (listing_id, trade_date) -> float_market_cap for authentic weight calculation
    mcap_map: dict[tuple[str, date], float] = {}
    for r in bars_df.select("listing_id", "trade_date", "float_market_cap").iter_rows(named=True):
        mcap_map[(str(r["listing_id"]), r["trade_date"])] = float(r["float_market_cap"])

    rows: list[dict[str, Any]] = []

    def _emit_universe(
        universe_id: str,
        members_pool: list[StockSpec],
        rebal_dates: list[date],
        prefix: str,
    ) -> None:
        for idx, rb_date in enumerate(rebal_dates):
            next_eff_to: date | None = (
                rebal_dates[idx + 1] - timedelta(days=1) if idx + 1 < len(rebal_dates) else None
            )
            alive = [
                s
                for s in members_pool
                if s.list_date <= rb_date and (s.delist_date is None or s.delist_date >= rb_date)
            ]
            # Use sqrt(float_mcap) weighting for broad realistic index dispersion without single-stock dominance
            raw_scores = []
            for s in alive:
                lid = f"{prefix}:{s.symbol}"
                mcap = mcap_map.get((lid, rb_date), s.init_price * s.float_shares_m * 1e6)
                raw_scores.append(float(np.sqrt(max(mcap, 1.0))))
            total_score = sum(raw_scores)
            weights = [sc / total_score for sc in raw_scores]
            # Exact normalization residual correction on largest member
            weights[0] += 1.0 - sum(weights)

            for s, w in zip(alive, weights):
                lid = f"{prefix}:{s.symbol}"
                eff_to = next_eff_to
                if s.delist_date is not None and (eff_to is None or s.delist_date < eff_to):
                    eff_to = s.delist_date
                rows.append(
                    {
                        "universe_id": universe_id,
                        "as_of_date": rb_date,
                        "listing_id": lid,
                        "symbol": s.symbol,
                        "weight": float(w),
                        "weight_quality": "official",
                        "effective_from": rb_date,
                        "effective_to": eff_to,
                    }
                )

    _emit_universe("CSI300", [s for s in cn_specs if s.csi300], cn_rebal_dates, "CN")
    _emit_universe("CSI500", [s for s in cn_specs if s.csi500], cn_rebal_dates, "CN")
    _emit_universe("CSI1000", [s for s in cn_specs if s.csi1000], cn_rebal_dates, "CN")
    _emit_universe("CN_ALL", cn_specs, cn_rebal_dates, "CN")
    _emit_universe("SP500_SAMPLE", us_specs, us_rebal_dates, "US")
    return rows


def _build_industry_memberships(cn_specs: list[StockSpec], us_specs: list[StockSpec]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for s in cn_specs:
        lid = f"CN:{s.symbol}"
        if s.industry_switch is not None:
            old_l1, switch_date = s.industry_switch
            rows.append(
                {
                    "listing_id": lid,
                    "symbol": s.symbol,
                    "taxonomy": "SW2021",
                    "l1_code": SW_CODE_MAP[old_l1],
                    "l1_name": old_l1,
                    "valid_from": s.list_date,
                    "valid_to": switch_date - timedelta(days=1),
                }
            )
            rows.append(
                {
                    "listing_id": lid,
                    "symbol": s.symbol,
                    "taxonomy": "SW2021",
                    "l1_code": SW_CODE_MAP[s.industry_l1],
                    "l1_name": s.industry_l1,
                    "valid_from": switch_date,
                    "valid_to": s.delist_date,
                }
            )
        else:
            rows.append(
                {
                    "listing_id": lid,
                    "symbol": s.symbol,
                    "taxonomy": "SW2021",
                    "l1_code": SW_CODE_MAP[s.industry_l1],
                    "l1_name": s.industry_l1,
                    "valid_from": s.list_date,
                    "valid_to": s.delist_date,
                }
            )

    for s in us_specs:
        rows.append(
            {
                "listing_id": f"US:{s.symbol}",
                "symbol": s.symbol,
                "taxonomy": "GICS",
                "l1_code": GICS_CODE_MAP[s.industry_l1],
                "l1_name": s.industry_l1,
                "valid_from": s.list_date,
                "valid_to": s.delist_date,
            }
        )
    return rows


def _build_lifecycle_rows(cn_specs: list[StockSpec], us_specs: list[StockSpec]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for s in cn_specs + us_specs:
        prefix = "CN" if s.market == Market.CN_A else "US"
        rows.append(
            {
                "listing_id": f"{prefix}:{s.symbol}",
                "issuer_id": f"ISSUER:{s.symbol}",
                "symbol": s.symbol,
                "name": s.name,
                "market": s.market.value,
                "board": s.board.value,
                "exchange": s.exchange,
                "list_date": s.list_date,
                "delist_date": s.delist_date,
                "is_st": _is_st_on_date(s, END_DATE),
                "st_intervals_json": str([(a.isoformat(), b.isoformat() if b else None) for a, b in s.st_intervals]),
                "currency": s.currency,
            }
        )
    return rows


def _build_seed_experiments(snapshot_id: str) -> list[ExperimentRecord]:
    """5 baseline & comparison ExperimentRecords aligned with Pro-Quant V2.0 UI & CPCV gate."""
    return [
        ExperimentRecord(
            experiment_id="EXP-0419",
            family_id="FAM-CSI500-ALPHA",
            name="OLS-ICIR 加权",
            model_type="OLS-ICIR",
            feature_summary="6 因子 · 线性 ICIR 加权",
            universe_id="CSI500",
            snapshot_id=snapshot_id,
            trial_count_m=6,
            is_sharpe=2.08,
            oos_sharpe=1.79,
            oos_is_ratio=round(1.79 / 2.08, 4),
            cpcv_pbo=0.07,
            dsr=0.98,
            oos_excess_ann=10.4,
            verdict=GateVerdict.PASS,
            status_badge="BASELINE",
            created_at=datetime(2026, 11, 18, 9, 15, tzinfo=UTC),
            notes="干净线性对照基线，低换手、零显著过拟合迹象。",
        ),
        ExperimentRecord(
            experiment_id="EXP-0425",
            family_id="FAM-CSI500-ALPHA",
            name="Ridge L2",
            model_type="Ridge",
            feature_summary="42 因子 · α=0.8 L2 正则",
            universe_id="CSI500",
            snapshot_id=snapshot_id,
            trial_count_m=14,
            is_sharpe=2.24,
            oos_sharpe=1.88,
            oos_is_ratio=round(1.88 / 2.24, 4),
            cpcv_pbo=0.09,
            dsr=0.98,
            oos_excess_ann=11.1,
            verdict=GateVerdict.PASS,
            status_badge="PASS",
            created_at=datetime(2026, 11, 20, 11, 30, tzinfo=UTC),
            notes="截面岭回归，CPCV 15 路径稳健通过，DSR 0.98。",
        ),
        ExperimentRecord(
            experiment_id="EXP-0427",
            family_id="FAM-CSI500-ALPHA",
            name="LightGBM 截面排序",
            model_type="LightGBM",
            feature_summary="158 因子 · lambdarank",
            universe_id="CSI500",
            snapshot_id=snapshot_id,
            trial_count_m=22,
            is_sharpe=2.71,
            oos_sharpe=2.12,
            oos_is_ratio=round(2.12 / 2.71, 4),
            cpcv_pbo=0.12,
            dsr=0.99,
            oos_excess_ann=13.6,
            verdict=GateVerdict.PASS,
            status_badge="PROMOTED",
            created_at=datetime(2026, 11, 22, 14, 40, tzinfo=UTC),
            notes="当前主推影子组合模型：CPCV PBO 0.12 <= 0.20, DSR 0.99 >= 0.95。",
        ),
        ExperimentRecord(
            experiment_id="EXP-0424",
            family_id="FAM-CSI500-ALPHA",
            name="CatBoost + 时序特征",
            model_type="CatBoost",
            feature_summary="182 特征 · depth 8",
            universe_id="CSI500",
            snapshot_id=snapshot_id,
            trial_count_m=28,
            is_sharpe=2.95,
            oos_sharpe=1.71,
            oos_is_ratio=round(1.71 / 2.95, 4),
            cpcv_pbo=0.22,
            dsr=0.91,
            oos_excess_ann=10.2,
            verdict=GateVerdict.INCONCLUSIVE,
            status_badge="HOLD",
            created_at=datetime(2026, 11, 21, 16, 10, tzinfo=UTC),
            notes="HOLD 观察：DSR 0.91 < 0.95 且 PBO 0.22 接近警戒线，需降低树深重验。",
        ),
        ExperimentRecord(
            experiment_id="RDA-0042",
            family_id="FAM-RD-AGENT",
            name="RD-Agent 候选 #42",
            model_type="RD-Agent",
            feature_summary="LLM 生成 · 17 层算子",
            universe_id="CSI500",
            snapshot_id=snapshot_id,
            trial_count_m=42,
            is_sharpe=3.42,
            oos_sharpe=0.63,
            oos_is_ratio=round(0.63 / 3.42, 4),
            cpcv_pbo=0.41,
            dsr=0.58,
            oos_excess_ann=3.1,
            verdict=GateVerdict.REJECT,
            status_badge="REJECTED",
            created_at=datetime(2026, 11, 24, 6, 42, tzinfo=UTC),
            notes="过拟合门禁拦截：PBO 0.41 > 0.25、DSR 0.58 < 0.95、OOS/IS 仅 0.184，已记录至失败记忆。",
        ),
    ]


def seed_golden_lake(
    *,
    root_dir: Path | str = "data/quant-lake",
    db_path: Path | str | None = None,
    snapshot_id: str = GOLDEN_SNAPSHOT_ID,
    seed: int = GOLDEN_SEED,
    export_qlib: bool = True,
) -> dict[str, Any]:
    """Build and persist the deterministic dual-market (CN_A + US_EQ) Golden PIT Snapshot."""
    rng = np.random.default_rng(seed)
    cn_cal = build_trading_calendar(START_DATE, END_DATE, CN_HOLIDAYS)
    us_cal = build_trading_calendar(START_DATE, END_DATE, US_HOLIDAYS)
    assert ST_MAIN_RULE_CHANGE_DATE in cn_cal, "2026-07-06 must be in CN_A trading calendar"

    cn_specs = _build_cn_specs()
    us_specs = _build_us_specs()

    corp_actions = _build_corp_actions(cn_cal, us_cal)
    micro_events = _build_microstructure_events(cn_cal)

    cn_bars = _generate_bars(
        specs=cn_specs,
        cal=cn_cal,
        corp_actions=corp_actions,
        micro_events=micro_events,
        rng=rng,
    )
    us_bars = _generate_bars(
        specs=us_specs,
        cal=us_cal,
        corp_actions=corp_actions,
        micro_events={},
        rng=rng,
    )
    bars_df = pl.DataFrame(cn_bars + us_bars)

    corp_df = pl.DataFrame(corp_actions)
    fin_df = pl.DataFrame(_build_pit_financials(cn_specs, us_specs, rng))
    uni_df = pl.DataFrame(_build_universe_members(cn_specs, us_specs, bars_df, cn_cal, us_cal))
    ind_df = pl.DataFrame(_build_industry_memberships(cn_specs, us_specs))
    life_df = pl.DataFrame(_build_lifecycle_rows(cn_specs, us_specs))

    lake = QuantLake(root_dir=root_dir, db_path=db_path)
    snapshot = lake.publish_snapshot(
        snapshot_id=snapshot_id,
        as_of=SNAPSHOT_AS_OF,
        created_at=SNAPSHOT_AS_OF,
        markets=[Market.CN_A, Market.US_EQ],
        source_provenance=[
            f"golden_cli_seed:{seed}",
            "cn_a:60_instruments_with_3_delisted_2_st_20260706_boundary",
            "us_eq:12_sp500_sample_sec_pit",
        ],
        tables={
            "bar_raw_1d": bars_df,
            "corp_action": corp_df,
            "financial_fact": fin_df,
            "universe_member": uni_df,
            "industry_membership": ind_df,
            "instrument_lifecycle": life_df,
        },
        run_qa=True,
    )

    for exp in _build_seed_experiments(snapshot.snapshot_id):
        lake.save_experiment(exp)

    qa_report = lake.run_qa_sentinel(snapshot.snapshot_id)
    if qa_report.overall_status != "pass":
        failed = [f"{c.check_id}: {c.detail}" for c in qa_report.checks if c.status != "pass"]
        raise RuntimeError(f"Golden dataset failed QA Sentinel: {failed}")

    qlib_exports: dict[str, Any] = {}
    if export_qlib:
        qlib_exports["CN_A"] = lake.export_qlib_bin(snapshot.snapshot_id, market="CN_A")
        qlib_exports["US_EQ"] = lake.export_qlib_bin(snapshot.snapshot_id, market="US_EQ")

    return {
        "snapshot": snapshot,
        "qa_report": qa_report,
        "qlib_exports": qlib_exports,
        "cn_trading_days": len(cn_cal),
        "us_trading_days": len(us_cal),
        "experiments_seeded": 5,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Pro-Quant V2.0 Golden PIT Data Lake")
    parser.add_argument("--root-dir", default="data/quant-lake", help="QuantLake root directory")
    parser.add_argument("--db-path", default="data/state/quant_registry.db", help="SQLite WAL registry path")
    parser.add_argument("--snapshot-id", default=GOLDEN_SNAPSHOT_ID, help="Snapshot identifier")
    args = parser.parse_args()

    result = seed_golden_lake(
        root_dir=args.root_dir,
        db_path=args.db_path,
        snapshot_id=args.snapshot_id,
    )
    snap = result["snapshot"]
    qa = result["qa_report"]

    print(f"=== Pro-Quant V2.0 Golden PIT Dataset Seeded ({snap.snapshot_id}) ===")
    print(f"Markets          : {[m.value for m in snap.markets]}")
    print(f"Trading Days     : CN_A={result['cn_trading_days']}, US_EQ={result['us_trading_days']}")
    print(f"Delisted Count   : {snap.delisted_count}")
    print(f"Manifest SHA-256 : {snap.manifest_sha256[:16]}...")
    print(f"Table Counts     : {snap.table_counts}")
    print(f"Experiments      : {result['experiments_seeded']} pre-seeded (EXP-0419..RDA-0042)")
    print(f"QA Sentinel      : {qa.overall_status.upper()} ({sum(c.status == 'pass' for c in qa.checks)}/{len(qa.checks)} checks passed)")
    print(f"Fact Fingerprint : {qa.fact_fingerprint[:16]}...")
    for chk in qa.checks:
        print(f"  [{chk.status.upper():4s}] {chk.check_id}: {chk.detail}")
    for mkt, exp in result["qlib_exports"].items():
        print(
            f"Qlib Bin [{mkt:5s}] : {exp['symbol_count']} symbols × {exp['calendar_days']} days "
            f"(cache_hit={exp['cache_hit']}) -> {exp['provider_uri']}"
        )


if __name__ == "__main__":
    main()
