"""検証セットの作成（仕様第6.2章・第11章）。

**この試験は A3.5 が実行できないという不具合の再発防止である。**
作成経路が `is_live=False` と `track="standard"` を固定していたため、
実APIの検証セットも早期確認の検証セットも作れず、
`docs/early-check-plan.md` の手順を実行できなかった。
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from tests.conftest import CSRF_VALUE


def _enable_live(monkeypatch):
    monkeypatch.setattr(get_settings(), "live_api_enabled", True)


def _create(client, **payload):
    body = {"name": "検証セット", **payload}
    return client.post("/api/experiments", json=body, headers={"x-csrf-token": CSRF_VALUE})


def _detail(client, experiment_id):
    return client.get(f"/api/experiments/{experiment_id}").json()


def test_live_experiment_is_refused_when_live_api_is_disabled(auth_client):
    """既定（LIVE_API_ENABLED=false）では実APIのセットを作れない。"""
    response = _create(auth_client, is_live=True, cost_cap_usd="1.50")
    assert response.status_code == 400
    assert "APP_LIVE_API_ENABLED" in response.text


def test_live_experiment_requires_a_cost_cap(auth_client, monkeypatch):
    """仕様第11章：上限額が無いまま実APIのセットは作れない。"""
    _enable_live(monkeypatch)
    response = _create(auth_client, is_live=True)
    assert response.status_code == 400
    assert "上限額" in response.text


def test_live_early_check_experiment_can_be_created(auth_client, monkeypatch):
    """A3.5 が必要とする「実API」かつ「早期確認」のセットを作れる。"""
    _enable_live(monkeypatch)
    response = _create(
        auth_client,
        name="A3.5 早期確認（無料プラン・販売不可）",
        is_live=True,
        track="early_check",
        cost_cap_usd="1.50",
    )
    assert response.status_code == 201, response.text
    detail = _detail(auth_client, response.json()["id"])
    assert detail["is_live"] is True
    assert detail["track"] == "early_check"


def test_cost_cap_keeps_cents_as_integer_micro_usd(auth_client, monkeypatch):
    """$1.50 を受け取れる。**浮動小数点を経由しない**（仕様第9章・CLAUDE.md 第6章）。"""
    _enable_live(monkeypatch)
    response = _create(auth_client, is_live=True, cost_cap_usd="1.50")
    assert response.status_code == 201, response.text
    experiment_id = response.json()["id"]
    rows = auth_client.get("/api/experiments").json()
    row = next(r for r in rows if r["id"] == experiment_id)
    assert row["cost_cap_micro_usd"] == 1_500_000
    assert isinstance(row["cost_cap_micro_usd"], int)


@pytest.mark.parametrize("value", ["1.50", "3", "0.30"])
def test_cost_cap_accepts_decimals_from_the_form(auth_client, monkeypatch, value):
    """画面のフォームからも小数を入れられる（以前は step=1 で $1.50 を入力できなかった）。"""
    _enable_live(monkeypatch)
    response = auth_client.post(
        "/experiments",
        data={
            "name": f"セット {value}",
            "csrf_token": CSRF_VALUE,
            "mode": "live",
            "track": "early_check",
            "cost_cap_usd": value,
        },
        headers={"x-csrf-token": CSRF_VALUE},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def test_unknown_track_is_refused(auth_client):
    response = _create(auth_client, track="なんでもあり")
    assert response.status_code == 400


def test_mock_experiment_is_still_the_default(auth_client):
    """既定はモック・通常検証のまま。既存の呼び出しの意味を変えない。"""
    response = _create(auth_client)
    assert response.status_code == 201
    detail = _detail(auth_client, response.json()["id"])
    assert detail["is_live"] is False
    assert detail["track"] == "standard"


def test_form_offers_the_live_and_track_choices(auth_client, monkeypatch):
    """フォームに「実/モック」と「集計区分」が出ている。"""
    _enable_live(monkeypatch)
    body = auth_client.get("/").text
    assert 'name="mode"' in body
    assert 'name="track"' in body
    assert "early_check" in body


def test_the_stale_mock_only_banner_is_gone(auth_client, monkeypatch):
    """A1当時の「モック専用です」という固定文言が残っていない。

    実際の設定と食い違う案内は、利用者を止めてしまう。
    """
    _enable_live(monkeypatch)
    body = auth_client.get("/").text
    assert "モック専用" not in body
    assert "LIVE_API_ENABLED=false" not in body
    assert "実API生成が有効です" in body


def test_live_option_is_disabled_when_live_api_is_off(auth_client):
    """実APIが無効なら、画面でも「実API」を選べないようにする。"""
    body = auth_client.get("/").text
    assert "いまは選べません" in body


def test_cost_cap_of_zero_is_not_treated_as_unset(auth_client):
    """上限額 0 が「未設定」に化けない。

    `str(payload.get("cost_cap_usd") or "")` と書くと **0 が偽と判定されて
    None になる**。0 は「1円も使わない」という指定であって、未設定ではない。
    """
    response = _create(auth_client, name="上限0のモック", cost_cap_usd=0)
    assert response.status_code == 201, response.text
    rows = auth_client.get("/api/experiments").json()
    row = next(r for r in rows if r["id"] == response.json()["id"])
    assert row["cost_cap_micro_usd"] == 0


def test_cost_cap_accepts_a_json_number(auth_client, monkeypatch):
    """JSON の数値（文字列でない）でも受け取れる。"""
    _enable_live(monkeypatch)
    response = _create(auth_client, is_live=True, cost_cap_usd=3)
    assert response.status_code == 201, response.text
    rows = auth_client.get("/api/experiments").json()
    row = next(r for r in rows if r["id"] == response.json()["id"])
    assert row["cost_cap_micro_usd"] == 3_000_000
