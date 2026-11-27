"""ローカル API への CSRF 系アクセス制御のテスト。

ブラウザからの POST は Origin を必ず送るため、ローカル以外の Origin を
持つリクエスト（任意サイトからの simple request）は拒否する。
"""

from __future__ import annotations

from tests.conftest import make_png


def test_rejects_foreign_origin_post(client):
    res = client.post(
        "/api/projects",
        files={"files": ("a.png", make_png(), "image/png")},
        headers={"Origin": "https://evil.example"},
    )
    assert res.status_code == 403


def test_rejects_null_origin(client):
    res = client.get("/api/health", headers={"Origin": "null"})
    assert res.status_code == 403


def test_rejects_localhost_subdomain(client):
    # *.localhost は別ローカルサービスになり得るため信頼しない
    res = client.get("/api/health", headers={"Origin": "http://evil.localhost:9999"})
    assert res.status_code == 403


def test_allows_loopback_origin(client):
    for origin in ("http://127.0.0.1:8095", "http://localhost:5173"):
        res = client.post(
            "/api/projects",
            files={"files": ("a.png", make_png(), "image/png")},
            headers={"Origin": origin},
        )
        assert res.status_code == 200, origin


def test_allows_configured_cors_origin(client):
    # SEQANNO_CORS_ORIGINS の既定値にも含まれる開発サーバ
    res = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert res.status_code == 200


def test_allows_requests_without_origin(client):
    # curl / スクリプト / 同一オリジンのナビゲーションは Origin を送らない
    res = client.get("/api/health")
    assert res.status_code == 200
    res = client.post(
        "/api/projects",
        files={"files": ("a.png", make_png(), "image/png")},
    )
    assert res.status_code == 200


# ---------------------------------------------------------------- DNS リバインディング
def test_rejects_foreign_host(client):
    # 攻撃サイトのドメインを 127.0.0.1 に向け直したとき、Host はそのドメインになる
    res = client.get("/api/projects", headers={"Host": "evil.example:8010"})
    assert res.status_code == 403


def test_allows_loopback_hosts(client):
    for host in ("127.0.0.1:8010", "localhost", "[::1]:8010", "LOCALHOST:3000"):
        res = client.get("/api/health", headers={"Host": host})
        assert res.status_code == 200, host


# ---------------------------------------------------------------- 守りのヘッダ
def test_security_headers_on_responses(client):
    for res in (client.get("/api/health"), client.get("/api/health", headers={"Origin": "https://evil.example"})):
        assert res.headers["X-Content-Type-Options"] == "nosniff"
        assert res.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in res.headers["Content-Security-Policy"]


# ---------------------------------------------------------------- 画面の静的ファイル
def test_spa_path_rejects_unc_and_traversal():
    from main import _is_plain_relative

    assert _is_plain_relative("favicon.ico")
    assert _is_plain_relative("img/logo.svg")
    for bad in ("/attacker/share/x", "\\\\attacker\\share\\x", "..\\secret", "../secret", "a/../../b",
                "C:/Windows/win.ini", "a//b", ""):
        assert not _is_plain_relative(bad), bad
