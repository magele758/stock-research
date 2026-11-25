"""Massive (formerly Polygon.io) REST adapter for US equities unadjusted EOD bars, dividends & splits."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
import polars as pl

from quant_core.providers.base import DataProviderAdapter, ProviderError, RetryPolicy
from quant_core.schemas import Market


class MassiveProvider(DataProviderAdapter):
    """Lightweight REST adapter for Massive (formerly Polygon.io).

    Reads ``MASSIVE_API_KEY`` from environment unless passed explicitly.
    Always requests ``adjusted=false`` for raw unadjusted OHLCV and pulls
    dividends/splits separately into canonical corporate actions.
    """

    provider_id = "massive"
    supported_markets = (Market.US_EQ,)
    BASE_URL = "https://api.massive.com"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = BASE_URL,
        rate_per_sec: float = 5.0,
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
        self._api_key_override = api_key
        self.base_url = base_url.rstrip("/")

    def check_credentials(self) -> None:
        self._require_env("MASSIVE_API_KEY", self._api_key_override)

    def _headers(self) -> dict[str, str]:
        key = self._require_env("MASSIVE_API_KEY", self._api_key_override)
        return {"Authorization": f"Bearer {key}", "Accept": "application/json"}

    def fetch_daily_bars(
        self,
        *,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        trade_date: date | None = None,
    ) -> pl.DataFrame:
        self.check_credentials()
        if not symbols:
            raise ProviderError("MassiveProvider.fetch_daily_bars requires an explicit symbols list.")
        s_date = start_date or trade_date
        e_date = end_date or trade_date
        if s_date is None or e_date is None:
            raise ProviderError("MassiveProvider requires start_date/end_date or trade_date.")

        rows: list[dict[str, Any]] = []
        now_utc = datetime.now(UTC)
        for sym in symbols:
            ticker = sym.strip().upper()
            url = (
                f"{self.base_url}/v2/aggs/ticker/{ticker}/range/1/day/"
                f"{s_date.isoformat()}/{e_date.isoformat()}"
            )
            payload = self._request_json(
                "GET",
                url,
                headers=self._headers(),
                params={"adjusted": "false", "sort": "asc", "limit": 50000},
            )
            prev_close: float | None = None
            for bar in payload.get("results") or []:
                ts_ms = int(bar["t"])
                t_date = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC).date()
                close_px = float(bar["c"])
                open_px = float(bar["o"])
                vwap_px = float(bar.get("vw", close_px))
                vol = round(float(bar.get("v", 0)))
                rows.append(
                    {
                        "symbol": ticker,
                        "market": Market.US_EQ.value,
                        "trade_date": t_date,
                        "open": open_px,
                        "high": float(bar["h"]),
                        "low": float(bar["l"]),
                        "close": close_px,
                        "pre_close": prev_close if prev_close is not None else open_px,
                        "vwap": vwap_px,
                        "volume_shares": vol,
                        "amount_ccy": vwap_px * vol,
                        "is_suspended": vol == 0,
                        "is_st": False,
                        "source_id": "massive_eod",
                        "available_at": now_utc,
                    }
                )
                prev_close = close_px
        return pl.DataFrame(rows) if rows else pl.DataFrame()

    def fetch_dividends(
        self,
        ticker: str,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        self.check_credentials()
        params: dict[str, Any] = {"ticker": ticker.upper(), "limit": 1000}
        if start_date:
            params["ex_dividend_date.gte"] = start_date.isoformat()
        if end_date:
            params["ex_dividend_date.lte"] = end_date.isoformat()
        payload = self._request_json(
            "GET",
            f"{self.base_url}/v3/reference/dividends",
            headers=self._headers(),
            params=params,
        )
        return list(payload.get("results") or [])

    def fetch_splits(
        self,
        ticker: str,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        self.check_credentials()
        params: dict[str, Any] = {"ticker": ticker.upper(), "limit": 1000}
        if start_date:
            params["execution_date.gte"] = start_date.isoformat()
        if end_date:
            params["execution_date.lte"] = end_date.isoformat()
        payload = self._request_json(
            "GET",
            f"{self.base_url}/v3/reference/splits",
            headers=self._headers(),
            params=params,
        )
        return list(payload.get("results") or [])

    def fetch_corp_actions(
        self,
        *,
        symbols: list[str],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pl.DataFrame:
        self.check_credentials()
        records: list[dict[str, Any]] = []
        now_utc = datetime.now(UTC)
        for sym in symbols:
            ticker = sym.strip().upper()
            for div in self.fetch_dividends(ticker, start_date=start_date, end_date=end_date):
                ex_str = div.get("ex_dividend_date")
                if not ex_str:
                    continue
                ex_d = date.fromisoformat(ex_str)
                rec_d = date.fromisoformat(div["record_date"]) if div.get("record_date") else ex_d
                pay_d = date.fromisoformat(div["pay_date"]) if div.get("pay_date") else ex_d
                decl_str = div.get("declaration_date")
                ann_ts = (
                    datetime.fromisoformat(f"{decl_str}T21:00:00+00:00")
                    if decl_str
                    else now_utc
                )
                records.append(
                    {
                        "action_id": f"massive:div:{ticker}:{ex_str}:{div.get('id', '0')}",
                        "listing_id": f"US:{ticker}",
                        "symbol": ticker,
                        "ex_date": ex_d,
                        "record_date": rec_d,
                        "pay_date": pay_d,
                        "cash_dividend_per_share": float(div.get("cash_amount") or 0.0),
                        "split_ratio": 1.0,
                        "bonus_share_ratio": 0.0,
                        "announced_at": ann_ts,
                        "source_id": "massive_dividends",
                    }
                )
            for sp in self.fetch_splits(ticker, start_date=start_date, end_date=end_date):
                ex_str = sp.get("execution_date")
                if not ex_str:
                    continue
                ex_d = date.fromisoformat(ex_str)
                split_from = float(sp.get("split_from") or 1.0)
                split_to = float(sp.get("split_to") or 1.0)
                ratio = split_to / split_from if split_from > 0 else 1.0
                records.append(
                    {
                        "action_id": f"massive:split:{ticker}:{ex_str}:{sp.get('id', '0')}",
                        "listing_id": f"US:{ticker}",
                        "symbol": ticker,
                        "ex_date": ex_d,
                        "record_date": ex_d,
                        "pay_date": ex_d,
                        "cash_dividend_per_share": 0.0,
                        "split_ratio": ratio,
                        "bonus_share_ratio": 0.0,
                        "announced_at": datetime(ex_d.year, ex_d.month, ex_d.day, 12, 0, tzinfo=UTC),
                        "source_id": "massive_splits",
                    }
                )
        return pl.DataFrame(records) if records else pl.DataFrame()
