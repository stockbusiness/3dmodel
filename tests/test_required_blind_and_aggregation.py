"""仕様第13章の必須試験：12・18、および判定表と校正の確認。

12：モックと実API、未評価と合格、ブラインドと非ブラインド、早期確認と通常検証を
    集計で混同しない
18：ブラインド中の比較について、画面HTML・APIレスポンス・配信URL・ファイル名に
    サービス名・モデルID・プリセット名が含まれない。評価確定後は含まれる
"""

from __future__ import annotations

from sqlalchemy import select

from app.db import session_scope
from app.models import Experiment, Generation, Operator, Review
from tests.conftest import (
    CSRF_VALUE,
    create_experiment,
    drain_worker,
    form_token,
    preset_id,
    upload_asset,
)

PASSING_REVIEW = {
    "score_fidelity": 5,
    "score_shape": 4,
    "score_color": 4,
    "score_appeal": 4,
    "score_mobile": 4,
    "mobile_checked": True,
    "verdict": "pass",
    "work_seconds": 60,
}


def _variant(client, experiment_id, png_bytes) -> str:
    asset_id = upload_asset(client, experiment_id, png_bytes)
    return client.get(f"/api/assets/{asset_id}").json()["variants"][0]["id"]


def _make_comparison(client, experiment_id, png_bytes) -> tuple[str, list[str]]:
    variant_id = _variant(client, experiment_id, png_bytes)
    response = client.post(
        "/api/comparisons",
        json={
            "asset_variant_id": variant_id,
            "preset_ids": [
                preset_id(client, "mock-a-standard"),
                preset_id(client, "mock-b-standard"),
            ],
        },
        headers={"x-csrf-token": CSRF_VALUE, "Idempotency-Key": form_token(client)},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    return body["id"], [g["id"] for g in body["generations"]]


def _review(client, generation_id: str, **overrides):
    payload = {**PASSING_REVIEW, **overrides}
    return client.post(
        f"/api/generations/{generation_id}/reviews",
        json=payload,
        headers={"x-csrf-token": CSRF_VALUE},
    )


def _set_cap(experiment_id: str, usd: str = "100") -> None:
    from tests.conftest import set_experiment_cap

    set_experiment_cap(experiment_id, usd)


# --- 試験18 ------------------------------------------------------------------


def test_blind_hides_provider_everywhere_until_reviews_are_done(auth_client, png_bytes):
    """ブラインド中はどこにもサービス名等が出ない。確定後は出る。"""
    experiment_id = create_experiment(auth_client, "ブラインド")
    _set_cap(experiment_id)
    comparison_id, generation_ids = _make_comparison(auth_client, experiment_id, png_bytes)
    drain_worker()

    with session_scope() as db:
        providers = sorted(db.get(Generation, gid).provider for gid in generation_ids)
        preset_codes = []
        for gid in generation_ids:
            snapshot = db.get(Generation, gid).preset_snapshot_json
            preset_codes.append(snapshot)
    assert providers == ["mock_a", "mock_b"]

    secrets = ["mock_a", "mock_b", "mock-a-standard", "mock-b-standard", "mock-shape-v1"]

    # 1. 比較のAPI
    body = auth_client.get(f"/api/comparisons/{comparison_id}").json()
    assert body["revealed"] is False
    for entry in body["generations"]:
        assert entry["provider"] is None
        assert entry["preset_code"] is None
        assert entry["model_id"] is None
    assert not any(secret in str(body) for secret in secrets)

    # 2. 生成のAPI
    for generation_id in generation_ids:
        detail = auth_client.get(f"/api/generations/{generation_id}").json()
        assert detail["blind"] is True
        assert detail["provider"] is None
        assert detail["preset"]["code"] is None
        assert detail["preset"]["model_id"] is None
        assert detail["provider_task_id"] is None
        assert not any(secret in str(detail) for secret in secrets)

    # 3. 一覧のAPI
    listing = auth_client.get(f"/api/experiments/{experiment_id}/generations").json()
    assert all(row["provider"] is None for row in listing)

    # 4. 画面のHTML
    for path in (
        f"/comparisons/{comparison_id}",
        f"/generations/{generation_ids[0]}",
        f"/generations/{generation_ids[1]}",
    ):
        html = auth_client.get(path).text
        for secret in secrets:
            assert secret not in html, f"{path} に {secret} が出ています"

    # 5. 配信URLとファイル名
    for generation_id in generation_ids:
        artifact = auth_client.get(f"/api/generations/{generation_id}").json()["artifact"]
        assert artifact is not None
        url = f"/api/files/{artifact['id']}?kind=artifact"
        assert not any(secret in url for secret in secrets)
        response = auth_client.get(url)
        disposition = response.headers["content-disposition"]
        assert not any(secret in disposition for secret in secrets)

    # --- 評価を確定すると開示される ---
    assert _review(auth_client, generation_ids[0]).status_code == 201
    assert auth_client.get(f"/api/comparisons/{comparison_id}").json()["revealed"] is False
    assert _review(auth_client, generation_ids[1]).status_code == 201

    revealed = auth_client.get(f"/api/comparisons/{comparison_id}").json()
    assert revealed["revealed"] is True
    assert sorted(entry["provider"] for entry in revealed["generations"]) == ["mock_a", "mock_b"]

    html = auth_client.get(f"/comparisons/{comparison_id}").text
    assert "mock_a" in html and "mock_b" in html

    detail = auth_client.get(f"/api/generations/{generation_ids[0]}").json()
    assert detail["blind"] is False
    assert detail["provider"] in ("mock_a", "mock_b")
    assert detail["provider_task_id"]


def test_reveal_requires_a_reason_and_is_audited(auth_client, png_bytes):
    experiment_id = create_experiment(auth_client, "手動開示")
    _set_cap(experiment_id)
    comparison_id, _ = _make_comparison(auth_client, experiment_id, png_bytes)

    without_reason = auth_client.post(
        f"/api/comparisons/{comparison_id}/reveal",
        json={"reason": ""},
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert without_reason.status_code == 400

    revealed = auth_client.post(
        f"/api/comparisons/{comparison_id}/reveal",
        json={"reason": "評価前だが運営が確認する必要があるため"},
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert revealed.status_code == 200
    assert auth_client.get(f"/api/comparisons/{comparison_id}").json()["revealed"] is True

    from app.models import AuditEvent

    with session_scope() as db:
        events = db.scalars(
            select(AuditEvent).where(
                AuditEvent.target_id == comparison_id, AuditEvent.action == "reveal_manual"
            )
        ).all()
    assert len(events) == 1
    assert "運営が確認" in events[0].reason


def test_reviews_record_whether_they_were_blind(auth_client, png_bytes):
    """非ブラインドで行った評価は集計で区別する（仕様第10章）。"""
    experiment_id = create_experiment(auth_client, "ブラインドの記録")
    _set_cap(experiment_id)
    comparison_id, generation_ids = _make_comparison(auth_client, experiment_id, png_bytes)
    drain_worker()

    _review(auth_client, generation_ids[0])  # ブラインド中の評価
    auth_client.post(
        f"/api/comparisons/{comparison_id}/reveal",
        json={"reason": "残りを開示してから評価する"},
        headers={"x-csrf-token": CSRF_VALUE},
    )
    _review(auth_client, generation_ids[1])  # 開示後の評価

    with session_scope() as db:
        flags = {
            review.generation_id: review.was_blind
            for review in db.scalars(
                select(Review).where(Review.generation_id.in_(generation_ids))
            ).all()
        }
    assert flags[generation_ids[0]] is True
    assert flags[generation_ids[1]] is False

    report = auth_client.get(f"/api/experiments/{experiment_id}/report").json()
    assert report["reviews"]["blind"] == 1
    assert report["reviews"]["non_blind"] == 1


# --- 試験12 ------------------------------------------------------------------


def test_mock_and_live_are_not_mixed(auth_client, png_bytes, operator):
    """モックと実APIを集計で混同しない。"""
    from tests.conftest import add_asset_directly, create_live_experiment

    mock_id = create_experiment(auth_client, "モックのセット")
    _set_cap(mock_id)
    _make_comparison(auth_client, mock_id, png_bytes)
    drain_worker()

    live_id = create_live_experiment("実APIのセット")
    add_asset_directly(live_id)

    mock_report = auth_client.get(f"/api/experiments/{mock_id}/report").json()
    live_report = auth_client.get(f"/api/experiments/{live_id}/report").json()

    assert mock_report["experiment"]["is_live"] is False
    assert live_report["experiment"]["is_live"] is True
    # 実APIのセットにはモックの生成が入っていない
    assert live_report["cells"] == []
    assert mock_report["cells"] != []


def test_early_check_and_standard_are_not_mixed(auth_client, png_bytes):
    """早期確認（A3.5）と通常検証を集計で混同しない。"""
    standard_id = create_experiment(auth_client, "通常検証")
    _set_cap(standard_id)
    _make_comparison(auth_client, standard_id, png_bytes)
    drain_worker()

    with session_scope() as db:
        early = Experiment(
            name="早期確認",
            is_live=False,
            track="early_check",
            cost_cap_micro_usd=100_000_000,
            created_by=db.scalars(select(Operator)).first().id,
        )
        db.add(early)
        db.flush()
        early_id = early.id

    standard = auth_client.get(f"/api/experiments/{standard_id}/report").json()
    early_report = auth_client.get(f"/api/experiments/{early_id}/report").json()

    assert standard["experiment"]["track"] == "standard"
    assert early_report["experiment"]["track"] == "early_check"
    assert early_report["cells"] == []


def test_unreviewed_is_not_counted_as_pass(auth_client, png_bytes):
    """未評価と合格を混同しない。未評価が残るあいだは暫定と示す。"""
    experiment_id = create_experiment(auth_client, "未評価と合格")
    _set_cap(experiment_id)
    _, generation_ids = _make_comparison(auth_client, experiment_id, png_bytes)
    drain_worker()

    report = auth_client.get(f"/api/experiments/{experiment_id}/report").json()
    for cell in report["cells"]:
        assert cell["unreviewed"] == 1
        assert cell["first_pass_assets"] == 0
        assert cell["is_provisional"] is True

    summary = auth_client.get(f"/api/experiments/{experiment_id}").json()["summary"]
    assert summary["passed"] == 0
    assert summary["is_provisional"] is True

    _review(auth_client, generation_ids[0])
    report = auth_client.get(f"/api/experiments/{experiment_id}/report").json()
    passed = sum(cell["first_pass_assets"] for cell in report["cells"])
    assert passed == 1


# --- 判定表 ------------------------------------------------------------------


def test_verdict_table_marks_insufficient_samples(auth_client, png_bytes):
    experiment_id = create_experiment(auth_client, "サンプル不足")
    _set_cap(experiment_id)
    _, generation_ids = _make_comparison(auth_client, experiment_id, png_bytes)
    drain_worker()
    for generation_id in generation_ids:
        _review(auth_client, generation_id)

    report = auth_client.get(f"/api/experiments/{experiment_id}/report").json()
    for cell in report["cells"]:
        # 初回送信題材数が1件なので、合格していてもサンプル不足
        assert cell["first_sent_assets"] == 1
        assert cell["within_two_pass_percent"] == 100.0
        assert cell["verdict"] == "サンプル不足"


def test_verdict_table_uses_thresholds(auth_client, png_bytes, monkeypatch):
    """閾値は設定値で、判定はそれに従う（仕様第10章）。"""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "verdict_min_samples", 2)

    experiment_id = create_experiment(auth_client, "判定")
    _set_cap(experiment_id)

    passing, failing = [], []
    for index in range(3):
        _, ids = _make_comparison(auth_client, experiment_id, png_bytes)
        drain_worker()
        (passing if index < 2 else failing).append(ids)

    with session_scope() as db:
        provider_of = {
            gid: db.get(Generation, gid).provider for group in passing + failing for gid in group
        }

    for group in passing:
        for gid in group:
            _review(auth_client, gid)
    for group in failing:
        for gid in group:
            _review(
                auth_client,
                gid,
                score_fidelity=2,
                score_shape=2,
                verdict="retry_recommended",
                defect_tags=["face_broken"],
            )

    report = auth_client.get(f"/api/experiments/{experiment_id}/report").json()
    assert report["cells"], provider_of
    for cell in report["cells"]:
        assert cell["first_sent_assets"] == 3
        assert cell["first_pass_assets"] == 2
        # 2/3 = 66.7% は保留の下限（70%）を下回るため不適
        assert cell["within_two_pass_percent"] == 66.7
        assert cell["verdict"] == "不適"


def test_quality_only_rate_excludes_technical_failures(auth_client, png_bytes):
    """品質のみ初回合格率は、技術失敗で評価に至らなかった題材を分母から除く。"""
    from tests.conftest import request_generation

    experiment_id = create_experiment(auth_client, "品質のみ")
    _set_cap(experiment_id)

    # 1件は成功して合格、1件は事業者側で失敗
    good = _variant(auth_client, experiment_id, png_bytes)
    bad_asset = upload_asset(auth_client, experiment_id, png_bytes)
    bad = auth_client.get(f"/api/assets/{bad_asset}").json()["variants"][0]["id"]

    ok = request_generation(auth_client, good, preset_id(auth_client, "mock-standard"))
    ng = request_generation(auth_client, bad, preset_id(auth_client, "mock-provider-failed"))
    assert ok.status_code == 202 and ng.status_code == 202
    drain_worker()
    _review(auth_client, ok.json()["id"])

    report = auth_client.get(f"/api/experiments/{experiment_id}/report").json()
    cell = next(c for c in report["cells"] if c["provider"] == "mock")
    assert cell["first_sent_assets"] == 2
    assert cell["tech_failed_unreviewed_assets"] == 1
    assert cell["first_pass_percent"] == 50.0  # 技術失敗を除外しない
    assert cell["quality_only_first_pass_percent"] == 100.0  # 技術失敗を分母から除く


# --- 校正 --------------------------------------------------------------------


def test_calibration_excludes_until_two_reviewers_and_warns_on_difference(
    auth_client, png_bytes, operator
):
    """校正対象は2名そろうまで集計に含めず、2段階以上の差を警告する（仕様第10章）。"""
    from app.auth import hash_password
    from app.services import calibration

    experiment_id = create_experiment(auth_client, "校正")
    _set_cap(experiment_id)
    variant_id = _variant(auth_client, experiment_id, png_bytes)
    from tests.conftest import request_generation

    generation_id = request_generation(
        auth_client, variant_id, preset_id(auth_client, "mock-standard")
    ).json()["id"]
    drain_worker()

    # 校正対象を1件にする
    assert (
        auth_client.post(
            f"/api/experiments/{experiment_id}/calibration",
            json={"target_count": 1, "reason": "校正を始める"},
            headers={"x-csrf-token": CSRF_VALUE},
        ).status_code
        == 200
    )

    # 1人目の評価だけでは集計に含まれない
    _review(auth_client, generation_id)
    report = auth_client.get(f"/api/experiments/{experiment_id}/report").json()
    assert report["calibration"]["incomplete"] == 1
    assert sum(cell["first_pass_assets"] for cell in report["cells"]) == 0

    # 2人目の評価を、差の大きい点数で入れる
    with session_scope() as db:
        second = Operator(
            login_name="teacher2",
            display_name="講師2",
            password_hash=hash_password("another-password-1234"),
        )
        db.add(second)
        db.flush()
        second_id = second.id
        experiment = db.get(Experiment, experiment_id)
        db.add(
            Review(
                generation_id=generation_id,
                reviewer_id=second_id,
                revision=1,
                score_fidelity=2,
                score_shape=2,
                score_color=4,
                score_appeal=4,
                score_mobile=4,
                mobile_checked=True,
                defect_tags_json="[]",
                verdict="retry_recommended",
                work_seconds=90,
                was_blind=False,
                is_calibration=True,
            )
        )
        db.flush()
        state = calibration.state(db, experiment)

    assert state.entries[0].complete is True
    axes = {difference.axis for difference in state.entries[0].differences}
    # 元画像の特徴（5対2）と全周の形状（4対2）で2段階以上の差がある
    assert "score_fidelity" in axes
    assert state.warning_count == 1

    report = auth_client.get(f"/api/experiments/{experiment_id}/report").json()
    assert report["calibration"]["warnings"] == 1
    # 採用する評価者が未指定のあいだは集計から除外する（平均は使わない）
    assert report["calibration"]["chosen_reviewer_id"] is None
    assert sum(cell["first_pass_assets"] for cell in report["cells"]) == 0

    # 採用する評価者を指定すると、その1名分だけが集計に入る
    assert (
        auth_client.post(
            f"/api/experiments/{experiment_id}/calibration",
            json={"reviewer_id": operator, "reason": "1人目の評価を採用する"},
            headers={"x-csrf-token": CSRF_VALUE},
        ).status_code
        == 200
    )
    report = auth_client.get(f"/api/experiments/{experiment_id}/report").json()
    assert sum(cell["first_pass_assets"] for cell in report["cells"]) == 1


def test_report_reports_timings_and_save_rate(auth_client, png_bytes):
    experiment_id = create_experiment(auth_client, "待ち時間")
    _set_cap(experiment_id)
    _make_comparison(auth_client, experiment_id, png_bytes)
    drain_worker()

    report = auth_client.get(f"/api/experiments/{experiment_id}/report").json()
    assert report["timings"]["completed"] == 2
    assert report["timings"]["p50_seconds"] is not None
    assert report["timings"]["p95_seconds"] is not None
    assert report["save_success_percent"] == 100.0


def test_confirmed_cost_over_estimate_stops_new_generations(auth_client, png_bytes):
    """確定実費が見積合計を超えたら新規生成を停止する（仕様第11章）。"""
    from tests.conftest import request_generation

    experiment_id = create_experiment(auth_client, "実費超過")
    _set_cap(experiment_id)
    variant_id = _variant(auth_client, experiment_id, png_bytes)
    generation_id = request_generation(
        auth_client, variant_id, preset_id(auth_client, "mock-priced")
    ).json()["id"]
    drain_worker()

    # 見積 $1.00 に対し、実費 $2.00 を記録する
    recorded = auth_client.post(
        f"/api/generations/{generation_id}/costs",
        json={
            "amount_micro_usd": 2_000_000,
            "reference": "invoice-over",
            "evidence_note": "事業者の請求で確認",
        },
        headers={"x-csrf-token": CSRF_VALUE},
    )
    assert recorded.status_code == 200

    blocked = request_generation(auth_client, variant_id, preset_id(auth_client, "mock-priced"))
    assert blocked.status_code == 409
    assert "実費" in blocked.json()["detail"]
