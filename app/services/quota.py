"""送信枠（仕様第9章）。

- 初期値は検証セット内の asset×provider で合計2回
- 枠の追加は理由付きの監査操作のみ
- provider_failed と未送信取消は返却、submission_unknown は保持
- 通信の再確認・再ダウンロードは新しい試行に数えない（新しい generation を作らないため）
- 加工画像・プリセットの変更で上限をリセットしない（数えるのは asset×provider のみ）
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Generation, Operator, QuotaGrant
from app.services import audit


class QuotaExceeded(Exception):
    """送信枠が残っていない。"""


@dataclass(frozen=True)
class QuotaState:
    limit: int
    used: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


def state(db: Session, *, experiment_id: str, asset_id: str, provider: str) -> QuotaState:
    base = get_settings().default_quota_per_asset_provider
    granted = db.scalar(
        select(func.coalesce(func.sum(QuotaGrant.additional), 0)).where(
            QuotaGrant.experiment_id == experiment_id,
            QuotaGrant.asset_id == asset_id,
            QuotaGrant.provider == provider,
        )
    )
    used = db.scalar(
        select(func.count(Generation.id)).where(
            Generation.experiment_id == experiment_id,
            Generation.asset_id == asset_id,
            Generation.provider == provider,
            Generation.consumes_quota.is_(True),
        )
    )
    return QuotaState(limit=base + int(granted or 0), used=int(used or 0))


def require_available(
    db: Session, *, experiment_id: str, asset_id: str, provider: str, needed: int = 1
) -> QuotaState:
    current = state(db, experiment_id=experiment_id, asset_id=asset_id, provider=provider)
    if current.remaining < needed:
        raise QuotaExceeded(
            f"送信枠が残っていません（{provider}：上限 {current.limit} 回、"
            f"使用 {current.used} 回）"
        )
    return current


def release(db: Session, generation: Generation, *, reason: str) -> None:
    """枠を返却する。二重返却しない。"""
    if not generation.consumes_quota:
        return
    generation.consumes_quota = False
    db.flush()
    audit.record(
        db,
        operator=None,
        target_kind="generation",
        target_id=generation.id,
        action="release_quota",
        reason=reason,
        after={"consumes_quota": False},
    )


def grant(
    db: Session,
    *,
    operator: Operator,
    experiment_id: str,
    asset_id: str,
    provider: str,
    additional: int,
    reason: str,
) -> QuotaGrant:
    """枠を追加する。理由必須、監査ログ必須（仕様第9章）。"""
    if additional < 1:
        raise ValueError("追加する枠は1以上にしてください")
    if not reason.strip():
        raise ValueError("枠を追加する理由を入力してください")
    row = QuotaGrant(
        experiment_id=experiment_id,
        asset_id=asset_id,
        provider=provider,
        additional=additional,
        reason=reason.strip(),
        granted_by=operator.id,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        operator=operator,
        target_kind="quota_grant",
        target_id=row.id,
        action="grant_quota",
        reason=reason.strip(),
        after={"asset_id": asset_id, "provider": provider, "additional": additional},
    )
    return row
