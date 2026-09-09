"""Tripo アダプター（仕様第4章・第8章・第12章）。

公式SDK `tripo3d`（PyPI、MIT）を版を固定して使う。確認内容は
docs/provider-contracts.md に記録した。実装上の要点は3つ。

1. **import 時の外部通信を止める。**
   SDK は import 時にバックグラウンドで第三者のIP位置情報サービス
   （ip-api.com・ipapi.co・ipinfo.io。うち2つは平文HTTP）へ問い合わせる。
   `TRIPO_DISABLE_GEO_DETECTION` を設定してから import する。

2. **SDKのダウンロード経路を使わない。**
   SDK の `_download_with_ssl_retry` は、SSL検証に失敗すると
   検証を無効にして取得し直す。仕様第12章の要求を満たさないため、
   成果物は `app/services/download_guard` で自前に取得する。

3. **タイムアウトを自分でかける。**
   SDK は個々の要求にタイムアウトを設定していない（aiohttp の既定に委ねている）。
   `asyncio.wait_for` で仕様第8章の秒数を適用する。

作成系の自動リトライは無い（`create_task` は POST を1回だけ行う）ことを
ソースで確認済み。したがって無効化の処理は不要。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.config import get_settings
from app.models import AssetVariant, Preset
from app.providers.base import (
    ERROR_PROVIDER_FAILED,
    ERROR_RATE_LIMITED,
    ERROR_TRANSPORT,
    ConnectionCheck,
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

logger = logging.getLogger("providers.tripo")

# import より前に、必ず設定する（SDKは import 時に位置検出スレッドを起動する）。
# setdefault ではなく上書きにするのは、"0" を明示されると検出が動いてしまうため。
# 外部への接続は仕様第12章で限定している
os.environ["TRIPO_DISABLE_GEO_DETECTION"] = "1"

# 正常終了・失敗の状態（SDK の TaskStatus と対応）
_SUCCESS = "success"
_FAILED = {"failed", "banned", "expired"}
_CANCELLED = "cancelled"
_RUNNING = {"queued", "running", "unknown"}


class TripoNotInstalled(ProviderError):
    def __init__(self) -> None:
        super().__init__(
            "Tripo公式SDK（tripo3d）が入っていません。"
            "pyproject.toml の tripo 追加依存を入れてください",
            kind=ERROR_TRANSPORT,
        )


def _client_class():
    try:
        from tripo3d import TripoClient
    except ImportError as exc:  # pragma: no cover - 依存が無い環境向け
        raise TripoNotInstalled() from exc
    return TripoClient


# 公式SDK 0.4.2 はキーの接頭辞を検査する（client.py の __init__）
API_KEY_PREFIX = "tsk_"


def _api_key() -> str:
    key = os.environ.get("TRIPO_API_KEY", "").strip()
    if not key:
        raise ProviderError("TRIPO_API_KEY が設定されていません", kind=ERROR_TRANSPORT)
    if not key.startswith(API_KEY_PREFIX):
        # 値そのものは出さない
        raise ProviderError(
            f"TRIPO_API_KEY の形式が違います（{API_KEY_PREFIX} で始まる必要があります）",
            kind=ERROR_TRANSPORT,
        )
    return key


def _classify(exc: Exception) -> ProviderError:
    """SDKの例外を、こちらの正規化した種別へ移す。原文は画面に出さない。"""
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if status == 429:
        return ProviderError("要求が多すぎます", kind=ERROR_RATE_LIMITED)
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return SubmitTimeout()
    if name == "TripoAPIError":
        code = getattr(exc, "code", "")
        return ProviderError(
            f"事業者がエラーを返しました（code={code}）", kind=ERROR_PROVIDER_FAILED
        )
    if name == "TripoRequestError":
        return ProviderError(f"通信に失敗しました（HTTP {status}）", kind=ERROR_TRANSPORT)
    return ProviderError("通信に失敗しました", kind=ERROR_TRANSPORT)


async def _with_client(coroutine_factory, timeout: float):
    client_class = _client_class()
    client = client_class(api_key=_api_key())
    try:
        return await asyncio.wait_for(coroutine_factory(client), timeout=timeout)
    finally:
        await client.close()


def _run(coroutine_factory, timeout: float):
    return asyncio.run(_with_client(coroutine_factory, timeout))


# 事業者へ申告できる形式（公式SDK 0.4.2 の `_EXT_TO_STS_FORMAT` にある画像形式）
_MIME_TO_SUFFIX = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


@contextmanager
def _named_copy(storage_key: str, mime: str):
    """保存物を、正しい拡張子を付けた一時ファイルとして渡す。

    公式SDKは拡張子から申告形式を決めるため、拡張子の無いパスを渡すと
    PNG/WebP でも "jpeg" と申告される（`docs/decisions.md` A-46）。

    一時ファイルの名前は無作為で、**元のファイル名は使わない**（仕様第12章）。
    抜けても残らないよう、必ず後始末する。
    """
    suffix = _MIME_TO_SUFFIX.get(mime)
    if suffix is None:
        raise ProviderError(
            f"送信できない画像形式です: {mime}",
            kind=ERROR_PROVIDER_FAILED,
        )
    data = storage.read_bytes(storage_key)
    handle = NamedTemporaryFile(suffix=suffix, delete=False)  # noqa: SIM115
    try:
        handle.write(data)
        handle.flush()
        handle.close()
        yield handle.name
    finally:
        Path(handle.name).unlink(missing_ok=True)


class TripoAdapter(ProviderAdapter):
    name = "tripo"
    # 公式SDK 0.4.2 に取消のメソッドは無い。公式資料で確認できるまで未対応とする
    supports_cancel = False

    def estimate(self, variant: AssetVariant, preset: Preset) -> Estimate:
        """価格表から上限側の見積を返す。ここで外部通信はしない。

        価格が未確認のプリセットは `is_bounded=False` を返し、実行できないようにする
        （仕様第11章）。
        """
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
        # EXIF方向を正規化し位置情報を除いた送信用コピーを送る（仕様第12章）
        key = variant.submission_storage_key or variant.storage_key
        params = self._call_params(preset)

        # SDK は**ファイル名の拡張子**から事業者へ申告する形式を決める
        # （`_EXT_TO_STS_FORMAT`。不明な拡張子は "jpeg" にされる）。
        # こちらの保存キーは拡張子を持たない（仕様第12章：元ファイル名を使わない）ため、
        # そのまま渡すと PNG/WebP でも "jpeg" と申告されてしまう。
        # 正しい拡張子を付けた一時ファイルを作って渡す（docs/decisions.md A-46）
        with _named_copy(key, variant.mime) as image_path:

            async def call(client):
                return await client.image_to_model(image=image_path, **params)

            try:
                task_id = _run(call, timeout=settings.submit_timeout_seconds)
            except TimeoutError as exc:
                # 応答が無いだけで未課金とは断定しない。呼び出し側が submission_unknown にする
                raise SubmitTimeout() from exc
            except ProviderError:
                raise
            except Exception as exc:  # noqa: BLE001 - SDKの例外を正規化する
                raise _classify(exc) from exc
        return SubmitResult(provider_task_id=str(task_id))

    def fetch_status(self, provider_task_id: str) -> StatusResult:
        settings = get_settings()

        async def call(client):
            return await client.get_task(provider_task_id)

        try:
            task = _run(call, timeout=settings.status_timeout_seconds)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc

        status = str(getattr(task.status, "value", task.status))
        progress = task.progress if isinstance(task.progress, int) else None

        if status == _SUCCESS:
            output = task.output
            # pbr_model があればそれを優先し、無ければ model を使う
            result_ref = output.pbr_model or output.model
            if not result_ref:
                return StatusResult(
                    state="failed", failure_reason="成果物のURLが返されませんでした"
                )
            return StatusResult(state="succeeded", progress_percent=progress, result_ref=result_ref)
        if status in _FAILED:
            reason = task.error_msg or f"事業者側の状態: {status}"
            return StatusResult(state="failed", failure_reason=reason, progress_percent=progress)
        if status == _CANCELLED:
            return StatusResult(state="failed", failure_reason="事業者側で取り消されました")
        if status in _RUNNING:
            return StatusResult(state="running", progress_percent=progress)
        return StatusResult(state="running", progress_percent=progress)

    def check_connection(self) -> ConnectionCheck:
        """公式SDKの `get_balance()`（GET /user/balance）で認証だけを確かめる。

        読み取り操作であり、生成を行わないので**課金は発生しない**。
        残高は参考表示にとどめ、金額として保存しない（仕様第11章）。
        """
        settings = get_settings()
        try:
            balance = _run(
                lambda client: client.get_balance(),
                float(settings.status_timeout_seconds),
            )
        except ProviderError:
            raise
        except Exception as exc:  # SDKの例外を正規化する
            raise _classify(exc) from exc
        return ConnectionCheck(
            ok=True,
            detail="認証が通りました（生成は行っていないため課金は発生していません）",
            note=f"事業者側のクレジット残高（参考）：{balance.balance}（凍結分 {balance.frozen}）",
        )

    def download_result(self, result_ref: str) -> DownloadedResult:
        """SDKのダウンロードは使わず、自前の検査つき取得を行う（仕様第12章）。"""
        settings = get_settings()
        policy = DownloadPolicy(
            allowed_hosts=settings.tripo_allowed_hosts,
            max_bytes=settings.max_download_bytes,
            read_timeout=float(settings.status_timeout_seconds),
            total_timeout=float(settings.download_timeout_seconds),
        )
        try:
            data = fetch_bytes(result_ref, policy)
        except DownloadRejected as exc:
            # URLは診断ログにも出さない（署名が含まれることがある）
            logger.warning("Tripoの成果物取得を拒否または失敗しました: %s", exc)
            raise ProviderError(str(exc), kind="download_failed") from exc
        return DownloadedResult(data=data)

    @staticmethod
    def _call_params(preset: Preset) -> dict:
        """プリセットの設定を image_to_model の引数にする。

        受け付ける引数名は公式SDK 0.4.2 の `image_to_model` の定義に合わせる。
        未知の名前は落とし、推測で送らない。
        """
        allowed = {
            "model_version",
            "face_limit",
            "texture",
            "pbr",
            "model_seed",
            "texture_seed",
            "texture_quality",
            "geometry_quality",
            "texture_alignment",
            "auto_size",
            "orientation",
            "quad",
            "compress",
            "generate_parts",
            "smart_low_poly",
            "enable_image_autofix",
            "export_uv",
        }
        try:
            settings = json.loads(preset.settings_json or "{}")
        except json.JSONDecodeError:
            settings = {}
        return {name: value for name, value in settings.items() if name in allowed}
