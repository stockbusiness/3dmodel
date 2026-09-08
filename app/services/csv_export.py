"""CSV出力（仕様第6.6章）。

- UTF-8 BOM
- 表計算での数式実行を防ぐエスケープ
- 秘密鍵・署名URL・個人の写真URLを含めない。追跡は画像IDと生成IDで行う
"""

from __future__ import annotations

import csv
import io
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, AssetVariant, Experiment, Generation, Review
from app.models.enums import (
    CONSENT_STATUSES,
    DEFECT_TAGS,
    SOURCE_CLASSES,
    SUBJECT_TAGS,
    TECH_STATUSES,
    VERDICTS,
)
from app.services import generation as generation_service
from app.services.review_aggregate import format_micro_usd, is_pass, latest_reviews

BOM = "﻿"
_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

HEADERS = [
    "生成ID",
    "検証セット",
    "実/モック",
    "集計区分",
    "題材ID",
    "題材名",
    "題材タグ",
    "出所分類",
    "利用同意",
    "画像ID",
    "画像種別",
    "画像SHA256",
    "サービス",
    "プリセット",
    "モデルID",
    "試行回数",
    "目的",
    "技術状態",
    "受付日時UTC",
    "完了日時UTC",
    "見積(上限)",
    "評価者",
    "評価改訂",
    "元画像の特徴",
    "全周の形状",
    "色・模様",
    "魅力・完成度",
    "スマホ表示操作性",
    "スマホ確認済み",
    "重大不具合タグ",
    "判定",
    "教室利用合格",
    "ブラインド評価",
    "作業秒",
    "コメント",
]


def escape_cell(value: object) -> str:
    """先頭が数式になりうる文字なら ' を前置する（ASSUMPTION A-11）。"""
    text = "" if value is None else str(value)
    if text.startswith(_DANGEROUS_PREFIXES):
        return "'" + text
    return text


def _iso(value) -> str:
    return value.isoformat() if value is not None else ""


def export_experiment_csv(db: Session, experiment: Experiment) -> str:
    generations = db.scalars(
        select(Generation)
        .where(Generation.experiment_id == experiment.id)
        .order_by(Generation.created_at.asc())
    ).all()
    reviews = latest_reviews(db, [g.id for g in generations])

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(HEADERS)

    for generation in generations:
        asset = db.get(Asset, generation.asset_id)
        variant = db.get(AssetVariant, generation.variant_id)
        snapshot = generation_service.preset_snapshot(generation)
        review: Review | None = reviews.get(generation.id)
        estimate = next((c for c in _cost_entries(db, generation.id) if c.kind == "estimate"), None)

        tags = ""
        if review is not None:
            try:
                loaded = json.loads(review.defect_tags_json or "[]")
            except json.JSONDecodeError:
                loaded = []
            tags = "・".join(DEFECT_TAGS.get(t, t) for t in loaded if isinstance(t, str))

        row = [
            generation.id,
            experiment.name,
            "実API" if generation.is_live else "モック",
            "早期確認" if experiment.track == "early_check" else "通常検証",
            asset.id if asset else "",
            asset.title if asset else "",
            SUBJECT_TAGS.get(asset.subject_tag, asset.subject_tag) if asset else "",
            SOURCE_CLASSES.get(asset.source_class, asset.source_class) if asset else "",
            CONSENT_STATUSES.get(asset.consent_status, asset.consent_status) if asset else "",
            variant.id if variant else "",
            "原画像" if variant and variant.kind == "original" else "加工版",
            variant.sha256 if variant else "",
            generation.provider,
            snapshot.get("code", ""),
            snapshot.get("model_id", ""),
            generation.attempt_index,
            generation.purpose,
            TECH_STATUSES.get(generation.tech_status, generation.tech_status),
            _iso(generation.created_at),
            _iso(generation.completed_at),
            format_micro_usd(estimate.amount_micro_usd) if estimate else "",
            review.reviewer_id if review else "",
            review.revision if review else "",
            review.score_fidelity if review else "",
            review.score_shape if review else "",
            review.score_color if review else "",
            review.score_appeal if review else "",
            review.score_mobile if review and review.score_mobile is not None else "",
            ("はい" if review.mobile_checked else "いいえ") if review else "",
            tags,
            VERDICTS.get(review.verdict, review.verdict) if review else VERDICTS["unreviewed"],
            ("はい" if is_pass(review) else "いいえ") if review else "いいえ",
            ("はい" if review.was_blind else "いいえ") if review else "",
            review.work_seconds if review else "",
            review.comment if review else "",
        ]
        writer.writerow([escape_cell(cell) for cell in row])

    return BOM + buffer.getvalue()


def _cost_entries(db: Session, generation_id: str):
    from app.models import CostEntry

    return db.scalars(select(CostEntry).where(CostEntry.generation_id == generation_id)).all()
