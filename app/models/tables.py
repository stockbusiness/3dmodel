"""仕様第9章のデータ設計。

- ID は UUIDv4 の文字列（ASSUMPTION A-7）
- 日時はタイムゾーン付き UTC で保存し、表示のみ日本時間に変換（ASSUMPTION A-8）
- 金額は micro-USD の整数（仕様第9章）。浮動小数点を使わない
- フェーズAで未使用の列も、この時点で作る（A1の範囲）
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Operator(Base, TimestampMixin):
    """運営・講師。仕様第2章により権限は同等（BLOCKER B-2 の回答）。

    is_admin は将来の権限分離に備えて用意するが、フェーズAでは判定に使わない
    （ASSUMPTION A-16）。判定は app.auth.require_admin_action() に集約する。
    """

    __tablename__ = "operators"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    login_name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class OperatorSession(Base, TimestampMixin):
    """セッション。Cookieには乱数、DBにはSHA256のみ保存（ASSUMPTION A-3）。"""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    operator_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("operators.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    operator: Mapped[Operator] = relationship()


class Experiment(Base, TimestampMixin):
    """検証セット。仕様第9章 experiments。"""

    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    purpose_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 上限額（仕様第11章）。未設定なら実API生成を無効にする
    cost_cap_micro_usd: Mapped[int | None] = mapped_column(Integer)
    # 参考換算レート。現在レートと偽らない（仕様第11章）
    reference_rate_jpy_per_usd: Mapped[int | None] = mapped_column(Integer)
    hourly_wage_jpy: Mapped[int | None] = mapped_column(Integer)
    # 実/モックの別。集計で混ぜない（仕様第5章）
    is_live: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 集計区分。A3.5の「早期確認」を通常検証と分離する（仕様第10章）
    track: Mapped[str] = mapped_column(String(32), default="standard", nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("operators.id"), nullable=False)

    __table_args__ = (
        CheckConstraint("track IN ('standard','early_check')", name="ck_experiments_track"),
    )


class Asset(Base, TimestampMixin):
    """題材。仕様第9章 assets。"""

    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    experiment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    subject_tag: Mapped[str] = mapped_column(String(32), nullable=False)
    source_class: Mapped[str] = mapped_column(String(32), nullable=False)

    # 利用同意（仕様第9章）。missing は外部送信禁止（仕様第12章）
    consent_status: Mapped[str] = mapped_column(String(16), nullable=False)
    consent_obtained_on: Mapped[str | None] = mapped_column(String(10))
    consent_scope_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    consent_recorded_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("operators.id"))

    # 仕様第14章：将来接続のための参照情報。認証根拠にしない
    external_system: Mapped[str | None] = mapped_column(String(64))
    external_tenant_id: Mapped[str | None] = mapped_column(String(64))
    external_user_id: Mapped[str | None] = mapped_column(String(64))
    external_image_id: Mapped[str | None] = mapped_column(String(64))

    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("operators.id"), nullable=False)

    experiment: Mapped[Experiment] = relationship()
    variants: Mapped[list[AssetVariant]] = relationship(
        back_populates="asset", order_by="AssetVariant.created_at"
    )

    __table_args__ = (
        CheckConstraint(
            "consent_status IN ('granted','not_required','missing')",
            name="ck_assets_consent_status",
        ),
        Index("ix_assets_experiment", "experiment_id"),
    )


class AssetVariant(Base, TimestampMixin):
    """原画像と加工版。原画像は上書きしない（仕様第9章）。"""

    __tablename__ = "asset_variants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    asset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    parent_variant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("asset_variants.id")
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    storage_key: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    mime: Mapped[str] = mapped_column(String(32), nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    # EXIF方向を正規化し位置情報等を除去した送信用コピー（仕様第12章）
    submission_storage_key: Mapped[str | None] = mapped_column(String(64))
    submission_sha256: Mapped[str | None] = mapped_column(String(64))
    submission_bytes: Mapped[int | None] = mapped_column(Integer)

    # 加工種別は選択式・複数可（仕様第9章）。JSON配列を文字列で保存
    edit_types_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    edit_tool: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    edit_note: Mapped[str] = mapped_column(Text, default="", nullable=False)

    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("operators.id"), nullable=False)

    asset: Mapped[Asset] = relationship(back_populates="variants")

    __table_args__ = (
        CheckConstraint("kind IN ('original','processed')", name="ck_variants_kind"),
        Index("ix_variants_asset", "asset_id"),
    )


class Preset(Base, TimestampMixin):
    """固定プリセット。過去実行を後から設定変更で書き換えない（仕様第8章）。"""

    __tablename__ = "presets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    settings_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    sdk_version: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # 上限側の見積（仕様第11章「見積は常に上限側を採る」）
    price_max_micro_usd: Mapped[int | None] = mapped_column(Integer)
    price_version: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    price_checked_on: Mapped[str | None] = mapped_column(String(10))
    price_source_url: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # 公式資料で確認できていない項目があるか（仕様第4章）。true なら実生成不可
    is_unverified: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    unverified_note: Mapped[str] = mapped_column(Text, default="", nullable=False)


class Comparison(Base, TimestampMixin):
    """2社比較。A/B割当は比較ごとにランダムに固定して保存（仕様第6.5章）。"""

    __tablename__ = "comparisons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    experiment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    variant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("asset_variants.id"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(32), default="benchmark", nullable=False)
    is_blind: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    revealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revealed_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("operators.id"))
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("operators.id"), nullable=False)


class Generation(Base, TimestampMixin):
    """生成の1試行。仕様第9章 generations。"""

    __tablename__ = "generations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    experiment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    comparison_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("comparisons.id"))
    variant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("asset_variants.id"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("assets.id"), nullable=False)

    preset_id: Mapped[str] = mapped_column(String(36), ForeignKey("presets.id"), nullable=False)
    # 実行時点のプリセットの写し。後から設定を変えても書き換えない（仕様第8章）
    preset_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)

    parent_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("generations.id"))
    attempt_index: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), default="benchmark", nullable=False)
    is_live: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    tech_status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    error_kind: Mapped[str | None] = mapped_column(String(64))
    error_note: Mapped[str] = mapped_column(Text, default="", nullable=False)

    provider_task_id: Mapped[str | None] = mapped_column(String(128))
    progress_percent: Mapped[int | None] = mapped_column(Integer)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 仕様第8章：停止後の取得競合を防ぐ
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 送信枠を消費しているか（仕様第9章の返却ルール。フェーズBの利用枠へ引き継ぐ）
    consumes_quota: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # ブラインド時のラベル。'A'/'B'（仕様第6.5章）
    blind_label: Mapped[str | None] = mapped_column(String(1))

    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("operators.id"), nullable=False)

    __table_args__ = (
        # 外部タスクIDは provider との組合せで一意（未取得 null は許容）
        UniqueConstraint("provider", "provider_task_id", name="uq_generations_provider_task"),
        Index("ix_generations_experiment", "experiment_id"),
        Index("ix_generations_status_next_check", "tech_status", "next_check_at"),
        Index("ix_generations_quota", "experiment_id", "asset_id", "provider"),
    )


class Artifact(Base, TimestampMixin):
    """成果物。形状メトリクスはローカル抽出（仕様第9章・第12章）。"""

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    generation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("generations.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    inspection_ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    inspection_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 抽出できない項目は null とし 0 と区別する（仕様第9章）
    metrics_json: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_artifacts_generation", "generation_id"),)


class Review(Base, TimestampMixin):
    """評価。改訂は履歴追加、集計は最新を使う（仕様第9章）。"""

    __tablename__ = "reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    generation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("generations.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_id: Mapped[str] = mapped_column(String(36), ForeignKey("operators.id"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    score_fidelity: Mapped[int] = mapped_column(Integer, nullable=False)
    score_shape: Mapped[int] = mapped_column(Integer, nullable=False)
    score_color: Mapped[int] = mapped_column(Integer, nullable=False)
    score_appeal: Mapped[int] = mapped_column(Integer, nullable=False)
    # スマホ未確認は null のままにする（仕様第10章）
    score_mobile: Mapped[int | None] = mapped_column(Integer)
    mobile_checked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    defect_tags_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    work_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    was_blind: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_calibration: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("generation_id", "reviewer_id", "revision", name="uq_reviews_revision"),
        CheckConstraint(
            "verdict IN ('unreviewed','pass','retry_recommended','unsuitable')",
            name="ck_reviews_verdict",
        ),
        Index("ix_reviews_generation", "generation_id"),
    )


class CostEntry(Base, TimestampMixin):
    """費用。見積と実績を別の行として保存し混ぜない（仕様第11章）。"""

    __tablename__ = "cost_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    generation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("generations.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_micro_usd: Mapped[int] = mapped_column(Integer, nullable=False)
    credits: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    price_version: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    evidence_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    evidence_checked_on: Mapped[str | None] = mapped_column(String(10))
    recorded_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("operators.id"))
    # 手動費用入力の二重計上を防ぐ一意キー（仕様第13章 試験13）
    dedupe_key: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        CheckConstraint(
            "kind IN ('estimate','confirmed_manual','failed_unreconciled')",
            name="ck_cost_entries_kind",
        ),
        UniqueConstraint("dedupe_key", name="uq_cost_entries_dedupe"),
        Index("ix_cost_entries_generation", "generation_id"),
    )


class IdempotencyRecord(Base, TimestampMixin):
    """冪等キー。同キー・別本文は409（仕様第7章）。A2で使用。"""

    __tablename__ = "idempotency_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    operator_id: Mapped[str] = mapped_column(String(36), ForeignKey("operators.id"), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_id: Mapped[str | None] = mapped_column(String(36))

    __table_args__ = (UniqueConstraint("operator_id", "operation", "key", name="uq_idempotency"),)


class AuditEvent(Base, TimestampMixin):
    """監査ログ。秘密を除いた変更前後を残す（仕様第9章）。"""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    operator_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("operators.id"))
    target_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    before_json: Mapped[str | None] = mapped_column(Text)
    after_json: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_audit_target", "target_kind", "target_id"),)
