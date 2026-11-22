"""ZIP の書き出し・取り込みの細かい場合（壊れた ZIP・大きすぎる ZIP・名前で引く画像・欠けたデータ）。"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from services import export_service as ex
from services.annotation_service import image_path
from tests.conftest import create_project, make_png


def _zip(files: dict[str, bytes], *, dirs=()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in dirs:
            zf.writestr(zipfile.ZipInfo(d), b"")
        for name, raw in files.items():
            zf.writestr(name, raw)
    return buf.getvalue()


def _import(client, blob: bytes):
    return client.post("/api/import", files={"archive": ("a.zip", blob, "application/zip")})


def _bundle(projects, classes=()):
    return json.dumps({"classes": list(classes), "projects": projects}).encode()


def test_broken_zip_and_bundle_and_empty_bundle(client):
    assert "ZIP ファイルとして読み込めません" in _import(client, b"PK not really").json()["detail"]
    assert "解析できません" in _import(client, _zip({"bundle.json": b"{"})).json()["detail"]
    assert "取り込めるデータが見つかりません" in _import(client, _zip({"bundle.json": _bundle([])})).json()["detail"]


def test_zip_limits(monkeypatch):
    with zipfile.ZipFile(io.BytesIO(_zip({"a.txt": b"1", "b.txt": b"2"}, dirs=["d/"]))) as zf:
        assert [i.filename for i in ex.safe_members(zf)] == ["a.txt", "b.txt"]
        monkeypatch.setattr(ex, "MAX_IMPORT_FILES", 1)
        with pytest.raises(ValueError, match="ファイル数"):
            ex.safe_members(zf)
        monkeypatch.setattr(ex, "MAX_IMPORT_FILES", 10)
        monkeypatch.setattr(ex, "MAX_IMPORT_UNCOMPRESSED_BYTES", 1)
        with pytest.raises(ValueError, match="展開後のサイズ"):
            ex.safe_members(zf)
    monkeypatch.undo()
    with (
        zipfile.ZipFile(io.BytesIO(_zip({"zeros.bin": bytes(11 * 1024**2)}))) as zf,
        pytest.raises(ValueError, match="ZIP 爆弾"),
    ):
        ex.safe_members(zf)


def test_bundle_with_new_classes_images_by_name_and_dangling_wires(client):
    png = make_png(64, 48)
    symbol = {"ref": "S1", "class_key": "breaker_new", "cx": 0.5, "cy": 0.5, "w": 0.1, "h": 0.1, "terminals": []}
    projects = [
        {"name": "by-name", "image_width": 0, "image_height": 0,
         "images": [{"filename": "sheet1.png", "sha256": "x"}, {"filename": "missing.png", "sha256": "y"}],
         "symbols": [symbol], "connections": [{"from_symbol_ref": "S1", "to_symbol_ref": "GONE"}]},
    ]
    classes = [{"key": ""}, {"key": "breaker_new", "label": "新しい種類"}]
    res = _import(client, _zip({"bundle.json": _bundle(projects, classes), "images/sheet1.png": png}))
    assert res.status_code == 200, res.text
    pid = res.json()["project_ids"][0]
    detail = client.get(f"/api/projects/{pid}").json()
    assert (detail["image_width"], detail["image_height"]) == (64, 48)
    assert [i["filename"] for i in detail["images"]] == ["sheet1.png"] and detail["connections"] == []
    assert any(c["key"] == "breaker_new" for c in client.get("/api/classes").json())


def test_export_skips_images_missing_on_disk(client):
    pid = create_project(client)
    sha = client.get(f"/api/projects/{pid}").json()["images"][0]["sha256"]
    image_path(sha).unlink()
    res = client.get(f"/api/projects/{pid}/export")
    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        assert not [n for n in zf.namelist() if n.endswith(".png")]
