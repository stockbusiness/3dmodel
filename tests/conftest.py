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
    monkeypatch.setenv("APP_GLOBAL_COST_CAP_USD", "100")

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


def preset_id(client: TestClient, code: str = "mock-standard") -> str:
    response = client.get("/api/presets")
    assert response.status_code == 200
    for preset in response.json():
        if preset["code"] == code:
            return preset["id"]
    raise AssertionError(f"プリセットが見つかりません: {code}")


def mock_preset_id(client: TestClient) -> str:
    return preset_id(client, "mock-standard")


def form_token(client: TestClient) -> str:
    """サーバー発行の冪等キーを取得する（仕様第6.3章）。"""
    response = client.get("/api/form-token")
    assert response.status_code == 200
    return response.json()["idempotency_key"]


def request_generation(client: TestClient, variant_id: str, preset: str, *, key: str | None = None):
    """単社生成を受け付ける。応答をそのまま返す。"""
    return client.post(
        "/api/generations",
        json={"asset_variant_id": variant_id, "preset_id": preset},
        headers={
            "x-csrf-token": CSRF_VALUE,
            "Idempotency-Key": key or form_token(client),
        },
    )


def _pull_schedule_forward() -> int:
    """次回確認の予定時刻を現在に引き寄せる。

    実運用ではワーカーがバックオフの秒数だけ待つ。テストで待ちたくないため、
    待つ以外にすることが無くなった時点で予定を現在に進める。
    """
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Generation, utcnow

    with session_scope() as db:
        pending = db.scalars(
            select(Generation).where(
                Generation.tech_status.in_(("queued", "running", "downloading")),
                Generation.next_check_at.is_not(None),
                Generation.next_check_at > utcnow(),
            )
        ).all()
        for generation in pending:
            generation.next_check_at = utcnow()
        return len(pending)


def _in_flight_count() -> int:
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Generation

    with session_scope() as db:
        return len(
            db.scalars(
                select(Generation.id).where(
                    Generation.tech_status.in_(("queued", "running", "downloading", "submitting"))
                )
            ).all()
        )


def drain_worker(max_cycles: int = 200, max_wait_seconds: float = 6.0) -> dict:
    """ワーカーを回して、進む状態がなくなるまで処理する。

    A1では受付時に同期処理していたが、A2ではワーカーが行う。
    バックオフの待ちは予定時刻を引き寄せて飛ばす。
    モックが実時間で待つ場合（delayed シナリオ）だけ、短く待って再試行する。
    """
    import time

    from app.services.worker_steps import run_cycle

    identity = "test-worker"
    totals = {"reclaimed": 0, "submitted": 0, "checked": 0, "downloaded": 0}
    waited = 0.0
    for _ in range(max_cycles):
        result = run_cycle(identity)
        for name, value in result.items():
            totals[name] += value
        if any(result.values()):
            continue
        pulled = _pull_schedule_forward()
        if _in_flight_count() == 0:
            break
        if waited >= max_wait_seconds:
            break
        # モックが実時間で待つ場合（delayed シナリオ）に備えて少しだけ待つ
        time.sleep(0.1)
        waited += 0.1
        if pulled == 0 and _in_flight_count() == 0:
            break
    return totals


def create_and_run(client: TestClient, variant_id: str, preset: str) -> str:
    """受付してワーカーを回し、生成IDを返す。"""
    response = request_generation(client, variant_id, preset)
    assert response.status_code == 202, response.text
    generation_id = response.json()["id"]
    drain_worker()
    return generation_id


class RecordingAdapter:
    """実APIのアダプターに見立てた、記録だけを行うテスト用アダプター。

    外部通信は行わない。送信が呼ばれたかどうかを確かめるために使う。
    """

    name = "test_external"
    supports_cancel = False

    def __init__(self) -> None:
        self.submitted: list[str] = []

    def estimate(self, variant, preset):
        from app.providers.base import Estimate

        return Estimate(
            max_micro_usd=preset.price_max_micro_usd or 0,
            price_version=preset.price_version or "test",
            is_bounded=True,
        )

    def submit(self, variant, preset, *, client_reference):
        from app.providers.base import SubmitResult

        self.submitted.append(client_reference)
        return SubmitResult(provider_task_id=f"ext-{client_reference[:12]}")

    def fetch_status(self, provider_task_id):
        from app.providers.base import StatusResult

        return StatusResult(state="succeeded", result_ref="sample_cube.glb")

    def download_result(self, result_ref):
        from pathlib import Path

        from app.providers.base import DownloadedResult

        root = Path(__file__).resolve().parents[1] / "fixtures"
        return DownloadedResult(data=(root / "sample_cube.glb").read_bytes())

    @staticmethod
    def preset_snapshot(preset):
        from app.providers.base import ProviderAdapter

        return ProviderAdapter.preset_snapshot(preset)


@pytest.fixture
def external_provider(operator, monkeypatch):
    """実API相当のプリセットと検証セットを用意する。

    LIVE_API_ENABLED を有効にし、外部送信が実際に呼ばれたかを確認できるようにする。
    """
    import json as _json

    from app.config import get_settings
    from app.db import session_scope
    from app.models import Preset
    from app.providers import registry

    adapter = RecordingAdapter()
    monkeypatch.setitem(registry._ADAPTERS, "test_external", adapter)
    monkeypatch.setattr(get_settings(), "live_api_enabled", True)

    with session_scope() as db:
        preset = Preset(
            code="test-external",
            display_name="テスト用外部サービス",
            provider="test_external",
            model_id="ext-model-1",
            settings_json=_json.dumps({}),
            version="1",
            sdk_version="test",
            is_enabled=True,
            price_max_micro_usd=300_000,
            price_version="test",
            price_checked_on="2026-09-08",
            price_source_url="https://example.invalid/pricing",
            is_unverified=False,
        )
        db.add(preset)
        db.flush()
        preset_id_value = preset.id

    return {"adapter": adapter, "preset_id": preset_id_value}


def create_live_experiment(name: str = "実API想定", cap_usd: int | None = 100) -> str:
    """実API扱いの検証セットを直接作る（画面からは作れない）。"""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Experiment, Operator

    with session_scope() as db:
        operator = db.scalars(select(Operator)).first()
        experiment = Experiment(
            name=name,
            is_live=True,
            cost_cap_micro_usd=None if cap_usd is None else cap_usd * 1_000_000,
            created_by=operator.id,
        )
        db.add(experiment)
        db.flush()
        return experiment.id


def add_asset_directly(experiment_id: str, *, consent_status: str = "not_required") -> str:
    """画像を直接登録し、原画像のIDを返す。"""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Asset, AssetVariant, Operator
    from app.services.image_intake import inspect_and_store

    result = inspect_and_store(make_png())
    with session_scope() as db:
        operator = db.scalars(select(Operator)).first()
        asset = Asset(
            experiment_id=experiment_id,
            title="題材",
            subject_tag="animal",
            source_class="staff_original",
            consent_status=consent_status,
            created_by=operator.id,
        )
        db.add(asset)
        db.flush()
        variant = AssetVariant(
            asset_id=asset.id,
            kind="original",
            storage_key=result.original.key,
            sha256=result.original.sha256,
            bytes=result.original.bytes,
            width=result.width,
            height=result.height,
            mime=result.mime,
            submission_storage_key=result.submission.key,
            submission_sha256=result.submission.sha256,
            submission_bytes=result.submission.bytes,
            created_by=operator.id,
        )
        db.add(variant)
        db.flush()
        return variant.id


def set_consent(variant_id: str, consent_status: str) -> None:
    from app.db import session_scope
    from app.models import AssetVariant

    with session_scope() as db:
        variant = db.get(AssetVariant, variant_id)
        variant.asset.consent_status = consent_status


def set_experiment_cap(experiment_id: str, cap_usd: str) -> None:
    from app.db import session_scope
    from app.models import Experiment
    from app.services.cost_guard import parse_usd_to_micro

    with session_scope() as db:
        db.get(Experiment, experiment_id).cost_cap_micro_usd = parse_usd_to_micro(cap_usd)
