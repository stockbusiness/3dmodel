"""仕様第13章の必須試験のうち、A1で実施するもの。

試験10：未認証の画像・GLB取得拒否、CSRF拒否、任意URL取得拒否
試験11：外部URI参照GLB・偽拡張子画像・過大ファイルを拒否
"""

from __future__ import annotations

import json
import struct

import pytest

from tests.conftest import (
    CSRF_VALUE,
    create_and_run,
    create_experiment,
    drain_worker,
    form_token,
    make_noise_png,
    make_png,
    mock_preset_id,
    upload_asset,
)


def _build_glb(gltf: dict, bin_chunk: bytes = b"") -> bytes:
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    bin_chunk += b"\x00" * (-len(bin_chunk) % 4)
    total = 12 + 8 + len(json_bytes) + (8 + len(bin_chunk) if bin_chunk else 0)
    out = struct.pack("<4sII", b"glTF", 2, total)
    out += struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    if bin_chunk:
        out += struct.pack("<II", len(bin_chunk), 0x004E4942) + bin_chunk
    return out


# --- 試験10 ------------------------------------------------------------------


def test_unauthenticated_file_access_is_rejected(client, auth_client, png_bytes):
    """未認証では画像もGLBも取得できない。"""
    experiment_id = create_experiment(auth_client)
    asset_id = upload_asset(auth_client, experiment_id, png_bytes)
    variant_id = auth_client.get(f"/api/assets/{asset_id}").json()["variants"][0]["id"]
    generation_id = create_and_run(auth_client, variant_id, mock_preset_id(auth_client))
    artifact_id = auth_client.get(f"/api/generations/{generation_id}").json()["artifact"]["id"]

    auth_client.cookies.clear()
    for url in (
        f"/api/files/{variant_id}?kind=variant",
        f"/api/files/{variant_id}?kind=submission",
        f"/api/files/{artifact_id}?kind=artifact",
        f"/api/experiments/{experiment_id}/export.csv",
        f"/api/assets/{asset_id}",
    ):
        response = auth_client.get(url)
        assert response.status_code == 401, url


def test_csrf_is_required(auth_client, png_bytes):
    """CSRFトークンがない、または一致しないPOSTは拒否する。"""
    experiment_id = create_experiment(auth_client)

    no_token = auth_client.post("/api/experiments", json={"name": "x"})
    assert no_token.status_code == 403

    mismatched = auth_client.post(
        "/api/experiments", json={"name": "x"}, headers={"x-csrf-token": "a-different-token"}
    )
    assert mismatched.status_code == 403

    form_without_token = auth_client.post(
        f"/experiments/{experiment_id}/assets",
        files={"file": ("a.png", png_bytes, "image/png")},
        data={
            "subject_tag": "animal",
            "source_class": "staff_original",
            "consent_status": "not_required",
        },
    )
    assert form_without_token.status_code == 403


def test_server_ignores_caller_supplied_urls_and_providers(auth_client, png_bytes):
    """任意のURLや外部エンドポイントを受け取らない（仕様第7章・第12章）。

    provider は preset から確定するため、本文に外部URLを混ぜても取得は起きない。
    """
    experiment_id = create_experiment(auth_client)
    asset_id = upload_asset(auth_client, experiment_id, png_bytes)
    variant_id = auth_client.get(f"/api/assets/{asset_id}").json()["variants"][0]["id"]

    response = auth_client.post(
        "/api/generations",
        json={
            "asset_variant_id": variant_id,
            "preset_id": mock_preset_id(auth_client),
            "image_url": "http://169.254.169.254/latest/meta-data/",
            "provider": "tripo",
            "endpoint": "https://attacker.example/generate",
        },
        headers={"x-csrf-token": CSRF_VALUE, "Idempotency-Key": form_token(auth_client)},
    )
    assert response.status_code == 202
    drain_worker()
    detail = auth_client.get(f"/api/generations/{response.json()['id']}").json()
    assert detail["provider"] == "mock"  # presetから確定。本文のproviderは無視される


def test_file_endpoint_rejects_path_traversal(auth_client):
    for bad in ("..%2F..%2Fetc%2Fpasswd", "abc", "0" * 31):
        response = auth_client.get(f"/api/files/{bad}?kind=variant")
        assert response.status_code in (404, 422), bad


def test_storage_key_validation_rejects_traversal():
    from app.services import storage

    for bad in ("../../etc/passwd", "..", "0" * 33, "ZZZZ" * 8):
        with pytest.raises(storage.StorageError):
            storage.path_for(bad)


# --- 試験11 ------------------------------------------------------------------


def test_glb_with_external_uri_is_rejected():
    from app.services.glb_inspect import GlbRejected, inspect

    external = _build_glb(
        {
            "asset": {"version": "2.0"},
            "buffers": [{"byteLength": 4, "uri": "https://attacker.example/payload.bin"}],
        }
    )
    with pytest.raises(GlbRejected, match="外部URI参照"):
        inspect(external)

    external_image = _build_glb(
        {
            "asset": {"version": "2.0"},
            "images": [{"uri": "http://169.254.169.254/meta"}],
        }
    )
    with pytest.raises(GlbRejected, match="外部URI参照"):
        inspect(external_image)


def test_html_is_not_accepted_as_glb():
    from app.services.glb_inspect import GlbRejected, inspect

    with pytest.raises(GlbRejected):
        inspect(b"<!doctype html><html><body>not a model</body></html>")


def test_glb_with_wrong_declared_length_is_rejected():
    from app.services.glb_inspect import GlbRejected, inspect

    valid = _build_glb({"asset": {"version": "2.0"}})
    tampered = struct.pack("<4sII", b"glTF", 2, len(valid) + 100) + valid[12:]
    with pytest.raises(GlbRejected, match="一致しません"):
        inspect(tampered)


def test_oversized_glb_is_rejected():
    from app.services.glb_inspect import GlbRejected, inspect

    valid = _build_glb({"asset": {"version": "2.0"}})
    with pytest.raises(GlbRejected, match="上限"):
        inspect(valid, max_bytes=10)


def test_invalid_glb_from_provider_becomes_validation_failed(auth_client, png_bytes, monkeypatch):
    """事業者から不正なGLBが返っても、保存せず validation_failed にする。"""
    from app.providers.base import DownloadedResult
    from app.providers.mock import MockAdapter

    monkeypatch.setattr(
        MockAdapter,
        "download_result",
        lambda self, ref: DownloadedResult(data=b"<html>not a model</html>"),
    )

    experiment_id = create_experiment(auth_client)
    asset_id = upload_asset(auth_client, experiment_id, png_bytes)
    variant_id = auth_client.get(f"/api/assets/{asset_id}").json()["variants"][0]["id"]
    generation_id = create_and_run(auth_client, variant_id, mock_preset_id(auth_client))

    detail = auth_client.get(f"/api/generations/{generation_id}").json()
    assert detail["tech_status"] == "validation_failed"
    assert detail["artifact"] is None


def test_fake_extension_image_is_rejected(auth_client):
    """拡張子だけでなくデコード検査を行う（仕様第12章）。"""
    experiment_id = create_experiment(auth_client)
    for payload in (
        b"<svg xmlns='http://www.w3.org/2000/svg'><rect width='10' height='10'/></svg>",
        b"MZ\x90\x00\x03" + b"\x00" * 100,  # 実行可能形式
        b"PK\x03\x04" + b"\x00" * 100,  # zip
    ):
        response = auth_client.post(
            f"/experiments/{experiment_id}/assets",
            files={"file": ("innocent.png", payload, "image/png")},
            data={
                "subject_tag": "other",
                "source_class": "staff_original",
                "consent_status": "not_required",
                "csrf_token": CSRF_VALUE,
            },
        )
        assert response.status_code == 400, response.text
        assert "画像として読み取れません" in response.text


def test_oversized_image_is_rejected(auth_client, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "max_image_bytes", 4096)

    experiment_id = create_experiment(auth_client)
    big = make_noise_png(512, 512)  # 単色PNGは圧縮されて小さくなるため雑音画像を使う
    assert len(big) > 4096
    response = auth_client.post(
        f"/experiments/{experiment_id}/assets",
        files={"file": ("big.png", big, "image/png")},
        data={
            "subject_tag": "other",
            "source_class": "staff_original",
            "consent_status": "not_required",
            "csrf_token": CSRF_VALUE,
        },
    )
    assert response.status_code == 400
    assert "上限" in response.text


def test_too_many_pixels_is_rejected(auth_client, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "max_image_pixels", 1000)

    experiment_id = create_experiment(auth_client)
    response = auth_client.post(
        f"/experiments/{experiment_id}/assets",
        files={"file": ("wide.png", make_png(200, 200), "image/png")},
        data={
            "subject_tag": "other",
            "source_class": "staff_original",
            "consent_status": "not_required",
            "csrf_token": CSRF_VALUE,
        },
    )
    assert response.status_code == 400
    assert "画素数" in response.text


def test_csp_allows_blob_for_embedded_textures():
    """`connect-src` に blob: を許す（`docs/decisions.md` A-50）。

    three.js の GLTFLoader は **GLBに埋め込まれたテクスチャ**を Blob URL に
    切り出してから fetch する。これを塞ぐと、形は出るのに**真っ白なモデル**になる。
    実際に A3.5 の1件目でそうなった。
    """
    from app.main import CSP

    directives = dict(
        part.strip().split(" ", 1)
        for part in CSP.split(";")
        if part.strip() and " " in part.strip()
    )
    assert "blob:" in directives["connect-src"], CSP
    # 同じ理由で既に許可している2つも、外れていないことを確かめる
    assert "blob:" in directives["img-src"], CSP
    assert "blob:" in directives["worker-src"], CSP


def test_csp_still_allows_no_external_origin():
    """blob: を足しても、**外部への接続は一切許していない**ことを確かめる。"""
    from app.main import CSP

    assert "http://" not in CSP
    assert "https://" not in CSP
    assert "*" not in CSP
    assert "'unsafe-eval'" not in CSP
    assert "'unsafe-inline'" not in CSP.split("style-src")[0]  # script 側には無い


def test_a_textured_sample_glb_exists_for_the_viewer_tests():
    """テクスチャ付きの見本が必ず在る（A-50）。

    これが無いと、テクスチャの読込経路を通る試験が書けず、
    今回の不具合（CSP がテクスチャを止める）を捕まえられない。
    """
    from pathlib import Path

    from app.providers.mock import SAMPLES
    from app.services import glb_inspect

    root = Path(__file__).resolve().parents[1] / "fixtures"
    textured = []
    for name in SAMPLES:
        metrics = glb_inspect.inspect((root / name).read_bytes())
        if (metrics.texture_count or 0) > 0:
            textured.append(name)
    assert textured, f"モックの見本にテクスチャ付きが1つも無い: {SAMPLES}"
