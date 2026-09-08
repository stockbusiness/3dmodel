"""管理画面（仕様第4章・第6.3章・第9章・第12章）。

運営が次の3つを一箇所で確認できるようにする。

1. **事業者接続** — APIキーが設定されているか、配信ホストが設定されているか、
   実際に認証が通るか（接続テスト）
2. **プリセット** — 価格・確認状態・実生成に選べない理由
3. **成果物** — 一覧、保存物との照合、保存領域の点検

守っていること：

- **APIキーの値を返さない。** 設定済みか否かと、先頭が想定どおりかだけを返す。
  値・先頭数文字・長さ・ハッシュのいずれも画面にもログにも出さない（仕様第12章）。
- **画面からキーを保存しない。** キーは環境変数からのみ読む
  （CLAUDE.md 第5章）。画面は `.env` に書く手順を案内するにとどめる。
- **接続テストは生成を行わない。** 読み取り専用の操作だけを使うため課金は発生しない。
  それでも外部通信ではあるので `LIVE_API_ENABLED=true` のときだけ実行でき、
  実行は監査ログに残す。
- **ブラインド評価中の事業者名を出さない。** 成果物一覧も `blind.public_generation`
  を通す（仕様第6.5章）。
- **削除機能は持たない**（仕様第9章。`docs/decisions.md` D-4）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import csrf_protect, current_operator, require_admin_action
from app.config import get_settings
from app.db import db_session
from app.models import Artifact, Experiment, Operator, Preset
from app.providers.base import ProviderAdapter, ProviderError, UnsupportedOperation
from app.providers.registry import get_adapter
from app.services import artifact_admin, audit, provider_health
from app.services.presets import selectable_reasons
from app.templating import render

router = APIRouter()


# --- 事業者接続 --------------------------------------------------------------


def _supports_connection_test(adapter: ProviderAdapter) -> bool:
    """この事業者が接続テストを実装しているか。

    基底のままなら未実装（呼ぶと UnsupportedOperation になる）。
    """
    return type(adapter).check_connection is not ProviderAdapter.check_connection


def _provider_state(db: Session, provider: str) -> dict:
    spec = provider_health.PROVIDER_KEYS[provider]
    presets = db.scalars(
        select(Preset).where(Preset.provider == provider).order_by(Preset.code.asc())
    ).all()
    adapter = get_adapter(provider)
    return {
        "provider": provider,
        "env_name": spec["env"],
        "env_hint": provider_health.env_hint(provider),
        # 真偽だけ。値は出さない
        "key_is_set": provider_health.key_is_set(provider),
        "key_prefix_ok": provider_health.key_prefix_ok(provider),
        "download_hosts": sorted(provider_health.download_hosts(provider)),
        "blocking_reasons": provider_health.blocking_reasons(provider),
        "checks": [
            {
                "label": check.label,
                "ok": check.ok,
                "detail": check.detail,
                "is_warning": check.is_warning,
            }
            for check in provider_health.diagnose(provider)
        ],
        "supports_connection_test": _supports_connection_test(adapter),
        "presets": [
            {
                "id": preset.id,
                "code": preset.code,
                "display_name": preset.display_name,
                "model_id": preset.model_id or None,
                "is_enabled": preset.is_enabled,
                "is_unverified": preset.is_unverified,
                "unverified_note": preset.unverified_note,
                "price_max_micro_usd": preset.price_max_micro_usd,
                "price_version": preset.price_version,
                "price_checked_on": preset.price_checked_on,
                "price_source_url": preset.price_source_url,
                "blocked_reasons": selectable_reasons(preset, live=True),
            }
            for preset in presets
        ],
    }


@router.get("/admin")
def admin_page(
    request: Request,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    settings = get_settings()
    return render(
        request,
        "admin.html",
        {
            "operator": operator,
            "providers": [_provider_state(db, name) for name in provider_health.REAL_PROVIDERS],
            "live_api_enabled": settings.live_api_enabled,
            "global_cost_cap_usd": settings.global_cost_cap_usd,
            "max_concurrent_total": settings.max_concurrent_total,
            "max_concurrent_per_provider": settings.max_concurrent_per_provider,
            "artifact_summary": artifact_admin.summary(db),
        },
    )


@router.get("/api/admin/providers")
def api_providers(
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """事業者接続の状態。**APIキーの値は含めない。**"""
    return [_provider_state(db, name) for name in provider_health.REAL_PROVIDERS]


@router.post(
    "/api/admin/providers/{provider}/connection-test",
    dependencies=[Depends(csrf_protect)],
)
def api_connection_test(
    provider: str,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """認証が通るかだけを確かめる。**生成は行わないので課金は発生しない。**

    外部通信ではあるので `LIVE_API_ENABLED=true` のときだけ実行できる
    （仕様第8章。既定は false）。実行は監査ログに残す。
    """
    if provider not in provider_health.PROVIDER_KEYS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "事業者が見つかりません")
    require_admin_action(operator, "接続テスト")

    settings = get_settings()
    if not settings.live_api_enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "実APIが無効です（LIVE_API_ENABLED=false）。"
            "接続テストは外部へ通信するため、有効にしてから実行してください",
        )

    reasons = provider_health.blocking_reasons(provider)
    key_reasons = [r for r in reasons if "APIキー" in r]
    if key_reasons:
        raise HTTPException(status.HTTP_409_CONFLICT, key_reasons[0])

    adapter = get_adapter(provider)
    try:
        result = adapter.check_connection()
    except UnsupportedOperation as exc:
        audit.record(
            db,
            operator=operator,
            target_kind="provider",
            target_id=provider,
            action="connection_test_unsupported",
            reason=str(exc),
        )
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(exc)) from exc
    except ProviderError as exc:
        # 事業者の応答原文は含めない（正規化済みの日本語だけ）
        audit.record(
            db,
            operator=operator,
            target_kind="provider",
            target_id=provider,
            action="connection_test_failed",
            reason=str(exc),
            after={"kind": exc.kind},
        )
        return {"ok": False, "detail": str(exc), "kind": exc.kind, "note": ""}

    audit.record(
        db,
        operator=operator,
        target_kind="provider",
        target_id=provider,
        action="connection_test",
        after={"ok": True},
    )
    return {"ok": result.ok, "detail": result.detail, "note": result.note, "kind": ""}


# --- 成果物 ------------------------------------------------------------------


@router.get("/admin/artifacts")
def artifacts_page(
    request: Request,
    experiment_id: str | None = Query(None),
    failed_only: bool = Query(False),
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    experiments = db.scalars(select(Experiment).order_by(Experiment.created_at.desc())).all()
    return render(
        request,
        "admin_artifacts.html",
        {
            "operator": operator,
            "rows": artifact_admin.list_rows(
                db, experiment_id=experiment_id, only_failed_inspection=failed_only
            ),
            "summary": artifact_admin.summary(db),
            "experiments": [{"id": e.id, "name": e.name} for e in experiments],
            "selected_experiment_id": experiment_id or "",
            "failed_only": failed_only,
        },
    )


@router.get("/api/admin/artifacts")
def api_artifacts(
    experiment_id: str | None = Query(None),
    failed_only: bool = Query(False),
    limit: int = Query(200, ge=1, le=1000),
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    return {
        "summary": artifact_admin.summary(db),
        "rows": artifact_admin.list_rows(
            db,
            experiment_id=experiment_id,
            only_failed_inspection=failed_only,
            limit=limit,
        ),
    }


@router.post(
    "/api/admin/artifacts/{artifact_id}/verify",
    dependencies=[Depends(csrf_protect)],
)
def api_verify_artifact(
    artifact_id: str,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """保存物がDBの記録と一致するかを確かめる。外部通信はしない。"""
    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "成果物が見つかりません")
    result = artifact_admin.verify(artifact)
    return {
        "artifact_id": result.artifact_id,
        "ok": result.ok,
        "exists": result.exists,
        "size_matches": result.size_matches,
        "digest_matches": result.digest_matches,
        "actual_bytes": result.actual_bytes,
        "detail": result.detail,
    }


@router.get("/api/admin/storage")
def api_storage_audit(
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    """保存領域の点検。欠落と孤立を報告するだけで、削除はしない。"""
    return artifact_admin.storage_audit(db)
