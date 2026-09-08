"""モックアダプター。外部通信を一切行わない（仕様第8章・第13章）。

仕様第13章が求める切替：成功、事業者失敗、429、作成応答タイムアウト、長時間待機、
ダウンロード失敗、不正GLB。上限額超過はプリセットの見積額で表す。

シナリオはプリセットの settings_json の `scenario` で指定する。
DBに入っているため、web と worker（別プロセス）で同じ挙動になる。

状態はプロセス内に持たない。必要な情報は provider_task_id に埋め込み、
再起動後も同じタスクIDから同じ判断ができるようにする（仕様第13章 試験5）。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path

from app.models import AssetVariant, Preset
from app.providers.base import (
    ERROR_DOWNLOAD_FAILED,
    ERROR_PROVIDER_FAILED,
    ERROR_RATE_LIMITED,
    ConnectionCheck,
    DownloadedResult,
    Estimate,
    ProviderAdapter,
    ProviderError,
    StatusResult,
    SubmitResult,
    SubmitTimeout,
)

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLES = ("sample_cube.glb", "sample_pyramid.glb")

SUCCESS = "success"
DELAYED = "delayed"
PROVIDER_FAILED = "provider_failed"
RATE_LIMITED = "rate_limited"
SUBMIT_TIMEOUT = "submit_timeout"
SLOW = "slow"
DOWNLOAD_FAILED = "download_failed"
INVALID_GLB = "invalid_glb"

SCENARIOS = (
    SUCCESS,
    DELAYED,
    PROVIDER_FAILED,
    RATE_LIMITED,
    SUBMIT_TIMEOUT,
    SLOW,
    DOWNLOAD_FAILED,
    INVALID_GLB,
)

# DELAYED のとき、この秒数だけ running を返してから成功にする
DELAYED_SECONDS = 2
INVALID_GLB_BODY = b"<!doctype html><html><body>this is not a model</body></html>"


def scenario_of(preset: Preset) -> str:
    try:
        settings = json.loads(preset.settings_json or "{}")
    except json.JSONDecodeError:
        return SUCCESS
    scenario = settings.get("scenario", SUCCESS)
    return scenario if scenario in SCENARIOS else SUCCESS


class MockAdapter(ProviderAdapter):
    """モックの生成サービス。

    2社比較を検証できるよう、名前の異なる複数のインスタンスを登録して使う。
    """

    supports_cancel = True

    def __init__(self, name: str = "mock") -> None:
        self.name = name

    # --- 5メソッド ---------------------------------------------------------

    def estimate(self, variant: AssetVariant, preset: Preset) -> Estimate:
        amount = preset.price_max_micro_usd or 0
        return Estimate(
            max_micro_usd=amount,
            credits=None,
            price_version=preset.price_version or "mock",
            is_bounded=True,
            note="モックのため実費は発生しない",
        )

    def submit(
        self, variant: AssetVariant, preset: Preset, *, client_reference: str
    ) -> SubmitResult:
        scenario = scenario_of(preset)
        if scenario == SUBMIT_TIMEOUT:
            # 応答が返らない。呼び出し側は submission_unknown にし、自動で再POSTしない
            raise SubmitTimeout()

        ready_at = int(time.time()) + (DELAYED_SECONDS if scenario == DELAYED else 0)
        # 生成ごとに一意にする（同じ画像・プリセットで再生成してもIDが衝突しない）
        unique = uuid.uuid5(uuid.NAMESPACE_URL, f"{self.name}:{client_reference}").hex[:16]
        return SubmitResult(provider_task_id=f"{self.name}:{scenario}:{ready_at}:{unique}")

    def fetch_status(self, provider_task_id: str) -> StatusResult:
        scenario, ready_at = self._parse(provider_task_id)

        if scenario == PROVIDER_FAILED:
            return StatusResult(state="failed", failure_reason="モック：事業者が失敗を返しました")
        if scenario == RATE_LIMITED:
            raise ProviderError("モック：要求が多すぎます", kind=ERROR_RATE_LIMITED, retry_after=30)
        if scenario == SLOW:
            # いつまでも完了しない。30分で monitoring_paused になることを確かめる
            return StatusResult(state="running")
        if time.time() < ready_at:
            return StatusResult(state="running")
        return StatusResult(state="succeeded", result_ref=self._sample_for(provider_task_id))

    def download_result(self, result_ref: str) -> DownloadedResult:
        if result_ref == DOWNLOAD_FAILED:
            raise ProviderError("モック：成果物を取得できません", kind=ERROR_DOWNLOAD_FAILED)
        if result_ref == INVALID_GLB:
            return DownloadedResult(data=INVALID_GLB_BODY)
        path = FIXTURES_DIR / result_ref
        # 読み出す先を fixtures/ の中に限定する（任意パスの読み出しを許さない）
        if result_ref not in SAMPLES or not path.is_file():
            raise ProviderError("サンプルGLBが見つかりません", kind=ERROR_DOWNLOAD_FAILED)
        return DownloadedResult(data=path.read_bytes())

    def check_connection(self) -> ConnectionCheck:
        """モックは外部へ出ないので常に成功する。"""
        return ConnectionCheck(
            ok=True,
            detail="モックのため外部通信は行いません",
            note="実APIの疎通確認にはなりません",
        )

    def cancel(self, provider_task_id: str) -> None:
        scenario, _ = self._parse(provider_task_id)
        if scenario == SLOW:
            # 進行中のタスクは外部で取り消せないことがある、という状況を再現する
            raise ProviderError("モック：このタスクは取り消せません", kind=ERROR_PROVIDER_FAILED)

    # --- 内部 --------------------------------------------------------------

    def _parse(self, provider_task_id: str) -> tuple[str, int]:
        parts = provider_task_id.split(":")
        if len(parts) != 4 or parts[1] not in SCENARIOS:
            raise ProviderError("モック：タスクIDの形式が不正です", kind=ERROR_PROVIDER_FAILED)
        try:
            return parts[1], int(parts[2])
        except ValueError as exc:
            raise ProviderError(
                "モック：タスクIDの形式が不正です", kind=ERROR_PROVIDER_FAILED
            ) from exc

    @staticmethod
    def _sample_for(provider_task_id: str) -> str:
        scenario = provider_task_id.split(":")[1]
        if scenario in (DOWNLOAD_FAILED, INVALID_GLB):
            return scenario
        index = int(hashlib.sha256(provider_task_id.encode()).hexdigest(), 16) % len(SAMPLES)
        return SAMPLES[index]
