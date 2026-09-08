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

# error_kind の正規化した値。画面表示と集計に使う
ERROR_TIMEOUT = "submit_timeout"
ERROR_RATE_LIMITED = "rate_limited"
ERROR_PROVIDER_FAILED = "provider_failed"
ERROR_DOWNLOAD_FAILED = "download_failed"
ERROR_VALIDATION_FAILED = "validation_failed"
ERROR_TRANSPORT = "transport_error"


class ProviderError(Exception):
    """事業者側または通信の失敗。kind に正規化した種別を持つ。"""

    def __init__(
        self, message: str, *, kind: str = ERROR_PROVIDER_FAILED, retry_after: int | None = None
    ) -> None:
        super().__init__(message)
        self.kind = kind
        # 429 の Retry-After（秒）。あればバックオフに反映する（仕様第8章）
        self.retry_after = retry_after


class SubmitTimeout(ProviderError):
    """送信の応答が得られなかった。自動で再POSTしない（仕様第8章）。

    課金されたかどうかを断定できないため、呼び出し側は submission_unknown にする。
    """

    def __init__(self, message: str = "送信の応答がありませんでした") -> None:
        super().__init__(message, kind=ERROR_TIMEOUT)


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


@dataclass(frozen=True)
class ConnectionCheck:
    """接続テストの結果（管理画面用）。

    detail / note は画面に出す日本語。**APIキー・Bearerヘッダー・事業者の応答原文は
    絶対に含めない**（仕様第12章）。
    """

    ok: bool
    detail: str = ""
    note: str = ""


class ProviderAdapter(ABC):
    name: str = "base"
    # 公式APIが取消に対応しているか（仕様第8章）
    supports_cancel: bool = False

    @abstractmethod
    def estimate(self, variant: AssetVariant, preset: Preset) -> Estimate: ...

    @abstractmethod
    def submit(
        self, variant: AssetVariant, preset: Preset, *, client_reference: str
    ) -> SubmitResult:
        """外部へ依頼を出す。

        client_reference は自システムの生成ID。事業者の履歴と照合するときに使う
        （仕様第8章：受付結果不明の手動照合）。
        """

    @abstractmethod
    def fetch_status(self, provider_task_id: str) -> StatusResult: ...

    @abstractmethod
    def download_result(self, result_ref: str) -> DownloadedResult: ...

    def cancel(self, provider_task_id: str) -> None:
        """公式APIが対応している場合だけ実装する（仕様第8章）。"""
        raise UnsupportedOperation(f"{self.name} は取消に対応していません")

    def check_connection(self) -> ConnectionCheck:
        """認証が通るかだけを確かめる。**生成は行わないので課金は発生しない。**

        公式資料で「無課金で叩ける読み取り操作」を確認できた事業者だけ実装する。
        確認できていない事業者は UnsupportedOperation を上げる（推測で叩かない）。
        """
        raise UnsupportedOperation(f"{self.name} は接続テストに対応していません")

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
