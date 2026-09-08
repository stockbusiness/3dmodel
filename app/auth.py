"""認証・CSRF。仕様第6.1章・第7章。

- パスワードは argon2id でハッシュ（ASSUMPTION A-2）
- セッションは Cookie に乱数、DB には SHA256 のみ保存（ASSUMPTION A-3）
- CSRF は Cookie とフォームの二重送信を HMAC で検証
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import db_session
from app.models import Operator, OperatorSession

SESSION_COOKIE = "art3d_session"
CSRF_COOKIE = "art3d_csrf"
CSRF_FIELD = "csrf_token"

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(password_hash: str, plain: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Session, operator: Operator) -> str:
    """セッションを作り、Cookieに入れる生トークンを返す。"""
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    db.add(
        OperatorSession(
            operator_id=operator.id,
            token_hash=_token_hash(token),
            expires_at=datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours),
        )
    )
    db.flush()
    return token


def revoke_session(db: Session, token: str) -> None:
    row = db.scalar(select(OperatorSession).where(OperatorSession.token_hash == _token_hash(token)))
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)


def _lookup_operator(db: Session, token: str | None) -> Operator | None:
    if not token:
        return None
    row = db.scalar(select(OperatorSession).where(OperatorSession.token_hash == _token_hash(token)))
    if row is None or row.revoked_at is not None:
        return None
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        return None
    operator = db.get(Operator, row.operator_id)
    if operator is None or not operator.is_active:
        return None
    return operator


def current_operator_optional(
    request: Request, db: Session = Depends(db_session)
) -> Operator | None:
    return _lookup_operator(db, request.cookies.get(SESSION_COOKIE))


def current_operator(request: Request, db: Session = Depends(db_session)) -> Operator:
    """認証必須（仕様第7章）。未認証は401。"""
    operator = _lookup_operator(db, request.cookies.get(SESSION_COOKIE))
    if operator is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "ログインが必要です")
    return operator


def require_admin_action(operator: Operator, action: str) -> None:
    """上限額変更・枠追加・費用確定・開示などの管理操作の可否。

    仕様第2章により管理者と講師は同等権限（BLOCKER B-2 の回答）。
    フェーズAでは「ログイン済みなら許可」とし、抑止は理由入力と監査ログによる。
    将来 is_admin で分離する場合は、この関数の中身だけを差し替える
    （呼び出し側に権限判定を散らさない。ASSUMPTION A-16）。
    """
    if not operator.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"{action} を実行できません")


# --- CSRF -------------------------------------------------------------------


def issue_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_signature(token: str) -> str:
    settings = get_settings()
    return hmac.new(settings.secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()


def verify_csrf(request: Request, submitted: str | None) -> None:
    """Cookieとフォーム／ヘッダーの一致を確認する（仕様第7章）。"""
    cookie_value = request.cookies.get(CSRF_COOKIE)
    if not cookie_value or not submitted:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRFトークンがありません")
    if not hmac.compare_digest(cookie_value, submitted):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRFトークンが一致しません")


async def csrf_protect(request: Request) -> None:
    """POST/PATCH/PUT/DELETE に適用する依存。"""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    submitted = request.headers.get("x-csrf-token")
    if submitted is None:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()
            value = form.get(CSRF_FIELD)
            submitted = value if isinstance(value, str) else None
    verify_csrf(request, submitted)
