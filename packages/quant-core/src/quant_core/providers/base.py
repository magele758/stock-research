"""Abstract provider adapter with token-bucket rate limiting and strict no-synthetic-fallback contract."""

from __future__ import annotations

import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
import polars as pl

from quant_core.schemas import Market


class ProviderError(RuntimeError):
    """Base error raised by market/fundamental data providers."""


class ProviderAuthError(ProviderError):
    """Raised when an API key, token, or required identity header is missing or rejected."""


class ProviderPermissionError(ProviderError):
    """Raised when the provider account lacks endpoint entitlements or quota."""


class ProviderRateLimitError(ProviderError):
    """Raised when an upstream rate limit persists beyond configured retries."""


class ProviderUpstreamError(ProviderError):
    """Raised on upstream HTTP/network/schema failures."""


class TokenBucketRateLimiter:
    """Thread-safe token bucket limiter for upstream API fairness & quota protection."""

    def __init__(self, rate_per_sec: float = 5.0, burst: int = 5) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        if burst < 1:
            raise ValueError("burst must be >= 1")
        self.rate_per_sec = float(rate_per_sec)
        self.capacity = float(burst)
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> float:
        """Block until ``tokens`` are available. Returns total seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = max(0.0, now - self._last_refill)
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_sec)
                self._last_refill = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return waited
                deficit = tokens - self._tokens
                sleep_sec = deficit / self.rate_per_sec
            time.sleep(sleep_sec)
            waited += sleep_sec


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_sec: float = 0.5
    max_delay_sec: float = 8.0
    backoff_multiplier: float = 2.0


class DataProviderAdapter(ABC):
    """Base adapter for external market and fundamental data sources.

    CRITICAL INVARIANT:
    No provider adapter is EVER permitted to silently synthesize fake/random market
    data on auth failure, permission denial, rate limit, or network error.
    All failures must raise an explicit ``ProviderError`` subclass.
    """

    provider_id: str = "base"
    supported_markets: tuple[Market, ...] = ()

    def __init__(
        self,
        *,
        rate_per_sec: float = 5.0,
        burst: int = 5,
        retry_policy: RetryPolicy | None = None,
        timeout_sec: float = 20.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.rate_limiter = TokenBucketRateLimiter(rate_per_sec=rate_per_sec, burst=burst)
        self.retry_policy = retry_policy or RetryPolicy()
        self.timeout_sec = timeout_sec
        self._external_client = http_client

    def _require_env(self, env_var: str, override: str | None = None) -> str:
        val = (override if override is not None else os.getenv(env_var, "")).strip()
        if not val:
            raise ProviderAuthError(
                f"Provider '{self.provider_id}' requires credential '{env_var}'. "
                "Refusing to silently fall back to synthetic market data."
            )
        return val

    def _get_client(self) -> tuple[httpx.Client, bool]:
        if self._external_client is not None:
            return self._external_client, False
        return httpx.Client(timeout=self.timeout_sec), True

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute an HTTP request with token-bucket rate limiting and exponential backoff."""
        client, owns_client = self._get_client()
        last_exc: Exception | None = None
        try:
            for attempt in range(1, self.retry_policy.max_attempts + 1):
                self.rate_limiter.acquire(1.0)
                try:
                    resp = client.request(
                        method,
                        url,
                        headers=headers,
                        params=params,
                        json=json_body,
                    )
                except (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError) as exc:
                    last_exc = exc
                    if attempt >= self.retry_policy.max_attempts:
                        raise ProviderUpstreamError(
                            f"{self.provider_id} transport failure after {attempt} attempts: {exc}"
                        ) from exc
                    self._sleep_backoff(attempt)
                    continue

                if resp.status_code in (401,):
                    raise ProviderAuthError(
                        f"{self.provider_id} authentication failed (HTTP 401): {resp.text[:240]}"
                    )
                if resp.status_code in (403,):
                    raise ProviderPermissionError(
                        f"{self.provider_id} permission denied (HTTP 403): {resp.text[:240]}"
                    )
                if resp.status_code == 429:
                    last_exc = ProviderRateLimitError(
                        f"{self.provider_id} rate limited (HTTP 429): {resp.text[:240]}"
                    )
                    if attempt >= self.retry_policy.max_attempts:
                        raise last_exc
                    self._sleep_backoff(attempt)
                    continue
                if resp.status_code >= 500:
                    last_exc = ProviderUpstreamError(
                        f"{self.provider_id} server error (HTTP {resp.status_code}): {resp.text[:240]}"
                    )
                    if attempt >= self.retry_policy.max_attempts:
                        raise last_exc
                    self._sleep_backoff(attempt)
                    continue
                if resp.status_code >= 400:
                    raise ProviderUpstreamError(
                        f"{self.provider_id} request failed (HTTP {resp.status_code}): {resp.text[:240]}"
                    )

                try:
                    payload = resp.json()
                except ValueError as exc:
                    raise ProviderUpstreamError(
                        f"{self.provider_id} returned non-JSON payload from {url}"
                    ) from exc
                if not isinstance(payload, dict):
                    return {"data": payload}
                return payload
        finally:
            if owns_client:
                client.close()

        raise ProviderUpstreamError(f"{self.provider_id} failed: {last_exc}")

    def _sleep_backoff(self, attempt: int) -> None:
        delay = min(
            self.retry_policy.max_delay_sec,
            self.retry_policy.base_delay_sec * (self.retry_policy.backoff_multiplier ** (attempt - 1)),
        )
        time.sleep(delay)

    @abstractmethod
    def check_credentials(self) -> None:
        """Verify local credential presence before issuing network requests."""

    def fetch_daily_bars(
        self,
        *,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        trade_date: date | None = None,
    ) -> pl.DataFrame:
        raise NotImplementedError(f"{self.provider_id} does not implement fetch_daily_bars")

    def fetch_adj_factors(
        self,
        *,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        trade_date: date | None = None,
    ) -> pl.DataFrame:
        raise NotImplementedError(f"{self.provider_id} does not implement fetch_adj_factors")

    def fetch_instrument_lifecycle(self) -> pl.DataFrame:
        raise NotImplementedError(f"{self.provider_id} does not implement fetch_instrument_lifecycle")

    def fetch_financial_facts(
        self,
        *,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pl.DataFrame:
        raise NotImplementedError(f"{self.provider_id} does not implement fetch_financial_facts")

    def fetch_universe_weights(
        self,
        universe_id: str,
        start_date: date,
        end_date: date,
    ) -> pl.DataFrame:
        raise NotImplementedError(f"{self.provider_id} does not implement fetch_universe_weights")

    def fetch_price_limits(
        self,
        *,
        trade_date: date | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pl.DataFrame:
        raise NotImplementedError(f"{self.provider_id} does not implement fetch_price_limits")
