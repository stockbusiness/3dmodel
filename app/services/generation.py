"""生成の受付と、A1のモック処理（仕様第8章）。

A1の範囲：
- 受付（queued）と、モックによる submitting → running → downloading → ready_for_review
- 利用同意 missing の実生成をサーバー側で拒否（仕様第12章）
- LIVE_API_ENABLED=false のとき実生成を拒否（仕様第8章）

A2で追加する：永続ワーカー、lease、冪等キー、上限額判定、送信枠、状態の分岐と復帰、比較。
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Artifact, AssetVariant, CostEntry, Generation, Operator, Preset, utcnow
from app.providers.base import ProviderError
from app.providers.registry import get_adapter
from app.services import glb_inspect, storage
from app.services.presets import selectable_reasons


class GenerationRejected(Exception):
    """受付できない依頼。理由は利用者に日本語で返す。"""


def _variant_or_error(db: Session, variant_id: str) -> AssetVariant:
    variant = db.get(AssetVariant, variant_id)
    if variant is None:
        raise GenerationRejected("画像が見つかりません")
    return variant


def check_can_submit(db: Session, variant: AssetVariant, preset: Preset, *, live: bool) -> None:
    """受付前の検査。UIの非活性だけに頼らずサーバー側でも拒否する（仕様第12章）。"""
    settings = get_settings()
    asset = variant.asset

    if live and not settings.live_api_enabled:
        raise GenerationRejected("実API生成は無効です（LIVE_API_ENABLED=false）")
    if live and asset.consent_status == "missing":
        raise GenerationRejected("利用同意が未取得の画像は外部送信できません")

    reasons = selectable_reasons(preset, live=live)
    if reasons:
        raise GenerationRejected("／".join(reasons))
    if not live and preset.provider != "mock":
        raise GenerationRejected("モード（モック）と選んだサービスが一致しません")
    if live and preset.provider == "mock":
        raise GenerationRejected("モックのプリセットでは実生成できません")


def create_generation(
    db: Session,
    *,
    operator: Operator,
    variant_id: str,
    preset_id: str,
    purpose: str = "benchmark",
    parent_generation_id: str | None = None,
) -> Generation:
    variant = _variant_or_error(db, variant_id)
    preset = db.get(Preset, preset_id)
    if preset is None:
        raise GenerationRejected("プリセットが見つかりません")

    asset = variant.asset
    experiment = asset.experiment
    live = experiment.is_live
    check_can_submit(db, variant, preset, live=live)

    parent = db.get(Generation, parent_generation_id) if parent_generation_id else None
    attempt_index = (parent.attempt_index + 1) if parent else 1

    adapter = get_adapter(preset.provider)
    estimate = adapter.estimate(variant, preset)
    if live and not estimate.is_bounded:
        raise GenerationRejected("上限額を見積もれないプリセットは実行できません")

    generation = Generation(
        experiment_id=experiment.id,
        variant_id=variant.id,
        asset_id=asset.id,
        preset_id=preset.id,
        preset_snapshot_json=adapter.preset_snapshot(preset),
        provider=preset.provider,
        parent_id=parent.id if parent else None,
        attempt_index=attempt_index,
        purpose=purpose,
        is_live=live,
        tech_status="queued",
        next_check_at=utcnow(),
        created_by=operator.id,
    )
    db.add(generation)
    db.flush()

    db.add(
        CostEntry(
            generation_id=generation.id,
            kind="estimate",
            amount_micro_usd=estimate.max_micro_usd,
            credits=estimate.credits,
            currency=estimate.currency,
            price_version=estimate.price_version,
            evidence_note=estimate.note,
            recorded_by=operator.id,
            dedupe_key=f"estimate:{generation.id}",
        )
    )
    db.flush()
    return generation


def process_generation(db: Session, generation: Generation) -> Generation:
    """A1のモック処理。1件を受付から評価待ちまで進める。

    A2ではこれを永続ワーカーの処理単位に分割し、状態の分岐と lease を扱う。
    """
    adapter = get_adapter(generation.provider)
    variant = db.get(AssetVariant, generation.variant_id)
    preset = db.get(Preset, generation.preset_id)
    if variant is None or preset is None:
        generation.tech_status = "provider_failed"
        generation.error_kind = "missing_input"
        generation.error_note = "入力またはプリセットが見つかりません"
        return generation

    try:
        # 仕様第8章：API送信前に submitting をコミットし、IDを得たら直ちに保存する
        generation.tech_status = "submitting"
        generation.submitted_at = utcnow()
        db.flush()

        submitted = adapter.submit(variant, preset)
        generation.provider_task_id = submitted.provider_task_id
        generation.tech_status = "running"
        db.flush()

        status = adapter.fetch_status(submitted.provider_task_id)
        generation.last_checked_at = utcnow()
        generation.progress_percent = status.progress_percent
        if status.state != "succeeded" or not status.result_ref:
            generation.tech_status = "provider_failed"
            generation.error_kind = "provider_failed"
            generation.error_note = status.failure_reason or "事業者が失敗を返しました"
            return generation

        generation.tech_status = "downloading"
        db.flush()

        downloaded = adapter.download_result(status.result_ref)
    except ProviderError as exc:
        generation.tech_status = (
            "download_failed" if exc.kind == "download_failed" else "provider_failed"
        )
        generation.error_kind = exc.kind
        generation.error_note = str(exc)
        return generation

    settings = get_settings()
    try:
        metrics = glb_inspect.inspect(downloaded.data, max_bytes=settings.max_download_bytes)
    except glb_inspect.GlbRejected as exc:
        generation.tech_status = "validation_failed"
        generation.error_kind = "validation_failed"
        generation.error_note = str(exc)
        return generation

    stored = storage.save_bytes(downloaded.data)
    db.add(
        Artifact(
            generation_id=generation.id,
            kind="glb",
            storage_key=stored.key,
            sha256=stored.sha256,
            bytes=stored.bytes,
            inspection_ok=True,
            inspection_note="形式・サイズ整合・外部URI参照なしを確認",
            metrics_json=metrics.to_json(),
        )
    )
    generation.tech_status = "ready_for_review"
    generation.completed_at = utcnow()
    generation.next_check_at = None
    db.flush()
    return generation


def glb_artifact(db: Session, generation_id: str) -> Artifact | None:
    return db.scalar(
        select(Artifact)
        .where(Artifact.generation_id == generation_id, Artifact.kind == "glb")
        .order_by(Artifact.created_at.desc())
    )


def preset_snapshot(generation: Generation) -> dict:
    try:
        return json.loads(generation.preset_snapshot_json)
    except json.JSONDecodeError:
        return {}
