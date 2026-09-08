"""ワーカーの処理単位（仕様第8章「ワーカーの処理単位」）。

- 「状態確認」と「ダウンロード」を別の処理単位にする。
  1ループで状態確認は全対象を順に行い、ダウンロードは1件だけ行う
- 外部通信のあいだDBのトランザクションを持たない。
  claim（短期トランザクション）→ 通信 → 結果の書込（短期トランザクション）の順にする
- claim は BEGIN IMMEDIATE ＋条件付きUPDATE。lease_owner / lease_until で競合を防ぐ
- lease が切れた対象を別のワーカーが拾ったとき、submitting のままなら
  submission_unknown へ移し、再submitしない（仕様第8章、試験16）
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import begin_immediate, session_scope
from app.models import Artifact, Asset, AssetVariant, CostEntry, Generation, Preset, utcnow
from app.providers.base import ERROR_RATE_LIMITED, ERROR_TIMEOUT, ProviderError, SubmitTimeout
from app.providers.registry import get_adapter
from app.services import glb_inspect, quota, storage
from app.services.generation import (
    IN_FLIGHT_STATUSES,
    INITIAL_POLL_SECONDS,
    MONITORING_TIMEOUT_SECONDS,
    next_poll_delay,
    seconds_from_now,
)

logger = logging.getLogger("worker")

# 1つの処理単位を抱えていられる時間。外部通信のタイムアウトより十分長くする
LEASE_SECONDS = 180


def _clear_lease(generation: Generation) -> None:
    generation.lease_owner = None
    generation.lease_until = None


def _record_failed_cost(db: Session, generation: Generation) -> None:
    """事業者側の失敗は「失敗・要照合」として見積を残す（仕様第8章・第11章）。

    未課金を確認したあとに運営が手動で0に確定する。二重計上しないよう一意キーを持つ。
    """
    dedupe_key = f"failed:{generation.id}"
    if db.scalar(select(CostEntry.id).where(CostEntry.dedupe_key == dedupe_key)):
        return
    estimate = db.scalar(
        select(CostEntry).where(
            CostEntry.generation_id == generation.id, CostEntry.kind == "estimate"
        )
    )
    db.add(
        CostEntry(
            generation_id=generation.id,
            kind="failed_unreconciled",
            amount_micro_usd=estimate.amount_micro_usd if estimate else 0,
            currency="USD",
            price_version=estimate.price_version if estimate else "",
            evidence_note="事業者側で失敗。未課金を確認したうえで運営が0に確定する",
            dedupe_key=dedupe_key,
        )
    )


def _mark_provider_failed(db: Session, generation: Generation, *, kind: str, note: str) -> None:
    generation.tech_status = "provider_failed"
    generation.error_kind = kind
    generation.error_note = note
    generation.next_check_at = None
    _clear_lease(generation)
    _record_failed_cost(db, generation)
    # 技術失敗は送信枠を返す（仕様第8章の状態遷移表、試験17）
    quota.release(db, generation, reason=f"事業者側の失敗（{kind}）")


# --- claim ------------------------------------------------------------------


def _claim(
    db: Session, *, status: str, identity: str, limit: int, respect_schedule: bool = True
) -> list[str]:
    """条件付きUPDATEで対象を確保する。確保した生成IDを返す。"""
    now = utcnow()
    lease_until = now + timedelta(seconds=LEASE_SECONDS)

    begin_immediate(db)
    query = select(Generation.id).where(
        Generation.tech_status == status,
        or_(Generation.lease_until.is_(None), Generation.lease_until < now),
    )
    if respect_schedule:
        query = query.where(
            or_(Generation.next_check_at.is_(None), Generation.next_check_at <= now)
        )
    candidates = list(
        db.scalars(query.order_by(Generation.next_check_at.asc().nulls_first()).limit(limit)).all()
    )

    claimed: list[str] = []
    for generation_id in candidates:
        result = db.execute(
            update(Generation)
            .where(
                Generation.id == generation_id,
                Generation.tech_status == status,
                or_(Generation.lease_until.is_(None), Generation.lease_until < now),
            )
            .values(lease_owner=identity, lease_until=lease_until)
        )
        if result.rowcount == 1:
            claimed.append(generation_id)
    return claimed


def reclaim_expired_leases(identity: str) -> int:
    """lease が切れた対象を回収する。

    submitting のままのものは submission_unknown へ移し、人の照合を待つ。
    自動で再送信しない（二重課金を避ける。仕様第8章、試験16）。
    """
    moved = 0
    with session_scope() as db:
        begin_immediate(db)
        now = utcnow()
        stale = db.scalars(
            select(Generation).where(
                Generation.tech_status.in_(IN_FLIGHT_STATUSES),
                Generation.lease_until.is_not(None),
                Generation.lease_until < now,
            )
        ).all()
        for generation in stale:
            if generation.tech_status == "submitting":
                generation.tech_status = "submission_unknown"
                generation.error_kind = ERROR_TIMEOUT
                generation.error_note = (
                    "送信の途中でワーカーが停止しました。事業者の履歴と照合してください。"
                    "自動での再送信は行いません"
                )
                generation.next_check_at = None
                # 枠は保持する（送信された可能性があるため）
                logger.warning(
                    "lease切れの送信中タスクを受付結果不明にしました id=%s", generation.id
                )
                moved += 1
            else:
                # 状態確認・ダウンロードは再開してよい
                generation.next_check_at = utcnow()
            _clear_lease(generation)
    return moved


# --- 送信 -------------------------------------------------------------------


def _capacity(db: Session) -> tuple[int, dict[str, int]]:
    rows = db.execute(
        select(Generation.provider, func.count(Generation.id))
        .where(Generation.tech_status.in_(IN_FLIGHT_STATUSES))
        .group_by(Generation.provider)
    ).all()
    per_provider = {provider: int(count) for provider, count in rows}
    return sum(per_provider.values()), per_provider


def submit_step(identity: str) -> bool:
    """queued の1件を外部へ送る。送ったら True。

    同時外部タスク上限（全体・各社）を超えないようにする（仕様第8章）。
    """
    settings = get_settings()
    generation_id: str | None = None
    task_input: tuple[str, str] | None = None

    with session_scope() as db:
        begin_immediate(db)
        total, per_provider = _capacity(db)
        if total >= settings.max_concurrent_total:
            return False

        now = utcnow()
        candidates = db.scalars(
            select(Generation)
            .where(
                Generation.tech_status == "queued",
                or_(Generation.next_check_at.is_(None), Generation.next_check_at <= now),
                or_(Generation.lease_until.is_(None), Generation.lease_until < now),
            )
            .order_by(Generation.created_at.asc())
            .limit(20)
        ).all()

        for candidate in candidates:
            if per_provider.get(candidate.provider, 0) >= settings.max_concurrent_per_provider:
                continue
            result = db.execute(
                update(Generation)
                .where(Generation.id == candidate.id, Generation.tech_status == "queued")
                .values(
                    # 送信前に submitting と送信意図をコミットする（仕様第8章）
                    tech_status="submitting",
                    submitted_at=utcnow(),
                    lease_owner=identity,
                    lease_until=utcnow() + timedelta(seconds=LEASE_SECONDS),
                )
            )
            if result.rowcount == 1:
                generation_id = candidate.id
                task_input = (candidate.variant_id, candidate.preset_id)
                break

    if generation_id is None or task_input is None:
        return False

    # ここから外部通信。DBのトランザクションは持っていない
    variant_id, preset_id = task_input
    with session_scope() as read_db:
        variant = read_db.get(AssetVariant, variant_id)
        preset = read_db.get(Preset, preset_id)
        provider = preset.provider if preset else None
        generation_row = read_db.get(Generation, generation_id)
        is_live = bool(generation_row.is_live) if generation_row else False
        asset = read_db.get(Asset, generation_row.asset_id) if generation_row else None
        consent_status = asset.consent_status if asset else None

    if variant is None or preset is None or provider is None:
        with session_scope() as db:
            generation = db.get(Generation, generation_id)
            _mark_provider_failed(
                db, generation, kind="missing_input", note="入力またはプリセットが見つかりません"
            )
        return True

    # 受付のあとで同意が取り下げられていることがあるため、送信の直前にも確かめる。
    # 利用同意が未取得の画像は外部へ送らない（仕様第12章、試験19）
    if is_live and consent_status == "missing":
        with session_scope() as db:
            generation = db.get(Generation, generation_id)
            generation.tech_status = "cancelled"
            generation.error_kind = "consent_missing"
            generation.error_note = "利用同意が未取得のため、外部へ送信せずに中止しました"
            generation.next_check_at = None
            _clear_lease(generation)
            quota.release(db, generation, reason="利用同意が未取得のため未送信で中止")
        logger.warning("同意未取得のため送信しませんでした id=%s", generation_id)
        return True

    adapter = get_adapter(provider)
    try:
        submitted = adapter.submit(variant, preset, client_reference=generation_id)
    except SubmitTimeout as exc:
        with session_scope() as db:
            generation = db.get(Generation, generation_id)
            # 課金されたか断定できない。自動で再POSTしない。枠は保持する
            generation.tech_status = "submission_unknown"
            generation.error_kind = ERROR_TIMEOUT
            generation.error_note = f"{exc}。事業者の履歴と照合してください"
            generation.next_check_at = None
            _clear_lease(generation)
        return True
    except ProviderError as exc:
        with session_scope() as db:
            generation = db.get(Generation, generation_id)
            if exc.kind == ERROR_RATE_LIMITED:
                # 429 は事業者が受け付けていない。待って受付からやり直す
                generation.tech_status = "queued"
                generation.error_kind = ERROR_RATE_LIMITED
                generation.error_note = str(exc)
                generation.poll_interval_seconds = next_poll_delay(
                    generation.poll_interval_seconds, retry_after=exc.retry_after
                )
                generation.next_check_at = seconds_from_now(generation.poll_interval_seconds)
                _clear_lease(generation)
            else:
                _mark_provider_failed(db, generation, kind=exc.kind, note=str(exc))
        return True

    with session_scope() as db:
        generation = db.get(Generation, generation_id)
        # 応答でタスクIDを得たら直ちに保存する（仕様第8章）
        generation.provider_task_id = submitted.provider_task_id
        generation.tech_status = "running"
        generation.error_kind = None
        generation.error_note = ""
        generation.poll_interval_seconds = INITIAL_POLL_SECONDS
        generation.next_check_at = seconds_from_now(INITIAL_POLL_SECONDS)
        _clear_lease(generation)
    return True


# --- 状態確認 ---------------------------------------------------------------


def status_step(identity: str, *, limit: int = 20) -> int:
    """running の対象を順に確認する。確認した件数を返す。"""
    with session_scope() as db:
        claimed = _claim(db, status="running", identity=identity, limit=limit)

    checked = 0
    for generation_id in claimed:
        with session_scope() as db:
            generation = db.get(Generation, generation_id)
            provider = generation.provider
            task_id = generation.provider_task_id
            submitted_at = generation.submitted_at

        if not task_id:
            with session_scope() as db:
                generation = db.get(Generation, generation_id)
                generation.tech_status = "submission_unknown"
                generation.error_kind = ERROR_TIMEOUT
                generation.error_note = "外部タスクIDが保存されていません"
                generation.next_check_at = None
                _clear_lease(generation)
            checked += 1
            continue

        adapter = get_adapter(provider)
        try:
            status = adapter.fetch_status(task_id)
            error: ProviderError | None = None
        except ProviderError as exc:
            status = None
            error = exc

        with session_scope() as db:
            generation = db.get(Generation, generation_id)
            generation.last_checked_at = utcnow()

            if error is not None:
                # 通信の失敗では外部失敗と断定しない。同じタスクIDを再確認する
                generation.error_kind = error.kind
                generation.error_note = str(error)
                generation.poll_interval_seconds = next_poll_delay(
                    generation.poll_interval_seconds, retry_after=error.retry_after
                )
                generation.next_check_at = seconds_from_now(generation.poll_interval_seconds)
                _clear_lease(generation)
                checked += 1
                continue

            if status.state == "failed":
                _mark_provider_failed(
                    db,
                    generation,
                    kind="provider_failed",
                    note=status.failure_reason or "事業者が失敗を返しました",
                )
            elif status.state == "succeeded" and status.result_ref:
                generation.tech_status = "downloading"
                generation.provider_result_ref = status.result_ref
                generation.progress_percent = status.progress_percent
                generation.error_kind = None
                generation.error_note = ""
                generation.next_check_at = utcnow()
                _clear_lease(generation)
            else:
                generation.progress_percent = status.progress_percent
                elapsed = (
                    (utcnow() - submitted_at).total_seconds() if submitted_at is not None else 0
                )
                if elapsed > MONITORING_TIMEOUT_SECONDS:
                    # 外部失敗とは断定しない。手動で再開できる
                    generation.tech_status = "monitoring_paused"
                    generation.error_note = (
                        "30分を過ぎても完了しません。外部の失敗とは断定していません。"
                        "「状態を再確認」で監視を再開できます"
                    )
                    generation.next_check_at = None
                else:
                    generation.poll_interval_seconds = next_poll_delay(
                        generation.poll_interval_seconds
                    )
                    generation.next_check_at = seconds_from_now(generation.poll_interval_seconds)
                _clear_lease(generation)
            checked += 1
    return checked


# --- ダウンロード -----------------------------------------------------------


def download_step(identity: str) -> bool:
    """downloading の1件だけを保存する。1件に限るのは、
    長いダウンロードが他タスクの状態確認を止めないようにするため（仕様第8章、試験20）。
    """
    with session_scope() as db:
        claimed = _claim(db, status="downloading", identity=identity, limit=1)
    if not claimed:
        return False
    generation_id = claimed[0]

    with session_scope() as db:
        generation = db.get(Generation, generation_id)
        provider = generation.provider
        result_ref = generation.provider_result_ref

    if not result_ref:
        with session_scope() as db:
            generation = db.get(Generation, generation_id)
            generation.tech_status = "download_failed"
            generation.error_kind = "download_failed"
            generation.error_note = "取得先の情報がありません"
            generation.next_check_at = None
            _clear_lease(generation)
        return True

    adapter = get_adapter(provider)
    try:
        downloaded = adapter.download_result(result_ref)
        failure: ProviderError | None = None
    except ProviderError as exc:
        downloaded = None
        failure = exc

    settings = get_settings()
    with session_scope() as db:
        generation = db.get(Generation, generation_id)
        if failure is not None:
            # 保存の失敗。外部に新規生成しない。枠は保持する
            generation.tech_status = "download_failed"
            generation.error_kind = "download_failed"
            generation.error_note = str(failure)
            generation.next_check_at = None
            _clear_lease(generation)
            return True

        try:
            metrics = glb_inspect.inspect(downloaded.data, max_bytes=settings.max_download_bytes)
        except glb_inspect.GlbRejected as exc:
            generation.tech_status = "validation_failed"
            generation.error_kind = "validation_failed"
            generation.error_note = str(exc)
            generation.next_check_at = None
            _clear_lease(generation)
            return True

        stored = storage.save_bytes(downloaded.data)
        db.add(
            Artifact(
                generation_id=generation.id,
                kind="glb",
                storage_key=stored.key,
                sha256=stored.sha256,
                bytes=stored.bytes,
                inspection_ok=True,
                inspection_note="形式・サイズ整合・外部URI参照なしを確認",
                metrics_json=metrics.to_json(),
            )
        )
        generation.tech_status = "ready_for_review"
        generation.completed_at = utcnow()
        generation.error_kind = None
        generation.error_note = ""
        generation.next_check_at = None
        _clear_lease(generation)
    return True


def run_cycle(identity: str) -> dict[str, int]:
    """1周分の処理。状態確認は全対象、ダウンロードは1件だけ。"""
    reclaimed = reclaim_expired_leases(identity)
    submitted = 1 if submit_step(identity) else 0
    checked = status_step(identity)
    downloaded = 1 if download_step(identity) else 0
    return {
        "reclaimed": reclaimed,
        "submitted": submitted,
        "checked": checked,
        "downloaded": downloaded,
    }
