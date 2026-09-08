"""生成受付・評価（仕様第7章・第10章）。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import csrf_protect, current_operator
from app.db import db_session
from app.models import Asset, AssetVariant, Generation, Operator, Review
from app.models.enums import DEFECT_TAGS, PURPOSES, VERDICTS
from app.services import audit
from app.services import generation as generation_service
from app.services.review_aggregate import defect_tags, is_pass, pass_blockers
from app.templating import render

router = APIRouter()


def _create_and_process(
    db: Session,
    operator: Operator,
    *,
    variant_id: str,
    preset_id: str,
    purpose: str,
    parent_generation_id: str | None = None,
) -> Generation:
    if purpose not in PURPOSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "目的の値が不正です")
    try:
        generation = generation_service.create_generation(
            db,
            operator=operator,
            variant_id=variant_id,
            preset_id=preset_id,
            purpose=purpose,
            parent_generation_id=parent_generation_id,
        )
    except generation_service.GenerationRejected as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    audit.record(
        db,
        operator=operator,
        target_kind="generation",
        target_id=generation.id,
        action="create",
        after={"provider": generation.provider, "is_live": generation.is_live},
    )
    # A1はモックの同期処理。A2で永続ワーカーに移す
    generation_service.process_generation(db, generation)
    return generation


@router.post("/generations", dependencies=[Depends(csrf_protect)])
def create_from_form(
    variant_id: str = Form(...),
    preset_id: str = Form(...),
    purpose: str = Form("benchmark"),
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    generation = _create_and_process(
        db, operator, variant_id=variant_id, preset_id=preset_id, purpose=purpose
    )
    return RedirectResponse(f"/generations/{generation.id}", status_code=303)


@router.post(
    "/api/generations",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(csrf_protect)],
)
def api_create(
    payload: dict,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """単社生成受付、202応答（仕様第7章）。

    provider は preset から確定する。任意の外部エンドポイントは受け取らない。
    冪等キーの必須化は A2 で追加する。
    """
    variant_id = payload.get("asset_variant_id")
    preset_id = payload.get("preset_id")
    if not variant_id or not preset_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "asset_variant_id と preset_id は必須です")
    generation = _create_and_process(
        db,
        operator,
        variant_id=str(variant_id),
        preset_id=str(preset_id),
        purpose=str(payload.get("purpose", "benchmark")),
        parent_generation_id=payload.get("parent_generation_id"),
    )
    return {"id": generation.id, "tech_status": generation.tech_status}


@router.get("/api/generations/{generation_id}")
def api_detail(
    generation_id: str,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    generation = db.get(Generation, generation_id)
    if generation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "生成が見つかりません")
    artifact = generation_service.glb_artifact(db, generation.id)
    reviews = db.scalars(
        select(Review)
        .where(Review.generation_id == generation.id)
        .order_by(Review.created_at.asc())
    ).all()
    latest = reviews[-1] if reviews else None
    snapshot = generation_service.preset_snapshot(generation)
    return {
        "id": generation.id,
        "tech_status": generation.tech_status,
        "error_kind": generation.error_kind,
        "error_note": generation.error_note,
        "provider": generation.provider,
        "is_live": generation.is_live,
        "attempt_index": generation.attempt_index,
        "progress_percent": generation.progress_percent,
        "submitted_at": generation.submitted_at.isoformat() if generation.submitted_at else None,
        "completed_at": generation.completed_at.isoformat() if generation.completed_at else None,
        "last_checked_at": (
            generation.last_checked_at.isoformat() if generation.last_checked_at else None
        ),
        "preset": {"code": snapshot.get("code"), "model_id": snapshot.get("model_id")},
        "artifact": (
            {
                "id": artifact.id,
                "bytes": artifact.bytes,
                "sha256": artifact.sha256,
                "metrics": json.loads(artifact.metrics_json) if artifact.metrics_json else None,
            }
            if artifact
            else None
        ),
        "review": (
            {
                "revision": latest.revision,
                "verdict": latest.verdict,
                "is_pass": is_pass(latest),
                "pass_blockers": pass_blockers(latest),
            }
            if latest
            else None
        ),
    }


def _save_review(
    db: Session,
    operator: Operator,
    generation: Generation,
    *,
    scores: dict[str, int],
    score_mobile: int | None,
    mobile_checked: bool,
    tags: list[str],
    verdict: str,
    comment: str,
    work_seconds: int,
) -> Review:
    if verdict not in VERDICTS or verdict == "unreviewed":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "判定の値が不正です")
    for value in tags:
        if value not in DEFECT_TAGS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "重大不具合タグの値が不正です")
    for name, value in scores.items():
        if not 1 <= value <= 5:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{name} は1〜5で入力してください")
    if score_mobile is not None and not 1 <= score_mobile <= 5:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "スマホ表示操作性は1〜5で入力してください")
    if not mobile_checked:
        # スマホ未確認は未確認のまま残す（仕様第10章）
        score_mobile = None

    previous = db.scalars(
        select(Review)
        .where(Review.generation_id == generation.id, Review.reviewer_id == operator.id)
        .order_by(Review.revision.desc())
    ).first()
    # 評価改訂は履歴追加。前の評価を書き換えない（仕様第9章）
    review = Review(
        generation_id=generation.id,
        reviewer_id=operator.id,
        revision=(previous.revision + 1) if previous else 1,
        score_fidelity=scores["score_fidelity"],
        score_shape=scores["score_shape"],
        score_color=scores["score_color"],
        score_appeal=scores["score_appeal"],
        score_mobile=score_mobile,
        mobile_checked=mobile_checked,
        defect_tags_json=json.dumps(tags, ensure_ascii=False),
        verdict=verdict,
        comment=comment.strip(),
        work_seconds=max(0, work_seconds),
        was_blind=False,  # ブラインド評価は A4 で実装する
        is_calibration=False,
    )
    db.add(review)
    db.flush()
    audit.record(
        db,
        operator=operator,
        target_kind="generation",
        target_id=generation.id,
        action="review",
        after={"revision": review.revision, "verdict": verdict},
    )
    return review


@router.post("/generations/{generation_id}/reviews", dependencies=[Depends(csrf_protect)])
def create_review_form(
    generation_id: str,
    score_fidelity: int = Form(...),
    score_shape: int = Form(...),
    score_color: int = Form(...),
    score_appeal: int = Form(...),
    score_mobile: str = Form(""),
    mobile_checked: str = Form(""),
    defect_tags: list[str] = Form(default=[]),
    verdict: str = Form(...),
    comment: str = Form(""),
    work_seconds: int = Form(0),
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    generation = db.get(Generation, generation_id)
    if generation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "生成が見つかりません")
    _save_review(
        db,
        operator,
        generation,
        scores={
            "score_fidelity": score_fidelity,
            "score_shape": score_shape,
            "score_color": score_color,
            "score_appeal": score_appeal,
        },
        score_mobile=int(score_mobile) if score_mobile.strip() else None,
        mobile_checked=bool(mobile_checked),
        tags=defect_tags,
        verdict=verdict,
        comment=comment,
        work_seconds=work_seconds,
    )
    return RedirectResponse(f"/generations/{generation_id}", status_code=303)


@router.post(
    "/api/generations/{generation_id}/reviews",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(csrf_protect)],
)
def api_create_review(
    generation_id: str,
    payload: dict,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    generation = db.get(Generation, generation_id)
    if generation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "生成が見つかりません")
    try:
        scores = {
            name: int(payload[name])
            for name in ("score_fidelity", "score_shape", "score_color", "score_appeal")
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "評価点が不足しています") from exc
    raw_mobile = payload.get("score_mobile")
    review = _save_review(
        db,
        operator,
        generation,
        scores=scores,
        score_mobile=int(raw_mobile) if raw_mobile not in (None, "") else None,
        mobile_checked=bool(payload.get("mobile_checked")),
        tags=[str(t) for t in (payload.get("defect_tags") or [])],
        verdict=str(payload.get("verdict", "")),
        comment=str(payload.get("comment", "")),
        work_seconds=int(payload.get("work_seconds", 0) or 0),
    )
    return {"id": review.id, "revision": review.revision, "is_pass": is_pass(review)}


@router.get("/generations/{generation_id}")
def detail_page(
    generation_id: str,
    request: Request,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    generation = db.get(Generation, generation_id)
    if generation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "生成が見つかりません")
    asset = db.get(Asset, generation.asset_id)
    variant = db.get(AssetVariant, generation.variant_id)
    artifact = generation_service.glb_artifact(db, generation.id)
    reviews = db.scalars(
        select(Review)
        .where(Review.generation_id == generation.id)
        .order_by(Review.created_at.asc())
    ).all()
    latest = reviews[-1] if reviews else None
    metrics = json.loads(artifact.metrics_json) if artifact and artifact.metrics_json else {}

    return render(
        request,
        "generation_detail.html",
        {
            "operator": operator,
            "generation": generation,
            "asset": asset,
            "variant": variant,
            "artifact": artifact,
            "metrics": metrics,
            "snapshot": generation_service.preset_snapshot(generation),
            "reviews": reviews,
            "latest_review": latest,
            "latest_defect_tags": defect_tags(latest) if latest else [],
            "pass_blockers": pass_blockers(latest) if latest else [],
        },
    )
