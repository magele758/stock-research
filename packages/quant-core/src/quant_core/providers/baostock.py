"""BaoStock adapter for independent CN A-share raw EOD and back-adjustment factor reconciliation."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import polars as pl

from quant_core.providers.base import DataProviderAdapter, ProviderError, ProviderUpstreamError
from quant_core.schemas import Market


def to_baostock_code(symbol: str) -> str:
    """Convert canonical ticker ``600519.SH`` / ``000001.SZ`` to BaoStock ``sh.600519``."""
    sym = symbol.strip().upper()
    if "." in sym:
        code, market = sym.split(".", 1)
        if market in ("SH", "SZ"):
            return f"{market.lower()}.{code}"
    return sym.lower()


def from_baostock_code(bs_code: str) -> str:
    """Convert BaoStock ``sh.600519`` back to canonical ``600519.SH``."""
    parts = bs_code.strip().split(".")
    if len(parts) == 2 and parts[0].lower() in ("sh", "sz"):
        return f"{parts[1]}.{parts[0].upper()}"
    return bs_code.upper()


class BaoStockProvider(DataProviderAdapter):
    """Independent backup/reconciliation adapter wrapping the official ``baostock`` SDK.

    Always queries unadjusted bars (``adjustflag='3'``) and explicit back-adjustment
    factors (``query_adjust_factor``) for cross-source verification.
    Never synthesizes fake data if ``baostock`` is missing or unreachable.
    """

    provider_id = "baostock"
    supported_markets = (Market.CN_A,)

    def __init__(self, *, sdk_module: Any | None = None, rate_per_sec: float = 4.0) -> None:
        super().__init__(rate_per_sec=rate_per_sec, burst=4)
        self._sdk = sdk_module

    def _load_sdk(self) -> Any:
        if self._sdk is not None:
            return self._sdk
        try:
            import baostock as bs  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ProviderError(
                "BaoStock SDK ('baostock') is not installed in this environment. "
                "Install 'baostock' to enable live reconciliation; refusing to synthesize fallback data."
            ) from exc
        return bs

    def check_credentials(self) -> None:
        # BaoStock is anonymous login, so availability depends on SDK installation.
        self._load_sdk()

    @staticmethod
    def _collect_result_set(rs: Any) -> pl.DataFrame:
        if str(getattr(rs, "error_code", "0")) != "0":
            raise ProviderUpstreamError(
                f"BaoStock query failed ({rs.error_code}): {getattr(rs, 'error_msg', '')}"
            )
        rows: list[list[Any]] = []
        while rs.next():
            rows.append(rs.get_row_data())
        fields = list(getattr(rs, "fields", []))
        if not fields:
            return pl.DataFrame()
        if not rows:
            return pl.DataFrame({f: [] for f in fields})
        return pl.DataFrame(rows, schema=fields, orient="row")

    def fetch_daily_bars(
        self,
        *,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        trade_date: date | None = None,
    ) -> pl.DataFrame:
        if not symbols:
            raise ProviderError("BaoStockProvider.fetch_daily_bars requires an explicit non-empty symbols list.")
        s_date = start_date or trade_date
        e_date = end_date or trade_date
        if s_date is None or e_date is None:
            raise ProviderError("BaoStockProvider requires start_date/end_date or trade_date.")

        bs = self._load_sdk()
        login_res = bs.login()
        if str(getattr(login_res, "error_code", "0")) != "0":
            raise ProviderUpstreamError(f"BaoStock login failed: {getattr(login_res, 'error_msg', '')}")
        frames: list[pl.DataFrame] = []
        try:
            for sym in symbols:
                self.rate_limiter.acquire(1.0)
                rs = bs.query_history_k_data_plus(
                    to_baostock_code(sym),
                    "date,code,open,high,low,close,preclose,volume,amount,tradestatus,isST",
                    start_date=s_date.isoformat(),
                    end_date=e_date.isoformat(),
                    frequency="d",
                    adjustflag="3",  # Strictly unadjusted raw prices
                )
                df = self._collect_result_set(rs)
                if df.is_empty():
                    continue
                frames.append(
                    df.select(
                        pl.lit(sym).alias("symbol"),
                        pl.col("date").str.strptime(pl.Date, "%Y-%m-%d").alias("trade_date"),
                        pl.col("open").cast(pl.Float64, strict=False),
                        pl.col("high").cast(pl.Float64, strict=False),
                        pl.col("low").cast(pl.Float64, strict=False),
                        pl.col("close").cast(pl.Float64, strict=False),
                        pl.col("preclose").cast(pl.Float64, strict=False).alias("pre_close"),
                        pl.col("volume").cast(pl.Int64, strict=False).fill_null(0).alias("volume_shares"),
                        pl.col("amount").cast(pl.Float64, strict=False).fill_null(0.0).alias("amount_ccy"),
                        (pl.col("tradestatus") == "0").alias("is_suspended"),
                        (pl.col("isST") == "1").alias("is_st"),
                        pl.lit("baostock_daily").alias("source_id"),
                        pl.lit(datetime.now(UTC)).alias("available_at"),
                    )
                )
        finally:
            bs.logout()
        return pl.concat(frames, how="vertical_relaxed") if frames else pl.DataFrame()

    def fetch_adj_factors(
        self,
        *,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        trade_date: date | None = None,
    ) -> pl.DataFrame:
        if not symbols:
            raise ProviderError("BaoStockProvider.fetch_adj_factors requires symbols.")
        s_date = start_date or trade_date or date(2015, 1, 1)
        e_date = end_date or trade_date or date.today()
        bs = self._load_sdk()
        login_res = bs.login()
        if str(getattr(login_res, "error_code", "0")) != "0":
            raise ProviderUpstreamError(f"BaoStock login failed: {getattr(login_res, 'error_msg', '')}")
        frames: list[pl.DataFrame] = []
        try:
            for sym in symbols:
                self.rate_limiter.acquire(1.0)
                rs = bs.query_adjust_factor(
                    code=to_baostock_code(sym),
                    start_date=s_date.isoformat(),
                    end_date=e_date.isoformat(),
                )
                df = self._collect_result_set(rs)
                if df.is_empty():
                    continue
                frames.append(
                    df.select(
                        pl.lit(sym).alias("symbol"),
                        pl.col("dividOperateDate").str.strptime(pl.Date, "%Y-%m-%d").alias("ex_date"),
                        pl.col("backAdjustFactor").cast(pl.Float64, strict=False).alias("adj_factor"),
                    )
                )
        finally:
            bs.logout()
        return pl.concat(frames, how="vertical_relaxed") if frames else pl.DataFrame()

    @staticmethod
    def reconcile_close_prices(
        primary_df: pl.DataFrame,
        backup_df: pl.DataFrame,
        tick_tolerance: float = 0.01,
    ) -> pl.DataFrame:
        """Return rows where primary and BaoStock raw closes diverge by more than ``tick_tolerance``."""
        joined = primary_df.select("symbol", "trade_date", pl.col("close").alias("primary_close")).join(
            backup_df.select("symbol", "trade_date", pl.col("close").alias("backup_close")),
            on=["symbol", "trade_date"],
            how="inner",
        )
        return joined.with_columns(
            (pl.col("primary_close") - pl.col("backup_close")).abs().alias("abs_diff")
        ).filter(pl.col("abs_diff") > tick_tolerance)
