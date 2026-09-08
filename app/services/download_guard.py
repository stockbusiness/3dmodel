"""事業者からの成果物ダウンロードの保護（仕様第12章）。

- HTTPS のみ
- 許可ホストのみ（設定で与える。既定は空で、設定しなければ何も取得しない）
- 名前解決した全アドレスを検査し、localhost / private / link-local 等へ接続させない
- リダイレクトは自動追従せず、1ホップごとに同じ検査をやり直す
- ストリーム保存し、サイズ上限を超えたら打ち切る
- 利用者が入力したURLは扱わない。事業者APIが返した参照だけを渡すこと

SDK が独自にダウンロードする経路は使わない。理由は docs/provider-contracts.md に記録した
（Tripo公式SDKは、SSL検証に失敗すると検証を無効にして再取得する実装になっている）。
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

MAX_REDIRECTS = 5
_ALLOWED_SCHEMES = ("https",)


class DownloadRejected(Exception):
    """取得を許さない、または取得に失敗した。"""


@dataclass(frozen=True)
class DownloadPolicy:
    allowed_hosts: frozenset[str]
    max_bytes: int
    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    total_timeout: float = 300.0


def _host_allowed(host: str, allowed: frozenset[str]) -> bool:
    """ホスト名の完全一致、またはドット区切りの下位ドメイン一致を許す。"""
    host = host.lower().rstrip(".")
    for entry in allowed:
        entry = entry.lower().rstrip(".")
        if host == entry or host.endswith("." + entry):
            return True
    return False


def _reject_special_address(address: str) -> None:
    parsed = ipaddress.ip_address(address)
    if (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
    ):
        raise DownloadRejected(f"接続を許さないアドレスです: {address}")


def check_url(url: str, policy: DownloadPolicy) -> str:
    """URLを検査する。問題なければホスト名を返す。

    名前解決した全てのアドレスを調べる。1つでも内部アドレスがあれば拒否する。
    """
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise DownloadRejected(f"HTTPS以外は取得しません: {parts.scheme or '(なし)'}")
    host = parts.hostname
    if not host:
        raise DownloadRejected("ホスト名がありません")
    if not policy.allowed_hosts:
        raise DownloadRejected(
            "ダウンロードを許可するホストが設定されていません。"
            "事業者の成果物配信ホストを確認してから設定してください"
        )
    if not _host_allowed(host, policy.allowed_hosts):
        raise DownloadRejected(f"許可していないホストです: {host}")

    # ホスト名がIPアドレスならそのまま、そうでなければ名前解決して全件を調べる
    try:
        parsed_ip = ipaddress.ip_address(host)
    except ValueError:
        parsed_ip = None
    if parsed_ip is not None:
        _reject_special_address(str(parsed_ip))
        return host

    try:
        infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise DownloadRejected(f"名前解決できません: {host}") from exc
    if not infos:
        raise DownloadRejected(f"名前解決できません: {host}")
    for info in infos:
        _reject_special_address(info[4][0])
    return host


def _verify_peer(response: httpx.Response) -> None:
    """接続後の相手アドレスも確かめる（名前解決の後で切り替えられる場合に備える）。

    取得できない環境ではこの確認を飛ばす。事前の名前解決の検査は済んでいる。
    """
    stream = response.extensions.get("network_stream")
    if stream is None:
        return
    try:
        server_addr = stream.get_extra_info("server_addr")
    except Exception:  # noqa: BLE001 - 取得できないだけなら検査を飛ばす
        return
    if not server_addr:
        return
    address = server_addr[0] if isinstance(server_addr, tuple) else str(server_addr)
    try:
        _reject_special_address(address)
    except ValueError:
        return


def _iter_body(response: httpx.Response, max_bytes: int) -> Iterator[bytes]:
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise DownloadRejected(f"ダウンロード上限 {max_bytes} バイトを超えました")
        yield chunk


def fetch_bytes(url: str, policy: DownloadPolicy) -> bytes:
    """検査を通したURLから取得する。リダイレクトは1ホップずつ検査し直す。"""
    timeout = httpx.Timeout(
        policy.total_timeout, connect=policy.connect_timeout, read=policy.read_timeout
    )
    current = url
    with httpx.Client(follow_redirects=False, timeout=timeout, trust_env=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            check_url(current, policy)
            with client.stream("GET", current) as response:
                _verify_peer(response)
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise DownloadRejected("転送先が示されていません")
                    # 相対・絶対どちらでも、転送先を同じ検査にかけ直す
                    current = str(httpx.URL(current).join(location))
                    response.close()
                    continue
                if response.status_code != 200:
                    raise DownloadRejected(f"取得に失敗しました（HTTP {response.status_code}）")

                declared = response.headers.get("content-length")
                if declared is not None:
                    try:
                        if int(declared) > policy.max_bytes:
                            raise DownloadRejected(
                                f"ダウンロード上限 {policy.max_bytes} バイトを超えます"
                            )
                    except ValueError:
                        pass
                return b"".join(_iter_body(response, policy.max_bytes))
    raise DownloadRejected("転送が多すぎます")
