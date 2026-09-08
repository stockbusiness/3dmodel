"""生成受付・復帰操作・評価（仕様第7章・第8章・第10章）。

受付は冪等キー必須。実際の外部送信はワーカーが行う。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import csrf_protect, current_operator, require_admin_action
from app.db import begin_immediate, db_session
from app.models import (
    Asset,
    AssetVariant,
    Comparison,
    CostEntry,
    Experiment,
    Generation,
    Operator,
    Review,
    utcnow,
)
from app.models.enums import DEFECT_TAGS, PURPOSES, VERDICTS
from app.services import audit, blind, calibration, cost_guard, idempotency, quota
from app.services import generation as generation_service
from app.services.review_aggregate import defect_tags, is_pass, pass_blockers
from app.templating import render

router = APIRouter()

# 生成そのものの失敗と、通信・保存の問題を区別して説明する（仕様第6.4章）
STATUS_EXPLANATIONS = {
    "queued": "受け付けました。ワーカーが順に外部へ送ります。",
    "submitting": "外部へ送信しています。",
    "running": "事業者側で生成中です。",
    "downloading": "成果物を受け取っています。",
    "submission_unknown": (
        "外部に届いたかどうかを確認できていません。生成の失敗とは限りません。"
        "自動での再送信は行いません。"
    ),
    "download_failed": (
        "生成は終わっていますが、ファイルの受け取りに失敗しました。"
        "保存だけ再試行できます（課金なし）。"
    ),
    "validation_failed": (
        "受け取ったファイルが検査に通りませんでした。まず保存だけ再試行してください（課金なし）。"
    ),
    "monitoring_paused": (
        "30分を過ぎても完了しません。外部の失敗とは断定していません。"
        "再確認で監視を再開できます。"
    ),
    "provider_failed": "事業者側で生成に失敗しました。送信枠は返却されています。",
    "cancelled": "中止しました。",
    "ready_for_review": "",
}


def _elapsed_label(generation: Generation) -> str:
    started = generation.submitted_at or generation.created_at
    if started is None:
        return "—"
    seconds = int((utcnow() - started).total_seconds())
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {remainder} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 時間 {minutes} 分"


IDEMPOTENT_OPERATIONS = ("create_generation", "create_comparison", "retry_generation")


def _idempotency_key(request: Request, form_value: str | None = None) -> str:
    key = request.headers.get("idempotency-key") or form_value
    if not key:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Idempotency-Key が必要です。画面を読み込み直してからやり直してください",
        )
    return key


def _run_idempotent(
    db: Session,
    *,
    operator: Operator,
    operation: str,
    key: str,
    body: dict,
    factory,
):
    """同キー・同本文は既存結果、同キー・別本文は409（仕様第7章）。

    冪等キーの確保から依頼登録までを1つの短期の書込トランザクションで行う。
    """
    begin_immediate(db)
    try:
        claim = idempotency.claim(db, operator=operator, operation=operation, key=key, body=body)
    except idempotency.IdempotencyConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except idempotency.InvalidIdempotencyKey as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    if not claim.is_new:
        return claim.record.result_id, False

    try:
        result_id = factory()
    except generation_service.GenerationRejected as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except quota.QuotaExceeded as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except cost_guard.CostCapExceeded as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    claim.record.result_id = result_id
    db.flush()
    return result_id, True


@router.get("/api/form-token")
def api_form_token(operator: Operator = Depends(current_operator)):
    """APIから受付する前に取得する、サーバー発行の冪等キー（仕様第6.3章）。"""
    return {"idempotency_key": idempotency.issue_key()}


# --- 受付 -------------------------------------------------------------------


@router.post("/generations", dependencies=[Depends(csrf_protect)])
def create_from_form(
    request: Request,
    variant_id: str = Form(...),
    preset_id: str = Form(...),
    purpose: str = Form("benchmark"),
    idempotency_key: str = Form(...),
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    if purpose not in PURPOSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "目的の値が不正です")
    body = {"variant_id": variant_id, "preset_id": preset_id, "purpose": purpose}
    generation_id, _ = _run_idempotent(
        db,
        operator=operator,
        operation="create_generation",
        key=_idempotency_key(request, idempotency_key),
        body=body,
        factory=lambda: generation_service.create_generation(
            db, operator=operator, variant_id=variant_id, preset_id=preset_id, purpose=purpose
        ).id,
    )
    return RedirectResponse(f"/generations/{generation_id}", status_code=303)


@router.post(
    "/api/generations", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(csrf_protect)]
)
def api_create(
    request: Request,
    payload: dict,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """単社生成受付、202応答（仕様第7章）。

    provider は preset から確定する。任意の外部エンドポイントは受け取らない。
    """
    variant_id = payload.get("asset_variant_id")
    preset_id = payload.get("preset_id")
    if not variant_id or not preset_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "asset_variant_id と preset_id は必須です")
    purpose = str(payload.get("purpose", "benchmark"))
    if purpose not in PURPOSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "目的の値が不正です")

    body = {
        "variant_id": str(variant_id),
        "preset_id": str(preset_id),
        "purpose": purpose,
        "parent_generation_id": payload.get("parent_generation_id"),
    }
    generation_id, _ = _run_idempotent(
        db,
        operator=operator,
        operation="create_generation",
        key=_idempotency_key(request),
        body=body,
        factory=lambda: generation_service.create_generation(
            db,
            operator=operator,
            variant_id=str(variant_id),
            preset_id=str(preset_id),
            purpose=purpose,
        ).id,
    )
    generation = db.get(Generation, generation_id)
    return {"id": generation_id, "tech_status": generation.tech_status if generation else None}


@router.post("/comparisons", dependencies=[Depends(csrf_protect)])
def create_comparison_from_form(
    request: Request,
    variant_id: str = Form(...),
    preset_a: str = Form(...),
    preset_b: str = Form(...),
    purpose: str = Form("benchmark"),
    idempotency_key: str = Form(...),
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    if purpose not in PURPOSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "目的の値が不正です")
    body = {"variant_id": variant_id, "preset_ids": [preset_a, preset_b], "purpose": purpose}

    def factory() -> str:
        comparison, _ = generation_service.create_comparison(
            db,
            operator=operator,
            variant_id=variant_id,
            preset_ids=[preset_a, preset_b],
            purpose=purpose,
        )
        return comparison.id

    comparison_id, _ = _run_idempotent(
        db,
        operator=operator,
        operation="create_comparison",
        key=_idempotency_key(request, idempotency_key),
        body=body,
        factory=factory,
    )
    return RedirectResponse(f"/comparisons/{comparison_id}", status_code=303)


@router.post(
    "/api/comparisons", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(csrf_protect)]
)
def api_create_comparison(
    request: Request,
    payload: dict,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """同一入力の2社比較をまとめて受付、202応答（仕様第7章）。

    2社分の上限見積を合算して判定する。片方の失敗で他方を破棄しない。
    """
    variant_id = payload.get("asset_variant_id")
    preset_ids = payload.get("preset_ids")
    if not variant_id or not isinstance(preset_ids, list) or len(preset_ids) != 2:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "asset_variant_id と2つの preset_ids が必要です"
        )
    purpose = str(payload.get("purpose", "benchmark"))
    if purpose not in PURPOSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "目的の値が不正です")

    body = {
        "variant_id": str(variant_id),
        "preset_ids": [str(p) for p in preset_ids],
        "purpose": purpose,
    }

    def factory() -> str:
        comparison, _ = generation_service.create_comparison(
            db,
            operator=operator,
            variant_id=str(variant_id),
            preset_ids=[str(p) for p in preset_ids],
            purpose=purpose,
        )
        return comparison.id

    comparison_id, _ = _run_idempotent(
        db,
        operator=operator,
        operation="create_comparison",
        key=_idempotency_key(request),
        body=body,
        factory=factory,
    )
    generations = db.scalars(
        select(Generation).where(Generation.comparison_id == comparison_id)
    ).all()
    comparison = db.get(Comparison, comparison_id)
    return {
        "id": comparison_id,
        "is_blind": comparison.is_blind if comparison else True,
        "generations": [
            {
                "id": g.id,
                "tech_status": g.tech_status,
                # ブラインド中はサービス名を返さない（開示の実装は A4）
                "label": g.blind_label,
            }
            for g in generations
        ],
    }


@router.post(
    "/api/generations/{generation_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(csrf_protect)],
)
def api_retry(
    generation_id: str,
    request: Request,
    payload: dict,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """品質再生成。新IDを作り、理由は必須（仕様第7章）。前の結果は残す。"""
    parent = db.get(Generation, generation_id)
    if parent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "生成が見つかりません")
    reason = str(payload.get("reason", ""))
    if not reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "再生成の理由は必須です")
    preset_id = str(payload.get("preset_id") or parent.preset_id)
    variant_id = str(payload.get("asset_variant_id") or parent.variant_id)

    body = {
        "parent": generation_id,
        "preset_id": preset_id,
        "variant_id": variant_id,
        "reason": reason,
    }
    new_id, _ = _run_idempotent(
        db,
        operator=operator,
        operation="retry_generation",
        key=_idempotency_key(request),
        body=body,
        factory=lambda: generation_service.create_generation(
            db,
            operator=operator,
            variant_id=variant_id,
            preset_id=preset_id,
            purpose=parent.purpose,
            parent_generation_id=generation_id,
            retry_reason=reason,
        ).id,
    )
    return {"id": new_id, "parent_id": generation_id}


# --- 復帰操作 ---------------------------------------------------------------


def _load(db: Session, generation_id: str) -> Generation:
    generation = db.get(Generation, generation_id)
    if generation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "生成が見つかりません")
    return generation


def _wrap(callable_):
    try:
        return callable_()
    except generation_service.OperationNotAllowed as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/api/generations/{generation_id}/refresh", dependencies=[Depends(csrf_protect)])
def api_refresh(
    generation_id: str,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """状態の再確認をキューに登録する。再生成はしない（追加課金なし）。"""
    generation = _load(db, generation_id)
    _wrap(lambda: generation_service.request_refresh(db, generation, operator=operator))
    return {"id": generation.id, "tech_status": generation.tech_status}


@router.post(
    "/api/generations/{generation_id}/retry-download", dependencies=[Depends(csrf_protect)]
)
def api_retry_download(
    generation_id: str,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """保存だけを再試行する。外部に新規生成しない（追加課金なし）。"""
    generation = _load(db, generation_id)
    _wrap(lambda: generation_service.request_retry_download(db, generation, operator=operator))
    return {"id": generation.id, "tech_status": generation.tech_status}


@router.post("/api/generations/{generation_id}/cancel", dependencies=[Depends(csrf_protect)])
def api_cancel(
    generation_id: str,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    generation = _load(db, generation_id)
    result = _wrap(lambda: generation_service.request_cancel(db, generation, operator=operator))
    return {"id": generation.id, "tech_status": generation.tech_status, **result}


@router.post(
    "/api/generations/{generation_id}/resolve-submission", dependencies=[Depends(csrf_protect)]
)
def api_resolve_submission(
    generation_id: str,
    payload: dict,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """不明な外部受付の手動照合。証跡・メモ必須（仕様第7章）。"""
    generation = _load(db, generation_id)
    require_admin_action(operator, "受付結果不明の照合")
    _wrap(
        lambda: generation_service.resolve_submission(
            db,
            generation,
            operator=operator,
            outcome=str(payload.get("outcome", "")),
            provider_task_id=payload.get("provider_task_id"),
            evidence=str(payload.get("evidence", "")),
        )
    )
    return {"id": generation.id, "tech_status": generation.tech_status}


@router.post(
    "/api/generations/{generation_id}/resolve-artifact", dependencies=[Depends(csrf_protect)]
)
def api_resolve_artifact(
    generation_id: str,
    payload: dict,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """再取得しても検査NGが続くとき、運営が事業者側の失敗と確定する（仕様第8章）。"""
    generation = _load(db, generation_id)
    require_admin_action(operator, "成果物の照合")
    _wrap(
        lambda: generation_service.resolve_artifact(
            db, generation, operator=operator, evidence=str(payload.get("evidence", ""))
        )
    )
    return {"id": generation.id, "tech_status": generation.tech_status}


@router.post("/api/assets/{asset_id}/quota", dependencies=[Depends(csrf_protect)])
def api_grant_quota(
    asset_id: str,
    payload: dict,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """送信枠の追加。理由必須、監査ログ必須（仕様第9章）。"""
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "題材が見つかりません")
    require_admin_action(operator, "送信枠の追加")
    try:
        grant = quota.grant(
            db,
            operator=operator,
            experiment_id=asset.experiment_id,
            asset_id=asset.id,
            provider=str(payload.get("provider", "")),
            additional=int(payload.get("additional", 0)),
            reason=str(payload.get("reason", "")),
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    state = quota.state(
        db, experiment_id=asset.experiment_id, asset_id=asset.id, provider=grant.provider
    )
    return {"id": grant.id, "limit": state.limit, "used": state.used}


@router.post("/api/generations/{generation_id}/costs", dependencies=[Depends(csrf_protect)])
def api_record_cost(
    generation_id: str,
    payload: dict,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """手動確認した実費の記録（仕様第11章）。

    見積と実績は別の行として保存し、混ぜない。監査ログ必須。
    重複して送っても二重計上しないよう一意キーを持つ（試験13）。
    画面からの入力は A4 で用意する。
    """
    generation = _load(db, generation_id)
    require_admin_action(operator, "実費の記録")

    evidence = str(payload.get("evidence_note", "")).strip()
    if not evidence:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "証跡・メモを入力してください")
    try:
        amount = int(payload.get("amount_micro_usd"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "金額は micro-USD の整数で指定してください"
        ) from exc
    if amount < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "金額に負の値は指定できません")

    reference = str(payload.get("reference", "")).strip()
    if not reference:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "事業者側の明細を識別する reference を入力してください（二重計上を防ぐため）",
        )
    dedupe_key = f"confirmed:{generation.id}:{reference}"

    existing = db.scalar(select(CostEntry).where(CostEntry.dedupe_key == dedupe_key))
    if existing is not None:
        # 同じ明細の再送。既存の行をそのまま返す（二重計上しない）
        return {
            "id": existing.id,
            "amount_micro_usd": existing.amount_micro_usd,
            "duplicated": True,
        }

    entry = CostEntry(
        generation_id=generation.id,
        kind="confirmed_manual",
        amount_micro_usd=amount,
        credits=payload.get("credits"),
        currency="USD",
        price_version=str(payload.get("price_version", "")),
        evidence_note=evidence,
        evidence_checked_on=payload.get("checked_on"),
        recorded_by=operator.id,
        dedupe_key=dedupe_key,
    )
    db.add(entry)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        again = db.scalar(select(CostEntry).where(CostEntry.dedupe_key == dedupe_key))
        if again is None:
            raise
        return {"id": again.id, "amount_micro_usd": again.amount_micro_usd, "duplicated": True}

    audit.record(
        db,
        operator=operator,
        target_kind="generation",
        target_id=generation.id,
        action="record_cost",
        reason=evidence,
        after={"kind": "confirmed_manual", "amount_micro_usd": amount, "reference": reference},
    )
    return {"id": entry.id, "amount_micro_usd": amount, "duplicated": False}


# --- 参照 -------------------------------------------------------------------


@router.get("/api/generations/{generation_id}")
def api_detail(
    generation_id: str,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    generation = _load(db, generation_id)
    artifact = generation_service.glb_artifact(db, generation.id)
    reviews = db.scalars(
        select(Review)
        .where(Review.generation_id == generation.id)
        .order_by(Review.created_at.asc())
    ).all()
    latest = reviews[-1] if reviews else None
    snapshot = generation_service.preset_snapshot(generation)
    # ブラインド中はサービス名・モデルID・プリセット名・外部タスクIDを返さない
    public = blind.public_generation(db, generation, snapshot)
    return {
        "id": generation.id,
        "tech_status": generation.tech_status,
        "error_kind": generation.error_kind,
        "error_note": generation.error_note,
        "blind": public["blind"],
        "label": public["label"],
        "provider": public["provider"],
        "is_live": generation.is_live,
        "attempt_index": generation.attempt_index,
        "parent_id": generation.parent_id,
        "comparison_id": generation.comparison_id,
        "consumes_quota": generation.consumes_quota,
        "progress_percent": generation.progress_percent,
        "provider_task_id": public["provider_task_id"],
        "submitted_at": generation.submitted_at.isoformat() if generation.submitted_at else None,
        "completed_at": generation.completed_at.isoformat() if generation.completed_at else None,
        "last_checked_at": (
            generation.last_checked_at.isoformat() if generation.last_checked_at else None
        ),
        "preset": {"code": public["preset_code"], "model_id": public["model_id"]},
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


@router.post("/api/comparisons/{comparison_id}/reveal", dependencies=[Depends(csrf_protect)])
def api_reveal_comparison(
    comparison_id: str,
    payload: dict,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """運営による手動開示（評価未確定でも可、監査ログ必須。仕様第7章）。"""
    comparison = db.get(Comparison, comparison_id)
    if comparison is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "比較が見つかりません")
    require_admin_action(operator, "比較の開示")
    reason = str(payload.get("reason", "")).strip()
    if not reason:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "開示の理由を入力してください（評価前の開示は結果の見方に影響します）",
        )
    blind.reveal(db, comparison, operator=operator, reason=reason, automatic=False)
    return {"id": comparison.id, "revealed": True}


@router.get("/api/comparisons/{comparison_id}")
def api_comparison(
    comparison_id: str,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    comparison = db.get(Comparison, comparison_id)
    if comparison is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "比較が見つかりません")
    members = db.scalars(
        select(Generation)
        .where(Generation.comparison_id == comparison.id)
        .order_by(Generation.blind_label.asc())
    ).all()
    revealed = blind.is_revealed(comparison)
    return {
        "id": comparison.id,
        "is_blind": comparison.is_blind,
        "revealed": revealed,
        "revealed_at": comparison.revealed_at.isoformat() if comparison.revealed_at else None,
        "generations": [
            {
                "id": member.id,
                "label": member.blind_label,
                "tech_status": member.tech_status,
                **{
                    key: value
                    for key, value in blind.public_generation(
                        db, member, generation_service.preset_snapshot(member)
                    ).items()
                    if key in ("provider", "preset_code", "model_id")
                },
            }
            for member in members
        ],
    }


# --- 評価 -------------------------------------------------------------------


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

    comparison = blind.comparison_of(db, generation)
    # ブラインド中に行った評価かどうかを記録する。非ブラインドは集計で区別する
    was_blind = blind.should_hide(comparison)
    experiment = db.get(Experiment, generation.experiment_id)
    is_calibration_target = generation.id in set(calibration.target_generation_ids(db, experiment))

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
        was_blind=was_blind,
        is_calibration=is_calibration_target,
    )
    db.add(review)
    db.flush()
    audit.record(
        db,
        operator=operator,
        target_kind="generation",
        target_id=generation.id,
        action="review",
        after={
            "revision": review.revision,
            "verdict": verdict,
            "was_blind": was_blind,
            "is_calibration": is_calibration_target,
        },
    )
    # 比較に含まれる全ての結果の評価が確定したら開示する（仕様第6.5章）
    blind.maybe_reveal_after_review(db, generation)
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
    generation = _load(db, generation_id)
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
    generation = _load(db, generation_id)
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
    generation = _load(db, generation_id)
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
    snapshot = generation_service.preset_snapshot(generation)
    public = blind.public_generation(db, generation, snapshot)
    comparison = blind.comparison_of(db, generation)

    return render(
        request,
        "generation_detail.html",
        {
            "operator": operator,
            "generation": generation,
            "public": public,
            "comparison": comparison,
            "asset": asset,
            "variant": variant,
            "artifact": artifact,
            "metrics": metrics,
            "snapshot": snapshot,
            "reviews": reviews,
            "latest_review": latest,
            "latest_defect_tags": defect_tags(latest) if latest else [],
            "pass_blockers": pass_blockers(latest) if latest else [],
            "in_progress": generation.tech_status
            in ("queued", "submitting", "running", "downloading"),
            "status_explanation": STATUS_EXPLANATIONS.get(generation.tech_status, ""),
            "elapsed_label": _elapsed_label(generation),
        },
    )
