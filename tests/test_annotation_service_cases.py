"""画像の保存・クラスの登録・アノテーションの置き換えの細かい場合（services/annotation_service）。"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from database import SessionLocal
from models import SymbolClass
from services import annotation_service as svc
from tests.conftest import create_project


def _png(mode: str, color) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (40, 30), color).save(buf, format="PNG")
    return buf.getvalue()


def test_store_image_rejects_empty_large_and_broken_files(monkeypatch):
    with pytest.raises(ValueError, match="空"):
        svc.store_image_bytes(b"")
    with pytest.raises(ValueError, match="読み込めません"):
        svc.store_image_bytes(b"not an image")
    monkeypatch.setattr(svc, "MAX_IMAGE_BYTES", 3)
    with pytest.raises(ValueError, match="上限"):
        svc.store_image_bytes(b"1234")


def test_transparent_and_grey_images_are_stored_and_thumbnailed(client):
    rgba_sha, w, h = svc.store_image_bytes(_png("RGBA", (255, 0, 0, 128)))
    with Image.open(svc.image_path(rgba_sha)) as img:
        assert img.mode == "RGB" and (img.width, img.height) == (w, h)
    grey_sha, _, _ = svc.store_image_bytes(_png("L", 200))
    with Image.open(svc.ensure_thumbnail(grey_sha)) as thumb:
        assert thumb.mode == "RGB"


def test_ensure_class_reuses_keys_and_skips_taken_yolo_indexes(client):
    db = SessionLocal()
    try:
        assert svc.ensure_class(db, "relay_coil").key == "relay_coil"
        count = db.query(SymbolClass).count()
        db.add(SymbolClass(key="taken", label="taken", yolo_index=count + 1, color="#000000", sort_order=0, is_active=True))
        db.flush()
        assert svc.ensure_class(db, "fresh").yolo_index == count + 2
    finally:
        db.rollback()
        db.close()


def test_broken_attribute_json_reads_as_empty():
    assert svc.load_attributes("{壊れた") == {} and svc.load_attributes("[1]") == {}


def test_duplicate_symbol_refs_are_renumbered_and_self_loops_skipped(client):
    pid = create_project(client)
    sym = {"class_key": "relay_coil", "cx": 0.5, "cy": 0.5, "w": 0.1, "h": 0.1,
           "terminals": [{"ref": "A-T1", "name": "1", "tx": 0.5, "ty": 0.45}]}
    payload = {"symbols": [{**sym, "ref": "A"}, {**sym, "ref": "A", "terminals": []}],
               "connections": [{"from_symbol_ref": "A", "from_terminal_ref": "A-T1",
                                "to_symbol_ref": "A", "to_terminal_ref": "A-T1"}]}
    res = client.put(f"/api/projects/{pid}/annotations", json=payload).json()
    assert res["skipped"] == ["A -> A（自己ループ）"]
    refs = [s["ref"] for s in client.get(f"/api/projects/{pid}").json()["symbols"]]
    assert refs[0] == "A" and refs[1] != "A" and len(set(refs)) == 2
