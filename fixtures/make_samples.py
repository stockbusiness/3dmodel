"""モック用のサンプルGLBを生成する。

自作の単純形状のみを使う合成データであり、実人物・有料素材は含まない（仕様第13章）。
再生成: python fixtures/make_samples.py
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

OUT_DIR = Path(__file__).parent


def _box(width: float, height: float, depth: float):
    hx, hy, hz = width / 2, height / 2, depth / 2
    faces = [
        ((0, 0, 1), [(-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]),
        ((0, 0, -1), [(hx, -hy, -hz), (-hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz)]),
        ((1, 0, 0), [(hx, -hy, hz), (hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz)]),
        ((-1, 0, 0), [(-hx, -hy, -hz), (-hx, -hy, hz), (-hx, hy, hz), (-hx, hy, -hz)]),
        ((0, 1, 0), [(-hx, hy, hz), (hx, hy, hz), (hx, hy, -hz), (-hx, hy, -hz)]),
        ((0, -1, 0), [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz), (-hx, -hy, hz)]),
    ]
    positions: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    indices: list[int] = []
    for normal, corners in faces:
        base = len(positions)
        positions.extend(corners)
        normals.extend([normal] * 4)
        indices.extend([base, base + 1, base + 2, base, base + 2, base + 3])
    return positions, normals, indices


def _pyramid(size: float, height: float):
    h = size / 2
    apex = (0.0, height, 0.0)
    base_corners = [(-h, 0.0, h), (h, 0.0, h), (h, 0.0, -h), (-h, 0.0, -h)]
    positions: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    indices: list[int] = []
    for i in range(4):
        a, b = base_corners[i], base_corners[(i + 1) % 4]
        base = len(positions)
        positions.extend([a, b, apex])
        nx = (a[0] + b[0] + apex[0]) / 3
        nz = (a[2] + b[2] + apex[2]) / 3
        length = max((nx * nx + nz * nz) ** 0.5, 1e-6)
        normals.extend([(nx / length, 0.4, nz / length)] * 3)
        indices.extend([base, base + 1, base + 2])
    base = len(positions)
    positions.extend(base_corners)
    normals.extend([(0.0, -1.0, 0.0)] * 4)
    indices.extend([base, base + 2, base + 1, base, base + 3, base + 2])
    return positions, normals, indices


def _pad(data: bytes, alignment: int = 4, filler: bytes = b"\x00") -> bytes:
    remainder = len(data) % alignment
    return data if remainder == 0 else data + filler * (alignment - remainder)


def _checker_png(size: int = 64) -> bytes:
    """合成の市松模様PNG。実素材は使わない（仕様第13章）。"""
    from io import BytesIO

    from PIL import Image

    image = Image.new("RGB", (size, size))
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            on = ((x // 8) + (y // 8)) % 2 == 0
            pixels[x, y] = (214, 140, 88) if on else (92, 68, 52)
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def build_glb(positions, normals, indices, base_color, uvs=None, texture_png=None) -> bytes:
    """サンプルGLBを組み立てる。

    `uvs` と `texture_png` を与えると、**GLBに埋め込まれたテクスチャ**を持つ
    モデルになる。テクスチャの有無で表示経路が変わるため、両方の見本が要る
    （`docs/decisions.md` A-50：テクスチャ無しの見本しか無かったため、
    CSP がテクスチャの読込を止めている不具合を試験で捕まえられなかった）。
    """
    position_bytes = b"".join(struct.pack("<3f", *p) for p in positions)
    normal_bytes = b"".join(struct.pack("<3f", *n) for n in normals)
    index_bytes = _pad(b"".join(struct.pack("<H", i) for i in indices))
    textured = uvs is not None and texture_png is not None
    uv_bytes = _pad(b"".join(struct.pack("<2f", *uv) for uv in uvs)) if textured else b""
    png_bytes = _pad(texture_png) if textured else b""

    buffer = position_bytes + normal_bytes + index_bytes + uv_bytes + png_bytes
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    zs = [p[2] for p in positions]

    gltf = {
        "asset": {"version": "2.0", "generator": "art3d-validation fixtures (synthetic)"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": 0,
                            "NORMAL": 1,
                            **({"TEXCOORD_0": 3} if textured else {}),
                        },
                        "indices": 2,
                        "material": 0,
                        "mode": 4,
                    }
                ]
            }
        ],
        "materials": [
            {
                "pbrMetallicRoughness": {
                    "baseColorFactor": base_color,
                    "metallicFactor": 0.0,
                    "roughnessFactor": 0.85,
                },
                "name": "sample",
            }
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": len(positions),
                "type": "VEC3",
                "min": [min(xs), min(ys), min(zs)],
                "max": [max(xs), max(ys), max(zs)],
            },
            {
                "bufferView": 1,
                "componentType": 5126,
                "count": len(normals),
                "type": "VEC3",
            },
            {
                "bufferView": 2,
                "componentType": 5123,
                "count": len(indices),
                "type": "SCALAR",
            },
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(position_bytes), "target": 34962},
            {
                "buffer": 0,
                "byteOffset": len(position_bytes),
                "byteLength": len(normal_bytes),
                "target": 34962,
            },
            {
                "buffer": 0,
                "byteOffset": len(position_bytes) + len(normal_bytes),
                "byteLength": len(index_bytes),
                "target": 34963,
            },
        ],
        "buffers": [{"byteLength": len(buffer)}],
    }

    if textured:
        uv_offset = len(position_bytes) + len(normal_bytes) + len(index_bytes)
        png_offset = uv_offset + len(uv_bytes)
        gltf["accessors"].append(
            {"bufferView": 3, "componentType": 5126, "count": len(uvs), "type": "VEC2"}
        )
        gltf["bufferViews"].append(
            {"buffer": 0, "byteOffset": uv_offset, "byteLength": len(uv_bytes), "target": 34962}
        )
        # 画像の bufferView に target は付けない（頂点でも索引でもないため）
        gltf["bufferViews"].append(
            {"buffer": 0, "byteOffset": png_offset, "byteLength": len(texture_png)}
        )
        gltf["images"] = [{"bufferView": 4, "mimeType": "image/png"}]
        gltf["samplers"] = [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}]
        gltf["textures"] = [{"sampler": 0, "source": 0}]
        gltf["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}

    json_chunk = _pad(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), filler=b" ")
    bin_chunk = _pad(buffer)
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    out = struct.pack("<4sII", b"glTF", 2, total)
    out += struct.pack("<II", len(json_chunk), 0x4E4F534A) + json_chunk
    out += struct.pack("<II", len(bin_chunk), 0x004E4942) + bin_chunk
    return out


def main() -> None:
    # 立方体は1面4頂点 × 6面。各面に同じUVを割り当てる
    face_uv = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    cube_uvs = face_uv * 6

    samples = {
        "sample_cube.glb": build_glb(*_box(1.0, 1.0, 1.0), [0.85, 0.55, 0.35, 1.0]),
        "sample_pyramid.glb": build_glb(*_pyramid(1.2, 1.1), [0.45, 0.6, 0.85, 1.0]),
        # テクスチャ付きの見本。**表示経路がテクスチャ無しと違う**ため必ず要る（A-50）
        "sample_textured_cube.glb": build_glb(
            *_box(1.0, 1.0, 1.0),
            [1.0, 1.0, 1.0, 1.0],
            uvs=cube_uvs,
            texture_png=_checker_png(),
        ),
    }
    for name, data in samples.items():
        (OUT_DIR / name).write_bytes(data)
        print(f"{name}: {len(data)} バイト")


if __name__ == "__main__":
    main()
