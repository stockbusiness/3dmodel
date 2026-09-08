"""GLBの検査と形状メトリクス抽出（仕様第12章・第9章）。

- 形式ヘッダー、サイズ整合、チャンク構造、glTF JSON の読込可否を確認する
- HTML等をGLBとして受理しない
- 外部URI参照を許可せず、参照先を自動取得しない
- 形状メトリクスはローカルで抽出する。取れない項目は None とし 0 と区別する

外部ライブラリを増やさないため、GLB/glTF の必要部分のみを自前で読む
（ASSUMPTION A-5）。仕様は「取れない項目はnull」を認めている。
"""

from __future__ import annotations

import io
import json
import struct
from dataclasses import asdict, dataclass, field
from typing import Any

GLB_MAGIC = b"glTF"
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942
HEADER_SIZE = 12
_TRIANGLE_MODES = {4, 5, 6}  # TRIANGLES / TRIANGLE_STRIP / TRIANGLE_FAN


class GlbRejected(Exception):
    """検査に通らないGLB。生成は validation_failed とする。"""


@dataclass
class GlbMetrics:
    vertex_count: int | None = None
    triangle_count: int | None = None
    mesh_count: int | None = None
    material_count: int | None = None
    texture_count: int | None = None
    texture_resolutions: list[str] = field(default_factory=list)
    bounding_box: dict[str, list[float]] | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def _parse_chunks(data: bytes) -> tuple[dict[str, Any], bytes | None]:
    if len(data) < HEADER_SIZE:
        raise GlbRejected("ファイルが短すぎます")
    magic, version, declared_length = struct.unpack_from("<4sII", data, 0)
    if magic != GLB_MAGIC:
        raise GlbRejected("GLBのヘッダーではありません")
    if version != 2:
        raise GlbRejected(f"未対応のGLBバージョンです（{version}）")
    if declared_length != len(data):
        raise GlbRejected(
            f"宣言された長さ {declared_length} と実際の長さ {len(data)} が一致しません"
        )

    json_chunk: dict[str, Any] | None = None
    bin_chunk: bytes | None = None
    offset = HEADER_SIZE
    while offset + 8 <= len(data):
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        end = offset + chunk_length
        if end > len(data):
            raise GlbRejected("チャンク長がファイル長を超えています")
        payload = data[offset:end]
        if chunk_type == CHUNK_JSON:
            if json_chunk is not None:
                raise GlbRejected("JSONチャンクが複数あります")
            try:
                parsed = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GlbRejected("JSONチャンクを読み取れません") from exc
            if not isinstance(parsed, dict):
                raise GlbRejected("JSONチャンクの内容が不正です")
            json_chunk = parsed
        elif chunk_type == CHUNK_BIN:
            bin_chunk = payload
        # 未知のチャンクは仕様上無視してよい
        offset = end + (-end % 4)

    if json_chunk is None:
        raise GlbRejected("JSONチャンクがありません")
    return json_chunk, bin_chunk


def _reject_external_uris(gltf: dict[str, Any]) -> None:
    """外部URI参照を拒否する（仕様第12章）。data: URI のみ許可する。"""
    for section in ("buffers", "images"):
        for index, item in enumerate(gltf.get(section) or []):
            if not isinstance(item, dict):
                continue
            uri = item.get("uri")
            if uri is None:
                continue
            if not isinstance(uri, str) or not uri.startswith("data:"):
                raise GlbRejected(
                    f"外部URI参照を含んでいます（{section}[{index}]）。"
                    "参照先の自動取得は行いません"
                )


def _image_bytes(
    gltf: dict[str, Any], image: dict[str, Any], bin_chunk: bytes | None
) -> bytes | None:
    uri = image.get("uri")
    if isinstance(uri, str) and uri.startswith("data:"):
        _, _, encoded = uri.partition(",")
        import base64

        try:
            return base64.b64decode(encoded, validate=False)
        except ValueError:
            return None
    view_index = image.get("bufferView")
    if view_index is None or bin_chunk is None:
        return None
    views = gltf.get("bufferViews") or []
    if not isinstance(view_index, int) or view_index >= len(views):
        return None
    view = views[view_index]
    offset = int(view.get("byteOffset", 0))
    length = int(view.get("byteLength", 0))
    if offset + length > len(bin_chunk):
        return None
    return bin_chunk[offset : offset + length]


def _extract_metrics(gltf: dict[str, Any], bin_chunk: bytes | None) -> GlbMetrics:
    metrics = GlbMetrics()
    accessors = gltf.get("accessors") or []
    meshes = gltf.get("meshes") or []

    metrics.mesh_count = len(meshes)
    metrics.material_count = len(gltf.get("materials") or [])
    metrics.texture_count = len(gltf.get("textures") or [])

    vertices = 0
    triangles = 0
    lows: list[float] | None = None
    highs: list[float] | None = None

    for mesh in meshes:
        for primitive in mesh.get("primitives") or []:
            position_index = (primitive.get("attributes") or {}).get("POSITION")
            count = 0
            if isinstance(position_index, int) and position_index < len(accessors):
                accessor = accessors[position_index]
                count = int(accessor.get("count", 0))
                vertices += count
                minimum, maximum = accessor.get("min"), accessor.get("max")
                if isinstance(minimum, list) and isinstance(maximum, list) and len(minimum) == 3:
                    lows = (
                        minimum[:]
                        if lows is None
                        else [min(a, b) for a, b in zip(lows, minimum, strict=False)]
                    )
                    highs = (
                        maximum[:]
                        if highs is None
                        else [max(a, b) for a, b in zip(highs, maximum, strict=False)]
                    )

            mode = primitive.get("mode", 4)
            if mode not in _TRIANGLE_MODES:
                continue
            index_accessor = primitive.get("indices")
            if isinstance(index_accessor, int) and index_accessor < len(accessors):
                count = int(accessors[index_accessor].get("count", 0))
            if mode == 4:
                triangles += count // 3
            elif count >= 3:
                triangles += count - 2

    metrics.vertex_count = vertices
    metrics.triangle_count = triangles
    if lows is not None and highs is not None:
        metrics.bounding_box = {"min": lows, "max": highs}

    resolutions: list[str] = []
    for image in gltf.get("images") or []:
        if not isinstance(image, dict):
            continue
        raw = _image_bytes(gltf, image, bin_chunk)
        if raw is None:
            continue
        try:
            from PIL import Image

            with Image.open(io.BytesIO(raw)) as texture:
                resolutions.append(f"{texture.width}x{texture.height}")
        except Exception:  # noqa: BLE001 - 取れない項目は記録しないだけ
            continue
    metrics.texture_resolutions = resolutions
    return metrics


def inspect(data: bytes, *, max_bytes: int | None = None) -> GlbMetrics:
    """GLBを検査し、形状メトリクスを返す。不合格なら GlbRejected を送出する。"""
    if max_bytes is not None and len(data) > max_bytes:
        raise GlbRejected(f"上限 {max_bytes} バイトを超えています")
    gltf, bin_chunk = _parse_chunks(data)
    _reject_external_uris(gltf)
    if not gltf.get("asset"):
        raise GlbRejected("glTFのasset情報がありません")
    return _extract_metrics(gltf, bin_chunk)
