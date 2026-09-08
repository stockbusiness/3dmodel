"""評価の集計（仕様第10章）。

- 合格は5軸すべて4以上かつ重大不具合なし
- スマホ未確認は合格にできない
- 評価改訂は履歴追加、集計は最新評価を使う（仕様第9章）
- 件数0でも除算エラーにしない（仕様第13章 試験15）。割れない場合は None を返す
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CostEntry, Generation, Review
from app.models.enums import SCORE_PASS_THRESHOLD


def latest_reviews(db: Session, generation_ids: list[str]) -> dict[str, Review]:
    """生成IDごとの最新評価。評価者ごとの最新改訂のうち、最も新しいものを採る。"""
    if not generation_ids:
        return {}
    rows = db.scalars(
        select(Review)
        .where(Review.generation_id.in_(generation_ids))
        .order_by(Review.created_at.asc(), Review.revision.asc())
    ).all()
    latest: dict[str, Review] = {}
    for review in rows:
        latest[review.generation_id] = review
    return latest


def defect_tags(review: Review) -> list[str]:
    try:
        tags = json.loads(review.defect_tags_json or "[]")
    except json.JSONDecodeError:
        return []
    return [t for t in tags if isinstance(t, str)]


def is_pass(review: Review) -> bool:
    """教室利用の合格判定（仕様第10章）。"""
    if review.verdict != "pass":
        return False
    if defect_tags(review):
        return False
    if not review.mobile_checked or review.score_mobile is None:
        return False
    scores = [
        review.score_fidelity,
        review.score_shape,
        review.score_color,
        review.score_appeal,
        review.score_mobile,
    ]
    return all(score is not None and score >= SCORE_PASS_THRESHOLD for score in scores)


def pass_blockers(review: Review) -> list[str]:
    """合格にならない理由を日本語で返す。画面での説明に使う。"""
    reasons: list[str] = []
    if not review.mobile_checked or review.score_mobile is None:
        reasons.append("スマホ表示が未確認です")
    tags = defect_tags(review)
    if tags:
        reasons.append(f"重大不具合タグが{len(tags)}件あります")
    low = [
        label
        for label, score in (
            ("元画像の特徴", review.score_fidelity),
            ("全周の形状", review.score_shape),
            ("色・模様", review.score_color),
            ("魅力・完成度", review.score_appeal),
            ("スマホ表示操作性", review.score_mobile),
        )
        if score is not None and score < SCORE_PASS_THRESHOLD
    ]
    if low:
        reasons.append("4未満の軸：" + "・".join(low))
    return reasons


@dataclass
class ExperimentSummary:
    generations_total: int = 0
    in_progress: int = 0
    ready_for_review: int = 0
    unreviewed: int = 0
    reviewed: int = 0
    passed: int = 0
    failed_technical: int = 0
    estimate_micro_usd: int = 0
    confirmed_micro_usd: int = 0
    unreconciled_micro_usd: int = 0
    work_seconds: int = 0
    # 合格が0件なら「算出不可」（仕様第9章）。None を返し 0 と区別する
    cost_per_passed_micro_usd: int | None = None

    @property
    def is_provisional(self) -> bool:
        """未評価が残っていれば暫定（仕様第10章）。"""
        return self.unreviewed > 0


_IN_PROGRESS = ("queued", "submitting", "running", "downloading")
_TECHNICAL_FAILURE = ("provider_failed", "download_failed", "validation_failed")


def summarize_experiment(db: Session, experiment_id: str) -> ExperimentSummary:
    summary = ExperimentSummary()
    generations = db.scalars(
        select(Generation).where(Generation.experiment_id == experiment_id)
    ).all()
    summary.generations_total = len(generations)
    reviews = latest_reviews(db, [g.id for g in generations])

    for generation in generations:
        if generation.tech_status in _IN_PROGRESS:
            summary.in_progress += 1
        elif generation.tech_status in _TECHNICAL_FAILURE:
            summary.failed_technical += 1
        if generation.tech_status == "ready_for_review":
            summary.ready_for_review += 1
            review = reviews.get(generation.id)
            if review is None:
                summary.unreviewed += 1
            else:
                summary.reviewed += 1
                if is_pass(review):
                    summary.passed += 1
        review = reviews.get(generation.id)
        if review is not None:
            summary.work_seconds += review.work_seconds

    if generations:
        totals = db.execute(
            select(CostEntry.kind, func.sum(CostEntry.amount_micro_usd))
            .where(CostEntry.generation_id.in_([g.id for g in generations]))
            .group_by(CostEntry.kind)
        ).all()
        for kind, total in totals:
            amount = int(total or 0)
            if kind == "estimate":
                summary.estimate_micro_usd = amount
            elif kind == "confirmed_manual":
                summary.confirmed_micro_usd = amount
            elif kind == "failed_unreconciled":
                summary.unreconciled_micro_usd = amount

    if summary.passed > 0:
        summary.cost_per_passed_micro_usd = summary.estimate_micro_usd // summary.passed
    return summary


def format_micro_usd(value: int | None) -> str:
    """micro-USD を表示用の文字列にする。浮動小数点で保持しない（仕様第9章）。"""
    if value is None:
        return "算出不可"
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    return f"{sign}${magnitude // 1_000_000}.{magnitude % 1_000_000 // 10_000:02d}"
