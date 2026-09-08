"""仕様第13章の必須試験：1・2・3・9・13・15。

冪等キー、上限額、送信枠、費用の二重計上、除算の安全。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from tests.conftest import (
    CSRF_VALUE,
    create_experiment,
    form_token,
    preset_id,
    request_generation,
    set_experiment_cap,
    upload_asset,
)


def _variant(client, experiment_id, png_bytes) -> str:
    asset_id = upload_asset(client, experiment_id, png_bytes)
    return client.get(f"/api/assets/{asset_id}").json()["variants"][0]["id"]


# --- 試験1 -------------------------------------------------------------------


def test_same_idempotency_key_creates_one_request(auth_client, png_bytes):
    """同じ冪等キーを連打しても依頼が1件（仕様第13章 試験1）。"""
    experiment_id = create_experiment(auth_client)
    variant_id = _variant(auth_client, experiment_id, png_bytes)
    preset = preset_id(auth_client, "mock-standard")
    key = form_token(auth_client)

    responses = [request_generation(auth_client, variant_id, preset, key=key) for _ in range(5)]
    assert all(r.status_code == 202 for r in responses), [r.text for r in responses]
    ids = {r.json()["id"] for r in responses}
    assert len(ids) == 1

    summary = auth_client.get(f"/api/experiments/{experiment_id}").json()["summary"]
    assert summary["generations_total"] == 1


def test_same_key_different_body_is_409(auth_client, png_bytes):
    """同キー・別本文は409（仕様第7章）。"""
    experiment_id = create_experiment(auth_client)
    variant_id = _variant(auth_client, experiment_id, png_bytes)
    key = form_token(auth_client)

    first = request_generation(
        auth_client, variant_id, preset_id(auth_client, "mock-standard"), key=key
    )
    assert first.status_code == 202
    second = request_generation(
        auth_client, variant_id, preset_id(auth_client, "mock-delayed"), key=key
    )
    assert second.status_code == 409
    assert "冪等キー" in second.json()["detail"]


def test_idempotency_key_must_be_server_issued(auth_client, png_bytes):
    experiment_id = create_experiment(auth_client)
    variant_id = _variant(auth_client, experiment_id, png_bytes)
    response = request_generation(
        auth_client,
        variant_id,
        preset_id(auth_client, "mock-standard"),
        key="client-invented-key.0123456789abcdef",
    )
    assert response.status_code == 400


def test_idempotency_key_is_required(auth_client, png_bytes):
    experiment_id = create_experiment(auth_client)
    variant_id = _variant(auth_client, experiment_id, png_bytes)
    response = auth_client.post(
        "/api/generations",
        json={"asset_variant_id": variant_id, "preset_id": preset_id(auth_client, "mock-standard")},
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert response.status_code == 400
    assert "Idempotency-Key" in response.json()["detail"]


# --- 試験2 -------------------------------------------------------------------


def test_comparison_is_all_or_nothing_when_budget_is_short(auth_client, png_bytes):
    """2社比較の上限額が片方分しかなければ両方とも未送信（仕様第13章 試験2）。"""
    experiment_id = create_experiment(auth_client, "比較の上限額")
    variant_id = _variant(auth_client, experiment_id, png_bytes)
    # 1件 $0.30 の見積を2社分＝$0.60 必要なところ、$0.40 しか無い
    set_experiment_cap(experiment_id, "0.40")

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
    assert response.status_code == 409
    assert "上限額" in response.json()["detail"]

    # 片方だけ作られていないこと
    summary = auth_client.get(f"/api/experiments/{experiment_id}").json()["summary"]
    assert summary["generations_total"] == 0


def test_comparison_succeeds_when_budget_covers_both(auth_client, png_bytes):
    experiment_id = create_experiment(auth_client, "比較")
    variant_id = _variant(auth_client, experiment_id, png_bytes)
    set_experiment_cap(experiment_id, "1.00")

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
    body = response.json()
    assert len(body["generations"]) == 2
    # A/B の割当が保存されている（画面での秘匿と開示は A4）
    assert sorted(g["label"] for g in body["generations"]) == ["A", "B"]


# --- 試験3 -------------------------------------------------------------------


def test_concurrent_requests_do_not_exceed_cost_cap(auth_client, png_bytes):
    """同時受付でも上限額を超過しない（仕様第13章 試験3）。"""
    experiment_id = create_experiment(auth_client, "同時受付・上限額")
    variant_id = _variant(auth_client, experiment_id, png_bytes)
    # 1件 $1.00 の見積。上限 $3.00 なので3件までしか通らない
    set_experiment_cap(experiment_id, "3.00")
    preset = preset_id(auth_client, "mock-priced")

    keys = [form_token(auth_client) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda k: request_generation(auth_client, variant_id, preset, key=k), keys)
        )

    accepted = [r for r in results if r.status_code == 202]
    rejected = [r for r in results if r.status_code == 409]
    assert len(accepted) + len(rejected) == 8, [r.status_code for r in results]
    # 送信枠（2回）と上限額（3件分）のうち、厳しいほうで止まる
    assert len(accepted) <= 2
    assert len(rejected) >= 6

    summary = auth_client.get(f"/api/experiments/{experiment_id}").json()["summary"]
    assert summary["generations_total"] == len(accepted)
    assert summary["estimate_micro_usd"] <= 3_000_000


def test_concurrent_requests_do_not_exceed_quota(auth_client, png_bytes):
    """同時受付でも送信枠を超過しない（仕様第13章 試験3）。"""
    experiment_id = create_experiment(auth_client, "同時受付・送信枠")
    variant_id = _variant(auth_client, experiment_id, png_bytes)
    preset = preset_id(auth_client, "mock-standard")

    keys = [form_token(auth_client) for _ in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(lambda k: request_generation(auth_client, variant_id, preset, key=k), keys)
        )

    accepted = [r for r in results if r.status_code == 202]
    assert len(accepted) == 2, [(r.status_code, r.text[:80]) for r in results]
    for rejected in (r for r in results if r.status_code != 202):
        assert rejected.status_code == 409
        assert "送信枠" in rejected.json()["detail"]


# --- 試験9 -------------------------------------------------------------------


def test_quota_cannot_be_bypassed_by_variant_or_preset(auth_client, png_bytes):
    """上限2回を画像加工・プリセット変更で回避できない（仕様第13章 試験9）。"""
    experiment_id = create_experiment(auth_client, "送信枠")
    asset_id = upload_asset(auth_client, experiment_id, png_bytes)

    # 加工版を追加する
    processed = auth_client.post(
        f"/assets/{asset_id}/variants",
        files={"file": ("edited.png", png_bytes, "image/png")},
        data={"edit_types": "crop", "edit_tool": "外部ツール", "csrf_token": CSRF_VALUE},
        follow_redirects=False,
    )
    assert processed.status_code == 303
    variants = auth_client.get(f"/api/assets/{asset_id}").json()["variants"]
    assert len(variants) == 2
    original_id, processed_id = variants[0]["id"], variants[1]["id"]

    # 同じ provider（mock）で 2 回まで
    first = request_generation(auth_client, original_id, preset_id(auth_client, "mock-standard"))
    assert first.status_code == 202
    second = request_generation(auth_client, processed_id, preset_id(auth_client, "mock-delayed"))
    assert second.status_code == 202

    # 加工版でもプリセットを変えても3回目は通らない
    third = request_generation(auth_client, processed_id, preset_id(auth_client, "mock-slow"))
    assert third.status_code == 409
    assert "送信枠" in third.json()["detail"]

    # 別のサービスなら枠は独立している
    other = request_generation(auth_client, original_id, preset_id(auth_client, "mock-a-standard"))
    assert other.status_code == 202


def test_quota_can_be_extended_only_with_reason(auth_client, png_bytes):
    experiment_id = create_experiment(auth_client, "枠追加")
    asset_id = upload_asset(auth_client, experiment_id, png_bytes)
    variant_id = auth_client.get(f"/api/assets/{asset_id}").json()["variants"][0]["id"]
    preset = preset_id(auth_client, "mock-standard")

    for _ in range(2):
        assert request_generation(auth_client, variant_id, preset).status_code == 202
    assert request_generation(auth_client, variant_id, preset).status_code == 409

    # 理由がなければ追加できない
    without_reason = auth_client.post(
        f"/api/assets/{asset_id}/quota",
        json={"provider": "mock", "additional": 1, "reason": ""},
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert without_reason.status_code == 400

    granted = auth_client.post(
        f"/api/assets/{asset_id}/quota",
        json={"provider": "mock", "additional": 1, "reason": "検証のため1回だけ追加する"},
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert granted.status_code == 200
    assert granted.json()["limit"] == 3
    assert request_generation(auth_client, variant_id, preset).status_code == 202


# --- 試験13 ------------------------------------------------------------------


def test_duplicate_manual_cost_is_not_double_counted(auth_client, png_bytes):
    """手動費用入力の重複送信でも実績が二重計上されない（仕様第13章 試験13）。"""
    from tests.conftest import create_and_run

    experiment_id = create_experiment(auth_client, "費用")
    variant_id = _variant(auth_client, experiment_id, png_bytes)
    generation_id = create_and_run(auth_client, variant_id, preset_id(auth_client, "mock-priced"))

    payload = {
        "amount_micro_usd": 250_000,
        "reference": "invoice-2026-09-0001",
        "evidence_note": "事業者の管理画面で確認",
        "checked_on": "2026-09-08",
    }
    results = [
        auth_client.post(
            f"/api/generations/{generation_id}/costs",
            json=payload,
            headers={"x-csrf-token": CSRF_VALUE},
        )
        for _ in range(4)
    ]
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert sum(1 for r in results if r.json()["duplicated"]) == 3

    summary = auth_client.get(f"/api/experiments/{experiment_id}").json()["summary"]
    assert summary["confirmed_micro_usd"] == 250_000
    # 見積と実績は別の行として保存され、混ざらない
    assert summary["estimate_micro_usd"] == 1_000_000


# --- 試験15 ------------------------------------------------------------------


def test_no_division_error_with_empty_data(auth_client, png_bytes):
    """合格0件・評価0件・生成0件で除算エラーにならない（仕様第13章 試験15）。"""
    empty_id = create_experiment(auth_client, "生成0件")
    summary = auth_client.get(f"/api/experiments/{empty_id}").json()["summary"]
    assert summary["generations_total"] == 0
    assert summary["cost_per_passed_micro_usd"] is None

    # 生成はあるが評価が0件
    from tests.conftest import create_and_run

    experiment_id = create_experiment(auth_client, "評価0件")
    variant_id = _variant(auth_client, experiment_id, png_bytes)
    create_and_run(auth_client, variant_id, preset_id(auth_client, "mock-priced"))
    summary = auth_client.get(f"/api/experiments/{experiment_id}").json()["summary"]
    assert summary["unreviewed"] == 1
    assert summary["passed"] == 0
    assert summary["cost_per_passed_micro_usd"] is None
    assert summary["is_provisional"] is True

    # 画面もCSVも壊れない
    assert auth_client.get(f"/experiments/{experiment_id}").status_code == 200
    assert auth_client.get(f"/api/experiments/{experiment_id}/export.csv").status_code == 200
