"""仕様第13章の必須試験：4・5・6・7・8・16・17・19・20。

状態遷移、再起動からの復旧、lease、送信枠の返却、同意の強制、処理単位の分離。
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.db import session_scope
from app.models import Artifact, CostEntry, Generation, utcnow
from app.services.worker_steps import (
    download_step,
    reclaim_expired_leases,
    status_step,
    submit_step,
)
from tests.conftest import (
    CSRF_VALUE,
    add_asset_directly,
    create_experiment,
    create_live_experiment,
    drain_worker,
    form_token,
    preset_id,
    request_generation,
    set_consent,
    upload_asset,
)

WORKER = "test-worker"


def _variant(client, experiment_id, png_bytes) -> str:
    asset_id = upload_asset(client, experiment_id, png_bytes)
    return client.get(f"/api/assets/{asset_id}").json()["variants"][0]["id"]


def _accept(client, png_bytes, preset_code: str, *, name: str = "検証") -> tuple[str, str]:
    experiment_id = create_experiment(client, name)
    variant_id = _variant(client, experiment_id, png_bytes)
    response = request_generation(client, variant_id, preset_id(client, preset_code))
    assert response.status_code == 202, response.text
    return experiment_id, response.json()["id"]


def _status(generation_id: str) -> Generation:
    with session_scope() as db:
        return db.get(Generation, generation_id)


# --- 試験4 -------------------------------------------------------------------


def test_submit_timeout_does_not_repost(auth_client, png_bytes):
    """作成応答タイムアウト後に自動で再POSTしない（仕様第13章 試験4）。"""
    _, generation_id = _accept(auth_client, png_bytes, "mock-submit-timeout")

    assert submit_step(WORKER) is True
    generation = _status(generation_id)
    assert generation.tech_status == "submission_unknown"
    assert generation.provider_task_id is None
    # 送信された可能性があるため、枠は保持する
    assert generation.consumes_quota is True

    # 何周回しても自動で送信し直さない
    for _ in range(5):
        submit_step(WORKER)
        status_step(WORKER)
    generation = _status(generation_id)
    assert generation.tech_status == "submission_unknown"
    assert generation.provider_task_id is None


def test_submission_unknown_needs_human_resolution(auth_client, png_bytes):
    """受付結果不明は人による照合が必要（仕様第13章 試験6）。"""
    _, generation_id = _accept(auth_client, png_bytes, "mock-submit-timeout")
    submit_step(WORKER)
    assert _status(generation_id).tech_status == "submission_unknown"

    # 証跡がなければ照合できない
    without_evidence = auth_client.post(
        f"/api/generations/{generation_id}/resolve-submission",
        json={"outcome": "link", "provider_task_id": "mock:success:0:abcdef", "evidence": ""},
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert without_evidence.status_code == 409

    # 未作成を確認したら枠が返る
    resolved = auth_client.post(
        f"/api/generations/{generation_id}/resolve-submission",
        json={"outcome": "not_created", "evidence": "事業者の履歴に無いことを確認した"},
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert resolved.status_code == 200
    generation = _status(generation_id)
    assert generation.tech_status == "cancelled"
    assert generation.consumes_quota is False


def test_resolve_submission_can_link_existing_task(auth_client, png_bytes):
    _, generation_id = _accept(auth_client, png_bytes, "mock-submit-timeout")
    submit_step(WORKER)

    task_id = f"mock:success:{int(utcnow().timestamp())}:linkedtask00000"
    resolved = auth_client.post(
        f"/api/generations/{generation_id}/resolve-submission",
        json={
            "outcome": "link",
            "provider_task_id": task_id,
            "evidence": "事業者の管理画面で該当タスクを確認した",
        },
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert resolved.status_code == 200
    generation = _status(generation_id)
    assert generation.tech_status == "running"
    assert generation.provider_task_id == task_id
    # 送信された可能性があるため枠は保持したまま
    assert generation.consumes_quota is True

    drain_worker()
    assert _status(generation_id).tech_status == "ready_for_review"


# --- 試験5 -------------------------------------------------------------------


def test_restart_rechecks_the_same_task(auth_client, png_bytes):
    """タスクID保存済みで再起動すると、同じタスクを再確認する（仕様第13章 試験5）。"""
    _, generation_id = _accept(auth_client, png_bytes, "mock-delayed")

    submit_step(WORKER)
    generation = _status(generation_id)
    assert generation.tech_status == "running"
    task_id = generation.provider_task_id
    assert task_id

    # 別のワーカーID（＝再起動後）で処理を続ける
    with session_scope() as db:
        db.get(Generation, generation_id).next_check_at = utcnow()
    restarted = "test-worker-after-restart"
    assert reclaim_expired_leases(restarted) == 0
    assert status_step(restarted) >= 1

    generation = _status(generation_id)
    # 同じタスクIDのまま。新しい送信は行われていない
    assert generation.provider_task_id == task_id
    assert generation.tech_status in ("running", "downloading")

    drain_worker()
    generation = _status(generation_id)
    assert generation.tech_status == "ready_for_review"
    assert generation.provider_task_id == task_id


# --- 試験16 ------------------------------------------------------------------


def test_expired_lease_on_submitting_becomes_submission_unknown(auth_client, png_bytes):
    """lease切れの submitting は submission_unknown へ移り、再submitされない
    （仕様第13章 試験16）。"""
    _, generation_id = _accept(auth_client, png_bytes, "mock-standard")

    # 送信の途中でワーカーが停止した状態を作る
    with session_scope() as db:
        generation = db.get(Generation, generation_id)
        generation.tech_status = "submitting"
        generation.submitted_at = utcnow()
        generation.lease_owner = "dead-worker"
        generation.lease_until = utcnow() - timedelta(seconds=1)

    moved = reclaim_expired_leases("test-worker-after-restart")
    assert moved == 1

    generation = _status(generation_id)
    assert generation.tech_status == "submission_unknown"
    assert generation.provider_task_id is None
    assert generation.lease_owner is None
    # 枠は保持（課金された可能性を否定できない）
    assert generation.consumes_quota is True

    # 再submitされないこと
    for _ in range(3):
        submit_step("test-worker-after-restart")
    assert _status(generation_id).provider_task_id is None


def test_expired_lease_on_running_is_resumed(auth_client, png_bytes):
    """状態確認の途中で止まった場合は、そのまま再開してよい。"""
    _, generation_id = _accept(auth_client, png_bytes, "mock-delayed")
    submit_step(WORKER)

    with session_scope() as db:
        generation = db.get(Generation, generation_id)
        generation.lease_owner = "dead-worker"
        generation.lease_until = utcnow() - timedelta(seconds=1)

    reclaim_expired_leases("test-worker-after-restart")
    generation = _status(generation_id)
    assert generation.tech_status == "running"
    assert generation.lease_owner is None

    drain_worker()
    assert _status(generation_id).tech_status == "ready_for_review"


# --- 試験7 -------------------------------------------------------------------


def test_download_failure_only_retries_download(auth_client, png_bytes):
    """保存失敗は再取得だけを行い、課金生成を増やさない（仕様第13章 試験7）。"""
    experiment_id, generation_id = _accept(auth_client, png_bytes, "mock-download-failed")
    drain_worker()

    generation = _status(generation_id)
    assert generation.tech_status == "download_failed"
    # 生成そのものは終わっている。枠は保持し、返却しない
    assert generation.consumes_quota is True
    task_id = generation.provider_task_id

    summary = auth_client.get(f"/api/experiments/{experiment_id}").json()["summary"]
    assert summary["generations_total"] == 1

    retried = auth_client.post(
        f"/api/generations/{generation_id}/retry-download",
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert retried.status_code == 200
    assert _status(generation_id).tech_status == "downloading"

    drain_worker()
    generation = _status(generation_id)
    # 同じタスクを見に行くだけ。新しい生成は作られない
    assert generation.provider_task_id == task_id
    summary = auth_client.get(f"/api/experiments/{experiment_id}").json()["summary"]
    assert summary["generations_total"] == 1


def test_invalid_glb_can_be_resolved_as_provider_failure(auth_client, png_bytes):
    """再取得しても検査NGが続くとき、運営が事業者側の失敗と確定できる（仕様第8章）。"""
    _, generation_id = _accept(auth_client, png_bytes, "mock-invalid-glb")
    drain_worker()

    generation = _status(generation_id)
    assert generation.tech_status == "validation_failed"
    assert generation.consumes_quota is True

    resolved = auth_client.post(
        f"/api/generations/{generation_id}/resolve-artifact",
        json={"evidence": "2回取得したが同じ内容だった"},
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert resolved.status_code == 200
    generation = _status(generation_id)
    assert generation.tech_status == "provider_failed"
    # 事業者側の失敗と確定したので枠は返る
    assert generation.consumes_quota is False


# --- 試験17 ------------------------------------------------------------------


def test_provider_failure_releases_quota_and_records_cost(auth_client, png_bytes):
    """provider_failed で送信枠が返却され、費用は失敗・要照合として残る
    （仕様第13章 試験17）。"""
    experiment_id, generation_id = _accept(auth_client, png_bytes, "mock-provider-failed")
    drain_worker()

    generation = _status(generation_id)
    assert generation.tech_status == "provider_failed"
    assert generation.consumes_quota is False

    with session_scope() as db:
        kinds = {
            entry.kind
            for entry in db.scalars(
                select(CostEntry).where(CostEntry.generation_id == generation_id)
            ).all()
        }
    assert kinds == {"estimate", "failed_unreconciled"}

    summary = auth_client.get(f"/api/experiments/{experiment_id}").json()["summary"]
    assert summary["failed_technical"] == 1


def test_submission_unknown_keeps_quota(auth_client, png_bytes):
    """submission_unknown では枠を返却しない（仕様第13章 試験17）。"""
    _, generation_id = _accept(auth_client, png_bytes, "mock-submit-timeout")
    submit_step(WORKER)
    generation = _status(generation_id)
    assert generation.tech_status == "submission_unknown"
    assert generation.consumes_quota is True


def test_monitoring_pauses_after_timeout_without_declaring_failure(auth_client, png_bytes):
    """30分で monitoring_paused。外部失敗とは断定しない（仕様第8章）。"""
    _, generation_id = _accept(auth_client, png_bytes, "mock-slow")
    submit_step(WORKER)

    with session_scope() as db:
        generation = db.get(Generation, generation_id)
        generation.submitted_at = utcnow() - timedelta(minutes=31)
        generation.next_check_at = utcnow()

    status_step(WORKER)
    generation = _status(generation_id)
    assert generation.tech_status == "monitoring_paused"
    # 失敗とは断定していないので枠はそのまま
    assert generation.consumes_quota is True

    # 手動で再開できる
    resumed = auth_client.post(
        f"/api/generations/{generation_id}/refresh", headers={"x-csrf-token": CSRF_VALUE}
    )
    assert resumed.status_code == 200
    assert _status(generation_id).tech_status == "running"


def test_rate_limited_backs_off_without_failing(auth_client, png_bytes):
    """429 は失敗にせず、Retry-After を考慮して待つ（仕様第8章）。"""
    _, generation_id = _accept(auth_client, png_bytes, "mock-rate-limited")
    submit_step(WORKER)
    with session_scope() as db:
        db.get(Generation, generation_id).next_check_at = utcnow()
    status_step(WORKER)

    generation = _status(generation_id)
    assert generation.tech_status == "running"
    assert generation.error_kind == "rate_limited"
    assert generation.next_check_at > utcnow()
    assert generation.consumes_quota is True


# --- 試験8 -------------------------------------------------------------------


def test_comparison_keeps_the_other_side_when_one_fails(auth_client, png_bytes):
    """2社の片方失敗でも他方の成果物と費用が残る（仕様第13章 試験8）。"""
    experiment_id = create_experiment(auth_client, "片方失敗")
    variant_id = _variant(auth_client, experiment_id, png_bytes)

    # mock_a は成功、mock_b は事業者失敗にする
    with session_scope() as db:
        from app.models import Preset

        failing = db.scalar(select(Preset).where(Preset.code == "mock-b-standard"))
        failing.settings_json = '{"scenario": "provider_failed"}'

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
    ids = [g["id"] for g in response.json()["generations"]]
    drain_worker()

    statuses = {}
    with session_scope() as db:
        for generation_id in ids:
            generation = db.get(Generation, generation_id)
            statuses[generation.provider] = generation.tech_status
            if generation.provider == "mock_a":
                artifact = db.scalar(
                    select(Artifact).where(Artifact.generation_id == generation_id)
                )
                assert artifact is not None
                assert artifact.bytes > 0

    assert statuses["mock_a"] == "ready_for_review"
    assert statuses["mock_b"] == "provider_failed"

    # 片方の失敗で他方の費用が消えない
    summary = auth_client.get(f"/api/experiments/{experiment_id}").json()["summary"]
    assert summary["estimate_micro_usd"] == 600_000
    assert summary["ready_for_review"] == 1
    assert summary["failed_technical"] == 1


# --- 試験19 ------------------------------------------------------------------


def test_consent_missing_blocks_live_submission_via_api(external_provider):
    """利用同意がmissingの画像は、APIを直接叩いても外部送信されない
    （仕様第13章 試験19）。"""
    experiment_id = create_live_experiment()
    variant_id = add_asset_directly(experiment_id, consent_status="missing")

    from app.db import session_scope as scope
    from app.models import Operator
    from app.services.generation import GenerationRejected, create_generation

    with scope() as db:
        operator = db.scalars(select(Operator)).first()
        try:
            create_generation(
                db,
                operator=operator,
                variant_id=variant_id,
                preset_id=external_provider["preset_id"],
            )
            raise AssertionError("同意未取得なのに受け付けられました")
        except GenerationRejected as exc:
            assert "利用同意" in str(exc)

    drain_worker()
    # 外部のアダプターは一度も呼ばれていない
    assert external_provider["adapter"].submitted == []


def test_consent_revoked_after_acceptance_is_not_submitted(external_provider):
    """受付のあとで同意が取り下げられた場合も、送信の直前で止める（仕様第12章）。"""
    experiment_id = create_live_experiment()
    variant_id = add_asset_directly(experiment_id, consent_status="granted")

    from app.db import session_scope as scope
    from app.models import Operator
    from app.services.generation import create_generation

    with scope() as db:
        operator = db.scalars(select(Operator)).first()
        generation = create_generation(
            db,
            operator=operator,
            variant_id=variant_id,
            preset_id=external_provider["preset_id"],
        )
        generation_id = generation.id

    # 送信前に同意が取り下げられる
    set_consent(variant_id, "missing")
    drain_worker()

    assert external_provider["adapter"].submitted == []
    generation = _status(generation_id)
    assert generation.tech_status == "cancelled"
    assert generation.error_kind == "consent_missing"
    # 外部へ出ていないので枠は返る
    assert generation.consumes_quota is False


# --- 試験20 ------------------------------------------------------------------


def test_status_checks_run_even_while_a_download_is_pending(auth_client, png_bytes):
    """ダウンロード中のタスクがあっても、他タスクの状態確認が同一ループ内で行われる
    （仕様第13章 試験20）。"""
    experiment_id = create_experiment(auth_client, "処理単位の分離")
    asset_a = upload_asset(auth_client, experiment_id, png_bytes)
    asset_b = upload_asset(auth_client, experiment_id, png_bytes)
    variant_a = auth_client.get(f"/api/assets/{asset_a}").json()["variants"][0]["id"]
    variant_b = auth_client.get(f"/api/assets/{asset_b}").json()["variants"][0]["id"]

    first = request_generation(auth_client, variant_a, preset_id(auth_client, "mock-a-standard"))
    second = request_generation(auth_client, variant_b, preset_id(auth_client, "mock-b-standard"))
    assert first.status_code == 202 and second.status_code == 202
    downloading_id, running_id = first.json()["id"], second.json()["id"]

    # 片方は保存待ち、もう片方は生成中の状態を作る
    with session_scope() as db:
        downloading = db.get(Generation, downloading_id)
        downloading.tech_status = "downloading"
        downloading.provider_task_id = "mock_a:success:0:aaaaaaaaaaaaaaaa"
        downloading.provider_result_ref = "sample_cube.glb"
        downloading.next_check_at = utcnow()

        running = db.get(Generation, running_id)
        running.tech_status = "running"
        running.provider_task_id = "mock_b:success:0:bbbbbbbbbbbbbbbb"
        running.submitted_at = utcnow()
        running.next_check_at = utcnow()

    # 状態確認とダウンロードは別の処理単位。1周でどちらも進む
    checked = status_step(WORKER)
    downloaded = download_step(WORKER)

    assert checked >= 1
    assert downloaded is True
    assert _status(running_id).tech_status == "downloading"
    assert _status(downloading_id).tech_status == "ready_for_review"


def test_download_step_handles_one_item_per_cycle(auth_client, png_bytes):
    """ダウンロードは1周に1件だけ（長い保存が他を止めないようにする）。"""
    experiment_id = create_experiment(auth_client, "ダウンロードは1件")
    ids = []
    for _ in range(2):
        asset_id = upload_asset(auth_client, experiment_id, png_bytes)
        variant_id = auth_client.get(f"/api/assets/{asset_id}").json()["variants"][0]["id"]
        response = request_generation(
            auth_client, variant_id, preset_id(auth_client, "mock-standard")
        )
        assert response.status_code == 202
        ids.append(response.json()["id"])

    with session_scope() as db:
        for generation_id in ids:
            generation = db.get(Generation, generation_id)
            generation.tech_status = "downloading"
            generation.provider_task_id = f"mock:success:0:{generation_id[:16].replace('-', '')}"
            generation.provider_result_ref = "sample_cube.glb"
            generation.next_check_at = utcnow()

    assert download_step(WORKER) is True
    statuses = [_status(i).tech_status for i in ids]
    assert statuses.count("ready_for_review") == 1
    assert statuses.count("downloading") == 1

    assert download_step(WORKER) is True
    assert all(_status(i).tech_status == "ready_for_review" for i in ids)
