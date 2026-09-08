"""ワーカー入口の確認（A1は保険としての処理のみ）。"""

from __future__ import annotations

from tests.conftest import CSRF_VALUE, create_experiment, mock_preset_id, upload_asset


def test_worker_processes_leftover_queued(auth_client, png_bytes, monkeypatch):
    from app.db import session_scope
    from app.models import Generation
    from app.worker import process_once, worker_identity

    experiment_id = create_experiment(auth_client)
    asset_id = upload_asset(auth_client, experiment_id, png_bytes)
    variant_id = auth_client.get(f"/api/assets/{asset_id}").json()["variants"][0]["id"]
    generation_id = auth_client.post(
        "/api/generations",
        json={"asset_variant_id": variant_id, "preset_id": mock_preset_id(auth_client)},
        headers={"x-csrf-token": CSRF_VALUE},
    ).json()["id"]

    # 受付直後の状態に戻し、ワーカーが拾えることを確かめる
    with session_scope() as db:
        generation = db.get(Generation, generation_id)
        generation.tech_status = "queued"
        generation.completed_at = None

    assert process_once() == 1
    with session_scope() as db:
        assert db.get(Generation, generation_id).tech_status == "ready_for_review"

    assert worker_identity() != worker_identity()  # 起動ごとに異なる識別子
