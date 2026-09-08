"""集計・判定表・費用・校正・品質比較画面（仕様第6.5章・第6.6章・第10章・第11章）。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import csrf_protect, current_operator, require_admin_action
from app.config import get_settings
from app.db import db_session
from app.models import Asset, AssetVariant, Comparison, Experiment, Generation, Operator
from app.services import audit, blind, calibration, cost_guard, verdict_table
from app.services import generation as generation_service
from app.services.review_aggregate import (
    defect_tags,
    format_micro_usd,
    is_pass,
    latest_reviews,
    pass_blockers,
    summarize_experiment,
)
from app.templating import render

router = APIRouter()


def _experiment(db: Session, experiment_id: str) -> Experiment:
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "検証セットが見つかりません")
    return experiment


# --- 集計・判定表 ------------------------------------------------------------


@router.get("/experiments/{experiment_id}/report")
def report_page(
    experiment_id: str,
    request: Request,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    experiment = _experiment(db, experiment_id)
    settings = get_settings()
    report = verdict_table.build_report(db, experiment)
    calibration_state = calibration.state(db, experiment)

    reviewers = db.scalars(
        select(Operator).where(Operator.is_active.is_(True)).order_by(Operator.login_name.asc())
    ).all()

    return render(
        request,
        "report.html",
        {
            "operator": operator,
            "experiment": experiment,
            "summary": summarize_experiment(db, experiment_id),
            "report": report,
            "calibration": calibration_state,
            "reviewers": reviewers,
            "cap_state": cost_guard.experiment_state(db, experiment),
            "overrun_micro_usd": cost_guard.overrun(db, experiment),
            "confirmed_micro_usd": cost_guard.confirmed_total(db, experiment_id),
            "thresholds": {
                "min_samples": settings.verdict_min_samples,
                "adopt_within_two": settings.verdict_adopt_within_two_percent,
                "adopt_first": settings.verdict_adopt_first_percent,
                "hold_within_two": settings.verdict_hold_within_two_percent,
                "unsuitable_defect": settings.verdict_unsuitable_defect_percent,
            },
            "targets": {
                "first": settings.target_first_pass_percent,
                "within_two": settings.target_within_two_pass_percent,
                "work_seconds": settings.target_work_seconds,
            },
        },
    )


@router.get("/api/experiments/{experiment_id}/report")
def api_report(
    experiment_id: str,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    experiment = _experiment(db, experiment_id)
    report = verdict_table.build_report(db, experiment)
    calibration_state = calibration.state(db, experiment)
    return {
        "experiment": {
            "id": experiment.id,
            "name": experiment.name,
            # 集計で混ぜない区分（仕様第13章 試験12）
            "is_live": experiment.is_live,
            "track": experiment.track,
        },
        "cells": [
            {
                "provider": cell.provider,
                "subject_tag": cell.subject_tag,
                "first_sent_assets": cell.first_sent_assets,
                "tech_completed": cell.tech_completed,
                "reviewed": cell.reviewed,
                "unreviewed": cell.unreviewed,
                "first_pass_assets": cell.first_pass_assets,
                "within_two_pass_assets": cell.within_two_pass_assets,
                "tech_failed_unreviewed_assets": cell.tech_failed_unreviewed_assets,
                "dropped_assets": cell.dropped_assets,
                "first_pass_percent": cell.first_pass_percent,
                "quality_only_first_pass_percent": cell.quality_only_first_pass_percent,
                "within_two_pass_percent": cell.within_two_pass_percent,
                "defect_percent": cell.defect_percent,
                "verdict": cell.verdict,
                "is_provisional": cell.is_provisional,
            }
            for cell in report.cells
        ],
        "timings": {
            "p50_seconds": report.timings.p50,
            "p95_seconds": report.timings.p95,
            "completed": report.timings.completed,
            "stopped": report.timings.stopped,
        },
        "reviews": {
            "blind": report.blind_reviews,
            "non_blind": report.non_blind_reviews,
        },
        "save_success_percent": report.save_success_percent,
        "work_seconds": report.work_seconds,
        "calibration": {
            "target_count": calibration_state.target_count,
            "incomplete": calibration_state.incomplete_count,
            "warnings": calibration_state.warning_count,
            "chosen_reviewer_id": calibration_state.chosen_reviewer_id,
            "excluded": report.excluded_calibration,
        },
    }


@router.post("/api/experiments/{experiment_id}/calibration", dependencies=[Depends(csrf_protect)])
def api_set_calibration(
    experiment_id: str,
    payload: dict,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """校正の対象件数と、集計に使う評価者を設定する（仕様第10章）。"""
    experiment = _experiment(db, experiment_id)
    require_admin_action(operator, "校正設定の変更")

    before = {
        "calibration_target_count": experiment.calibration_target_count,
        "calibration_reviewer_id": experiment.calibration_reviewer_id,
    }

    if "target_count" in payload:
        raw = payload.get("target_count")
        try:
            count = int(raw) if raw not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "件数の値が不正です") from exc
        if count is not None and count < 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "件数は0以上にしてください")
        experiment.calibration_target_count = count

    if "reviewer_id" in payload:
        reviewer_id = payload.get("reviewer_id") or None
        if reviewer_id is not None and db.get(Operator, str(reviewer_id)) is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "評価者が見つかりません")
        experiment.calibration_reviewer_id = reviewer_id

    db.flush()
    audit.record(
        db,
        operator=operator,
        target_kind="experiment",
        target_id=experiment.id,
        action="update_calibration",
        reason=str(payload.get("reason", "")),
        before=before,
        after={
            "calibration_target_count": experiment.calibration_target_count,
            "calibration_reviewer_id": experiment.calibration_reviewer_id,
        },
    )
    return {
        "target_count": experiment.calibration_target_count,
        "reviewer_id": experiment.calibration_reviewer_id,
    }


# --- 品質比較画面 ------------------------------------------------------------


@router.get("/comparisons/{comparison_id}")
def comparison_page(
    comparison_id: str,
    request: Request,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """元画像と2つの結果を並べて見る画面（仕様第6.5章）。

    ブラインド中は「A」「B」だけを見せ、サービス名・モデルID・プリセット名を出さない。
    """
    comparison = db.get(Comparison, comparison_id)
    if comparison is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "比較が見つかりません")

    variant = db.get(AssetVariant, comparison.variant_id)
    asset = db.get(Asset, variant.asset_id) if variant else None
    members = db.scalars(
        select(Generation)
        .where(Generation.comparison_id == comparison.id)
        .order_by(Generation.blind_label.asc())
    ).all()
    reviews = latest_reviews(db, [m.id for m in members])
    revealed = blind.is_revealed(comparison)

    entries = []
    for member in members:
        snapshot = generation_service.preset_snapshot(member)
        artifact = generation_service.glb_artifact(db, member.id)
        review = reviews.get(member.id)
        metrics = json.loads(artifact.metrics_json) if artifact and artifact.metrics_json else {}
        entries.append(
            {
                "generation": member,
                "label": member.blind_label or "—",
                "artifact": artifact,
                "review": review,
                "is_pass": is_pass(review) if review else False,
                "pass_blockers": pass_blockers(review) if review else [],
                "defect_tags": defect_tags(review) if review else [],
                # ブラインド中は伏せる
                "public": blind.public_generation(db, member, snapshot),
                # 形状メトリクスは評価に影響しないよう、開示後に折りたたみで見せる
                "metrics": metrics if revealed else {},
            }
        )

    return render(
        request,
        "comparison_detail.html",
        {
            "operator": operator,
            "comparison": comparison,
            "experiment": db.get(Experiment, comparison.experiment_id),
            "asset": asset,
            "variant": variant,
            "entries": entries,
            "revealed": revealed,
            "all_reviewed": all(e["review"] is not None for e in entries) if entries else False,
        },
    )


@router.get("/experiments/{experiment_id}/comparisons")
def api_list_comparisons(
    experiment_id: str,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    _experiment(db, experiment_id)
    comparisons = db.scalars(
        select(Comparison)
        .where(Comparison.experiment_id == experiment_id)
        .order_by(Comparison.created_at.desc())
    ).all()
    return [
        {
            "id": comparison.id,
            "is_blind": comparison.is_blind,
            "revealed": blind.is_revealed(comparison),
            "created_at": comparison.created_at.isoformat(),
        }
        for comparison in comparisons
    ]


# --- 費用（手動実績の一覧） --------------------------------------------------


@router.get("/api/experiments/{experiment_id}/costs")
def api_costs(
    experiment_id: str,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """見積・手動確認済み実績・失敗要照合を、混ぜずに返す（仕様第11章）。"""
    from app.models import CostEntry

    experiment = _experiment(db, experiment_id)
    rows = db.execute(
        select(CostEntry, Generation)
        .join(Generation, Generation.id == CostEntry.generation_id)
        .where(Generation.experiment_id == experiment_id)
        .order_by(CostEntry.created_at.asc())
    ).all()
    cap_state = cost_guard.experiment_state(db, experiment)
    return {
        "cap_micro_usd": experiment.cost_cap_micro_usd,
        "estimate_micro_usd": cap_state.committed_micro_usd,
        "confirmed_micro_usd": cost_guard.confirmed_total(db, experiment_id),
        "remaining_micro_usd": cap_state.remaining_micro_usd,
        "overrun_micro_usd": cost_guard.overrun(db, experiment),
        "entries": [
            {
                "id": entry.id,
                "generation_id": entry.generation_id,
                "kind": entry.kind,
                "amount": format_micro_usd(entry.amount_micro_usd),
                "amount_micro_usd": entry.amount_micro_usd,
                "evidence_note": entry.evidence_note,
                "checked_on": entry.evidence_checked_on,
            }
            for entry, _ in rows
        ],
    }


@router.get("/api/experiments/{experiment_id}/reviews")
def api_reviews(
    experiment_id: str,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """評価の一覧。ブラインドと非ブラインドを区別して返す（仕様第13章 試験12）。"""
    _experiment(db, experiment_id)
    generations = db.scalars(
        select(Generation).where(Generation.experiment_id == experiment_id)
    ).all()
    reviews = latest_reviews(db, [g.id for g in generations])
    return [
        {
            "generation_id": generation_id,
            "verdict": review.verdict,
            "is_pass": is_pass(review),
            "was_blind": review.was_blind,
            "is_calibration": review.is_calibration,
            "work_seconds": review.work_seconds,
        }
        for generation_id, review in reviews.items()
    ]


@router.get("/api/experiments/{experiment_id}/generations")
def api_experiment_generations(
    experiment_id: str,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """一覧のポーリング用（仕様第6.4章：一覧は15〜30秒程度）。"""
    _experiment(db, experiment_id)
    generations = db.scalars(
        select(Generation)
        .where(Generation.experiment_id == experiment_id)
        .order_by(Generation.created_at.desc())
    ).all()
    reviews = latest_reviews(db, [g.id for g in generations])
    return [
        {
            "id": generation.id,
            "asset_id": generation.asset_id,
            "tech_status": generation.tech_status,
            "attempt_index": generation.attempt_index,
            # ブラインド中はサービス名を出さない
            "provider": blind.public_generation(db, generation, {})["provider"],
            "label": generation.blind_label,
            "verdict": reviews[generation.id].verdict if generation.id in reviews else "unreviewed",
        }
        for generation in generations
    ]
