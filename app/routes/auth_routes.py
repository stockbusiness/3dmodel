"""ログイン・ログアウト（仕様第6.1章）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    SESSION_COOKIE,
    create_session,
    csrf_protect,
    current_operator_optional,
    revoke_session,
    verify_password,
)
from app.config import get_settings
from app.db import db_session
from app.models import Operator
from app.templating import render

router = APIRouter()


def _set_session_cookie(response: RedirectResponse, request: Request, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )


@router.get("/login")
def login_form(request: Request, operator: Operator | None = Depends(current_operator_optional)):
    if operator is not None:
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", {"operator": None})


@router.post("/login", dependencies=[Depends(csrf_protect)])
def login(
    request: Request,
    login_name: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(db_session),
):
    operator = db.scalar(select(Operator).where(Operator.login_name == login_name))
    # 利用者名の有無を区別しない
    if (
        operator is None
        or not operator.is_active
        or not verify_password(operator.password_hash, password)
    ):
        return render(
            request,
            "login.html",
            {"operator": None, "error": "ログイン名またはパスワードが違います"},
            status_code=401,
        )
    token = create_session(db, operator)
    response = RedirectResponse("/", status_code=303)
    _set_session_cookie(response, request, token)
    return response


@router.post("/logout", dependencies=[Depends(csrf_protect)])
def logout(request: Request, db: Session = Depends(db_session)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        revoke_session(db, token)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
