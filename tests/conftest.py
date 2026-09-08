"""テスト共通の準備。実APIは呼ばない（仕様第13章）。"""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from PIL import Image

CSRF_VALUE = "test-csrf-token"


@pytest.fixture
def settings_env(tmp_path, monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-key-that-is-long-enough-0123456789")
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_LIVE_API_ENABLED", "false")
    monkeypatch.setenv("APP_SESSION_TTL_HOURS", "1")

    from app.config import get_settings
    from app.db import reset_engine

    get_settings.cache_clear()
    reset_engine()
    yield
    get_settings.cache_clear()
    reset_engine()


@pytest.fixture
def db_ready(settings_env):
    from app.db import get_engine, session_scope
    from app.models import Base
    from app.services.presets import seed_presets

    Base.metadata.create_all(get_engine())
    with session_scope() as db:
        seed_presets(db)
    yield


@pytest.fixture
def operator_password() -> str:
    return "test-password-1234"


@pytest.fixture
def operator(db_ready, operator_password):
    from app.auth import hash_password
    from app.db import session_scope
    from app.models import Operator

    with session_scope() as db:
        row = Operator(
            login_name="teacher1",
            display_name="講師1",
            password_hash=hash_password(operator_password),
        )
        db.add(row)
        db.flush()
        operator_id = row.id
    return operator_id


@pytest.fixture
def client(db_ready) -> Iterator[TestClient]:
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def auth_client(client, operator, operator_password) -> TestClient:
    """ログイン済みのクライアント。CSRFトークンもCookieに載せる。"""
    client.cookies.set("art3d_csrf", CSRF_VALUE)
    response = client.post(
        "/login",
        data={
            "login_name": "teacher1",
            "password": operator_password,
            "csrf_token": CSRF_VALUE,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    client.cookies.set("art3d_csrf", CSRF_VALUE)
    return client


def make_png(width: int = 64, height: int = 64, color=(200, 120, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def make_noise_png(width: int = 512, height: int = 512) -> bytes:
    """圧縮の効かない雑音画像。容量上限の試験に使う。"""
    import os

    buffer = io.BytesIO()
    Image.frombytes("RGB", (width, height), os.urandom(width * height * 3)).save(
        buffer, format="PNG", compress_level=0
    )
    return buffer.getvalue()


@pytest.fixture
def png_bytes() -> bytes:
    return make_png()


def create_experiment(client: TestClient, name: str = "モック検証") -> str:
    response = client.post(
        "/api/experiments",
        json={"name": name},
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload_asset(
    client: TestClient,
    experiment_id: str,
    data: bytes,
    filename: str = "a.png",
    consent_status: str = "not_required",
) -> str:
    response = client.post(
        f"/experiments/{experiment_id}/assets",
        files={"file": (filename, data, "image/png")},
        data={
            "title": "題材",
            "subject_tag": "single_character",
            "source_class": "staff_original",
            "consent_status": consent_status,
            "csrf_token": CSRF_VALUE,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return response.headers["location"].rsplit("/", 1)[-1]


def mock_preset_id(client: TestClient) -> str:
    response = client.get("/api/presets")
    assert response.status_code == 200
    for preset in response.json():
        if preset["code"] == "mock-standard":
            return preset["id"]
    raise AssertionError("モックのプリセットが見つかりません")
