"""生成アダプターの共通契約（仕様第8章）。

estimate / submit / fetch_status / download_result / cancel の5メソッド。
APIレスポンス原文を画面へ返さない。診断ログは秘密情報を除いたものに限る。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.models import AssetVariant, Preset


class ProviderError(Exception):
    """事業者側または通信の失敗。error_kind に正規化した種別を持つ。"""

    def __init__(self, message: str, *, kind: str = "provider_error") -> None:
        super().__init__(message)
        self.kind = kind


class UnsupportedOperation(Exception):
    """公式APIが対応していない操作（仕様第8章：cancel など）。"""


@dataclass(frozen=True)
class Estimate:
    """上限側の見積（仕様第11章「見積は常に上限側を採る」）。"""

    max_micro_usd: int
    currency: str = "USD"
    credits: int | None = None
    price_version: str = ""
    # 上限を見積もれないプリセットは実行不可にする（仕様第11章）
    is_bounded: bool = True
    note: str = ""


@dataclass(frozen=True)
class SubmitResult:
    provider_task_id: str


@dataclass(frozen=True)
class StatusResult:
    """正規化した状態。

    state は queued / running / succeeded / failed のいずれか。
    progress_percent は事業者が返すときのみ設定する（仕様第6.4章：架空の進捗率を出さない）。
    """

    state: str
    progress_percent: int | None = None
    result_ref: str | None = None
    failure_reason: str = ""
    billing: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DownloadedResult:
    data: bytes
    content_type: str = "model/gltf-binary"


class ProviderAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def estimate(self, variant: AssetVariant, preset: Preset) -> Estimate: ...

    @abstractmethod
    def submit(self, variant: AssetVariant, preset: Preset) -> SubmitResult: ...

    @abstractmethod
    def fetch_status(self, provider_task_id: str) -> StatusResult: ...

    @abstractmethod
    def download_result(self, result_ref: str) -> DownloadedResult: ...

    def cancel(self, provider_task_id: str) -> None:
        """公式APIが対応している場合だけ実装する（仕様第8章）。"""
        raise UnsupportedOperation(f"{self.name} は取消に対応していません")

    @staticmethod
    def preset_snapshot(preset: Preset) -> str:
        """実行時点のプリセットの写し。後から設定変更で書き換えない（仕様第8章）。"""
        return json.dumps(
            {
                "preset_id": preset.id,
                "code": preset.code,
                "display_name": preset.display_name,
                "provider": preset.provider,
                "model_id": preset.model_id,
                "settings": json.loads(preset.settings_json or "{}"),
                "version": preset.version,
                "sdk_version": preset.sdk_version,
                "price_max_micro_usd": preset.price_max_micro_usd,
                "price_version": preset.price_version,
                "price_checked_on": preset.price_checked_on,
                "is_unverified": preset.is_unverified,
            },
            ensure_ascii=False,
        )
