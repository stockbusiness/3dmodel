"""Meshy アダプター（仕様第4章・第8章・第12章）。

Meshy に公式の Python SDK は無いため、公式の契約に基づく HTTP クライアントとして
実装する。契約は公式CLI `meshy-cli`（npm、MIT）のソースで確認した。
確認内容と確認日は docs/provider-contracts.md に記録した。

- ベース: https://api.meshy.ai/openapi/v1（text-to-3d だけ v2。今回は使わない）
- 認証: Authorization: Bearer <APIキー>
- 作成: POST /image-to-3d → {"result": "<task_id>"}
- 取得: GET /image-to-3d/{task_id} → Task
- 状態: PENDING / IN_PROGRESS / SUCCEEDED / FAILED / CANCELED
- 取消: DELETE /image-to-3d/{task_id}
  公式資料で「実行中タスクの取消」であることを確認した（2026-09-08）。
  PENDING の取消は作成時クレジットが返却され、IN_PROGRESS の取消は返却されない。
  終了済みのタスクは取り消せない。返却の有無を当方では判定できないため、
  取消しても送信枠は保持する（仕様第8章の状態遷移表どおり）。

入力画像は data: URI として本文に載せる。利用者が入力したURLは扱わないし、
こちらの画像を公開URLに置くこともしない（仕様第12章）。
"""

from __future__ import annotations

import base64
import json
import logging

import httpx

from app.config import get_settings
from app.models import AssetVariant, Preset
from app.providers.base import (
    ERROR_PROVIDER_FAILED,
    ERROR_RATE_LIMITED,
    ERROR_TRANSPORT,
    DownloadedResult,
    Estimate,
    ProviderAdapter,
    ProviderError,
    StatusResult,
    SubmitResult,
    SubmitTimeout,
)
from app.services import storage
from app.services.download_guard import DownloadPolicy, DownloadRejected, fetch_bytes

logger = logging.getLogger("providers.meshy")

BASE_URL = "https://api.meshy.ai/openapi/v1"
RESOURCE = "/image-to-3d"

_TERMINAL_SUCCESS = "SUCCEEDED"
_TERMINAL_FAILED = {"FAILED", "CANCELED"}
_RUNNING = {"PENDING", "IN_PROGRESS"}

# 公式CLIが image-to-3d の作成本文に載せる項目だけを受け付ける。
# 未知の名前は落とし、推測で送らない
ALLOWED_CREATE_FIELDS = frozenset(
    {
        "model_type",
        "target_polycount",
        "ultra_mode",
        "should_texture",
        "enable_pbr",
        "texture_prompt",
        "texture_resolution",
        "pose_mode",
        "image_enhancement",
        "remove_lighting",
        "target_formats",
    }
)


def _api_key() -> str:
    import os

    key = os.environ.get("MESHY_API_KEY", "").strip()
    if not key:
        raise ProviderError("MESHY_API_KEY が設定されていません", kind=ERROR_TRANSPORT)
    return key


def _raise_for_status(response: httpx.Response) -> None:
    """状態コードを正規化した種別へ移す。応答の原文は画面に返さない。"""
    if response.status_code < 400:
        return
    status = response.status_code
    if status == 429:
        retry_after = response.headers.get("retry-after")
        seconds: int | None = None
        if retry_after and retry_after.isdigit():
            seconds = int(retry_after)
        raise ProviderError("要求が多すぎます", kind=ERROR_RATE_LIMITED, retry_after=seconds)
    if status in (400, 422):
        raise ProviderError("依頼の内容が受け付けられませんでした", kind=ERROR_PROVIDER_FAILED)
    if status == 401:
        raise ProviderError("APIキーが受け付けられませんでした", kind=ERROR_TRANSPORT)
    if status == 402:
        raise ProviderError("事業者側の残高が不足しています", kind=ERROR_PROVIDER_FAILED)
    if status == 404:
        raise ProviderError("タスクが見つかりません", kind=ERROR_PROVIDER_FAILED)
    raise ProviderError(f"事業者側でエラーが発生しました（HTTP {status}）", kind=ERROR_TRANSPORT)


class MeshyAdapter(ProviderAdapter):
    name = "meshy"
    # DELETE が実行中タスクの取消であることを公式資料で確認済み（第2.2節）
    supports_cancel = True

    def estimate(self, variant: AssetVariant, preset: Preset) -> Estimate:
        amount = preset.price_max_micro_usd
        return Estimate(
            max_micro_usd=amount or 0,
            price_version=preset.price_version or "",
            is_bounded=amount is not None and not preset.is_unverified,
            note="価格は管理設定の版付きデータによる",
        )

    def submit(
        self, variant: AssetVariant, preset: Preset, *, client_reference: str
    ) -> SubmitResult:
        settings = get_settings()
        key = variant.submission_storage_key or variant.storage_key
        data = storage.read_bytes(key)
        mime = variant.mime if variant.mime.startswith("image/") else "image/png"
        payload: dict = {
            "image_url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        }
        payload.update(self._create_fields(preset))

        try:
            with self._client(settings.submit_timeout_seconds) as client:
                response = client.post(RESOURCE, json=payload)
        except httpx.TimeoutException as exc:
            # 応答が無いだけで未課金とは断定しない
            raise SubmitTimeout() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("通信に失敗しました", kind=ERROR_TRANSPORT) from exc

        _raise_for_status(response)
        try:
            task_id = response.json()["result"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("応答の形式が想定と異なります", kind=ERROR_TRANSPORT) from exc
        return SubmitResult(provider_task_id=str(task_id))

    def fetch_status(self, provider_task_id: str) -> StatusResult:
        settings = get_settings()
        try:
            with self._client(settings.status_timeout_seconds) as client:
                response = client.get(f"{RESOURCE}/{provider_task_id}")
        except httpx.TimeoutException as exc:
            raise ProviderError("状態確認がタイムアウトしました", kind=ERROR_TRANSPORT) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("通信に失敗しました", kind=ERROR_TRANSPORT) from exc

        _raise_for_status(response)
        try:
            task = response.json()
        except ValueError as exc:
            raise ProviderError("応答の形式が想定と異なります", kind=ERROR_TRANSPORT) from exc

        status = str(task.get("status", ""))
        raw_progress = task.get("progress")
        progress = raw_progress if isinstance(raw_progress, int) else None

        if status == _TERMINAL_SUCCESS:
            model_urls = task.get("model_urls") or {}
            result_ref = model_urls.get("glb") if isinstance(model_urls, dict) else None
            if not result_ref:
                return StatusResult(state="failed", failure_reason="GLBのURLが返されませんでした")
            return StatusResult(state="succeeded", progress_percent=progress, result_ref=result_ref)
        if status in _TERMINAL_FAILED:
            error = task.get("task_error") or {}
            message = error.get("message") if isinstance(error, dict) else ""
            return StatusResult(
                state="failed",
                progress_percent=progress,
                failure_reason=message or f"事業者側の状態: {status}",
            )
        if status in _RUNNING:
            return StatusResult(state="running", progress_percent=progress)
        return StatusResult(state="running", progress_percent=progress)

    def cancel(self, provider_task_id: str) -> None:
        """実行中の依頼を取り消す（仕様第8章）。

        公式資料の DELETE /image-to-3d/{id} を使う。
        取り消せなかった場合（終了済み・見つからない等）は ProviderError を上げ、
        呼び出し側が「未取消」として照合を続ける。
        """
        settings = get_settings()
        try:
            with self._client(settings.status_timeout_seconds) as client:
                response = client.delete(f"{RESOURCE}/{provider_task_id}")
        except httpx.TimeoutException as exc:
            raise ProviderError("取消がタイムアウトしました", kind=ERROR_TRANSPORT) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("通信に失敗しました", kind=ERROR_TRANSPORT) from exc

        _raise_for_status(response)

    def download_result(self, result_ref: str) -> DownloadedResult:
        settings = get_settings()
        policy = DownloadPolicy(
            allowed_hosts=settings.meshy_allowed_hosts,
            max_bytes=settings.max_download_bytes,
            read_timeout=float(settings.status_timeout_seconds),
            total_timeout=float(settings.download_timeout_seconds),
        )
        try:
            data = fetch_bytes(result_ref, policy)
        except DownloadRejected as exc:
            logger.warning("Meshyの成果物取得を拒否または失敗しました: %s", exc)
            raise ProviderError(str(exc), kind="download_failed") from exc
        return DownloadedResult(data=data)

    # --- 内部 --------------------------------------------------------------

    @staticmethod
    def _client(timeout_seconds: int) -> httpx.Client:
        return httpx.Client(
            base_url=BASE_URL,
            headers={
                "Authorization": f"Bearer {_api_key()}",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(float(timeout_seconds), connect=10.0),
            follow_redirects=False,
            trust_env=False,
        )

    @staticmethod
    def _create_fields(preset: Preset) -> dict:
        try:
            settings = json.loads(preset.settings_json or "{}")
        except json.JSONDecodeError:
            settings = {}
        return {name: value for name, value in settings.items() if name in ALLOWED_CREATE_FIELDS}
