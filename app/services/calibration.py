"""評価の校正（仕様第6.6章・第10章）。

- セット内の最初のN件（既定は設定値。講師1名運用のため0）を校正対象とする
- 校正対象は、講師2名がそれぞれ評価するまで集計に含めない
- 2名の評価が2段階以上離れた軸を警告する
- 集計には平均ではなく、運営が指定した1名分を使う。指定がなければ集計から除外する
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Experiment, Generation, Review
from app.models.enums import REVIEW_AXES

# 2名の差がこれ以上ある軸を警告する（仕様第10章）
WARN_DIFFERENCE = 2


def target_count(experiment: Experiment) -> int:
    if experiment.calibration_target_count is not None:
        return max(0, experiment.calibration_target_count)
    return max(0, get_settings().calibration_target_count)


def target_generation_ids(db: Session, experiment: Experiment) -> list[str]:
    """校正対象の生成ID。セット内の作成順で最初のN件。"""
    count = target_count(experiment)
    if count == 0:
        return []
    return list(
        db.scalars(
            select(Generation.id)
            .where(Generation.experiment_id == experiment.id)
            .order_by(Generation.created_at.asc())
            .limit(count)
        ).all()
    )


def _latest_per_reviewer(db: Session, generation_id: str) -> dict[str, Review]:
    rows = db.scalars(
        select(Review)
        .where(Review.generation_id == generation_id)
        .order_by(Review.created_at.asc(), Review.revision.asc())
    ).all()
    latest: dict[str, Review] = {}
    for review in rows:
        latest[review.reviewer_id] = review
    return latest


@dataclass
class AxisDifference:
    axis: str
    label: str
    scores: list[int]
    difference: int


@dataclass
class CalibrationEntry:
    generation_id: str
    reviewer_count: int
    complete: bool
    differences: list[AxisDifference] = field(default_factory=list)

    @property
    def has_warning(self) -> bool:
        return bool(self.differences)


@dataclass
class CalibrationState:
    target_count: int
    entries: list[CalibrationEntry] = field(default_factory=list)
    chosen_reviewer_id: str | None = None

    @property
    def in_use(self) -> bool:
        return self.target_count > 0

    @property
    def warning_count(self) -> int:
        return sum(1 for entry in self.entries if entry.has_warning)

    @property
    def incomplete_count(self) -> int:
        return sum(1 for entry in self.entries if not entry.complete)

    def excluded_generation_ids(self) -> set[str]:
        """集計から外す生成ID。

        2名そろっていない校正対象、または採用する評価者が未指定の校正対象は除外する。
        """
        if not self.in_use:
            return set()
        excluded = {entry.generation_id for entry in self.entries if not entry.complete}
        if self.chosen_reviewer_id is None:
            excluded |= {entry.generation_id for entry in self.entries}
        return excluded


def state(db: Session, experiment: Experiment) -> CalibrationState:
    result = CalibrationState(
        target_count=target_count(experiment),
        chosen_reviewer_id=experiment.calibration_reviewer_id,
    )
    for generation_id in target_generation_ids(db, experiment):
        reviews = _latest_per_reviewer(db, generation_id)
        entry = CalibrationEntry(
            generation_id=generation_id,
            reviewer_count=len(reviews),
            complete=len(reviews) >= 2,
        )
        if entry.complete:
            entry.differences = _differences(list(reviews.values()))
        result.entries.append(entry)
    return result


def _differences(reviews: list[Review]) -> list[AxisDifference]:
    """軸ごとに、評価者間の最大差が2以上のものを返す。"""
    found: list[AxisDifference] = []
    for axis, label in REVIEW_AXES.items():
        scores = [getattr(review, axis) for review in reviews]
        present = [score for score in scores if score is not None]
        if len(present) < 2:
            continue
        difference = max(present) - min(present)
        if difference >= WARN_DIFFERENCE:
            found.append(
                AxisDifference(axis=axis, label=label, scores=present, difference=difference)
            )
    return found


def review_for_aggregation(
    db: Session, generation_id: str, chosen_reviewer_id: str | None
) -> Review | None:
    """校正対象の集計に使う評価。指定された評価者の分だけを使う。"""
    if chosen_reviewer_id is None:
        return None
    reviews = _latest_per_reviewer(db, generation_id)
    return reviews.get(chosen_reviewer_id)
