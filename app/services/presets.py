"""固定プリセットの投入（仕様第8章）。

Tripo / Meshy の モデルID・パラメーター・価格は公式資料で未確認のため、
UNVERIFIED として無効のまま登録する。A3で確認して更新する（仕様第4章）。
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Preset

UNVERIFIED_NOTE = (
    "モデルID・パラメーター・価格・データ取扱い条件を公式資料で未確認。"
    "A3で確認するまで実生成は選択できません。"
)

SEED: list[dict] = [
    {
        "code": "mock-standard",
        "display_name": "モック標準",
        "provider": "mock",
        "model_id": "mock-shape-v1",
        "settings": {"texture": "standard"},
        "version": "1",
        "sdk_version": "n/a",
        "is_enabled": True,
        "price_max_micro_usd": 0,
        "price_version": "mock",
        "price_checked_on": None,
        "price_source_url": "",
        "is_unverified": False,
        "unverified_note": "",
    },
    {
        "code": "tripo-standard",
        "display_name": "Tripo 標準（未確認）",
        "provider": "tripo",
        "model_id": "",
        "settings": {},
        "version": "0",
        "sdk_version": "",
        "is_enabled": False,
        "price_max_micro_usd": None,
        "price_version": "",
        "price_checked_on": None,
        "price_source_url": "https://developers.tripo3d.ai/en/pricing",
        "is_unverified": True,
        "unverified_note": UNVERIFIED_NOTE,
    },
    {
        "code": "meshy-standard",
        "display_name": "Meshy 標準（未確認）",
        "provider": "meshy",
        "model_id": "",
        "settings": {},
        "version": "0",
        "sdk_version": "",
        "is_enabled": False,
        "price_max_micro_usd": None,
        "price_version": "",
        "price_checked_on": None,
        "price_source_url": "https://docs.meshy.ai/en/api/pricing",
        "is_unverified": True,
        "unverified_note": UNVERIFIED_NOTE,
    },
]

# --- A2で追加：障害切替と2社比較の検証用（仕様第13章） --------------------
#
# いずれもモックであり外部通信を行わない。実費は発生しない。
# 見積額を持つものは、上限額判定（仕様第11章）をモックで検証するために使う。


def _mock(
    code: str,
    name: str,
    *,
    provider: str = "mock",
    scenario: str = "success",
    price_max_micro_usd: int | None = 0,
) -> dict:
    return {
        "code": code,
        "display_name": name,
        "provider": provider,
        "model_id": "mock-shape-v1",
        "settings": {"texture": "standard", "scenario": scenario},
        "version": "1",
        "sdk_version": "n/a",
        "is_enabled": True,
        "price_max_micro_usd": price_max_micro_usd,
        "price_version": "mock",
        "price_checked_on": None,
        "price_source_url": "",
        "is_unverified": False,
        "unverified_note": "",
    }


SEED += [
    # 2社比較用。送信枠は asset×provider で数えるため provider を分ける
    _mock("mock-a-standard", "モックA 標準", provider="mock_a", price_max_micro_usd=300_000),
    _mock("mock-b-standard", "モックB 標準", provider="mock_b", price_max_micro_usd=300_000),
    # 上限額判定の検証用（1件あたり $1.00）
    _mock("mock-priced", "モック 見積あり", price_max_micro_usd=1_000_000),
    # 障害の切替
    _mock("mock-delayed", "モック 生成に時間がかかる", scenario="delayed"),
    _mock("mock-provider-failed", "モック 事業者側で失敗", scenario="provider_failed"),
    _mock("mock-rate-limited", "モック 429", scenario="rate_limited"),
    _mock("mock-submit-timeout", "モック 作成応答タイムアウト", scenario="submit_timeout"),
    _mock("mock-slow", "モック 長時間待機", scenario="slow"),
    _mock("mock-download-failed", "モック ダウンロード失敗", scenario="download_failed"),
    _mock("mock-invalid-glb", "モック 不正GLB", scenario="invalid_glb"),
]


def seed_presets(db: Session) -> int:
    """未登録のプリセットだけを追加する。既存の行は書き換えない。"""
    created = 0
    for item in SEED:
        if db.scalar(select(Preset).where(Preset.code == item["code"])) is not None:
            continue
        db.add(
            Preset(
                code=item["code"],
                display_name=item["display_name"],
                provider=item["provider"],
                model_id=item["model_id"],
                settings_json=json.dumps(item["settings"], ensure_ascii=False),
                version=item["version"],
                sdk_version=item["sdk_version"],
                is_enabled=item["is_enabled"],
                price_max_micro_usd=item["price_max_micro_usd"],
                price_version=item["price_version"],
                price_checked_on=item["price_checked_on"],
                price_source_url=item["price_source_url"],
                is_unverified=item["is_unverified"],
                unverified_note=item["unverified_note"],
            )
        )
        created += 1
    return created


def selectable_reasons(preset: Preset, *, live: bool) -> list[str]:
    """実生成を選べない理由を日本語で返す（仕様第6.3章）。空なら選択可。"""
    reasons: list[str] = []
    if not preset.is_enabled:
        reasons.append("プリセットが無効です")
    if preset.is_unverified:
        reasons.append("価格・モデルIDが未確認です")
    if live and preset.price_max_micro_usd is None:
        reasons.append("上限額を見積もれません")
    return reasons
