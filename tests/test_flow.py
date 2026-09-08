"""A1の正常系：ログイン→画像→モック生成→GLB→評価→CSV。"""

from __future__ import annotations

from tests.conftest import CSRF_VALUE, create_experiment, mock_preset_id, upload_asset


def test_login_required_redirects_html(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_with_wrong_password_is_rejected(client, operator):
    client.cookies.set("art3d_csrf", CSRF_VALUE)
    response = client.post(
        "/login",
        data={"login_name": "teacher1", "password": "wrong-password", "csrf_token": CSRF_VALUE},
    )
    assert response.status_code == 401
    assert "ログイン名またはパスワードが違います" in response.text


def test_full_mock_flow(auth_client, png_bytes):
    experiment_id = create_experiment(auth_client)
    asset_id = upload_asset(auth_client, experiment_id, png_bytes)

    asset = auth_client.get(f"/api/assets/{asset_id}").json()
    assert len(asset["variants"]) == 1
    variant = asset["variants"][0]
    assert variant["kind"] == "original"
    # 送信用コピーが作られている（EXIF除去、仕様第12章）
    assert variant["submission_sha256"]
    assert variant["mime"] == "image/png"

    response = auth_client.post(
        "/api/generations",
        json={"asset_variant_id": variant["id"], "preset_id": mock_preset_id(auth_client)},
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert response.status_code == 202, response.text
    generation_id = response.json()["id"]

    detail = auth_client.get(f"/api/generations/{generation_id}").json()
    assert detail["tech_status"] == "ready_for_review"
    assert detail["is_live"] is False
    assert detail["artifact"] is not None
    assert detail["artifact"]["metrics"]["triangle_count"] > 0

    glb = auth_client.get(f"/api/files/{detail['artifact']['id']}?kind=artifact")
    assert glb.status_code == 200
    assert glb.content[:4] == b"glTF"

    # 評価：スマホ未確認なので合格にはならない（仕様第10章）
    review = auth_client.post(
        f"/api/generations/{generation_id}/reviews",
        json={
            "score_fidelity": 5,
            "score_shape": 5,
            "score_color": 5,
            "score_appeal": 5,
            "verdict": "pass",
            "work_seconds": 120,
        },
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert review.status_code == 201, review.text
    assert review.json()["is_pass"] is False

    # スマホ確認済みで再評価すると合格になり、改訂として履歴が増える
    revised = auth_client.post(
        f"/api/generations/{generation_id}/reviews",
        json={
            "score_fidelity": 5,
            "score_shape": 4,
            "score_color": 4,
            "score_appeal": 4,
            "score_mobile": 4,
            "mobile_checked": True,
            "verdict": "pass",
            "work_seconds": 60,
        },
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert revised.status_code == 201
    assert revised.json()["revision"] == 2
    assert revised.json()["is_pass"] is True

    summary = auth_client.get(f"/api/experiments/{experiment_id}").json()["summary"]
    assert summary["passed"] == 1
    assert summary["unreviewed"] == 0
    assert summary["is_provisional"] is False

    csv_response = auth_client.get(f"/api/experiments/{experiment_id}/export.csv")
    assert csv_response.status_code == 200
    body = csv_response.content.decode("utf-8")
    assert body.startswith("﻿")  # UTF-8 BOM（仕様第6.6章）
    assert generation_id in body
    assert "教室利用合格" in body


def test_defect_tag_blocks_pass(auth_client, png_bytes):
    experiment_id = create_experiment(auth_client)
    asset_id = upload_asset(auth_client, experiment_id, png_bytes)
    variant_id = auth_client.get(f"/api/assets/{asset_id}").json()["variants"][0]["id"]
    generation_id = auth_client.post(
        "/api/generations",
        json={"asset_variant_id": variant_id, "preset_id": mock_preset_id(auth_client)},
        headers={"x-csrf-token": CSRF_VALUE},
    ).json()["id"]

    review = auth_client.post(
        f"/api/generations/{generation_id}/reviews",
        json={
            "score_fidelity": 5,
            "score_shape": 5,
            "score_color": 5,
            "score_appeal": 5,
            "score_mobile": 5,
            "mobile_checked": True,
            "defect_tags": ["face_broken"],
            "verdict": "pass",
        },
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert review.status_code == 201
    # 重大不具合タグがあれば合格にならない（仕様第10章）
    assert review.json()["is_pass"] is False


def test_summary_with_no_generations_does_not_divide_by_zero(auth_client):
    experiment_id = create_experiment(auth_client, "空のセット")
    summary = auth_client.get(f"/api/experiments/{experiment_id}").json()["summary"]
    assert summary["generations_total"] == 0
    assert summary["passed"] == 0
    # 合格0件の単価は 0 ではなく「算出不可」（null）
    assert summary["cost_per_passed_micro_usd"] is None


def test_pages_render(auth_client, png_bytes):
    experiment_id = create_experiment(auth_client)
    asset_id = upload_asset(auth_client, experiment_id, png_bytes)
    variant_id = auth_client.get(f"/api/assets/{asset_id}").json()["variants"][0]["id"]
    generation_id = auth_client.post(
        "/api/generations",
        json={"asset_variant_id": variant_id, "preset_id": mock_preset_id(auth_client)},
        headers={"x-csrf-token": CSRF_VALUE},
    ).json()["id"]

    for path in (
        "/",
        f"/experiments/{experiment_id}",
        f"/assets/{asset_id}",
        f"/generations/{generation_id}",
    ):
        response = auth_client.get(path)
        assert response.status_code == 200, path
        assert "text/html" in response.headers["content-type"]
    # 3D表示は同梱した固定版を読む（CDNのlatestを動的取得しない。仕様第3章）
    page = auth_client.get(f"/generations/{generation_id}").text
    assert "/static/vendor/model-viewer-4.3.1.min.js" not in page  # viewer.js 経由で読む
    assert '<script type="module" src="/static/viewer.js">' in page
