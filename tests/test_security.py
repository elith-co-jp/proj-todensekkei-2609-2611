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


def test_allows_loopback_origin(client):
    for origin in ("http://127.0.0.1:8095", "http://localhost:5173", "http://foo.localhost"):
        res = client.post(
            "/api/projects",
            files={"files": ("a.png", make_png(), "image/png")},
            headers={"Origin": origin},
        )
        assert res.status_code == 200, origin


def test_allows_requests_without_origin(client):
    # curl / スクリプト / 同一オリジンのナビゲーションは Origin を送らない
    res = client.get("/api/health")
    assert res.status_code == 200
    res = client.post(
        "/api/projects",
        files={"files": ("a.png", make_png(), "image/png")},
    )
    assert res.status_code == 200
