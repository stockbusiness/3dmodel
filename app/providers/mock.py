"""モックアダプター。外部通信を一切行わない（仕様第8章・第13章）。

A1では成功系のみを実装する。障害の切替はA2で追加する。
返すGLBは fixtures/ の自作サンプル（合成データ）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.models import AssetVariant, Preset
from app.providers.base import (
    DownloadedResult,
    Estimate,
    ProviderAdapter,
    ProviderError,
    StatusResult,
    SubmitResult,
)

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLES = ("sample_cube.glb", "sample_pyramid.glb")


class MockAdapter(ProviderAdapter):
    name = "mock"

    def estimate(self, variant: AssetVariant, preset: Preset) -> Estimate:
        return Estimate(
            max_micro_usd=preset.price_max_micro_usd or 0,
            credits=None,
            price_version=preset.price_version or "mock",
            is_bounded=True,
            note="モックのため実費は発生しない",
        )

    def submit(self, variant: AssetVariant, preset: Preset) -> SubmitResult:
        # 入力に対して安定したIDを作る。外部通信はしない
        seed = f"{variant.id}:{preset.id}:{variant.sha256}".encode()
        return SubmitResult(provider_task_id="mock-" + hashlib.sha256(seed).hexdigest()[:24])

    def fetch_status(self, provider_task_id: str) -> StatusResult:
        # A1は即時成功。段階的な進行はA2の永続ワーカーで扱う
        return StatusResult(state="succeeded", result_ref=self._sample_for(provider_task_id))

    def download_result(self, result_ref: str) -> DownloadedResult:
        path = FIXTURES_DIR / result_ref
        # 保存先を fixtures/ の中に限定する（任意パスの読み出しを許さない）
        if result_ref not in SAMPLES or not path.is_file():
            raise ProviderError("サンプルGLBが見つかりません", kind="download_failed")
        return DownloadedResult(data=path.read_bytes())

    @staticmethod
    def _sample_for(provider_task_id: str) -> str:
        index = int(hashlib.sha256(provider_task_id.encode()).hexdigest(), 16) % len(SAMPLES)
        return SAMPLES[index]
