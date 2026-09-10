"""検証セット（仕様第6.2章・第7章）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import csrf_protect, current_operator
from app.config import get_settings
from app.db import db_session
from app.models import Asset, Experiment, Generation, Operator
from app.models.enums import TECH_STATUSES, VERDICTS
from app.services import audit
from app.services.cost_guard import parse_usd_to_micro
from app.services.review_aggregate import latest_reviews, summarize_experiment
from app.templating import render

router = APIRouter()
USD_MICRO = 1_000_000


TRACKS = ("standard", "early_check")


def _create(
    db: Session,
    operator: Operator,
    *,
    name: str,
    purpose_note: str,
    cost_cap_micro_usd: int | None,
    reference_rate: int | None,
    hourly_wage: int | None,
    is_live: bool = False,
    track: str = "standard",
) -> Experiment:
    """検証セットを作る。

    **実APIのセットは作成時にも止める。** 生成時にも `LIVE_API_ENABLED` と
    上限額は確認されるが（`app/services/generation.py`・`app/services/cost_guard.py`）、
    作ってから生成で弾かれるより、作る時点で理由を出したほうが分かりやすい。
    """
    if not name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "名称を入力してください")
    if track not in TRACKS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "集計区分の指定が不正です")

    settings = get_settings()
    if is_live and not settings.live_api_enabled:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "実APIの検証セットは作れません。実API生成が無効です"
            "（環境変数 APP_LIVE_API_ENABLED=true が要ります）",
        )
    # 仕様第11章：上限額が無いまま実API生成はできない
    if is_live and cost_cap_micro_usd is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "実APIの検証セットには上限額（USD）が要ります（仕様第11章）",
        )

    experiment = Experiment(
        name=name.strip(),
        purpose_note=purpose_note.strip(),
        # 金額は整数 micro-USD で保持する（仕様第9章）
        cost_cap_micro_usd=cost_cap_micro_usd,
        reference_rate_jpy_per_usd=reference_rate,
        hourly_wage_jpy=hourly_wage,
        is_live=is_live,
        track=track,
        created_by=operator.id,
    )
    db.add(experiment)
    db.flush()
    audit.record(
        db,
        operator=operator,
        target_kind="experiment",
        target_id=experiment.id,
        action="create",
        after={
            "name": experiment.name,
            "cost_cap_micro_usd": experiment.cost_cap_micro_usd,
            "is_live": experiment.is_live,
            "track": experiment.track,
        },
    )
    return experiment


@router.get("/")
def list_page(
    request: Request,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    experiments = db.scalars(select(Experiment).order_by(Experiment.created_at.desc())).all()
    rows = []
    for experiment in experiments:
        asset_count = db.scalar(
            select(func.count(Asset.id)).where(Asset.experiment_id == experiment.id)
        )
        rows.append(
            {
                "experiment": experiment,
                "asset_count": asset_count or 0,
                "summary": summarize_experiment(db, experiment.id),
            }
        )
    settings = get_settings()
    return render(
        request,
        "experiments.html",
        {
            "operator": operator,
            "experiments": rows,
            "live_api_enabled": settings.live_api_enabled,
            "global_cost_cap_usd": settings.global_cost_cap_usd,
        },
    )


def _as_int(value: str) -> int | None:
    value = value.strip()
    return int(value) if value else None


def _as_cap_micro(value: str) -> int | None:
    """上限額のUSD文字列を micro-USD の整数にする。**浮動小数点を経由しない**（仕様第9章）。

    `$1.50` のような端数を受け取れる必要がある（A3.5 の見積は $1.50）。
    """
    try:
        return parse_usd_to_micro(value)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/experiments", dependencies=[Depends(csrf_protect)])
def create_from_form(
    name: str = Form(...),
    purpose_note: str = Form(""),
    cost_cap_usd: str = Form(""),
    reference_rate: str = Form(""),
    hourly_wage: str = Form(""),
    mode: str = Form("mock"),
    track: str = Form("standard"),
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    experiment = _create(
        db,
        operator,
        name=name,
        purpose_note=purpose_note,
        cost_cap_micro_usd=_as_cap_micro(cost_cap_usd),
        reference_rate=_as_int(reference_rate),
        hourly_wage=_as_int(hourly_wage),
        is_live=(mode == "live"),
        track=track,
    )
    return RedirectResponse(f"/experiments/{experiment.id}", status_code=303)


@router.get("/experiments/{experiment_id}")
def detail_page(
    experiment_id: str,
    request: Request,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "検証セットが見つかりません")

    assets = db.scalars(
        select(Asset).where(Asset.experiment_id == experiment_id).order_by(Asset.created_at.asc())
    ).all()
    rows = []
    for asset in assets:
        generations = db.scalars(
            select(Generation)
            .where(Generation.asset_id == asset.id)
            .order_by(Generation.created_at.desc())
        ).all()
        latest = generations[0] if generations else None
        status_label = "—"
        if latest is not None:
            reviews = latest_reviews(db, [latest.id])
            verdict = reviews[latest.id].verdict if latest.id in reviews else "unreviewed"
            status_label = (
                f"{TECH_STATUSES.get(latest.tech_status, latest.tech_status)}"
                f"／{VERDICTS.get(verdict, verdict)}"
            )
        rows.append(
            {
                "asset": asset,
                "variant_count": len(asset.variants),
                "generation_count": len(generations),
                "status_label": status_label,
            }
        )
    return render(
        request,
        "experiment_detail.html",
        {
            "operator": operator,
            "experiment": experiment,
            "assets": rows,
            "summary": summarize_experiment(db, experiment_id),
            "list_poll_seconds": get_settings().list_poll_seconds,
        },
    )


# --- JSON API（仕様第7章） ---------------------------------------------------


@router.get("/api/experiments")
def api_list(operator: Operator = Depends(current_operator), db: Session = Depends(db_session)):
    experiments = db.scalars(select(Experiment).order_by(Experiment.created_at.desc())).all()
    return [
        {
            "id": e.id,
            "name": e.name,
            "is_live": e.is_live,
            "track": e.track,
            "cost_cap_micro_usd": e.cost_cap_micro_usd,
            "created_at": e.created_at.isoformat(),
        }
        for e in experiments
    ]


@router.post(
    "/api/experiments", status_code=status.HTTP_201_CREATED, dependencies=[Depends(csrf_protect)]
)
def api_create(
    payload: dict,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    experiment = _create(
        db,
        operator,
        name=str(payload.get("name", "")),
        purpose_note=str(payload.get("purpose_note", "")),
        cost_cap_micro_usd=_as_cap_micro(str(payload.get("cost_cap_usd") or "")),
        reference_rate=payload.get("reference_rate"),
        hourly_wage=payload.get("hourly_wage"),
        is_live=bool(payload.get("is_live", False)),
        track=str(payload.get("track", "standard")),
    )
    return {"id": experiment.id}


@router.get("/api/experiments/{experiment_id}")
def api_detail(
    experiment_id: str,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "検証セットが見つかりません")
    summary = summarize_experiment(db, experiment_id)
    return {
        "id": experiment.id,
        "name": experiment.name,
        "is_live": experiment.is_live,
        "track": experiment.track,
        "summary": {
            "generations_total": summary.generations_total,
            "in_progress": summary.in_progress,
            "ready_for_review": summary.ready_for_review,
            "unreviewed": summary.unreviewed,
            "reviewed": summary.reviewed,
            "passed": summary.passed,
            "failed_technical": summary.failed_technical,
            "is_provisional": summary.is_provisional,
            "estimate_micro_usd": summary.estimate_micro_usd,
            "confirmed_micro_usd": summary.confirmed_micro_usd,
            "unreconciled_micro_usd": summary.unreconciled_micro_usd,
            # 合格0件のときは null（「算出不可」）。0 と区別する（仕様第9章）
            "cost_per_passed_micro_usd": summary.cost_per_passed_micro_usd,
        },
    }
