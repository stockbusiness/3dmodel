"""ブラインド評価（仕様第6.5章・第7章・第9章）。

- 比較の各結果は「A」「B」として表示する。A/Bの割当は比較ごとに固定して保存済み（A2）
- 評価が確定するまで、画面・HTML・APIレスポンス・ファイル名・URLに
  サービス名・モデルID・プリセット名を含めない
- 評価確定後、その比較についてのみ開示する
- 運営は手動で開示できる（監査ログ必須）
- 単社生成の画面では通常どおり表示してよい
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Comparison, Generation, Operator, Review, utcnow
from app.services import audit

# ブラインド中に伏せる値の代わりに使う表示
MASKED = "（評価確定まで非表示）"


def is_revealed(comparison: Comparison | None) -> bool:
    if comparison is None:
        return True
    if not comparison.is_blind:
        return True
    return comparison.revealed_at is not None


def should_hide(comparison: Comparison | None) -> bool:
    """この比較のサービス名等をまだ伏せるべきか。"""
    return comparison is not None and comparison.is_blind and comparison.revealed_at is None


def comparison_of(db: Session, generation: Generation) -> Comparison | None:
    if generation.comparison_id is None:
        return None
    return db.get(Comparison, generation.comparison_id)


def label_for(generation: Generation) -> str:
    """ブラインド中の表示名。単社生成なら空。"""
    return generation.blind_label or ""


def reveal(
    db: Session,
    comparison: Comparison,
    *,
    operator: Operator | None,
    reason: str,
    automatic: bool,
) -> Comparison:
    """開示する。二重に開示しない。監査ログを残す（仕様第7章）。"""
    if comparison.revealed_at is not None:
        return comparison
    comparison.revealed_at = utcnow()
    comparison.revealed_by = operator.id if operator else None
    db.flush()
    audit.record(
        db,
        operator=operator,
        target_kind="comparison",
        target_id=comparison.id,
        action="reveal_automatic" if automatic else "reveal_manual",
        reason=reason,
        after={"revealed_at": comparison.revealed_at.isoformat()},
    )
    return comparison


def maybe_reveal_after_review(db: Session, generation: Generation) -> Comparison | None:
    """比較に含まれる全ての生成が評価されたら開示する（仕様第6.5章）。

    技術的に失敗して評価できない生成は、評価済みとみなす
    （いつまでも開示できないままにしない）。
    """
    comparison = comparison_of(db, generation)
    if comparison is None or not should_hide(comparison):
        return comparison

    members = db.scalars(select(Generation).where(Generation.comparison_id == comparison.id)).all()
    for member in members:
        if member.tech_status != "ready_for_review":
            # 評価に至らなかった生成は待たない
            continue
        has_review = db.scalar(select(Review.id).where(Review.generation_id == member.id).limit(1))
        if has_review is None:
            return comparison

    return reveal(
        db,
        comparison,
        operator=None,
        reason="比較に含まれる全ての結果の評価が確定したため自動で開示",
        automatic=True,
    )


def public_generation(db: Session, generation: Generation, snapshot: dict) -> dict:
    """APIと画面に渡してよい範囲へ落とす。

    ブラインド中は provider / model_id / プリセット名・コード・外部タスクIDを含めない。
    """
    comparison = comparison_of(db, generation)
    hidden = should_hide(comparison)
    return {
        "blind": hidden,
        "label": generation.blind_label,
        "provider": None if hidden else generation.provider,
        "preset_code": None if hidden else snapshot.get("code"),
        "preset_name": None if hidden else snapshot.get("display_name"),
        "model_id": None if hidden else snapshot.get("model_id"),
        "provider_task_id": None if hidden else generation.provider_task_id,
    }
