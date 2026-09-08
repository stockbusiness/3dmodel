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
from app.services.review_aggregate import latest_reviews, summarize_experiment
from app.templating import render

router = APIRouter()
USD_MICRO = 1_000_000


def _create(
    db: Session,
    operator: Operator,
    *,
    name: str,
    purpose_note: str,
    cost_cap_usd: int | None,
    reference_rate: int | None,
    hourly_wage: int | None,
) -> Experiment:
    if not name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "名称を入力してください")
    experiment = Experiment(
        name=name.strip(),
        purpose_note=purpose_note.strip(),
        # 金額は整数 micro-USD で保持する（仕様第9章）
        cost_cap_micro_usd=cost_cap_usd * USD_MICRO if cost_cap_usd is not None else None,
        reference_rate_jpy_per_usd=reference_rate,
        hourly_wage_jpy=hourly_wage,
        is_live=False,  # 実API生成は A3 以降。A1は必ずモック
        track="standard",
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
        after={"name": experiment.name, "cost_cap_micro_usd": experiment.cost_cap_micro_usd},
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
    return render(request, "experiments.html", {"operator": operator, "experiments": rows})


@router.post("/experiments", dependencies=[Depends(csrf_protect)])
def create_from_form(
    name: str = Form(...),
    purpose_note: str = Form(""),
    cost_cap_usd: str = Form(""),
    reference_rate: str = Form(""),
    hourly_wage: str = Form(""),
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    def as_int(value: str) -> int | None:
        value = value.strip()
        return int(value) if value else None

    experiment = _create(
        db,
        operator,
        name=name,
        purpose_note=purpose_note,
        cost_cap_usd=as_int(cost_cap_usd),
        reference_rate=as_int(reference_rate),
        hourly_wage=as_int(hourly_wage),
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
        cost_cap_usd=payload.get("cost_cap_usd"),
        reference_rate=payload.get("reference_rate"),
        hourly_wage=payload.get("hourly_wage"),
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
