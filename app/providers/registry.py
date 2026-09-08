"""providerコードからアダプターを引く。

provider は preset から確定する。任意の外部エンドポイントを受け取らない（仕様第7章）。
"""

from __future__ import annotations

from app.providers.base import ProviderAdapter
from app.providers.mock import MockAdapter

_ADAPTERS: dict[str, ProviderAdapter] = {"mock": MockAdapter()}


def get_adapter(provider: str) -> ProviderAdapter:
    adapter = _ADAPTERS.get(provider)
    if adapter is None:
        # Tripo/Meshy は A3 で追加する
        raise KeyError(f"未実装のサービスです: {provider}")
    return adapter


def available_providers() -> list[str]:
    return sorted(_ADAPTERS)
