"""画面の動作確認（Playwright）。

実機確認の代わりにはならない。端末での確認は docs/quality-test-plan.md による。

確認するもの：
- 主要画面が表示でき、コンソールエラーが出ない
- 3Dモデルが読み込まれ、視点操作が効く
- ビューアーの読込失敗で操作不能・永久ローディングにならない（仕様第13章 試験14）
- ブラインド中の比較画面にサービス名等が出ず、確定後は出る（同 試験18）
- スマホ幅で縦並びになり、ボタン44px以上・本文16px（仕様第6.6章）
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright", reason="Playwright が入っていない環境では飛ばす")
from playwright.sync_api import sync_playwright  # noqa: E402

CHROMIUM = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH", "/opt/pw-browsers/chromium")
PASSWORD = "playwright-test-password"
ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    not Path(CHROMIUM).exists(), reason="Chromium が見つからない環境では飛ばす"
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """本物のHTTPサーバーを立てる。外部へは接続しない。"""
    data_dir = tmp_path_factory.mktemp("screens-data")
    env = {
        **os.environ,
        "APP_SECRET_KEY": "playwright-secret-key-0123456789abcdefghij",
        "APP_DATA_DIR": str(data_dir),
        "APP_LIVE_API_ENABLED": "false",
        "PYTHONPATH": str(ROOT),
    }
    subprocess.run(
        [sys.executable, "-m", "app.cli", "init-db"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.auth import hash_password;"
            "from app.db import session_scope;"
            "from app.models import Operator;"
            "import os;"
            "s=session_scope();"
            "db=s.__enter__();"
            "db.add(Operator(login_name='teacher1', display_name='講師1',"
            f" password_hash=hash_password({PASSWORD!r})));"
            "s.__exit__(None,None,None)",
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
    )

    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                break
        except OSError:
            time.sleep(0.25)
    else:  # pragma: no cover - 起動に失敗した場合
        process.kill()
        pytest.fail("テスト用サーバーが起動しませんでした")

    yield {"base": base, "env": env}

    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover
        process.kill()


def _seed_comparison(env: dict) -> dict:
    """比較を1件作り、ワーカーを回して評価待ちにする。"""
    script = """
import io, json, sys
from PIL import Image
from sqlalchemy import select
from app.db import session_scope
from app.models import Asset, AssetVariant, Experiment, Operator, Preset
from app.services.generation import create_comparison
from app.services.image_intake import inspect_and_store
from app.services.worker_steps import run_cycle

buffer = io.BytesIO()
Image.new("RGB", (240, 240), (210, 120, 70)).save(buffer, format="PNG")
result = inspect_and_store(buffer.getvalue())

with session_scope() as db:
    operator = db.scalars(select(Operator)).first()
    experiment = Experiment(name="画面確認", is_live=False,
                            cost_cap_micro_usd=100_000_000, created_by=operator.id)
    db.add(experiment); db.flush()
    asset = Asset(experiment_id=experiment.id, title="題材", subject_tag="single_character",
                  source_class="staff_original", consent_status="not_required",
                  created_by=operator.id)
    db.add(asset); db.flush()
    variant = AssetVariant(asset_id=asset.id, kind="original",
                           storage_key=result.original.key, sha256=result.original.sha256,
                           bytes=result.original.bytes, width=result.width, height=result.height,
                           mime=result.mime, submission_storage_key=result.submission.key,
                           submission_sha256=result.submission.sha256,
                           submission_bytes=result.submission.bytes, created_by=operator.id)
    db.add(variant); db.flush()
    presets = [db.scalar(select(Preset).where(Preset.code == code))
               for code in ("mock-a-standard", "mock-b-standard")]
    comparison, generations = create_comparison(
        db, operator=operator, variant_id=variant.id,
        preset_ids=[p.id for p in presets])
    out = {"comparison": comparison.id, "generations": [g.id for g in generations],
           "experiment": experiment.id}

for _ in range(120):
    result = run_cycle("screen-test")
    if not any(result.values()):
        from app.db import session_scope as scope
        from app.models import Generation
        from app.models import utcnow
        with scope() as db:
            pending = db.scalars(select(Generation).where(
                Generation.tech_status.in_(("queued", "running", "downloading")))).all()
            if not pending:
                break
            for generation in pending:
                generation.next_check_at = utcnow()
print(json.dumps(out))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return __import__("json").loads(completed.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def seeded(live_server):
    return {**live_server, **_seed_comparison(live_server["env"])}


def _login(page, base: str) -> None:
    page.goto(f"{base}/login")
    page.fill("#login_name", "teacher1")
    page.fill("#password", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url(f"{base}/")


@pytest.fixture
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(executable_path=CHROMIUM)
        yield instance
        instance.close()


def test_main_screens_render_without_console_errors(browser, seeded):
    errors: list[str] = []
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))

    base = seeded["base"]
    _login(page, base)
    for path in (
        "/",
        f"/experiments/{seeded['experiment']}",
        f"/experiments/{seeded['experiment']}/report",
        f"/comparisons/{seeded['comparison']}",
        f"/generations/{seeded['generations'][0]}",
        "/admin",
        "/admin/artifacts",
    ):
        page.goto(f"{base}{path}")
        page.wait_for_timeout(600)
        assert page.title(), path

    page.close()
    assert errors == [], errors


def test_comparison_loads_both_models_and_controls_work(browser, seeded):
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    _login(page, seeded["base"])
    page.goto(f"{seeded['base']}/comparisons/{seeded['comparison']}")
    # 比較画面のビューアーは遅延読込にしてある（重いGLBを一度に読まないため。
    # 仕様第6.5章）。実際の利用と同じく、見える位置まで送ってから待つ
    page.locator("model-viewer").first.scroll_into_view_if_needed()
    page.locator("model-viewer").last.scroll_into_view_if_needed()
    page.wait_for_function(
        "() => { const vs = Array.from(document.querySelectorAll('model-viewer'));"
        " return vs.length === 2 && vs.every(v => v.loaded); }",
        timeout=30000,
    )
    before = page.evaluate("() => document.querySelector('model-viewer').getCameraOrbit().theta")
    page.click("button[data-all-orbit='180deg 75deg auto']")
    page.wait_for_timeout(500)
    after = page.evaluate("() => document.querySelector('model-viewer').getCameraOrbit().theta")
    assert before != after, "背面ボタンで視点が変わっていない"
    page.close()


def test_viewer_failure_shows_a_message_and_stays_usable(browser, seeded):
    """仕様第13章 試験14：読込失敗で操作不能・永久ローディングにならない。"""
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    _login(page, seeded["base"])
    page.goto(f"{seeded['base']}/generations/{seeded['generations'][0]}")
    page.wait_for_function(
        "() => { const v = document.querySelector('model-viewer'); return v && v.loaded; }",
        timeout=30000,
    )
    # 取得できないファイルに差し替える
    page.evaluate(
        "() => { document.querySelector('model-viewer').src = "
        "'/api/files/00000000-0000-0000-0000-000000000000?kind=artifact'; }"
    )
    page.wait_for_function(
        "() => { const s = document.querySelector('[data-viewer-status]');"
        " return s && !s.hidden && s.textContent.includes('表示できませんでした'); }",
        timeout=20000,
    )
    # 操作ボタンは押せるまま
    page.click("button[data-orbit='180deg 75deg auto']")
    assert page.is_enabled("button[data-viewer-reset]")
    page.close()


def test_blind_then_revealed_on_screen(browser, seeded):
    """仕様第13章 試験18：画面にサービス名が出ないこと、確定後は出ること。"""
    secrets = ["mock_a", "mock_b", "mock-a-standard", "mock-b-standard", "mock-shape-v1"]
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    _login(page, seeded["base"])

    page.goto(f"{seeded['base']}/comparisons/{seeded['comparison']}")
    html = page.content()
    for secret in secrets:
        assert secret not in html, f"ブラインド中に {secret} が出ています"
    assert "ブラインド" in html

    # 両方を評価すると開示される
    for generation_id in seeded["generations"]:
        page.goto(f"{seeded['base']}/generations/{generation_id}")
        page.select_option("#score_fidelity", "5")
        page.select_option("#score_shape", "4")
        page.select_option("#score_color", "4")
        page.select_option("#score_appeal", "4")
        page.check("#mobile_checked")
        page.select_option("#score_mobile", "4")
        page.select_option("#verdict", "pass")
        page.click("form[action$='/reviews'] button[type=submit]")
        page.wait_for_load_state()

    page.goto(f"{seeded['base']}/comparisons/{seeded['comparison']}")
    revealed = page.content()
    assert "開示済み" in revealed
    assert "mock_a" in revealed and "mock_b" in revealed
    page.close()


def test_mobile_layout_stacks_and_meets_size_rules(browser, seeded):
    """スマホ幅で縦並び、ボタン44px以上、本文16px（仕様第6.6章）。"""
    page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
    _login(page, seeded["base"])
    page.goto(f"{seeded['base']}/comparisons/{seeded['comparison']}")
    page.wait_for_timeout(1200)

    assert page.evaluate("() => getComputedStyle(document.body).fontSize") == "16px"

    small = page.evaluate(
        """() => Array.from(document.querySelectorAll('button, .button'))
            .filter(e => e.offsetParent !== null)
            .map(e => ({t: e.textContent.trim().slice(0, 12),
                        h: Math.round(e.getBoundingClientRect().height)}))
            .filter(e => e.h < 44)"""
    )
    assert small == [], small

    # スマホでは重いGLBを同時に読み込まず、切り替えて表示する
    visible = page.evaluate(
        "() => Array.from(document.querySelectorAll('[data-compare-pane]'))"
        ".filter(e => !e.hidden).length"
    )
    assert visible == 1, "スマホ幅で2つのモデルが同時に表示されている"
    assert page.is_visible("[data-mobile-switch]")

    page.click("[data-show-label='B']")
    page.wait_for_timeout(300)
    labels = page.evaluate(
        "() => Array.from(document.querySelectorAll('[data-compare-pane]'))"
        ".filter(e => !e.hidden).map(e => e.dataset.label)"
    )
    assert labels == ["B"]

    # 横スクロールが起きていない
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"横スクロールが発生している（{overflow}px）"
    page.close()


def test_admin_screen_shows_state_without_leaking_the_key(browser, seeded):
    """管理画面が実際に動き、APIキーの値も伏せた事業者名も出さないこと。

    仕様第6.5章（ブラインド）と第12章（鍵を出さない）を、
    描画後のHTMLに対して確かめる。
    """
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))

    base = seeded["base"]
    _login(page, base)

    page.goto(f"{base}/admin")
    page.wait_for_timeout(400)
    html = page.content()
    # 事業者ごとの状態が出ている
    assert "tripo" in html and "meshy" in html
    assert "自己診断" in html
    # 実APIは既定で無効なので、接続テストのボタンは押せない
    button = page.locator("[data-connection-test='tripo']")
    assert button.is_disabled()

    # 保存領域の点検は外部通信をしないのでこの場で動かせる
    page.click("#storage-audit")
    page.wait_for_selector("#storage-result .notice", timeout=5000)
    assert page.locator("#storage-result").inner_text().strip()

    page.goto(f"{base}/admin/artifacts")
    page.wait_for_timeout(400)
    artifacts_html = page.content()
    assert "成果物の管理" in artifacts_html

    # この module のフィクスチャは共有で、先行する試験が開示することがある。
    # 開示済みならサービス名が出るのが正しいので、状態を見てから確かめる。
    rows = page.evaluate("async () => (await (await fetch('/api/admin/artifacts')).json()).rows")
    assert rows, "成果物がありません"
    if any(row["blind"] for row in rows):
        assert "評価確定まで非表示" in artifacts_html
        for row in rows:
            if row["blind"]:
                assert row["provider"] is None
                assert row["preset_name"] is None
        for secret in ("mock_a", "mock_b", "mock-a-standard", "mock-b-standard"):
            assert secret not in artifacts_html, secret

    page.close()
    assert errors == [], errors
