"""題材・画像（仕様第6.2章・第6.3章・第7章）。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import csrf_protect, current_operator, require_admin_action
from app.config import get_settings
from app.db import db_session
from app.models import Asset, AssetVariant, Experiment, Generation, Operator, Preset
from app.models.enums import (
    CONSENT_STATUSES,
    EDIT_TYPES,
    SOURCE_CLASSES,
    SUBJECT_TAGS,
    VERDICTS,
)
from app.providers.registry import get_adapter
from app.services import audit
from app.services.image_intake import ImageRejected, inspect_and_store
from app.services.presets import selectable_reasons
from app.services.review_aggregate import format_micro_usd, latest_reviews
from app.templating import render

router = APIRouter()


async def _read_upload(file: UploadFile) -> bytes:
    """上限を超えた分を読み込まずに打ち切る（仕様第12章）。"""
    settings = get_settings()
    limit = settings.max_image_bytes
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(256 * 1024):
        total += len(chunk)
        if total > limit:
            raise ImageRejected(f"画像の容量が上限（{limit // (1024 * 1024)}MiB）を超えています")
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_choice(value: str, allowed: dict[str, str], label: str) -> str:
    if value not in allowed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{label}の値が不正です")
    return value


def _store_variant(
    db: Session,
    *,
    operator: Operator,
    asset: Asset,
    data: bytes,
    kind: str,
    parent: AssetVariant | None,
    edit_types: list[str],
    edit_tool: str,
    edit_note: str,
) -> AssetVariant:
    result = inspect_and_store(data)
    variant = AssetVariant(
        asset_id=asset.id,
        parent_variant_id=parent.id if parent else None,
        kind=kind,
        storage_key=result.original.key,
        sha256=result.original.sha256,
        bytes=result.original.bytes,
        width=result.width,
        height=result.height,
        mime=result.mime,
        submission_storage_key=result.submission.key,
        submission_sha256=result.submission.sha256,
        submission_bytes=result.submission.bytes,
        edit_types_json=json.dumps(edit_types, ensure_ascii=False),
        edit_tool=edit_tool.strip(),
        edit_note=edit_note.strip(),
        created_by=operator.id,
    )
    db.add(variant)
    db.flush()
    return variant


@router.post("/experiments/{experiment_id}/assets", dependencies=[Depends(csrf_protect)])
async def create_asset_form(
    experiment_id: str,
    file: UploadFile = File(...),
    title: str = Form(""),
    subject_tag: str = Form(...),
    source_class: str = Form(...),
    consent_status: str = Form(...),
    consent_obtained_on: str = Form(""),
    consent_scope_note: str = Form(""),
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "検証セットが見つかりません")

    _validate_choice(subject_tag, SUBJECT_TAGS, "題材タグ")
    _validate_choice(source_class, SOURCE_CLASSES, "出所分類")
    _validate_choice(consent_status, CONSENT_STATUSES, "利用同意")

    try:
        data = await _read_upload(file)
    except ImageRejected as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    asset = Asset(
        experiment_id=experiment.id,
        title=title.strip(),
        subject_tag=subject_tag,
        source_class=source_class,
        consent_status=consent_status,
        consent_obtained_on=consent_obtained_on.strip() or None,
        consent_scope_note=consent_scope_note.strip(),
        consent_recorded_by=operator.id,
        created_by=operator.id,
    )
    db.add(asset)
    db.flush()

    try:
        _store_variant(
            db,
            operator=operator,
            asset=asset,
            data=data,
            kind="original",
            parent=None,
            edit_types=[],
            edit_tool="",
            edit_note="",
        )
    except ImageRejected as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    audit.record(
        db,
        operator=operator,
        target_kind="asset",
        target_id=asset.id,
        action="create",
        after={"subject_tag": subject_tag, "consent_status": consent_status},
    )
    return RedirectResponse(f"/assets/{asset.id}", status_code=303)


@router.post("/assets/{asset_id}/variants", dependencies=[Depends(csrf_protect)])
async def create_variant_form(
    asset_id: str,
    file: UploadFile = File(...),
    edit_types: list[str] = Form(default=[]),
    edit_tool: str = Form(""),
    edit_note: str = Form(""),
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "題材が見つかりません")
    for value in edit_types:
        _validate_choice(value, EDIT_TYPES, "加工種別")

    original = next((v for v in asset.variants if v.kind == "original"), None)
    try:
        data = await _read_upload(file)
        _store_variant(
            db,
            operator=operator,
            asset=asset,
            data=data,
            kind="processed",
            parent=original,
            edit_types=edit_types,
            edit_tool=edit_tool,
            edit_note=edit_note,
        )
    except ImageRejected as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    audit.record(
        db,
        operator=operator,
        target_kind="asset",
        target_id=asset.id,
        action="add_variant",
        after={"edit_types": edit_types, "edit_tool": edit_tool},
    )
    return RedirectResponse(f"/assets/{asset.id}", status_code=303)


@router.post("/assets/{asset_id}/consent", dependencies=[Depends(csrf_protect)])
def update_consent_form(
    asset_id: str,
    consent_status: str = Form(...),
    consent_obtained_on: str = Form(""),
    consent_scope_note: str = Form(""),
    reason: str = Form(...),
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "題材が見つかりません")
    _validate_choice(consent_status, CONSENT_STATUSES, "利用同意")
    require_admin_action(operator, "利用同意の更新")
    if not reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "変更理由を入力してください")

    before = {
        "consent_status": asset.consent_status,
        "consent_obtained_on": asset.consent_obtained_on,
    }
    asset.consent_status = consent_status
    asset.consent_obtained_on = consent_obtained_on.strip() or None
    asset.consent_scope_note = consent_scope_note.strip()
    asset.consent_recorded_by = operator.id
    db.flush()
    # 利用同意の変更は監査ログ必須（仕様第7章）
    audit.record(
        db,
        operator=operator,
        target_kind="asset",
        target_id=asset.id,
        action="update_consent",
        reason=reason.strip(),
        before=before,
        after={"consent_status": consent_status, "consent_obtained_on": asset.consent_obtained_on},
    )
    return RedirectResponse(f"/assets/{asset.id}", status_code=303)


@router.get("/assets/{asset_id}")
def asset_detail_page(
    asset_id: str,
    request: Request,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "題材が見つかりません")
    experiment = asset.experiment

    variants = []
    for variant in asset.variants:
        try:
            types = json.loads(variant.edit_types_json or "[]")
        except json.JSONDecodeError:
            types = []
        variant.edit_type_labels = [EDIT_TYPES.get(t, t) for t in types]  # type: ignore[attr-defined]
        variants.append(variant)

    presets = []
    for preset in db.scalars(select(Preset).order_by(Preset.code.asc())).all():
        reasons = selectable_reasons(preset, live=experiment.is_live)
        if experiment.is_live and asset.consent_status == "missing":
            reasons.append("利用同意が未取得です")
        if not experiment.is_live and preset.provider != "mock":
            reasons.append("モックのセットでは選べません")
        estimate_label = "—"
        try:
            adapter = get_adapter(preset.provider)
            if variants:
                estimate_label = format_micro_usd(
                    adapter.estimate(variants[0], preset).max_micro_usd
                )
        except KeyError:
            reasons.append("アダプター未実装（A3で追加）")
        presets.append({"preset": preset, "reasons": reasons, "estimate_label": estimate_label})

    generations = db.scalars(
        select(Generation)
        .where(Generation.asset_id == asset.id)
        .order_by(Generation.created_at.desc())
    ).all()
    reviews = latest_reviews(db, [g.id for g in generations])
    generation_rows = [
        {
            "generation": g,
            "verdict_label": VERDICTS.get(
                reviews[g.id].verdict if g.id in reviews else "unreviewed", "未評価"
            ),
        }
        for g in generations
    ]

    return render(
        request,
        "asset_detail.html",
        {
            "operator": operator,
            "asset": asset,
            "experiment": experiment,
            "variants": variants,
            "presets": presets,
            "generations": generation_rows,
        },
    )


@router.get("/api/assets/{asset_id}")
def api_asset(
    asset_id: str,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "題材が見つかりません")
    generations = db.scalars(select(Generation).where(Generation.asset_id == asset.id)).all()
    return {
        "id": asset.id,
        "title": asset.title,
        "subject_tag": asset.subject_tag,
        "source_class": asset.source_class,
        "consent_status": asset.consent_status,
        "variants": [
            {
                "id": v.id,
                "kind": v.kind,
                "sha256": v.sha256,
                "width": v.width,
                "height": v.height,
                "mime": v.mime,
                "bytes": v.bytes,
                "submission_sha256": v.submission_sha256,
                "edit_types": json.loads(v.edit_types_json or "[]"),
                "edit_tool": v.edit_tool,
            }
            for v in asset.variants
        ],
        "generations": [
            {
                "id": g.id,
                "provider": g.provider,
                "attempt_index": g.attempt_index,
                "tech_status": g.tech_status,
                "is_live": g.is_live,
            }
            for g in generations
        ],
    }


@router.get("/api/presets")
def api_presets(
    live: bool = False,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """利用可否、モデル、設定、価格の確認状態（仕様第7章）。"""
    presets = db.scalars(select(Preset).order_by(Preset.code.asc())).all()
    return [
        {
            "id": p.id,
            "code": p.code,
            "display_name": p.display_name,
            "provider": p.provider,
            "model_id": p.model_id or None,
            "settings": json.loads(p.settings_json or "{}"),
            "version": p.version,
            "is_enabled": p.is_enabled,
            "is_unverified": p.is_unverified,
            "unverified_note": p.unverified_note,
            "price_max_micro_usd": p.price_max_micro_usd,
            "price_checked_on": p.price_checked_on,
            "price_source_url": p.price_source_url,
            "blocked_reasons": selectable_reasons(p, live=live),
        }
        for p in presets
    ]
