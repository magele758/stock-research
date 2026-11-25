"""SEC EDGAR PIT submissions & XBRL companyfacts adapter (strict <=10 req/s & User-Agent compliance)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
import polars as pl

from quant_core.providers.base import DataProviderAdapter, ProviderAuthError, RetryPolicy
from quant_core.schemas import Market

# Canonical US-GAAP XBRL tags mapped to Pro-Quant PIT metric_code.
GAAP_TAG_MAP: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "net_profit_deducted": ("NetIncomeLoss",),
    "ocf": ("NetCashProvidedByUsedInOperatingActivities",),
    "equity": ("StockholdersEquity",),
    "total_debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "eps": ("EarningsPerShareDiluted", "EarningsPerShareBasic"),
}


class SecEdgarProvider(DataProviderAdapter):
    """SEC EDGAR API adapter strictly adhering to SEC Fair Access rules.

    - Enforces a declared ``User-Agent`` (``SEC_EDGAR_USER_AGENT`` env var or
      explicit constructor argument, e.g. ``"ProQuantResearch research@org.com"``).
    - Enforces ``<= 10 req/s`` via token bucket (default ``8.0 req/s``, burst ``2``).
    - Pulls ``submissions`` and ``companyfacts`` with original filing dates (``filed``)
      for PIT-accurate financial statement replay.
    """

    provider_id = "sec_edgar"
    supported_markets = (Market.US_EQ,)
    BASE_URL = "https://data.sec.gov"

    def __init__(
        self,
        user_agent: str | None = None,
        *,
        base_url: str = BASE_URL,
        rate_per_sec: float = 8.0,
        burst: int = 2,
        retry_policy: RetryPolicy | None = None,
        timeout_sec: float = 20.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if rate_per_sec > 10.0:
            raise ValueError("SEC EDGAR Fair Access policy forbids rate_per_sec > 10.0")
        super().__init__(
            rate_per_sec=rate_per_sec,
            burst=burst,
            retry_policy=retry_policy,
            timeout_sec=timeout_sec,
            http_client=http_client,
        )
        self._user_agent_override = user_agent
        self.base_url = base_url.rstrip("/")

    def check_credentials(self) -> None:
        ua = self._require_env("SEC_EDGAR_USER_AGENT", self._user_agent_override)
        if "@" not in ua and " " not in ua:
            raise ProviderAuthError(
                "SEC_EDGAR_USER_AGENT must identify application and contact email "
                "(e.g. 'ProQuantResearch quant@example.com')."
            )

    def _headers(self) -> dict[str, str]:
        self.check_credentials()
        ua = self._require_env("SEC_EDGAR_USER_AGENT", self._user_agent_override)
        return {
            "User-Agent": ua,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
        }

    @staticmethod
    def normalize_cik(cik: str | int) -> str:
        digits = "".join(ch for ch in str(cik) if ch.isdigit())
        if not digits:
            raise ValueError(f"Invalid CIK: {cik!r}")
        return digits.zfill(10)

    def fetch_submissions(self, cik: str | int) -> dict[str, Any]:
        cik10 = self.normalize_cik(cik)
        url = f"{self.base_url}/submissions/CIK{cik10}.json"
        return self._request_json("GET", url, headers=self._headers())

    def fetch_companyfacts(self, cik: str | int) -> dict[str, Any]:
        cik10 = self.normalize_cik(cik)
        url = f"{self.base_url}/api/xbrl/companyfacts/CIK{cik10}.json"
        return self._request_json("GET", url, headers=self._headers())

    def extract_pit_financial_facts(
        self,
        *,
        cik: str | int,
        symbol: str,
        companyfacts: dict[str, Any] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pl.DataFrame:
        """Parse SEC XBRL companyfacts into canonical dual-temporal PIT financial rows."""
        raw = companyfacts if companyfacts is not None else self.fetch_companyfacts(cik)
        cik10 = self.normalize_cik(cik)
        us_gaap = (raw.get("facts") or {}).get("us-gaap") or {}
        now_utc = datetime.now(UTC)

        records: list[dict[str, Any]] = []
        for metric_code, tag_candidates in GAAP_TAG_MAP.items():
            chosen_tag = next((t for t in tag_candidates if t in us_gaap), None)
            if chosen_tag is None:
                continue
            units = (us_gaap[chosen_tag] or {}).get("units") or {}
            unit_key = "USD/shares" if metric_code == "eps" and "USD/shares" in units else "USD"
            obs_list = units.get(unit_key) or next(iter(units.values()), [])

            # Group by report_period_end and sort chronologically by filed date to assign revision_seq.
            by_period: dict[date, list[dict[str, Any]]] = {}
            for item in obs_list:
                form = str(item.get("form") or "")
                if form not in ("10-K", "10-Q", "10-K/A", "10-Q/A"):
                    continue
                end_str = item.get("end")
                filed_str = item.get("filed")
                val = item.get("val")
                if not end_str or not filed_str or val is None:
                    continue
                period_end = date.fromisoformat(end_str)
                ann_d = date.fromisoformat(filed_str)
                if ann_d < period_end:
                    # Drop/reject inverted filing dates rather than corrupting PIT order.
                    continue
                if start_date and ann_d < start_date:
                    continue
                if end_date and ann_d > end_date:
                    continue
                by_period.setdefault(period_end, []).append(item)

            for period_end, items in sorted(by_period.items()):
                # Deduplicate same accession & sort by filed date then accession number.
                dedup: dict[str, dict[str, Any]] = {}
                for it in items:
                    accn = str(it.get("accn") or f"{it.get('filed')}-{it.get('val')}")
                    dedup[accn] = it
                ordered = sorted(
                    dedup.values(),
                    key=lambda x: (str(x.get("filed")), str(x.get("accn") or "")),
                )
                for seq, it in enumerate(ordered, start=1):
                    ann_d = date.fromisoformat(str(it["filed"]))
                    published_at = datetime(
                        ann_d.year, ann_d.month, ann_d.day, 21, 30, 0, tzinfo=UTC
                    )
                    accn = str(it.get("accn") or f"edgar-{seq}")
                    records.append(
                        {
                            "fact_id": f"edgar:{cik10}:{metric_code}:{period_end.isoformat()}:v{seq}:{accn}",
                            "issuer_id": f"CIK{cik10}",
                            "symbol": symbol.upper(),
                            "market": Market.US_EQ.value,
                            "metric_code": metric_code,
                            "report_period_end": period_end,
                            "ann_date": ann_d,
                            "published_at": published_at,
                            "first_seen_at": max(published_at, now_utc),
                            "revision_seq": seq,
                            "is_restated": seq > 1 or str(it.get("form", "")).endswith("/A"),
                            "is_withdrawn": False,
                            "value": float(it["val"]),
                            "currency": "USD",
                            "source_id": "sec_edgar_xbrl",
                            "quality_state": "observed",
                        }
                    )
        if not records:
            return pl.DataFrame()
        return pl.DataFrame(records)

    def fetch_financial_facts(
        self,
        *,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        cik_map: dict[str, str] | None = None,
    ) -> pl.DataFrame:
        self.check_credentials()
        if not symbols or not cik_map:
            raise ValueError("SecEdgarProvider.fetch_financial_facts requires symbols and cik_map.")
        frames = [
            self.extract_pit_financial_facts(
                cik=cik_map[sym],
                symbol=sym,
                start_date=start_date,
                end_date=end_date,
            )
            for sym in symbols
            if sym in cik_map
        ]
        non_empty = [f for f in frames if not f.is_empty()]
        return pl.concat(non_empty, how="vertical_relaxed") if non_empty else pl.DataFrame()
