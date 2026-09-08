"""成果物の確認・管理（管理画面用）。仕様第9章・第12章。

- **削除は実装しない。** 仕様第9章はデータ削除機能を対象外とし、
  バックアップからの復元手順で代替すると定めている（`docs/decisions.md` D-4）。
  ここが行うのは「一覧」「照合」「取りこぼしの検出」までである。
- **ブラインド評価中の成果物は事業者名を伏せる。** 管理画面から漏れないよう、
  一覧も詳細も `app.services.blind.public_generation` を通す（仕様第6.5章）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Artifact, Asset, AssetVariant, Experiment, Generation
from app.services import blind, storage
from app.services.review_aggregate import latest_reviews


@dataclass(frozen=True)
class VerifyResult:
    """保存物とDBの照合結果。"""

    artifact_id: str
    exists: bool
    size_matches: bool
    digest_matches: bool
    actual_bytes: int | None
    detail: str

    @property
    def ok(self) -> bool:
        return self.exists and self.size_matches and self.digest_matches


def verify(artifact: Artifact) -> VerifyResult:
    """DBの記録どおりのファイルが実在し、サイズとSHA256が一致するか確かめる。"""
    try:
        data = storage.read_bytes(artifact.storage_key)
    except storage.StorageError:
        return VerifyResult(
            artifact_id=artifact.id,
            exists=False,
            size_matches=False,
            digest_matches=False,
            actual_bytes=None,
            detail="保存物が見つかりません",
        )

    actual_bytes = len(data)
    size_matches = actual_bytes == artifact.bytes
    digest_matches = hashlib.sha256(data).hexdigest() == artifact.sha256

    if size_matches and digest_matches:
        detail = "記録と一致しました"
    elif not size_matches:
        detail = f"大きさが違います（記録 {artifact.bytes} / 実物 {actual_bytes}）"
    else:
        detail = "SHA256が一致しません"

    return VerifyResult(
        artifact_id=artifact.id,
        exists=True,
        size_matches=size_matches,
        digest_matches=digest_matches,
        actual_bytes=actual_bytes,
        detail=detail,
    )


def list_rows(
    db: Session,
    *,
    experiment_id: str | None = None,
    only_failed_inspection: bool = False,
    limit: int = 200,
) -> list[dict]:
    """成果物の一覧。ブラインド中の事業者名は伏せる。"""
    stmt = (
        select(Artifact, Generation, AssetVariant, Asset, Experiment)
        .join(Generation, Artifact.generation_id == Generation.id)
        .join(AssetVariant, Generation.variant_id == AssetVariant.id)
        .join(Asset, Generation.asset_id == Asset.id)
        .join(Experiment, Generation.experiment_id == Experiment.id)
        .order_by(Artifact.created_at.desc())
        .limit(limit)
    )
    if experiment_id:
        stmt = stmt.where(Experiment.id == experiment_id)
    if only_failed_inspection:
        stmt = stmt.where(Artifact.inspection_ok.is_(False))

    found = db.execute(stmt).all()
    reviews = latest_reviews(db, [row[1].id for row in found])

    rows: list[dict] = []
    for artifact, generation, variant, asset, experiment in found:
        try:
            snapshot = json.loads(generation.preset_snapshot_json or "{}")
        except json.JSONDecodeError:
            snapshot = {}
        public = blind.public_generation(db, generation, snapshot)
        rows.append(
            {
                "artifact_id": artifact.id,
                "generation_id": generation.id,
                "experiment_id": experiment.id,
                "experiment_name": experiment.name,
                "asset_id": asset.id,
                "asset_title": asset.title,
                "variant_id": variant.id,
                "kind": artifact.kind,
                "bytes": artifact.bytes,
                "sha256": artifact.sha256,
                "inspection_ok": artifact.inspection_ok,
                "inspection_note": artifact.inspection_note,
                "has_metrics": artifact.metrics_json is not None,
                "tech_status": generation.tech_status,
                "verdict": (
                    reviews[generation.id].verdict if generation.id in reviews else "unreviewed"
                ),
                "is_live": generation.is_live,
                "created_at": artifact.created_at.isoformat(),
                # ブラインド中は None のまま渡す
                "blind": public["blind"],
                "blind_label": public["label"],
                "provider": public["provider"],
                "preset_name": public["preset_name"],
            }
        )
    return rows


def summary(db: Session) -> dict:
    """保存領域の要約。件数と合計サイズ、検査NGの件数。"""
    total = db.scalar(select(func.count()).select_from(Artifact)) or 0
    total_bytes = db.scalar(select(func.coalesce(func.sum(Artifact.bytes), 0))) or 0
    failed = (
        db.scalar(
            select(func.count()).select_from(Artifact).where(Artifact.inspection_ok.is_(False))
        )
        or 0
    )
    without_metrics = (
        db.scalar(select(func.count()).select_from(Artifact).where(Artifact.metrics_json.is_(None)))
        or 0
    )
    live = (
        db.scalar(
            select(func.count())
            .select_from(Artifact)
            .join(Generation, Artifact.generation_id == Generation.id)
            .where(Generation.is_live.is_(True))
        )
        or 0
    )
    return {
        "count": total,
        "total_bytes": int(total_bytes),
        "inspection_failed": failed,
        "without_metrics": without_metrics,
        # モックと実APIを混ぜて数えない（CLAUDE.md 第6章）
        "live_count": live,
        "mock_count": total - live,
    }


def storage_audit(db: Session, *, limit: int = 50) -> dict:
    """保存領域の点検。

    - 記録はあるのに実物が無いもの（欠落）
    - 実物はあるのにどのレコードからも参照されていないもの（孤立）

    **どちらも削除はしない。** 見つけて報告するだけで、対処は運営が判断する。
    """
    settings = get_settings()

    known: set[str] = set()
    for (key,) in db.execute(select(Artifact.storage_key)).all():
        known.add(key)
    for (key,) in db.execute(select(AssetVariant.storage_key)).all():
        known.add(key)
    for (key,) in db.execute(select(AssetVariant.submission_storage_key)).all():
        if key:
            known.add(key)

    on_disk: set[str] = set()
    objects_dir = settings.objects_dir
    if objects_dir.exists():
        for path in objects_dir.rglob("*"):
            if path.is_file():
                on_disk.add(path.name)

    missing = sorted(known - on_disk)
    orphans = sorted(on_disk - known)
    return {
        "known_count": len(known),
        "on_disk_count": len(on_disk),
        "missing": missing[:limit],
        "missing_count": len(missing),
        "orphan": orphans[:limit],
        "orphan_count": len(orphans),
    }
