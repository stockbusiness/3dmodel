"""固定プリセットの投入（仕様第8章）。

Tripo / Meshy のエンドポイント・モデルID・パラメーター・状態値・**価格**は
公式資料で確認済み（2026-09-08、docs/provider-contracts.md）。
データ取扱い条件（保持期間・学習利用の可否とopt-out・生成物の利用条件）が
未確認のため、**無効のまま**登録する（仕様第4章・第11章）。
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Preset

UNVERIFIED_NOTE = (
    "モデルID・パラメーター・価格・データ取扱い条件を公式資料で未確認。"
    "確認するまで実生成は選択できません。"
)

# 価格は確認できた。残るのはデータ取扱い条件で、これが確認できるまで実生成は選べない
DATA_TERMS_UNVERIFIED_NOTE = (
    "エンドポイント・モデルID・パラメーター・状態値・エラー・価格は公式資料で確認済み（2026-09-08）。"
    "データ保持期間、学習利用の可否とopt-out手段、生成物の利用条件が未確認のため、"
    "実生成は選べません。詳細は docs/provider-contracts.md を参照。"
)

# --- 確認済みの価格（仕様第11章。金額は micro-USD の整数で持つ） ----------------
#
# Tripo: 1credit = $0.01、画像→3D テクスチャ付き 30credits ＝ $0.30/件
# Meshy: 画像→3D テクスチャ付き 30credits。USD単価は購入経路で変わるため、
#        **最も高い経路**（追加クレジットパック $10 / 250credits ＝ $0.04/credit）で
#        見積もる。price_max_micro_usd は「上限額」なので最大値を入れるのが正しい。
#        定額プラン（Pro $20/1,000credits ＝ $0.02/credit）ならこれより安くなる。
_MICRO = 1_000_000
TRIPO_CREDITS_PER_ITEM = 30
TRIPO_MICRO_USD_PER_CREDIT = 10_000  # $0.01
MESHY_CREDITS_PER_ITEM = 30
MESHY_MICRO_USD_PER_CREDIT = 40_000  # $0.04（$10 / 250credits の追加クレジットパック）

CONFIRMED_PRICES: dict[str, dict] = {
    "tripo-standard": {
        "price_max_micro_usd": TRIPO_CREDITS_PER_ITEM * TRIPO_MICRO_USD_PER_CREDIT,  # 300_000
        "price_version": "tripo-2026-09-08",
        "price_checked_on": "2026-09-08",
        "price_source_url": "https://developers.tripo3d.ai/en/pricing",
        "display_name": "Tripo 標準",
    },
    "meshy-standard": {
        "price_max_micro_usd": MESHY_CREDITS_PER_ITEM * MESHY_MICRO_USD_PER_CREDIT,  # 1_200_000
        "price_version": "meshy-2026-09-08-extra-pack",
        "price_checked_on": "2026-09-08",
        "price_source_url": "https://meshy.ai/settings/subscription",
        "display_name": "Meshy 標準",
    },
}

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
        # モデルID・パラメーターは公式SDK tripo3d 0.4.2 の image_to_model の定義で確認済み。
        # 価格とデータ取扱い条件が未確認のため、無効のままにする
        "code": "tripo-standard",
        "display_name": "Tripo 標準",
        "provider": "tripo",
        "model_id": "v2.5-20250123",
        "settings": {
            "model_version": "v2.5-20250123",
            "texture": True,
            "pbr": True,
            "texture_quality": "standard",
            "geometry_quality": "standard",
            "texture_alignment": "original_image",
            "export_uv": True,
        },
        "version": "1",
        "sdk_version": "tripo3d==0.4.2",
        # データ取扱い条件が未確認のため無効のまま
        "is_enabled": False,
        "price_max_micro_usd": CONFIRMED_PRICES["tripo-standard"]["price_max_micro_usd"],
        "price_version": CONFIRMED_PRICES["tripo-standard"]["price_version"],
        "price_checked_on": CONFIRMED_PRICES["tripo-standard"]["price_checked_on"],
        "price_source_url": CONFIRMED_PRICES["tripo-standard"]["price_source_url"],
        "is_unverified": False,
        "unverified_note": DATA_TERMS_UNVERIFIED_NOTE,
    },
    {
        # エンドポイント・パラメーター・状態値は公式CLI meshy-cli 0.2.0 のソースで確認済み。
        # Meshy は「モードがモデル」で、standard は meshy-7 に対応する
        "code": "meshy-standard",
        "display_name": "Meshy 標準",
        "provider": "meshy",
        "model_id": "standard (meshy-7)",
        "settings": {
            "model_type": "standard",
            "should_texture": True,
            "enable_pbr": True,
            "texture_resolution": "4k",
            "target_formats": ["glb"],
        },
        "version": "1",
        "sdk_version": "meshy-cli==0.2.0 で確認した公式契約に基づく自前のHTTP実装",
        # データ取扱い条件が未確認のため無効のまま
        "is_enabled": False,
        "price_max_micro_usd": CONFIRMED_PRICES["meshy-standard"]["price_max_micro_usd"],
        "price_version": CONFIRMED_PRICES["meshy-standard"]["price_version"],
        "price_checked_on": CONFIRMED_PRICES["meshy-standard"]["price_checked_on"],
        "price_source_url": CONFIRMED_PRICES["meshy-standard"]["price_source_url"],
        "is_unverified": False,
        "unverified_note": DATA_TERMS_UNVERIFIED_NOTE,
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


def refresh_placeholder_presets(db: Session) -> int:
    """まだ一度も内容が入っていないプリセット行を、確認済みの定義に更新する。

    使われたことのある行（モデルIDが入っているもの）は書き換えない。
    過去の実行は generations のスナップショットを見るため、この更新では変わらない
    （仕様第8章「過去実行を後から設定変更で書き換えない」）。
    """
    updated = 0
    for item in SEED:
        if not item["model_id"]:
            continue
        preset = db.scalar(select(Preset).where(Preset.code == item["code"]))
        if preset is None or preset.model_id:
            continue
        preset.model_id = item["model_id"]
        preset.display_name = item["display_name"]
        preset.settings_json = json.dumps(item["settings"], ensure_ascii=False)
        preset.version = item["version"]
        preset.sdk_version = item["sdk_version"]
        preset.price_source_url = item["price_source_url"]
        preset.unverified_note = item["unverified_note"]
        updated += 1
    db.flush()
    return updated


def apply_confirmed_prices(db: Session) -> int:
    """公式資料で確認できた価格を、まだ価格が入っていない行に反映する（仕様第11章）。

    既に価格が入っている行は書き換えない。運営が管理画面で入れた値を勝手に
    上書きしないため。過去の実行は generations のスナップショットを見るので、
    この更新では変わらない（仕様第8章）。

    `is_enabled` はここでは触らない。データ取扱い条件が未確認のあいだ、
    実生成に選べない状態を保つ。
    """
    updated = 0
    for code, price in CONFIRMED_PRICES.items():
        preset = db.scalar(select(Preset).where(Preset.code == code))
        if preset is None or preset.price_max_micro_usd is not None:
            continue
        preset.price_max_micro_usd = price["price_max_micro_usd"]
        preset.price_version = price["price_version"]
        preset.price_checked_on = price["price_checked_on"]
        preset.price_source_url = price["price_source_url"]
        preset.display_name = price["display_name"]
        preset.is_unverified = False
        preset.unverified_note = DATA_TERMS_UNVERIFIED_NOTE
        updated += 1
    db.flush()
    return updated


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
