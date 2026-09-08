"""成果物ダウンロードの保護（仕様第12章）。

localhost / private / link-local へ接続させないこと、
許可ホスト以外を拒否すること、リダイレクトをホップごとに検査し直すこと。
"""

from __future__ import annotations

import pytest

from app.services.download_guard import (
    DownloadPolicy,
    DownloadRejected,
    _host_allowed,
    check_url,
)

POLICY = DownloadPolicy(allowed_hosts=frozenset({"example.com"}), max_bytes=1024)
EMPTY_POLICY = DownloadPolicy(allowed_hosts=frozenset(), max_bytes=1024)


def test_http_is_rejected():
    with pytest.raises(DownloadRejected, match="HTTPS以外"):
        check_url("http://example.com/model.glb", POLICY)


def test_non_http_schemes_are_rejected():
    for url in ("file:///etc/passwd", "ftp://example.com/x", "gopher://example.com/"):
        with pytest.raises(DownloadRejected):
            check_url(url, POLICY)


def test_empty_allowlist_downloads_nothing():
    """許可ホストが未設定なら何も取得しない（既定は空）。"""
    with pytest.raises(DownloadRejected, match="許可するホストが設定されていません"):
        check_url("https://example.com/model.glb", EMPTY_POLICY)


def test_host_not_in_allowlist_is_rejected():
    with pytest.raises(DownloadRejected, match="許可していないホスト"):
        check_url("https://attacker.example/model.glb", POLICY)


def test_lookalike_host_is_rejected():
    """接尾辞の一致は「.」区切りのときだけ許す。"""
    assert _host_allowed("cdn.example.com", frozenset({"example.com"})) is True
    assert _host_allowed("example.com", frozenset({"example.com"})) is True
    assert _host_allowed("notexample.com", frozenset({"example.com"})) is False
    assert _host_allowed("example.com.attacker.test", frozenset({"example.com"})) is False


def test_internal_addresses_are_rejected():
    """名前解決の結果が内部アドレスなら拒否する。"""
    cases = {
        "https://127.0.0.1/model.glb": "127.0.0.1",
        "https://localhost/model.glb": None,
        "https://10.0.0.5/model.glb": "10.0.0.5",
        "https://192.168.1.1/model.glb": "192.168.1.1",
        "https://172.16.0.1/model.glb": "172.16.0.1",
        # クラウドのメタデータ用アドレス
        "https://169.254.169.254/latest/meta-data/": "169.254.169.254",
        "https://[::1]/model.glb": "::1",
    }
    for url in cases:
        host = url.split("//", 1)[1].split("/", 1)[0].strip("[]")
        policy = DownloadPolicy(allowed_hosts=frozenset({host}), max_bytes=1024)
        with pytest.raises(DownloadRejected):
            check_url(url, policy)


def test_unresolvable_host_is_rejected():
    policy = DownloadPolicy(allowed_hosts=frozenset({"invalid.invalid"}), max_bytes=1024)
    with pytest.raises(DownloadRejected, match="名前解決できません"):
        check_url("https://invalid.invalid/model.glb", policy)


def test_redirect_target_is_checked_again(monkeypatch):
    """転送先も同じ検査にかけ直す（許可ホストから内部アドレスへ飛ばされても止める）。"""
    import httpx

    from app.services import download_guard

    checked: list[str] = []
    real_check = download_guard.check_url

    def recording_check(url: str, policy: DownloadPolicy) -> str:
        checked.append(url)
        return real_check(url, policy)

    monkeypatch.setattr(download_guard, "check_url", recording_check)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/model.glb":
            # 許可ホストから内部アドレスへ転送しようとする
            return httpx.Response(
                302, headers={"location": "https://169.254.169.254/latest/meta-data/"}
            )
        return httpx.Response(200, content=b"glTF")

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)

    with pytest.raises(DownloadRejected):
        download_guard.fetch_bytes("https://example.com/model.glb", POLICY)

    # 転送先も検査対象になっている
    assert checked[0] == "https://example.com/model.glb"
    assert any("169.254.169.254" in url for url in checked)


def test_oversized_download_is_stopped(monkeypatch):
    import httpx

    from app.services import download_guard

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 5000)

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **kw: original_client(*a, **{**kw, "transport": transport}),
    )
    monkeypatch.setattr(download_guard, "check_url", lambda url, policy: "example.com")

    with pytest.raises(DownloadRejected, match="上限"):
        download_guard.fetch_bytes("https://example.com/model.glb", POLICY)


def test_successful_download_returns_bytes(monkeypatch):
    import httpx

    from app.services import download_guard

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"glTF-body")

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **kw: original_client(*a, **{**kw, "transport": transport}),
    )
    monkeypatch.setattr(download_guard, "check_url", lambda url, policy: "example.com")

    assert download_guard.fetch_bytes("https://example.com/model.glb", POLICY) == b"glTF-body"
