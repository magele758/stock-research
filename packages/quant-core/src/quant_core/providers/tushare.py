"""Tushare Pro HTTP adapter for CN A-share raw OHLCV, adj_factor, lifecycle, PIT income, index weights & limits."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
import polars as pl

from quant_core.providers.base import (
    DataProviderAdapter,
    ProviderAuthError,
    ProviderPermissionError,
    ProviderRateLimitError,
    ProviderUpstreamError,
    RetryPolicy,
)
from quant_core.schemas import Market

TUSHARE_INDEX_MAP = {
    "CSI300": "000300.SH",
    "CSI500": "000905.SH",
    "CSI1000": "000852.SH",
}


def _fmt_ts_date(d: date | None) -> str | None:
    return d.strftime("%Y%m%d") if d is not None else None


class TushareProvider(DataProviderAdapter):
    """Lightweight HTTP adapter for Tushare Pro (https://api.tushare.pro).

    Reads ``TUSHARE_TOKEN`` from environment unless explicitly supplied.
    Supports: ``daily``, ``adj_factor``, ``stock_basic`` (L/D/P),
    ``income_vip``, ``index_weight``, and ``stk_limit``.
    """

    provider_id = "tushare"
    supported_markets = (Market.CN_A,)
    BASE_URL = "https://api.tushare.pro"

    def __init__(
        self,
        token: str | None = None,
        *,
        base_url: str = BASE_URL,
        rate_per_sec: float = 3.0,
        burst: int = 5,
        retry_policy: RetryPolicy | None = None,
        timeout_sec: float = 20.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            rate_per_sec=rate_per_sec,
            burst=burst,
            retry_policy=retry_policy,
            timeout_sec=timeout_sec,
            http_client=http_client,
        )
        self._token_override = token
        self.base_url = base_url

    def check_credentials(self) -> None:
        self._require_env("TUSHARE_TOKEN", self._token_override)

    def _get_token(self) -> str:
        return self._require_env("TUSHARE_TOKEN", self._token_override)

    def query(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        fields: str = "",
    ) -> pl.DataFrame:
        token = self._get_token()
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        payload = self._request_json(
            "POST",
            self.base_url,
            json_body={
                "api_name": api_name,
                "token": token,
                "params": clean_params,
                "fields": fields,
            },
        )
        code = payload.get("code")
        msg = str(payload.get("msg") or "")
        if code != 0:
            lower = msg.lower()
            if code in (40101, 40201) or "token" in lower:
                raise ProviderAuthError(f"Tushare auth error ({code}) on {api_name}: {msg}")
            if code in (40203,) or "积分" in msg or "权限" in msg or "permission" in lower:
                raise ProviderPermissionError(f"Tushare entitlement error ({code}) on {api_name}: {msg}")
            if "频" in msg or "limit" in lower or "too many" in lower:
                raise ProviderRateLimitError(f"Tushare rate limit ({code}) on {api_name}: {msg}")
            raise ProviderUpstreamError(f"Tushare error ({code}) on {api_name}: {msg}")

        data = payload.get("data") or {}
        cols = data.get("fields") or []
        items = data.get("items") or []
        if not cols:
            return pl.DataFrame()
        if not items:
            return pl.DataFrame({c: [] for c in cols})
        return pl.DataFrame(items, schema=cols, orient="row")

    # ------------------------------------------------------------------
    # Direct Tushare endpoint helpers
    # ------------------------------------------------------------------

    def fetch_daily(
        self,
        *,
        trade_date: date | None = None,
        ts_code: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pl.DataFrame:
        df = self.query(
            "daily",
            params={
                "trade_date": _fmt_ts_date(trade_date),
                "ts_code": ts_code,
                "start_date": _fmt_ts_date(start_date),
                "end_date": _fmt_ts_date(end_date),
            },
            fields="ts_code,trade_date,open,high,low,close,pre_close,vol,amount",
        )
        if df.is_empty():
            return df
        # Tushare daily vol is in 100-share lots ("手"), amount is in 1,000 CNY ("千元").
        return df.select(
            pl.col("ts_code").alias("symbol"),
            pl.col("trade_date").str.strptime(pl.Date, "%Y%m%d").alias("trade_date"),
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("pre_close").cast(pl.Float64),
            (pl.col("vol").cast(pl.Float64) * 100.0).round(0).cast(pl.Int64).alias("volume_shares"),
            (pl.col("amount").cast(pl.Float64) * 1000.0).alias("amount_ccy"),
            pl.lit("tushare_daily").alias("source_id"),
            pl.lit(datetime.now(UTC)).alias("available_at"),
        )

    def fetch_adj_factor(
        self,
        *,
        trade_date: date | None = None,
        ts_code: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pl.DataFrame:
        df = self.query(
            "adj_factor",
            params={
                "trade_date": _fmt_ts_date(trade_date),
                "ts_code": ts_code,
                "start_date": _fmt_ts_date(start_date),
                "end_date": _fmt_ts_date(end_date),
            },
            fields="ts_code,trade_date,adj_factor",
        )
        if df.is_empty():
            return df
        return df.select(
            pl.col("ts_code").alias("symbol"),
            pl.col("trade_date").str.strptime(pl.Date, "%Y%m%d").alias("trade_date"),
            pl.col("adj_factor").cast(pl.Float64),
        )

    def fetch_stock_basic(self, list_statuses: tuple[str, ...] = ("L", "D", "P")) -> pl.DataFrame:
        frames: list[pl.DataFrame] = []
        for status in list_statuses:
            part = self.query(
                "stock_basic",
                params={"list_status": status},
                fields="ts_code,symbol,name,market,exchange,list_status,list_date,delist_date",
            )
            if not part.is_empty():
                frames.append(part)
        if not frames:
            return pl.DataFrame()
        return pl.concat(frames, how="vertical_relaxed").unique(subset=["ts_code"], keep="first")

    def fetch_income_vip(
        self,
        *,
        period: str | None = None,
        ann_date: date | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        ts_code: str | None = None,
    ) -> pl.DataFrame:
        return self.query(
            "income_vip",
            params={
                "period": period,
                "ann_date": _fmt_ts_date(ann_date),
                "start_date": _fmt_ts_date(start_date),
                "end_date": _fmt_ts_date(end_date),
                "ts_code": ts_code,
            },
            fields="ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,update_flag,revenue,n_income_attr_p,basic_eps",
        )

    def fetch_index_weight(
        self,
        index_code: str,
        *,
        start_date: date,
        end_date: date,
    ) -> pl.DataFrame:
        ts_index = TUSHARE_INDEX_MAP.get(index_code.upper(), index_code)
        df = self.query(
            "index_weight",
            params={
                "index_code": ts_index,
                "start_date": _fmt_ts_date(start_date),
                "end_date": _fmt_ts_date(end_date),
            },
            fields="index_code,con_code,trade_date,weight",
        )
        if df.is_empty():
            return df
        return df.select(
            pl.lit(index_code).alias("universe_id"),
            pl.col("con_code").alias("symbol"),
            pl.col("trade_date").str.strptime(pl.Date, "%Y%m%d").alias("as_of_date"),
            (pl.col("weight").cast(pl.Float64) / 100.0).alias("weight"),
        )

    def fetch_stk_limit(
        self,
        *,
        trade_date: date | None = None,
        ts_code: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pl.DataFrame:
        df = self.query(
            "stk_limit",
            params={
                "trade_date": _fmt_ts_date(trade_date),
                "ts_code": ts_code,
                "start_date": _fmt_ts_date(start_date),
                "end_date": _fmt_ts_date(end_date),
            },
            fields="ts_code,trade_date,pre_close,up_limit,down_limit",
        )
        if df.is_empty():
            return df
        return df.select(
            pl.col("ts_code").alias("symbol"),
            pl.col("trade_date").str.strptime(pl.Date, "%Y%m%d").alias("trade_date"),
            pl.col("pre_close").cast(pl.Float64),
            pl.col("up_limit").cast(pl.Float64),
            pl.col("down_limit").cast(pl.Float64),
        )

    # ------------------------------------------------------------------
    # DataProviderAdapter interface implementations
    # ------------------------------------------------------------------

    def fetch_daily_bars(
        self,
        *,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        trade_date: date | None = None,
    ) -> pl.DataFrame:
        if symbols:
            frames = [
                self.fetch_daily(ts_code=sym, start_date=start_date, end_date=end_date, trade_date=trade_date)
                for sym in symbols
            ]
            non_empty = [f for f in frames if not f.is_empty()]
            return pl.concat(non_empty, how="vertical_relaxed") if non_empty else pl.DataFrame()
        return self.fetch_daily(trade_date=trade_date, start_date=start_date, end_date=end_date)

    def fetch_adj_factors(
        self,
        *,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        trade_date: date | None = None,
    ) -> pl.DataFrame:
        if symbols:
            frames = [
                self.fetch_adj_factor(
                    ts_code=sym, start_date=start_date, end_date=end_date, trade_date=trade_date
                )
                for sym in symbols
            ]
            non_empty = [f for f in frames if not f.is_empty()]
            return pl.concat(non_empty, how="vertical_relaxed") if non_empty else pl.DataFrame()
        return self.fetch_adj_factor(trade_date=trade_date, start_date=start_date, end_date=end_date)

    def fetch_instrument_lifecycle(self) -> pl.DataFrame:
        return self.fetch_stock_basic(list_statuses=("L", "D", "P"))

    def fetch_financial_facts(
        self,
        *,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pl.DataFrame:
        if symbols:
            frames = [
                self.fetch_income_vip(ts_code=sym, start_date=start_date, end_date=end_date)
                for sym in symbols
            ]
            non_empty = [f for f in frames if not f.is_empty()]
            return pl.concat(non_empty, how="vertical_relaxed") if non_empty else pl.DataFrame()
        return self.fetch_income_vip(start_date=start_date, end_date=end_date)

    def fetch_universe_weights(
        self,
        universe_id: str,
        start_date: date,
        end_date: date,
    ) -> pl.DataFrame:
        return self.fetch_index_weight(universe_id, start_date=start_date, end_date=end_date)

    def fetch_price_limits(
        self,
        *,
        trade_date: date | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pl.DataFrame:
        return self.fetch_stk_limit(trade_date=trade_date, start_date=start_date, end_date=end_date)
