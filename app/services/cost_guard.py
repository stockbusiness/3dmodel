"""上限額の判定（仕様第11章、フェーズA縮小版）。

判定式：
    送信済み全件（失敗・要照合を含む）の上限見積の合計 ＋ 新規依頼の上限見積 ＞ 上限額
    → 受付拒否

- 見積は常に上限側を採り、保守的に倒す
- 上限を見積もれないプリセットは実行不可
- 比較は2社分を合算して同一トランザクションで判定する
- 予約／確定／解除のライフサイクルは持たない（フェーズBで再設計）

セット上限額は設定されていれば実/モックを問わず効く（モックでの検証を可能にするため）。
全体上限額は実API生成にのみ効く。モックは実費が発生しないため（ASSUMPTION A-23）。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CostEntry, Experiment, Generation

USD_MICRO = 1_000_000


class CostCapExceeded(Exception):
    """上限額を超えるため受付できない。"""


def parse_usd_to_micro(value: str | None) -> int | None:
    """設定値のUSD文字列を micro-USD の整数にする。浮動小数点を経由しない。"""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    negative = text.startswith("-")
    if negative:
        raise ValueError("上限額に負の値は指定できません")
    whole, _, fraction = text.partition(".")
    fraction = (fraction + "000000")[:6]
    if not whole.isdigit() or not fraction.isdigit():
        raise ValueError(f"上限額の形式が不正です: {value}")
    return int(whole) * USD_MICRO + int(fraction)


def global_cap_micro_usd() -> int | None:
    return parse_usd_to_micro(get_settings().global_cost_cap_usd)


def _committed(db: Session, *, experiment_id: str | None, live_only: bool) -> int:
    """送信済み全件の上限見積の合計。

    未送信のまま取り消したもの（外部へ出ていない）は含めない。
    失敗・要照合は含める（課金されている可能性を否定できないため）。
    """
    query = (
        select(func.coalesce(func.sum(CostEntry.amount_micro_usd), 0))
        .select_from(CostEntry)
        .join(Generation, Generation.id == CostEntry.generation_id)
        .where(
            CostEntry.kind == "estimate",
            ~((Generation.tech_status == "cancelled") & (Generation.provider_task_id.is_(None))),
        )
    )
    if experiment_id is not None:
        query = query.where(Generation.experiment_id == experiment_id)
    if live_only:
        query = query.where(Generation.is_live.is_(True))
    return int(db.scalar(query) or 0)


@dataclass(frozen=True)
class CapState:
    cap_micro_usd: int | None
    committed_micro_usd: int

    @property
    def remaining_micro_usd(self) -> int | None:
        if self.cap_micro_usd is None:
            return None
        return self.cap_micro_usd - self.committed_micro_usd


def experiment_state(db: Session, experiment: Experiment) -> CapState:
    return CapState(
        cap_micro_usd=experiment.cost_cap_micro_usd,
        committed_micro_usd=_committed(db, experiment_id=experiment.id, live_only=False),
    )


def global_state(db: Session) -> CapState:
    return CapState(
        cap_micro_usd=global_cap_micro_usd(),
        committed_micro_usd=_committed(db, experiment_id=None, live_only=True),
    )


def _format(micro_usd: int) -> str:
    return f"${micro_usd // USD_MICRO}.{micro_usd % USD_MICRO // 10_000:02d}"


def require_headroom(db: Session, experiment: Experiment, additional_micro_usd: int) -> None:
    """新規依頼の上限見積を加えても上限額を超えないことを確かめる。

    比較のときは2社分を合算した額を渡す（片方だけ通すことがないように）。
    """
    if experiment.is_live and experiment.cost_cap_micro_usd is None:
        raise CostCapExceeded("この検証セットに上限額が設定されていません")

    per_set = experiment_state(db, experiment)
    if per_set.cap_micro_usd is not None:
        if per_set.committed_micro_usd + additional_micro_usd > per_set.cap_micro_usd:
            raise CostCapExceeded(
                "セットの上限額を超えます"
                f"（上限 {_format(per_set.cap_micro_usd)}、"
                f"送信済み見積 {_format(per_set.committed_micro_usd)}、"
                f"今回 {_format(additional_micro_usd)}）"
            )

    if not experiment.is_live:
        return

    overall = global_state(db)
    if overall.cap_micro_usd is None:
        raise CostCapExceeded("全体の上限額が設定されていません（APP_GLOBAL_COST_CAP_USD）")
    if overall.committed_micro_usd + additional_micro_usd > overall.cap_micro_usd:
        raise CostCapExceeded(
            "全体の上限額を超えます"
            f"（上限 {_format(overall.cap_micro_usd)}、"
            f"送信済み見積 {_format(overall.committed_micro_usd)}、"
            f"今回 {_format(additional_micro_usd)}）"
        )
