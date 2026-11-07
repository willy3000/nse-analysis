"""Builds provider adapter instances from configuration."""

from __future__ import annotations

from nse_platform.core.config import Settings, get_settings
from nse_platform.providers.base import MarketDataProvider
from nse_platform.providers.mystocks import MyStocksProvider
from nse_platform.providers.synthetic import SyntheticProvider


def build_providers(settings: Settings | None = None) -> dict[str, MarketDataProvider]:
    s = settings or get_settings()
    providers: dict[str, MarketDataProvider] = {
        "mystocks": MyStocksProvider(
            api_key=s.mystocks_api_key.get_secret_value() if s.mystocks_api_key else None,
            base_url=s.mystocks_base_url,
            timeout=s.mystocks_timeout_seconds,
        )
    }
    if s.enable_synthetic_provider and s.app_env != "production":
        providers["synthetic"] = SyntheticProvider()
    return providers
