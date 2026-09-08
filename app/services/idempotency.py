"""冪等キー（仕様第7章・第6.3章）。

- キーはサーバーが発行する。画面描画時にフォームへ埋め込み、APIは事前に取得して送る
- 同キー・同本文は既存の結果を返す
- 同キー・別本文は409
- キーと本文ハッシュを保存する（`idempotency_records`）
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import IdempotencyRecord, Operator

_SEPARATOR = "."


class IdempotencyConflict(Exception):
    """同じキーで別の本文が送られた（409）。"""


class InvalidIdempotencyKey(Exception):
    """サーバーが発行していないキー。"""


def issue_key() -> str:
    """サーバー発行のキー。乱数と署名を組み合わせる。"""
    nonce = secrets.token_urlsafe(24)
    return f"{nonce}{_SEPARATOR}{_sign(nonce)}"


def _sign(nonce: str) -> str:
    secret = get_settings().secret_key.encode()
    return hmac.new(secret, nonce.encode(), hashlib.sha256).hexdigest()[:32]


def verify_key(key: str) -> None:
    nonce, separator, signature = key.rpartition(_SEPARATOR)
    if not separator or not hmac.compare_digest(signature, _sign(nonce)):
        raise InvalidIdempotencyKey(
            "冪等キーが不正です。画面を読み込み直してからやり直してください"
        )


def body_hash(body: dict[str, Any]) -> str:
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class Claim:
    record: IdempotencyRecord
    is_new: bool

    @property
    def existing_result_id(self) -> str | None:
        return None if self.is_new else self.record.result_id


def claim(
    db: Session, *, operator: Operator, operation: str, key: str, body: dict[str, Any]
) -> Claim:
    """キーを確保する。既存なら本文の一致を確かめる。

    呼び出し側は、新規のときだけ実際の処理を行い、結果IDを `record.result_id` に入れる。
    """
    verify_key(key)
    digest = body_hash(body)

    existing = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.operator_id == operator.id,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.key == key,
        )
    )
    if existing is not None:
        if existing.body_hash != digest:
            raise IdempotencyConflict(
                "同じ冪等キーで内容の異なる依頼が送られました。"
                "画面を読み込み直してからやり直してください"
            )
        return Claim(record=existing, is_new=False)

    record = IdempotencyRecord(
        operator_id=operator.id, operation=operation, key=key, body_hash=digest
    )
    db.add(record)
    try:
        db.flush()
    except IntegrityError:
        # 同時に同じキーが入った。入った側の結果を返す
        db.rollback()
        again = db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.operator_id == operator.id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.key == key,
            )
        )
        if again is None:
            raise
        if again.body_hash != digest:
            raise IdempotencyConflict(
                "同じ冪等キーで内容の異なる依頼が送られました。"
                "画面を読み込み直してからやり直してください"
            ) from None
        return Claim(record=again, is_new=False)
    return Claim(record=record, is_new=True)
