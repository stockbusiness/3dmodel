"""providerコードからアダプターを引く。

provider は preset から確定する。任意の外部エンドポイントを受け取らない（仕様第7章）。

`mock_a` / `mock_b` は、2社比較の受付と集計をモックで検証するために用意した
別サービス扱いのモックである（送信枠は asset×provider で数えるため、
同じ provider 名では比較にならない）。Tripo / Meshy は A3 で追加する。
"""

from __future__ import annotations

from app.providers.base import ProviderAdapter
from app.providers.mock import MockAdapter

_ADAPTERS: dict[str, ProviderAdapter] = {
    "mock": MockAdapter("mock"),
    "mock_a": MockAdapter("mock_a"),
    "mock_b": MockAdapter("mock_b"),
}

MOCK_PROVIDERS = frozenset(_ADAPTERS)


def get_adapter(provider: str) -> ProviderAdapter:
    adapter = _ADAPTERS.get(provider)
    if adapter is None:
        # Tripo / Meshy は A3 で追加する
        raise KeyError(f"未実装のサービスです: {provider}")
    return adapter


def is_mock(provider: str) -> bool:
    return provider in MOCK_PROVIDERS


def available_providers() -> list[str]:
    return sorted(_ADAPTERS)
