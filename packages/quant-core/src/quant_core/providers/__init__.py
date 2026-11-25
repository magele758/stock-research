"""Multi-source market & fundamental data provider adapters (zero synthetic fallback)."""

from __future__ import annotations

from quant_core.providers.baostock import BaoStockProvider, from_baostock_code, to_baostock_code
from quant_core.providers.base import (
    DataProviderAdapter,
    ProviderAuthError,
    ProviderError,
    ProviderPermissionError,
    ProviderRateLimitError,
    ProviderUpstreamError,
    RetryPolicy,
    TokenBucketRateLimiter,
)
from quant_core.providers.massive import MassiveProvider
from quant_core.providers.sec_edgar import SecEdgarProvider
from quant_core.providers.tushare import TushareProvider

PROVIDER_REGISTRY: dict[str, type[DataProviderAdapter]] = {
    "tushare": TushareProvider,
    "baostock": BaoStockProvider,
    "edgar": SecEdgarProvider,
    "sec_edgar": SecEdgarProvider,
    "massive": MassiveProvider,
}


def get_provider(name: str, **kwargs) -> DataProviderAdapter:
    """Instantiate a registered provider adapter by name."""
    key = name.strip().lower()
    cls = PROVIDER_REGISTRY.get(key)
    if cls is None:
        raise ProviderError(
            f"Unknown provider '{name}'. Supported providers: {sorted(PROVIDER_REGISTRY.keys())}"
        )
    return cls(**kwargs)


__all__ = [
    "PROVIDER_REGISTRY",
    "BaoStockProvider",
    "DataProviderAdapter",
    "MassiveProvider",
    "ProviderAuthError",
    "ProviderError",
    "ProviderPermissionError",
    "ProviderRateLimitError",
    "ProviderUpstreamError",
    "RetryPolicy",
    "SecEdgarProvider",
    "TokenBucketRateLimiter",
    "TushareProvider",
    "from_baostock_code",
    "get_provider",
    "to_baostock_code",
]
