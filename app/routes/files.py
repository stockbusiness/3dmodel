"""認証付きファイル配信とCSV出力（仕様第6.6章・第12章）。

- 内部保存キーは UUID。元ファイル名を保存パスに使わない
- パストラバーサル禁止
- 利用者入力のURLをサーバーに取得させない（このAPIはIDのみを受け取る）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.auth import current_operator
from app.db import db_session
from app.models import Artifact, AssetVariant, Experiment, Operator
from app.services import storage
from app.services.csv_export import export_experiment_csv

router = APIRouter()

_INLINE_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}


@router.get("/api/files/{file_id}")
def serve_file(
    file_id: str,
    kind: str = Query("variant", pattern="^(variant|artifact|submission)$"),
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    if kind in ("variant", "submission"):
        variant = db.get(AssetVariant, file_id)
        if variant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "画像が見つかりません")
        if kind == "submission":
            key = variant.submission_storage_key
            if key is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "送信用の画像がありません")
        else:
            key = variant.storage_key
        media_type = (
            variant.mime if variant.mime in _INLINE_IMAGE_TYPES else "application/octet-stream"
        )
    else:
        artifact = db.get(Artifact, file_id)
        if artifact is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "成果物が見つかりません")
        key = artifact.storage_key
        media_type = "model/gltf-binary"

    try:
        data = storage.read_bytes(key)
    except storage.StorageError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "保存物が見つかりません") from exc

    return Response(
        content=data,
        media_type=media_type,
        headers={
            # 保存名にサービス名や元ファイル名を含めない
            "Content-Disposition": f'inline; filename="{file_id}"',
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/experiments/{experiment_id}/export.csv")
def export_csv(
    experiment_id: str,
    operator: Operator = Depends(current_operator),
    db: Session = Depends(db_session),
):
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "検証セットが見つかりません")
    body = export_experiment_csv(db, experiment)
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="experiment-{experiment.id}.csv"'},
    )
