"""画像の受入処理とCSV出力（仕様第12章・第6.6章）。"""

from __future__ import annotations

import csv
import io

import pytest
from PIL import Image

from tests.conftest import CSRF_VALUE, create_experiment, mock_preset_id, upload_asset


def _jpeg_with_exif(orientation: int = 6) -> bytes:
    """回転指定と位置情報を持つJPEGを作る。"""
    image = Image.new("RGB", (40, 20), (10, 120, 200))
    exif = image.getexif()
    exif[0x0112] = orientation  # Orientation
    exif[0x8825] = {1: "N", 2: (35.0, 0.0, 0.0)}  # GPSInfo
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif.tobytes())
    return buffer.getvalue()


def test_submission_copy_has_exif_removed_and_orientation_applied(db_ready):
    from app.services.image_intake import inspect_and_store
    from app.services.storage import read_bytes

    data = _jpeg_with_exif(orientation=6)
    result = inspect_and_store(data)

    # 原画像はそのまま保存され、上書きされない（仕様第9章）
    assert read_bytes(result.original.key) == data
    assert result.width == 40 and result.height == 20

    with Image.open(io.BytesIO(read_bytes(result.submission.key))) as submission:
        # EXIF方向を反映しているので縦横が入れ替わる
        assert submission.size == (20, 40)
        # 位置情報を含むEXIFは残っていない（仕様第12章）
        assert not submission.getexif()

    assert result.submission.sha256 != result.original.sha256


def test_consent_missing_blocks_live_generation(db_ready, operator, monkeypatch):
    """UIを迂回しても、同意未取得の画像は外部送信できない（仕様第12章）。

    LIVE_API_ENABLED も同意も、どちらか一方でも欠ければ送信しない。
    ここでは同意の判定そのものを確かめるため、実API有効の状態を作る。
    """
    from app.config import get_settings
    from app.db import session_scope

    monkeypatch.setattr(get_settings(), "live_api_enabled", True)
    from app.models import Asset, AssetVariant, Experiment, Operator, Preset
    from app.services.generation import GenerationRejected, check_can_submit

    with session_scope() as db:
        op = db.get(Operator, operator)
        experiment = Experiment(name="実API想定", is_live=True, created_by=op.id)
        db.add(experiment)
        db.flush()
        asset = Asset(
            experiment_id=experiment.id,
            subject_tag="person",
            source_class="classroom_existing",
            consent_status="missing",
            created_by=op.id,
        )
        db.add(asset)
        db.flush()
        variant = AssetVariant(
            asset_id=asset.id,
            kind="original",
            storage_key="0" * 32,
            sha256="a" * 64,
            width=10,
            height=10,
            mime="image/png",
            bytes=100,
            created_by=op.id,
        )
        db.add(variant)
        db.flush()
        preset = db.query(Preset).filter(Preset.code == "mock-standard").one()

        with pytest.raises(GenerationRejected, match="利用同意が未取得"):
            check_can_submit(db, variant, preset, live=True)


def test_csv_escapes_formula_cells():
    from app.services.csv_export import escape_cell

    assert escape_cell("=1+1") == "'=1+1"
    assert escape_cell("+cmd") == "'+cmd"
    assert escape_cell("-2") == "'-2"
    assert escape_cell("@SUM(A1)") == "'@SUM(A1)"
    assert escape_cell("\tTAB") == "'\tTAB"
    assert escape_cell("ふつうの値") == "ふつうの値"
    assert escape_cell(None) == ""


def test_csv_contains_no_storage_keys(auth_client, png_bytes):
    """CSVに保存キー・署名URL・秘密情報を含めない（仕様第6.6章）。"""
    experiment_id = create_experiment(auth_client)
    asset_id = upload_asset(auth_client, experiment_id, png_bytes)
    variant = auth_client.get(f"/api/assets/{asset_id}").json()["variants"][0]
    auth_client.post(
        "/api/generations",
        json={"asset_variant_id": variant["id"], "preset_id": mock_preset_id(auth_client)},
        headers={"x-csrf-token": CSRF_VALUE},
    )

    body = auth_client.get(f"/api/experiments/{experiment_id}/export.csv").content.decode("utf-8")
    rows = list(csv.reader(io.StringIO(body.lstrip("﻿"))))
    assert len(rows) == 2
    header, row = rows

    from app.db import session_scope
    from app.models import AssetVariant

    with session_scope() as db:
        storage_key = db.get(AssetVariant, variant["id"]).storage_key
    assert storage_key not in body
    assert "http" not in body

    # 画像IDと生成IDから内部画面で追跡できる
    assert variant["id"] in row
    assert header[0] == "生成ID"


def test_csv_formula_from_user_input_is_escaped(auth_client, png_bytes):
    experiment_id = create_experiment(auth_client)
    asset_id = upload_asset(auth_client, experiment_id, png_bytes)
    variant_id = auth_client.get(f"/api/assets/{asset_id}").json()["variants"][0]["id"]
    generation_id = auth_client.post(
        "/api/generations",
        json={"asset_variant_id": variant_id, "preset_id": mock_preset_id(auth_client)},
        headers={"x-csrf-token": CSRF_VALUE},
    ).json()["id"]
    auth_client.post(
        f"/api/generations/{generation_id}/reviews",
        json={
            "score_fidelity": 3,
            "score_shape": 3,
            "score_color": 3,
            "score_appeal": 3,
            "verdict": "retry_recommended",
            "comment": '=HYPERLINK("http://attacker.example")',
        },
        headers={"x-csrf-token": CSRF_VALUE},
    )
    body = auth_client.get(f"/api/experiments/{experiment_id}/export.csv").content.decode("utf-8")
    assert "'=HYPERLINK" in body
    assert ",=HYPERLINK" not in body
