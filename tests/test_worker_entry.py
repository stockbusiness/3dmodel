"""ワーカーの入口と識別子（仕様第8章）。"""

from __future__ import annotations

from tests.conftest import (
    create_experiment,
    drain_worker,
    mock_preset_id,
    request_generation,
    upload_asset,
)


def test_worker_identity_is_unique_per_start():
    from app.worker import worker_identity

    assert worker_identity() != worker_identity()


def test_worker_drives_generation_to_ready(auth_client, png_bytes):
    """受付だけでは完了せず、ワーカーが動いて評価待ちになる。"""
    from app.db import session_scope
    from app.models import Generation

    experiment_id = create_experiment(auth_client)
    asset_id = upload_asset(auth_client, experiment_id, png_bytes)
    variant_id = auth_client.get(f"/api/assets/{asset_id}").json()["variants"][0]["id"]

    response = request_generation(auth_client, variant_id, mock_preset_id(auth_client))
    assert response.status_code == 202
    generation_id = response.json()["id"]

    # 受付した時点ではまだ外部へ送っていない
    with session_scope() as db:
        assert db.get(Generation, generation_id).tech_status == "queued"
        assert db.get(Generation, generation_id).provider_task_id is None

    drain_worker()

    with session_scope() as db:
        generation = db.get(Generation, generation_id)
        assert generation.tech_status == "ready_for_review"
        assert generation.provider_task_id
        assert generation.lease_owner is None
