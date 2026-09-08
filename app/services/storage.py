"""保存層。save/read/stat の境界を持ち、フェーズAではローカル実装のみ（仕様第14章）。

保存キーは UUID とし、元ファイル名を保存パスに使わない（仕様第12章）。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.config import get_settings

_KEY_LENGTH = 32


@dataclass(frozen=True)
class StoredObject:
    key: str
    sha256: str
    bytes: int


class StorageError(Exception):
    pass


def new_key() -> str:
    return uuid.uuid4().hex


def _validate_key(key: str) -> None:
    """パストラバーサル禁止（仕様第12章）。鍵は16進32桁のみ許可する。"""
    if len(key) != _KEY_LENGTH or not all(c in "0123456789abcdef" for c in key):
        raise StorageError("保存キーの形式が不正です")


def path_for(key: str) -> Path:
    _validate_key(key)
    root = get_settings().objects_dir
    return root / key[:2] / key


def save_bytes(data: bytes, *, key: str | None = None) -> StoredObject:
    key = key or new_key()
    target = path_for(key)
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
        tmp.write(data)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    # 検査後の原子的 rename（仕様第12章）
    tmp_path.replace(target)
    return StoredObject(key=key, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))


def save_stream(chunks: Iterable[bytes], *, max_bytes: int, key: str | None = None) -> StoredObject:
    """ストリーム保存。上限超過は保存せず失敗させる（仕様第12章）。"""
    key = key or new_key()
    target = path_for(key)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    tmp_path: Path | None = None
    try:
        with NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            for chunk in chunks:
                total += len(chunk)
                if total > max_bytes:
                    raise StorageError(f"上限 {max_bytes} バイトを超えました")
                digest.update(chunk)
                tmp.write(chunk)
            tmp.flush()
            os.fsync(tmp.fileno())
        tmp_path.replace(target)
        tmp_path = None
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()
    return StoredObject(key=key, sha256=digest.hexdigest(), bytes=total)


def save_file(source: Path, *, key: str | None = None) -> StoredObject:
    key = key or new_key()
    target = path_for(key)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    with source.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
            total += len(chunk)
    shutil.copyfile(source, target)
    return StoredObject(key=key, sha256=digest.hexdigest(), bytes=total)


def read_bytes(key: str) -> bytes:
    path = path_for(key)
    if not path.is_file():
        raise StorageError("保存物が見つかりません")
    return path.read_bytes()


def stat(key: str) -> int:
    path = path_for(key)
    if not path.is_file():
        raise StorageError("保存物が見つかりません")
    return path.stat().st_size


def exists(key: str) -> bool:
    try:
        return path_for(key).is_file()
    except StorageError:
        return False
