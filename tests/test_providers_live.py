"""Tripo / Meshy アダプター（仕様第4章・第8章・第12章）。

実APIは呼ばない。HTTPはモックトランスポートで差し替え、
公式資料で確認した契約どおりに組み立て・読み取りができるかを確かめる。
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.providers.base import ProviderError, SubmitTimeout
from app.providers.meshy import BASE_URL, MeshyAdapter
from app.providers.tripo import TripoAdapter


class _Preset:
    """Preset の代わり。DBを使わずに設定だけ渡す。"""

    def __init__(self, settings: dict, *, unverified: bool = True, price: int | None = None):
        self.settings_json = json.dumps(settings)
        self.is_unverified = unverified
        self.price_max_micro_usd = price
        self.price_version = "test"


class _Variant:
    def __init__(self, storage_key: str, mime: str = "image/png"):
        self.submission_storage_key = storage_key
        self.storage_key = storage_key
        self.mime = mime


# --- 見積 -------------------------------------------------------------------


@pytest.mark.parametrize("adapter", [TripoAdapter(), MeshyAdapter()])
def test_unverified_preset_is_not_bounded(adapter):
    """価格未確認のプリセットは上限を見積もれない扱いにする（仕様第11章）。"""
    estimate = adapter.estimate(None, _Preset({}, unverified=True, price=None))
    assert estimate.is_bounded is False


@pytest.mark.parametrize("adapter", [TripoAdapter(), MeshyAdapter()])
def test_verified_preset_is_bounded(adapter):
    estimate = adapter.estimate(None, _Preset({}, unverified=False, price=300_000))
    assert estimate.is_bounded is True
    assert estimate.max_micro_usd == 300_000


@pytest.mark.parametrize("adapter", [TripoAdapter(), MeshyAdapter()])
def test_cancel_is_unsupported(adapter):
    """公式APIで取消を確認できていないため未対応（仕様第8章）。"""
    from app.providers.base import UnsupportedOperation

    assert adapter.supports_cancel is False
    with pytest.raises(UnsupportedOperation):
        adapter.cancel("task-1")


# --- Tripo ------------------------------------------------------------------


def test_tripo_disables_geo_detection_before_import():
    """SDKは import 時に第三者のIP位置情報サービスへ問い合わせる。必ず止める。"""
    import os

    import app.providers.tripo  # noqa: F401  - import の副作用を確かめる

    assert os.environ.get("TRIPO_DISABLE_GEO_DETECTION") == "1"


def test_tripo_only_sends_known_parameters():
    """公式SDKの image_to_model に無い引数は送らない（推測で送らない）。"""
    preset = _Preset(
        {
            "model_version": "v2.5-20250123",
            "texture": True,
            "geometry_quality": "standard",
            "存在しない引数": "値",
            "prompt": "これはTripoの引数ではない",
        }
    )
    params = TripoAdapter._call_params(preset)
    assert params == {
        "model_version": "v2.5-20250123",
        "texture": True,
        "geometry_quality": "standard",
    }


def test_tripo_download_uses_the_guard_not_the_sdk(settings_env, monkeypatch):
    """SDKのダウンロード（SSL検証を落とす経路）を使わないこと。"""
    from app.services import download_guard

    called: dict = {}

    def fake_fetch(url, policy):
        called["url"] = url
        called["hosts"] = policy.allowed_hosts
        return b"glTF"

    monkeypatch.setattr("app.providers.tripo.fetch_bytes", fake_fetch)
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "tripo_download_hosts", "cdn.example.com")

    result = TripoAdapter().download_result("https://cdn.example.com/a.glb")
    assert result.data == b"glTF"
    assert called["url"] == "https://cdn.example.com/a.glb"
    assert called["hosts"] == frozenset({"cdn.example.com"})
    assert download_guard.fetch_bytes is not fake_fetch  # 差し替えは局所的


def test_tripo_download_is_blocked_without_allowed_hosts(settings_env):
    """配信ホストが未設定なら取得しない（既定）。"""
    with pytest.raises(ProviderError) as excinfo:
        TripoAdapter().download_result("https://cdn.example.com/a.glb")
    assert excinfo.value.kind == "download_failed"


# --- Meshy ------------------------------------------------------------------


def _meshy_client(monkeypatch, handler):
    """MeshyAdapter._client をモックトランスポートに差し替える。"""
    monkeypatch.setenv("MESHY_API_KEY", "msy_test_key")

    def factory(timeout_seconds: int) -> httpx.Client:
        return httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": "Bearer msy_test_key"},
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(MeshyAdapter, "_client", staticmethod(factory))


def test_meshy_submit_builds_the_documented_request(monkeypatch, db_ready):
    from app.services import storage
    from tests.conftest import make_png

    stored = storage.save_bytes(make_png())
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"result": "task-abc"})

    _meshy_client(monkeypatch, handler)
    preset = _Preset(
        {
            "model_type": "standard",
            "should_texture": True,
            "enable_pbr": True,
            "texture_resolution": "4k",
            "target_formats": ["glb"],
            "存在しない引数": "値",
        }
    )
    result = MeshyAdapter().submit(_Variant(stored.key), preset, client_reference="generation-1")

    assert result.provider_task_id == "task-abc"
    assert seen["method"] == "POST"
    assert seen["path"] == "/openapi/v1/image-to-3d"
    assert seen["auth"] == "Bearer msy_test_key"
    # 画像は data: URI で送る。公開URLは作らない（仕様第12章）
    assert seen["body"]["image_url"].startswith("data:image/png;base64,")
    assert base64.b64decode(seen["body"]["image_url"].split(",", 1)[1])[:4] == b"\x89PNG"
    assert seen["body"]["model_type"] == "standard"
    assert seen["body"]["target_formats"] == ["glb"]
    # 契約に無い項目は送らない
    assert "存在しない引数" not in seen["body"]


def test_meshy_status_maps_documented_states(settings_env, monkeypatch):
    cases = [
        ({"status": "PENDING", "progress": 0}, "running"),
        ({"status": "IN_PROGRESS", "progress": 42}, "running"),
        (
            {"status": "SUCCEEDED", "progress": 100, "model_urls": {"glb": "https://x/y.glb"}},
            "succeeded",
        ),
        ({"status": "FAILED", "task_error": {"message": "だめでした"}}, "failed"),
        ({"status": "CANCELED"}, "failed"),
    ]
    for payload, expected in cases:
        _meshy_client(monkeypatch, lambda request, p=payload: httpx.Response(200, json=p))
        status = MeshyAdapter().fetch_status("task-1")
        assert status.state == expected, payload

    _meshy_client(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"status": "SUCCEEDED", "model_urls": {"glb": "https://x/y.glb"}}
        ),
    )
    assert MeshyAdapter().fetch_status("task-1").result_ref == "https://x/y.glb"


def test_meshy_rate_limit_is_not_a_failure(settings_env, monkeypatch):
    """429 は失敗にせず、Retry-After を伝える（仕様第8章）。"""
    _meshy_client(
        monkeypatch,
        lambda request: httpx.Response(429, headers={"retry-after": "45"}, json={}),
    )
    with pytest.raises(ProviderError) as excinfo:
        MeshyAdapter().fetch_status("task-1")
    assert excinfo.value.kind == "rate_limited"
    assert excinfo.value.retry_after == 45


def test_meshy_submit_timeout_is_not_treated_as_failure(monkeypatch, db_ready):
    """送信の応答が無い場合は受付結果不明にする（自動で再POSTしない）。"""
    from app.services import storage
    from tests.conftest import make_png

    stored = storage.save_bytes(make_png())

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    _meshy_client(monkeypatch, handler)
    with pytest.raises(SubmitTimeout):
        MeshyAdapter().submit(_Variant(stored.key), _Preset({}), client_reference="g1")


def test_meshy_error_body_is_not_returned_to_the_screen(settings_env, monkeypatch):
    """事業者の応答原文を画面へ返さない（仕様第8章）。"""
    secret = "internal-stack-trace-with-token-msy_abcdef"
    _meshy_client(monkeypatch, lambda request: httpx.Response(500, json={"message": secret}))
    with pytest.raises(ProviderError) as excinfo:
        MeshyAdapter().fetch_status("task-1")
    assert secret not in str(excinfo.value)


def test_meshy_download_is_blocked_without_allowed_hosts(settings_env):
    with pytest.raises(ProviderError) as excinfo:
        MeshyAdapter().download_result("https://cdn.example.com/a.glb")
    assert excinfo.value.kind == "download_failed"


# --- 実アダプターの経路でも検査が効くこと（仕様第13章 試験11） -----------------


def test_external_uri_glb_from_a_real_adapter_is_rejected(operator, monkeypatch):
    """事業者から外部URI参照つきGLBが返っても、保存せず validation_failed にする。

    A2はモックで確認したが、ここでは実アダプター（Meshy）の取得経路を通す。
    """
    import struct

    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Generation, Operator, Preset
    from app.services.worker_steps import download_step
    from tests.conftest import add_asset_directly, create_live_experiment

    malicious = json.dumps(
        {
            "asset": {"version": "2.0"},
            "buffers": [{"byteLength": 4, "uri": "https://attacker.example/payload.bin"}],
        },
        separators=(",", ":"),
    ).encode()
    malicious += b" " * (-len(malicious) % 4)
    body = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(malicious))
    body += struct.pack("<II", len(malicious), 0x4E4F534A) + malicious

    monkeypatch.setattr("app.providers.meshy.fetch_bytes", lambda url, policy: body)

    experiment_id = create_live_experiment()
    variant_id = add_asset_directly(experiment_id)

    with session_scope() as db:
        op = db.scalars(select(Operator)).first()
        preset = db.scalar(select(Preset).where(Preset.code == "meshy-standard"))
        from app.models import AssetVariant

        variant_row = db.get(AssetVariant, variant_id)
        generation = Generation(
            experiment_id=experiment_id,
            variant_id=variant_id,
            asset_id=variant_row.asset_id,
            preset_id=preset.id,
            preset_snapshot_json="{}",
            provider="meshy",
            is_live=True,
            tech_status="downloading",
            provider_task_id="meshy-task-1",
            provider_result_ref="https://cdn.example.com/model.glb",
            created_by=op.id,
        )
        db.add(generation)
        db.flush()
        generation_id = generation.id

    assert download_step("test-worker") is True

    with session_scope() as db:
        generation = db.get(Generation, generation_id)
        assert generation.tech_status == "validation_failed"
        assert "外部URI参照" in generation.error_note
        from app.models import Artifact

        assert db.scalar(select(Artifact).where(Artifact.generation_id == generation_id)) is None


def test_unverified_presets_cannot_be_used_for_live_generation(operator):
    """価格未確認のプリセットは実生成に選べない（仕様第6.3章・第11章）。"""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Operator, Preset
    from app.services.generation import GenerationRejected, create_generation
    from tests.conftest import add_asset_directly, create_live_experiment

    experiment_id = create_live_experiment()
    variant_id = add_asset_directly(experiment_id)

    with session_scope() as db:
        op = db.scalars(select(Operator)).first()
        for code in ("tripo-standard", "meshy-standard"):
            preset = db.scalar(select(Preset).where(Preset.code == code))
            assert preset.is_unverified is True
            assert preset.is_enabled is False
            with pytest.raises(GenerationRejected) as excinfo:
                create_generation(db, operator=op, variant_id=variant_id, preset_id=preset.id)
            assert "未確認" in str(excinfo.value) or "無効" in str(excinfo.value)


# --- 公式SDKを実際に通す結合試験（外部へは接続しない） -----------------------


@pytest.fixture
def fake_tripo_api(settings_env, monkeypatch):
    """Tripo APIの代わりになるローカルHTTPサーバー。

    実際の公式SDKをこのサーバーに向けて動かす。外部への接続は起きない。
    """
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    pytest.importorskip("tripo3d", reason="Tripo公式SDKが入っていない環境では飛ばす")

    state: dict = {"requests": [], "status": "success"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # サーバーのログを黙らせる
            pass

        def _send(self, payload: dict) -> None:
            body = _json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            state["requests"].append((self.path, self.headers.get("Authorization")))
            if self.path.endswith("/upload"):
                self._send({"code": 0, "data": {"image_token": "tok-1"}})
            else:
                self._send({"code": 0, "data": {"task_id": "tripo-task-1"}})

        def do_GET(self):
            state["requests"].append((self.path, self.headers.get("Authorization")))
            self._send(
                {
                    "code": 0,
                    "data": {
                        "task_id": "tripo-task-1",
                        "type": "image_to_model",
                        "status": state["status"],
                        "input": {},
                        "output": {"pbr_model": "https://cdn.example.com/model.glb"},
                        "progress": 100 if state["status"] == "success" else 30,
                        "create_time": 1788844000,
                    },
                }
            )

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    from tripo3d import TripoClient

    monkeypatch.setattr(
        TripoClient, "BASE_URL", f"http://127.0.0.1:{server.server_port}/v2/openapi"
    )
    monkeypatch.setenv("TRIPO_API_KEY", "tsk_test_key")
    yield state
    server.shutdown()
    server.server_close()


def test_tripo_submit_and_status_through_the_official_sdk(fake_tripo_api, db_ready):
    """公式SDK 0.4.2 を実際に動かし、送信とタスク取得ができることを確かめる。"""
    from app.services import storage
    from tests.conftest import make_png

    stored = storage.save_bytes(make_png())
    preset = _Preset(
        {"model_version": "v2.5-20250123", "texture": True, "geometry_quality": "standard"}
    )

    submitted = TripoAdapter().submit(_Variant(stored.key), preset, client_reference="generation-1")
    assert submitted.provider_task_id == "tripo-task-1"

    paths = [path for path, _ in fake_tripo_api["requests"]]
    assert "/v2/openapi/upload" in paths
    assert "/v2/openapi/task" in paths
    # 認証はBearerで送られる
    assert all(auth == "Bearer tsk_test_key" for _, auth in fake_tripo_api["requests"])

    status = TripoAdapter().fetch_status("tripo-task-1")
    assert status.state == "succeeded"
    assert status.result_ref == "https://cdn.example.com/model.glb"
    assert status.progress_percent == 100


def test_tripo_running_status_is_not_a_failure(fake_tripo_api):
    fake_tripo_api["status"] = "running"
    status = TripoAdapter().fetch_status("tripo-task-1")
    assert status.state == "running"
    assert status.progress_percent == 30


def test_tripo_failed_status_is_reported_as_failure(fake_tripo_api):
    for provider_status in ("failed", "banned", "expired"):
        fake_tripo_api["status"] = provider_status
        status = TripoAdapter().fetch_status("tripo-task-1")
        assert status.state == "failed", provider_status
