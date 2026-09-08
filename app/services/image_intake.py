"""画像の受入検査（仕様第12章）。

- PNG/JPEG/WebP のみ。拡張子ではなくデコード結果で判定する
- 上限 10MiB・20メガピクセル
- SVG・実行可能形式は不可（デコードできないため自動的に拒否される）
- EXIF方向を正規化し、位置情報等を除去した送信用コピーを作る
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import get_settings
from app.services import storage

ALLOWED_FORMATS: dict[str, str] = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}


class ImageRejected(Exception):
    """受け入れられない画像。利用者に日本語で理由を返す。"""


@dataclass(frozen=True)
class IntakeResult:
    original: storage.StoredObject
    submission: storage.StoredObject
    width: int
    height: int
    mime: str
    image_format: str


def _decode(data: bytes) -> Image.Image:
    settings = get_settings()
    # 展開爆弾対策。Pillow の既定上限も設定値に合わせる
    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = settings.max_image_pixels
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()  # 構造の検査（このあと再オープンが必要）
        image = Image.open(io.BytesIO(data))
        image.load()
        return image
    except Image.DecompressionBombError as exc:
        raise ImageRejected("画像の画素数が上限を超えています") from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise ImageRejected(
            "画像として読み取れません。PNG・JPEG・WebP のいずれかを指定してください"
        ) from exc
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit


def inspect_and_store(data: bytes) -> IntakeResult:
    settings = get_settings()

    if not data:
        raise ImageRejected("ファイルが空です")
    if len(data) > settings.max_image_bytes:
        limit_mib = settings.max_image_bytes // (1024 * 1024)
        raise ImageRejected(f"画像の容量が上限（{limit_mib}MiB）を超えています")

    image = _decode(data)
    try:
        image_format = (image.format or "").upper()
        if image_format not in ALLOWED_FORMATS:
            raise ImageRejected(
                "対応していない形式です。PNG・JPEG・WebP のいずれかを指定してください"
            )
        width, height = image.size
        if width * height > settings.max_image_pixels:
            raise ImageRejected("画像の画素数が上限（20メガピクセル）を超えています")

        # EXIF方向を反映してから、メタデータを持たない送信用コピーを作る
        normalized = ImageOps.exif_transpose(image) or image
        buffer = io.BytesIO()
        if image_format == "JPEG":
            normalized.convert("RGB").save(buffer, format="JPEG", quality=95, optimize=True)
        elif image_format == "PNG":
            normalized.convert("RGBA").save(buffer, format="PNG", optimize=True)
        else:
            normalized.convert("RGBA").save(buffer, format="WEBP", quality=95)
        submission_bytes = buffer.getvalue()
    finally:
        image.close()

    original = storage.save_bytes(data)
    submission = storage.save_bytes(submission_bytes)
    return IntakeResult(
        original=original,
        submission=submission,
        width=width,
        height=height,
        mime=ALLOWED_FORMATS[image_format],
        image_format=image_format,
    )
