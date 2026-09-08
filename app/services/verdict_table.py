"""集計と判定表（仕様第10章）。

判定はサービス×題材タグの単位で行う。閾値は設定値で、初期値は仮置きである。
最終判断は運営が行うため、ここでは表示に必要な数え上げと自動表示だけを行う。

集計で混ぜないもの（仕様第5章・第10章、第13章 試験12）：
- モックと実API
- 未評価と合格
- ブラインド評価と非ブラインド評価
- 早期確認（A3.5）と通常検証
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Artifact, Asset, Experiment, Generation, Review
from app.models.enums import SUBJECT_TAGS
from app.services import calibration
from app.services.review_aggregate import defect_tags, is_pass, latest_reviews

VERDICT_INSUFFICIENT = "サンプル不足"
VERDICT_CANDIDATE = "採用候補"
VERDICT_HOLD = "保留"
VERDICT_UNSUITABLE = "不適"

_TECHNICAL_FAILURE = ("provider_failed", "download_failed", "validation_failed")
_IN_PROGRESS = ("queued", "submitting", "running", "downloading")


def percent(numerator: int, denominator: int) -> float | None:
    """割合（%）。分母が0なら None（「算出不可」）を返す（仕様第13章 試験15）。"""
    if denominator <= 0:
        return None
    return round(numerator * 100 / denominator, 1)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return round(ordered[low] * (1 - weight) + ordered[high] * weight, 1)


@dataclass
class Cell:
    provider: str
    subject_tag: str

    first_sent_assets: int = 0
    tech_completed: int = 0
    reviewed: int = 0
    unreviewed: int = 0
    first_pass_assets: int = 0
    within_two_pass_assets: int = 0
    tech_failed_unreviewed_assets: int = 0
    dropped_assets: int = 0
    defect_reviews: int = 0

    @property
    def subject_label(self) -> str:
        return SUBJECT_TAGS.get(self.subject_tag, self.subject_tag)

    @property
    def first_pass_percent(self) -> float | None:
        """初回合格率。技術失敗を除外しない（仕様第10章）。"""
        return percent(self.first_pass_assets, self.first_sent_assets)

    @property
    def quality_only_first_pass_percent(self) -> float | None:
        """品質のみ初回合格率。技術失敗で評価に至らなかった題材を分母から除く。"""
        return percent(
            self.first_pass_assets,
            self.first_sent_assets - self.tech_failed_unreviewed_assets,
        )

    @property
    def within_two_pass_percent(self) -> float | None:
        return percent(self.within_two_pass_assets, self.first_sent_assets)

    @property
    def defect_percent(self) -> float | None:
        return percent(self.defect_reviews, self.reviewed)

    @property
    def is_provisional(self) -> bool:
        return self.unreviewed > 0

    @property
    def verdict(self) -> str:
        settings = get_settings()
        if self.first_sent_assets < settings.verdict_min_samples:
            return VERDICT_INSUFFICIENT

        defect = self.defect_percent
        if defect is not None and defect >= settings.verdict_unsuitable_defect_percent:
            return VERDICT_UNSUITABLE

        within_two = self.within_two_pass_percent
        first = self.first_pass_percent
        if within_two is None:
            return VERDICT_INSUFFICIENT
        if (
            within_two >= settings.verdict_adopt_within_two_percent
            and first is not None
            and first >= settings.verdict_adopt_first_percent
        ):
            return VERDICT_CANDIDATE
        if within_two >= settings.verdict_hold_within_two_percent:
            return VERDICT_HOLD
        return VERDICT_UNSUITABLE


@dataclass
class Timings:
    """受付から評価待ちになるまでの時間（秒）。"""

    p50: float | None = None
    p95: float | None = None
    completed: int = 0
    stopped: int = 0


@dataclass
class Report:
    cells: list[Cell] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    subject_tags: list[str] = field(default_factory=list)
    timings: Timings = field(default_factory=Timings)

    # 混ぜないための区分（仕様第13章 試験12）
    blind_reviews: int = 0
    non_blind_reviews: int = 0
    artifacts_expected: int = 0
    artifacts_saved: int = 0
    work_seconds: int = 0
    excluded_calibration: int = 0

    @property
    def save_success_percent(self) -> float | None:
        return percent(self.artifacts_saved, self.artifacts_expected)

    def cell(self, provider: str, subject_tag: str) -> Cell | None:
        for item in self.cells:
            if item.provider == provider and item.subject_tag == subject_tag:
                return item
        return None

    def candidate_tags(self, provider: str) -> list[str]:
        return [
            cell.subject_label
            for cell in self.cells
            if cell.provider == provider and cell.verdict == VERDICT_CANDIDATE
        ]


def build_report(db: Session, experiment: Experiment) -> Report:
    """1つの検証セットの集計を組み立てる。

    セットは実/モックと集計区分（通常検証／早期確認）で分かれているため、
    ここでセットをまたいで混ぜることはない。
    """
    report = Report()

    generations = db.scalars(
        select(Generation)
        .where(Generation.experiment_id == experiment.id)
        .order_by(Generation.created_at.asc())
    ).all()
    if not generations:
        return report

    calibration_state = calibration.state(db, experiment)
    excluded = calibration_state.excluded_generation_ids()
    report.excluded_calibration = len(excluded)

    reviews = latest_reviews(db, [g.id for g in generations])
    assets = {
        asset.id: asset
        for asset in db.scalars(select(Asset).where(Asset.experiment_id == experiment.id)).all()
    }

    # 保存成功率：評価待ちになるべきだった件数のうち、成果物が残っている件数
    for generation in generations:
        if generation.tech_status in ("ready_for_review", "download_failed", "validation_failed"):
            report.artifacts_expected += 1
    saved = db.scalars(
        select(Artifact.generation_id).where(
            Artifact.generation_id.in_([g.id for g in generations]), Artifact.kind == "glb"
        )
    ).all()
    report.artifacts_saved = len(set(saved))

    # 受付〜評価待ちまでの時間
    durations: list[float] = []
    for generation in generations:
        if generation.tech_status == "ready_for_review" and generation.completed_at:
            durations.append((generation.completed_at - generation.created_at).total_seconds())
            report.timings.completed += 1
        elif generation.tech_status in ("monitoring_paused", "submission_unknown"):
            report.timings.stopped += 1
    report.timings.p50 = _percentile(durations, 0.5)
    report.timings.p95 = _percentile(durations, 0.95)

    # ブラインド／非ブラインドの別と作業時間
    for review in reviews.values():
        if review.was_blind:
            report.blind_reviews += 1
        else:
            report.non_blind_reviews += 1
        report.work_seconds += review.work_seconds

    # 題材×サービスで数える。初回は parent_id が無い試行
    cells: dict[tuple[str, str], Cell] = {}
    by_asset_provider: dict[tuple[str, str], list[Generation]] = {}
    for generation in generations:
        by_asset_provider.setdefault((generation.asset_id, generation.provider), []).append(
            generation
        )

    calibration_ids = {entry.generation_id for entry in calibration_state.entries}

    def usable_review(generation: Generation) -> Review | None:
        """集計に使ってよい評価。校正対象は運営が指定した1名分だけを使う。"""
        if generation.id in excluded:
            return None
        if generation.id in calibration_ids:
            return calibration.review_for_aggregation(
                db, generation.id, calibration_state.chosen_reviewer_id
            )
        return reviews.get(generation.id)

    for (asset_id, provider), attempts in by_asset_provider.items():
        asset = assets.get(asset_id)
        if asset is None:
            continue
        first = next((g for g in attempts if g.parent_id is None), None)
        if first is None:
            continue

        key = (provider, asset.subject_tag)
        cell = cells.setdefault(key, Cell(provider=provider, subject_tag=asset.subject_tag))
        cell.first_sent_assets += 1

        first_review = usable_review(first)
        if first.tech_status == "ready_for_review":
            cell.tech_completed += 1
            if first_review is None:
                cell.unreviewed += 1
            else:
                cell.reviewed += 1
                if defect_tags(first_review):
                    cell.defect_reviews += 1
                if is_pass(first_review):
                    cell.first_pass_assets += 1
        elif first.tech_status in _TECHNICAL_FAILURE:
            # 技術失敗で評価に至らなかった題材（品質のみ初回合格率の分母から除く）
            cell.tech_failed_unreviewed_assets += 1

        passed_within_two = False
        pending = False
        for attempt in attempts:
            review = usable_review(attempt)
            if review is not None and is_pass(review):
                passed_within_two = True
            if attempt.tech_status in _IN_PROGRESS or (
                attempt.tech_status == "ready_for_review" and review is None
            ):
                pending = True
        if passed_within_two:
            cell.within_two_pass_assets += 1
        elif not pending:
            cell.dropped_assets += 1

    report.cells = sorted(cells.values(), key=lambda c: (c.provider, c.subject_tag))
    report.providers = sorted({cell.provider for cell in report.cells})
    report.subject_tags = sorted(
        {cell.subject_tag for cell in report.cells},
        key=lambda tag: list(SUBJECT_TAGS).index(tag) if tag in SUBJECT_TAGS else 99,
    )
    return report
