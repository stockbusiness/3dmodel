"""画面描画。表示は日本語、日時は日本時間に変換する（仕様第9章・第6.6章）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.auth import CSRF_COOKIE, issue_csrf_token
from app.models.enums import (
    CONSENT_STATUSES,
    DEFECT_TAGS,
    EDIT_TYPES,
    REVIEW_AXES,
    SOURCE_CLASSES,
    SUBJECT_TAGS,
    TECH_STATUSES,
    VERDICTS,
)
from app.services.review_aggregate import format_micro_usd

JST = timezone(timedelta(hours=9), "JST")
TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _jst(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(JST).strftime("%Y-%m-%d %H:%M")


templates.env.filters["jst"] = _jst
templates.env.filters["usd"] = format_micro_usd

COMMON_CONTEXT: dict[str, Any] = {
    "subject_tags": SUBJECT_TAGS,
    "source_classes": SOURCE_CLASSES,
    "consent_statuses": CONSENT_STATUSES,
    "edit_types": EDIT_TYPES,
    "tech_statuses": TECH_STATUSES,
    "verdicts": VERDICTS,
    "defect_tags": DEFECT_TAGS,
    "review_axes": REVIEW_AXES,
}


def render(request: Request, name: str, context: dict[str, Any], status_code: int = 200):
    """CSRFトークンをCookieとフォームの両方に載せて描画する。"""
    token = request.cookies.get(CSRF_COOKIE) or issue_csrf_token()
    merged = {**COMMON_CONTEXT, "csrf_token": token, **context}
    response = templates.TemplateResponse(request, name, merged, status_code=status_code)
    response.set_cookie(
        CSRF_COOKIE,
        token,
        httponly=False,  # フォーム送信値との突合に使うため JS から読める必要はないが、
        samesite="lax",  # 二重送信方式のため Cookie 自体は秘密ではない
        secure=request.url.scheme == "https",
        max_age=60 * 60 * 12,
        path="/",
    )
    return response
