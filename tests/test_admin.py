"""管理画面（仕様第4章・第6.3章・第9章・第12章）。

守っているかを確かめる要点：

- APIキーの値を画面にもAPIにも出さない
- 画面からキーを保存できない
- 接続テストは LIVE_API_ENABLED=false のとき実行できない
- 接続テストで生成を行わない
- ブラインド評価中の成果物でサービス名を出さない
- 削除の口を持たない
"""

from __future__ import annotations

import pytest

from tests.conftest import CSRF_VALUE

SECRET_KEY_VALUE = "tsk_this_value_must_never_appear_anywhere"


@pytest.fixture
def with_tripo_key(monkeypatch):
    monkeypatch.setenv("TRIPO_API_KEY", SECRET_KEY_VALUE)
    return SECRET_KEY_VALUE


# --- キーを漏らさない -------------------------------------------------------


def test_admin_page_never_shows_the_key_value(auth_client, with_tripo_key):
    """画面HTMLにキーの値が現れない（仕様第12章）。"""
    response = auth_client.get("/admin")
    assert response.status_code == 200
    assert SECRET_KEY_VALUE not in response.text
    # 先頭数文字だけの表示も行わない
    assert "this_value" not in response.text
    # 設定済みであることは分かる
    assert "設定済み" in response.text


def test_provider_api_never_returns_the_key_value(auth_client, with_tripo_key):
    """APIレスポンスにもキーの値を含めない（仕様第12章）。"""
    response = auth_client.get("/api/admin/providers")
    assert response.status_code == 200
    assert SECRET_KEY_VALUE not in response.text

    body = response.json()
    tripo = next(item for item in body if item["provider"] == "tripo")
    assert tripo["key_is_set"] is True
    assert tripo["key_prefix_ok"] is True
    # 値・長さ・ハッシュに類する項目が無いこと
    for forbidden in ("key", "api_key", "key_value", "key_length", "key_hash"):
        assert forbidden not in tripo


def test_unset_key_is_reported_without_inventing_a_value(auth_client, monkeypatch):
    monkeypatch.delenv("TRIPO_API_KEY", raising=False)
    body = auth_client.get("/api/admin/providers").json()
    tripo = next(item for item in body if item["provider"] == "tripo")
    assert tripo["key_is_set"] is False
    assert any("APIキー" in reason for reason in tripo["blocking_reasons"])


def test_wrong_prefix_blocks_tripo(auth_client, monkeypatch):
    """公式SDKが先頭を強制するため、形式違いは実生成に進めない。"""
    monkeypatch.setenv("TRIPO_API_KEY", "wrong-prefix-key")
    body = auth_client.get("/api/admin/providers").json()
    tripo = next(item for item in body if item["provider"] == "tripo")
    assert tripo["key_is_set"] is True
    assert tripo["key_prefix_ok"] is False
    assert any("形式" in reason for reason in tripo["blocking_reasons"])


def test_admin_has_no_endpoint_that_stores_a_key(client):
    """画面からキーを保存する口を作らない（CLAUDE.md 第5章）。"""
    for route in client.app.routes:
        path = getattr(route, "path", "")
        assert "api-key" not in path
        assert "apikey" not in path
    # 事業者に対して状態を変える口は接続テストだけ
    provider_writes = {
        getattr(route, "path", "")
        for route in client.app.routes
        if getattr(route, "path", "").startswith("/api/admin/providers")
        and getattr(route, "methods", set()) - {"GET", "HEAD", "OPTIONS"}
    }
    assert provider_writes == {"/api/admin/providers/{provider}/connection-test"}


# --- 接続テスト -------------------------------------------------------------


def test_connection_test_is_refused_while_live_api_is_disabled(auth_client, with_tripo_key):
    """既定（LIVE_API_ENABLED=false）では外部へ通信しない（仕様第8章）。"""
    response = auth_client.post(
        "/api/admin/providers/tripo/connection-test",
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert response.status_code == 409
    assert "LIVE_API_ENABLED" in response.json()["detail"]


def test_connection_test_requires_a_key(auth_client, monkeypatch):
    monkeypatch.delenv("MESHY_API_KEY", raising=False)
    monkeypatch.setattr(_settings(), "live_api_enabled", True)
    response = auth_client.post(
        "/api/admin/providers/meshy/connection-test",
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert response.status_code == 409
    assert "APIキー" in response.json()["detail"]


def test_connection_test_does_not_generate(auth_client, monkeypatch):
    """接続テストは読み取りだけ。作成系のエンドポイントを叩かない。"""
    import httpx

    from app.providers.meshy import BASE_URL, MeshyAdapter

    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json=[{"id": "task-1"}])

    monkeypatch.setenv("MESHY_API_KEY", "msy_test_key")
    monkeypatch.setattr(
        MeshyAdapter,
        "_client",
        staticmethod(
            lambda timeout_seconds: httpx.Client(
                base_url=BASE_URL,
                headers={"Authorization": "Bearer msy_test_key"},
                transport=httpx.MockTransport(handler),
            )
        ),
    )
    monkeypatch.setattr(_settings(), "live_api_enabled", True)

    response = auth_client.post(
        "/api/admin/providers/meshy/connection-test",
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    # GET だけで、POST は一度も出していない
    assert seen and all(method == "GET" for method, _ in seen)


def test_connection_test_failure_does_not_leak_the_response_body(auth_client, monkeypatch):
    """事業者の応答原文を画面に返さない（仕様第8章）。"""
    import httpx

    from app.providers.meshy import BASE_URL, MeshyAdapter

    secret = "internal-trace-with-token-msy_abcdef"
    monkeypatch.setenv("MESHY_API_KEY", "msy_test_key")
    monkeypatch.setattr(
        MeshyAdapter,
        "_client",
        staticmethod(
            lambda timeout_seconds: httpx.Client(
                base_url=BASE_URL,
                headers={"Authorization": "Bearer msy_test_key"},
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(500, json={"message": secret})
                ),
            )
        ),
    )
    monkeypatch.setattr(_settings(), "live_api_enabled", True)

    response = auth_client.post(
        "/api/admin/providers/meshy/connection-test",
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert secret not in response.text


def test_connection_test_needs_csrf(auth_client, with_tripo_key):
    response = auth_client.post("/api/admin/providers/tripo/connection-test")
    assert response.status_code == 403


def test_admin_requires_login(client):
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code in (302, 303, 401, 403)


# --- 成果物 ------------------------------------------------------------------


def test_artifact_list_hides_the_provider_while_blind(auth_client):
    """ブラインド評価中は管理画面でもサービス名を出さない（仕様第6.5章）。"""
    from tests.conftest import (
        create_experiment,
        drain_worker,
        form_token,
        make_png,
        preset_id,
        upload_asset,
    )

    experiment_id = create_experiment(auth_client)
    asset_id = upload_asset(auth_client, experiment_id, make_png())
    variant_id = auth_client.get(f"/api/assets/{asset_id}").json()["variants"][0]["id"]
    response = auth_client.post(
        "/api/comparisons",
        json={
            "asset_variant_id": variant_id,
            "preset_ids": [
                preset_id(auth_client, "mock-a-standard"),
                preset_id(auth_client, "mock-b-standard"),
            ],
        },
        headers={"x-csrf-token": CSRF_VALUE, "Idempotency-Key": form_token(auth_client)},
    )
    assert response.status_code == 202, response.text
    drain_worker()

    body = auth_client.get("/api/admin/artifacts").json()
    assert body["rows"], "成果物が作られていません"
    blind_rows = [row for row in body["rows"] if row["blind"]]
    assert blind_rows, "ブラインド中の成果物がありません"
    for row in blind_rows:
        assert row["provider"] is None
        assert row["preset_name"] is None
        assert row["blind_label"] in ("A", "B")

    page = auth_client.get("/admin/artifacts")
    assert page.status_code == 200
    assert "評価確定まで非表示" in page.text
    # 画面HTMLにもサービス名・プリセット名が出ない
    for secret in ("mock_a", "mock_b", "mock-a-standard", "mock-b-standard"):
        assert secret not in page.text


def test_artifact_summary_separates_live_and_mock(auth_client):
    """モックと実APIを混ぜて数えない（CLAUDE.md 第6章）。"""
    from tests.conftest import (
        add_asset_directly,
        create_and_run,
        create_experiment,
        preset_id,
    )

    experiment_id = create_experiment(auth_client)
    variant_id = add_asset_directly(experiment_id)
    create_and_run(auth_client, variant_id, preset_id(auth_client, "mock-standard"))

    summary = auth_client.get("/api/admin/artifacts").json()["summary"]
    assert summary["count"] >= 1
    assert summary["live_count"] == 0
    assert summary["mock_count"] == summary["count"]


def test_verify_detects_a_missing_file(auth_client):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Artifact
    from app.services import storage
    from tests.conftest import (
        add_asset_directly,
        create_and_run,
        create_experiment,
        preset_id,
    )

    experiment_id = create_experiment(auth_client)
    variant_id = add_asset_directly(experiment_id)
    create_and_run(auth_client, variant_id, preset_id(auth_client, "mock-standard"))

    with session_scope() as db:
        artifact = db.scalars(select(Artifact)).first()
        artifact_id = artifact.id
        key = artifact.storage_key

    ok = auth_client.post(
        f"/api/admin/artifacts/{artifact_id}/verify", headers={"x-csrf-token": CSRF_VALUE}
    ).json()
    assert ok["ok"] is True

    storage.path_for(key).unlink()

    missing = auth_client.post(
        f"/api/admin/artifacts/{artifact_id}/verify", headers={"x-csrf-token": CSRF_VALUE}
    ).json()
    assert missing["ok"] is False
    assert missing["exists"] is False


def test_storage_audit_reports_without_deleting(auth_client):
    """点検は報告だけ。削除はしない（仕様第9章）。"""
    from app.config import get_settings

    settings = get_settings()
    stray = settings.objects_dir / "zz" / "ffffffffffffffffffffffffffffffff"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"stray")

    result = auth_client.get("/api/admin/storage").json()
    assert result["orphan_count"] >= 1
    # 報告しても消さない
    assert stray.exists()


def test_admin_has_no_delete_endpoint(client):
    """削除機能は持たない（仕様第9章・decisions.md D-4）。"""
    for route in client.app.routes:
        if getattr(route, "path", "").startswith("/api/admin"):
            assert "DELETE" not in getattr(route, "methods", set())


# --- プリセットの選択可否 ----------------------------------------------------


def test_preset_is_not_selectable_without_a_key(db_ready, monkeypatch):
    """キー未設定は実生成に選べない（仕様第6.3章）。"""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Preset
    from app.services.presets import selectable_reasons

    monkeypatch.delenv("TRIPO_API_KEY", raising=False)
    with session_scope() as db:
        preset = db.scalar(select(Preset).where(Preset.code == "tripo-standard"))
        reasons = selectable_reasons(preset, live=True)
        assert any("APIキー" in reason for reason in reasons)
        # モック運用（live=False）ではキーを要求しない
        assert not any("APIキー" in reason for reason in selectable_reasons(preset, live=False))


def _settings():
    from app.config import get_settings

    return get_settings()


# --- 配置（compose）--------------------------------------------------------


def test_compose_gives_the_api_keys_to_both_services():
    """web と worker の両方にAPIキーが渡ること。

    - worker：外部への送信・状態確認・成果物取得を行う
    - web：管理画面の接続テストと `app.cli check-provider --connect` を行う

    片方に渡し忘れると「.env に書いたのに未設定と出る」ことになるので試験で守る。
    """
    from pathlib import Path

    import yaml

    compose = yaml.safe_load((Path(__file__).resolve().parents[1] / "compose.yaml").read_text())
    for service in ("web", "worker"):
        env = compose["services"][service]["environment"]
        for name in ("TRIPO_API_KEY", "MESHY_API_KEY"):
            assert name in env, f"{service} に {name} が渡っていません"
            # 値はコミットしない。.env から差し込む形であること
            assert env[name].startswith("${"), f"{service} の {name} が直書きされています"


def test_compose_does_not_hardcode_any_secret():
    """compose.yaml に秘密の値を直書きしない（CLAUDE.md 第5章）。"""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "compose.yaml").read_text()
    assert "tsk_" not in text
    assert "msy_" not in text


# --- 設定 ------------------------------------------------------------------


def test_the_example_secret_key_is_rejected(monkeypatch, tmp_path):
    """`.env.example` の値のままでは起動させない（仕様第6.1章「固定値禁止」）。

    `.env.example` を写しただけで動いてしまうと、セッションCookieの署名鍵が
    公開の既知値のまま運用に入ってしまう。
    """
    import pytest

    from app.config import EXAMPLE_SECRET_KEY, Settings

    monkeypatch.setenv("APP_SECRET_KEY", EXAMPLE_SECRET_KEY)
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="例示値"):
        Settings()

    # 作り直した値なら通る
    monkeypatch.setenv("APP_SECRET_KEY", "a-freshly-generated-key-long-enough-0123456789")
    assert Settings().secret_key
