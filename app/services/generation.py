"""生成の受付と、状態の復帰操作（仕様第7章・第8章・第11章）。

受付は「上限額判定・送信枠確認・依頼登録」を同一の短期トランザクションで行い、
その中でネットワーク通信をしない。実際の外部送信はワーカーが行う。
"""

from __future__ import annotations

import json
import random
import secrets
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import begin_immediate
from app.models import (
    Artifact,
    AssetVariant,
    Comparison,
    CostEntry,
    Experiment,
    Generation,
    Operator,
    Preset,
    utcnow,
)
from app.providers.registry import get_adapter
from app.services import audit, cost_guard, quota
from app.services.presets import selectable_reasons

# 状態確認の間隔（仕様第8章：初期10秒、最大60秒）
INITIAL_POLL_SECONDS = 10
MAX_POLL_SECONDS = 60
# これを過ぎても完了しなければ monitoring_paused。外部失敗とは断定しない
MONITORING_TIMEOUT_SECONDS = 30 * 60

IN_FLIGHT_STATUSES = ("submitting", "running", "downloading")


class GenerationRejected(Exception):
    """受付できない依頼。理由は利用者に日本語で返す。"""


class OperationNotAllowed(Exception):
    """その状態では行えない操作。"""


def next_poll_delay(current: int, *, retry_after: int | None = None) -> int:
    """指数バックオフ＋ジッター。429 の Retry-After があれば優先する（仕様第8章）。"""
    if retry_after is not None:
        return max(INITIAL_POLL_SECONDS, min(retry_after, MAX_POLL_SECONDS * 5))
    doubled = min(max(current, INITIAL_POLL_SECONDS) * 2, MAX_POLL_SECONDS)
    jitter = random.randint(0, max(1, doubled // 4))
    return min(doubled + jitter, MAX_POLL_SECONDS + MAX_POLL_SECONDS // 4)


def seconds_from_now(seconds: int):
    from datetime import timedelta

    return utcnow() + timedelta(seconds=seconds)


# --- 受付 -------------------------------------------------------------------


def check_can_submit(variant: AssetVariant, preset: Preset, *, live: bool) -> None:
    """受付前の検査。UIの非活性だけに頼らずサーバー側でも拒否する（仕様第12章）。"""
    settings = get_settings()
    asset = variant.asset

    if live and not settings.live_api_enabled:
        raise GenerationRejected("実API生成は無効です（LIVE_API_ENABLED=false）")
    if live and asset.consent_status == "missing":
        raise GenerationRejected("利用同意が未取得の画像は外部送信できません")

    reasons = selectable_reasons(preset, live=live)
    if reasons:
        raise GenerationRejected("／".join(reasons))

    from app.providers.registry import is_mock

    if not live and not is_mock(preset.provider):
        raise GenerationRejected("モード（モック）と選んだサービスが一致しません")
    if live and is_mock(preset.provider):
        raise GenerationRejected("モックのプリセットでは実生成できません")


@dataclass
class PlannedGeneration:
    variant: AssetVariant
    preset: Preset
    estimate_micro_usd: int
    estimate_note: str
    estimate_price_version: str
    estimate_credits: int | None


def _plan(db: Session, variant_id: str, preset_id: str, *, live: bool) -> PlannedGeneration:
    variant = db.get(AssetVariant, variant_id)
    if variant is None:
        raise GenerationRejected("画像が見つかりません")
    preset = db.get(Preset, preset_id)
    if preset is None:
        raise GenerationRejected("プリセットが見つかりません")

    check_can_submit(variant, preset, live=live)

    # 見積は価格表から求める。ここで外部通信はしない
    estimate = get_adapter(preset.provider).estimate(variant, preset)
    if not estimate.is_bounded:
        raise GenerationRejected("上限額を見積もれないプリセットは実行できません")
    return PlannedGeneration(
        variant=variant,
        preset=preset,
        estimate_micro_usd=estimate.max_micro_usd,
        estimate_note=estimate.note,
        estimate_price_version=estimate.price_version,
        estimate_credits=estimate.credits,
    )


def _insert(
    db: Session,
    *,
    operator: Operator,
    plan: PlannedGeneration,
    experiment: Experiment,
    purpose: str,
    comparison: Comparison | None = None,
    parent: Generation | None = None,
    blind_label: str | None = None,
) -> Generation:
    adapter = get_adapter(plan.preset.provider)
    generation = Generation(
        experiment_id=experiment.id,
        comparison_id=comparison.id if comparison else None,
        variant_id=plan.variant.id,
        asset_id=plan.variant.asset_id,
        preset_id=plan.preset.id,
        preset_snapshot_json=adapter.preset_snapshot(plan.preset),
        provider=plan.preset.provider,
        parent_id=parent.id if parent else None,
        attempt_index=(parent.attempt_index + 1) if parent else 1,
        purpose=purpose,
        is_live=experiment.is_live,
        tech_status="queued",
        next_check_at=utcnow(),
        poll_interval_seconds=INITIAL_POLL_SECONDS,
        consumes_quota=True,
        blind_label=blind_label,
        created_by=operator.id,
    )
    db.add(generation)
    db.flush()
    db.add(
        CostEntry(
            generation_id=generation.id,
            kind="estimate",
            amount_micro_usd=plan.estimate_micro_usd,
            credits=plan.estimate_credits,
            currency="USD",
            price_version=plan.estimate_price_version,
            evidence_note=plan.estimate_note,
            recorded_by=operator.id,
            dedupe_key=f"estimate:{generation.id}",
        )
    )
    db.flush()
    audit.record(
        db,
        operator=operator,
        target_kind="generation",
        target_id=generation.id,
        action="create",
        after={
            "provider": generation.provider,
            "is_live": generation.is_live,
            "estimate_micro_usd": plan.estimate_micro_usd,
        },
    )
    return generation


def create_generation(
    db: Session,
    *,
    operator: Operator,
    variant_id: str,
    preset_id: str,
    purpose: str = "benchmark",
    parent_generation_id: str | None = None,
    retry_reason: str = "",
) -> Generation:
    """単社生成の受付。

    上限額判定・送信枠確認・依頼登録を同一の短期トランザクションで行う（仕様第7章）。
    """
    # 書込トランザクションを先に取り、同時受付が直列化されるようにする
    begin_immediate(db)

    parent = db.get(Generation, parent_generation_id) if parent_generation_id else None
    if parent_generation_id and parent is None:
        raise GenerationRejected("再生成のもとになる生成が見つかりません")
    if parent is not None and not retry_reason.strip():
        raise GenerationRejected("再生成の理由を入力してください")

    variant = db.get(AssetVariant, variant_id)
    if variant is None:
        raise GenerationRejected("画像が見つかりません")
    experiment = variant.asset.experiment

    plan = _plan(db, variant_id, preset_id, live=experiment.is_live)
    cost_guard.require_headroom(db, experiment, plan.estimate_micro_usd)
    quota.require_available(
        db,
        experiment_id=experiment.id,
        asset_id=variant.asset_id,
        provider=plan.preset.provider,
    )

    generation = _insert(
        db,
        operator=operator,
        plan=plan,
        experiment=experiment,
        purpose=purpose,
        parent=parent,
    )
    if parent is not None:
        audit.record(
            db,
            operator=operator,
            target_kind="generation",
            target_id=generation.id,
            action="retry",
            reason=retry_reason.strip(),
            before={"parent_id": parent.id, "parent_status": parent.tech_status},
        )
    return generation


def create_comparison(
    db: Session,
    *,
    operator: Operator,
    variant_id: str,
    preset_ids: list[str],
    purpose: str = "benchmark",
    is_blind: bool = True,
) -> tuple[Comparison, list[Generation]]:
    """2社比較の受付（仕様第7章）。

    同じ加工版に対して2社分をまとめて受け付ける。
    2社分の上限見積を合算して上限額を判定し、片方だけ通ることがないようにする。
    """
    begin_immediate(db)

    if len(preset_ids) != 2:
        raise GenerationRejected("比較にはプリセットを2つ指定してください")

    variant = db.get(AssetVariant, variant_id)
    if variant is None:
        raise GenerationRejected("画像が見つかりません")
    experiment = variant.asset.experiment

    plans = [_plan(db, variant_id, preset_id, live=experiment.is_live) for preset_id in preset_ids]
    providers = [plan.preset.provider for plan in plans]
    if providers[0] == providers[1]:
        raise GenerationRejected("比較には異なるサービスのプリセットを指定してください")

    # 2社分を合算して判定する。片方分しかなければ両方とも受け付けない
    cost_guard.require_headroom(db, experiment, sum(p.estimate_micro_usd for p in plans))
    for plan in plans:
        quota.require_available(
            db,
            experiment_id=experiment.id,
            asset_id=variant.asset_id,
            provider=plan.preset.provider,
        )

    comparison = Comparison(
        experiment_id=experiment.id,
        variant_id=variant.id,
        purpose=purpose,
        is_blind=is_blind,
        created_by=operator.id,
    )
    db.add(comparison)
    db.flush()

    # A/B の割当は比較ごとにランダムに固定して保存する（仕様第6.5章）。
    # 開示と画面での秘匿は A4 で実装する
    labels = ["A", "B"]
    if secrets.randbelow(2):
        labels.reverse()

    generations = [
        _insert(
            db,
            operator=operator,
            plan=plan,
            experiment=experiment,
            purpose=purpose,
            comparison=comparison,
            blind_label=label,
        )
        for plan, label in zip(plans, labels, strict=True)
    ]
    audit.record(
        db,
        operator=operator,
        target_kind="comparison",
        target_id=comparison.id,
        action="create",
        after={"providers": providers, "is_blind": is_blind},
    )
    return comparison, generations


# --- 復帰操作（仕様第7章・第8章の状態遷移表） --------------------------------


def request_refresh(db: Session, generation: Generation, *, operator: Operator) -> Generation:
    """状態の再確認をキューに登録する。再生成はしない。新しい試行に数えない。"""
    if generation.tech_status not in ("running", "monitoring_paused"):
        raise OperationNotAllowed("この状態では再確認できません")
    generation.tech_status = "running"
    generation.next_check_at = utcnow()
    generation.poll_interval_seconds = INITIAL_POLL_SECONDS
    generation.lease_owner = None
    generation.lease_until = None
    db.flush()
    audit.record(
        db,
        operator=operator,
        target_kind="generation",
        target_id=generation.id,
        action="refresh",
        after={"tech_status": "running"},
    )
    return generation


def request_retry_download(
    db: Session, generation: Generation, *, operator: Operator
) -> Generation:
    """保存だけをやり直す。外部に新規生成しない。新しい試行に数えない。"""
    if generation.tech_status not in ("download_failed", "validation_failed"):
        raise OperationNotAllowed("この状態では再ダウンロードできません")
    if not generation.provider_result_ref:
        raise OperationNotAllowed("取得先の情報がありません。先に状態の再確認を行ってください")
    generation.tech_status = "downloading"
    generation.error_kind = None
    generation.error_note = ""
    generation.next_check_at = utcnow()
    generation.lease_owner = None
    generation.lease_until = None
    db.flush()
    audit.record(
        db,
        operator=operator,
        target_kind="generation",
        target_id=generation.id,
        action="retry_download",
        after={"tech_status": "downloading"},
    )
    return generation


def request_cancel(db: Session, generation: Generation, *, operator: Operator) -> dict:
    """ローカルの中止要望。外部の取消成立とは区別する（仕様第8章）。"""
    begin_immediate(db)
    if generation.tech_status in ("ready_for_review", "cancelled"):
        raise OperationNotAllowed("この状態では中止できません")

    generation.cancel_requested_at = utcnow()

    if generation.tech_status == "queued" and generation.provider_task_id is None:
        # 外部へ出ていないことを確かめて取り消す。枠は返す
        generation.tech_status = "cancelled"
        generation.next_check_at = None
        quota.release(db, generation, reason="未送信のまま中止した")
        db.flush()
        audit.record(
            db,
            operator=operator,
            target_kind="generation",
            target_id=generation.id,
            action="cancel",
            after={"tech_status": "cancelled", "external": "not_submitted"},
        )
        return {"cancelled": True, "external": "not_submitted"}

    # 送信後。外部の取消可否を確かめる。費用の返却は保証しない
    adapter = get_adapter(generation.provider)
    if not adapter.supports_cancel or generation.provider_task_id is None:
        db.flush()
        audit.record(
            db,
            operator=operator,
            target_kind="generation",
            target_id=generation.id,
            action="cancel_requested",
            after={"external": "unsupported"},
        )
        return {"cancelled": False, "external": "unsupported"}

    task_id = generation.provider_task_id
    db.commit()  # ここでロックを手放してから外部通信する

    from app.providers.base import ProviderError, UnsupportedOperation

    try:
        adapter.cancel(task_id)
    except (ProviderError, UnsupportedOperation) as exc:
        audit.record(
            db,
            operator=operator,
            target_kind="generation",
            target_id=generation.id,
            action="cancel_failed",
            reason=str(exc),
            after={"external": "not_cancelled"},
        )
        db.flush()
        # 未取消のタスクは状態・費用の照合を続ける
        return {"cancelled": False, "external": "not_cancelled", "detail": str(exc)}

    generation.tech_status = "cancelled"
    generation.next_check_at = None
    db.flush()
    audit.record(
        db,
        operator=operator,
        target_kind="generation",
        target_id=generation.id,
        action="cancel",
        after={"tech_status": "cancelled", "external": "cancelled"},
    )
    # 送信後の取消は枠を保持する（仕様第8章の状態遷移表）
    return {"cancelled": True, "external": "cancelled"}


def resolve_submission(
    db: Session,
    generation: Generation,
    *,
    operator: Operator,
    outcome: str,
    provider_task_id: str | None,
    evidence: str,
) -> Generation:
    """受付結果不明の手動照合（仕様第7章・第8章）。

    outcome は "link"（事業者側にタスクが存在した）または
    "not_created"（運営が未作成を確認した）。証跡とメモが必須。
    """
    if generation.tech_status != "submission_unknown":
        raise OperationNotAllowed("受付結果不明の生成にだけ行える操作です")
    if not evidence.strip():
        raise OperationNotAllowed("証跡・メモを入力してください")

    if outcome == "link":
        if not provider_task_id or not provider_task_id.strip():
            raise OperationNotAllowed("ひも付ける外部タスクIDを入力してください")
        generation.provider_task_id = provider_task_id.strip()
        generation.tech_status = "running"
        generation.error_kind = None
        generation.error_note = ""
        generation.next_check_at = utcnow()
        generation.poll_interval_seconds = INITIAL_POLL_SECONDS
        # 枠は保持する（送信された可能性があるため）
    elif outcome == "not_created":
        generation.tech_status = "cancelled"
        generation.next_check_at = None
        quota.release(db, generation, reason="運営が未作成を確認した")
    else:
        raise OperationNotAllowed("outcome は link か not_created を指定してください")

    generation.lease_owner = None
    generation.lease_until = None
    db.flush()
    audit.record(
        db,
        operator=operator,
        target_kind="generation",
        target_id=generation.id,
        action="resolve_submission",
        reason=evidence.strip(),
        after={"outcome": outcome, "tech_status": generation.tech_status},
    )
    return generation


def resolve_artifact(
    db: Session, generation: Generation, *, operator: Operator, evidence: str
) -> Generation:
    """再取得しても検査NGが続くとき、運営が provider_failed 相当と確定する（仕様第8章）。"""
    if generation.tech_status != "validation_failed":
        raise OperationNotAllowed("検査で不合格の生成にだけ行える操作です")
    if not evidence.strip():
        raise OperationNotAllowed("証跡・メモを入力してください")

    generation.tech_status = "provider_failed"
    generation.error_kind = "validation_failed"
    generation.next_check_at = None
    quota.release(db, generation, reason="運営が事業者側の失敗と確定した")
    db.flush()
    audit.record(
        db,
        operator=operator,
        target_kind="generation",
        target_id=generation.id,
        action="resolve_artifact",
        reason=evidence.strip(),
        after={"tech_status": "provider_failed"},
    )
    return generation


# --- 参照 -------------------------------------------------------------------


def glb_artifact(db: Session, generation_id: str) -> Artifact | None:
    return db.scalar(
        select(Artifact)
        .where(Artifact.generation_id == generation_id, Artifact.kind == "glb")
        .order_by(Artifact.created_at.desc())
    )


def preset_snapshot(generation: Generation) -> dict:
    try:
        return json.loads(generation.preset_snapshot_json)
    except json.JSONDecodeError:
        return {}
