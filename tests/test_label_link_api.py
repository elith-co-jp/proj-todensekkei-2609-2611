"""ラベルの文字の位置（label_box）の保存・書き出し・取り込みと、近くの文字からのラベルの候補（DD-0049）。"""

from __future__ import annotations

import io
import json
import zipfile

from tests.conftest import create_project

W, H = 400, 300
# 印だけを囲んだ接点（白丸 2 個と棒）と、その右に書かれた器具番号の文字。
MARK = {"cx": 100 / W, "cy": 110 / H, "w": 12 / W, "h": 28 / H}
LABEL_BOX = {"cx": 130 / W, "cy": 108 / H, "w": 40 / W, "h": 24 / H}


def _symbols(label_box=LABEL_BOX, label="83DX-1 P33P22"):
    return [
        {"ref": "C1", "class_key": "contact_a", "label": label, "label_box": label_box, **MARK,
         "terminals": [{"ref": "C1-T1", "name": "1", "tx": 100 / W, "ty": 98 / H},
                       {"ref": "C1-T2", "name": "2", "tx": 100 / W, "ty": 122 / H}]},
        {"ref": "J1", "class_key": "junction", "label": None, "cx": 0.5, "cy": 0.5, "w": 0.02, "h": 0.02},
    ]


def _put(client, pid, symbols):
    res = client.put(f"/api/projects/{pid}/annotations", json={"symbols": symbols, "connections": []})
    assert res.status_code == 200, res.text


def _close(a, b):
    return all(abs(a[k] - b[k]) < 1e-9 for k in ("cx", "cy", "w", "h"))


def test_label_box_is_saved_and_returned(client):
    pid = create_project(client, w=W, h=H)
    _put(client, pid, _symbols())
    by_ref = {s["ref"]: s for s in client.get(f"/api/projects/{pid}").json()["symbols"]}
    assert _close(by_ref["C1"]["label_box"], LABEL_BOX)
    assert by_ref["J1"]["label_box"] is None


def test_label_box_survives_bundle_export_and_import(client):
    pid = create_project(client, w=W, h=H)
    _put(client, pid, _symbols())
    blob = client.post("/api/export", json={"ids": [pid]}).content
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        bundle = json.loads(zf.read("bundle.json"))
        labels = [n for n in zf.namelist() if n.startswith("dataset/labels/") and n.endswith(".txt")]
        yolo = zf.read(labels[0]).decode()
    sym = next(s for s in bundle["projects"][0]["symbols"] if s["ref"] == "C1")
    assert _close(sym["label_box"], LABEL_BOX)
    # 検出の学習データ（YOLO）は印の枠だけ。ラベルの文字の枠は入れない。
    assert len(yolo.strip().splitlines()) == 2
    client.delete(f"/api/projects/{pid}")
    imported = client.post("/api/import", files={"archive": ("export.zip", blob, "application/zip")})
    assert imported.status_code == 200, imported.text
    new_id = imported.json()["project_ids"][0]
    sym = next(s for s in client.get(f"/api/projects/{new_id}").json()["symbols"] if s["ref"] == "C1")
    assert _close(sym["label_box"], LABEL_BOX)


def test_label_box_survives_cvat_round_trip(client):
    pid = create_project(client, w=W, h=H)
    _put(client, pid, _symbols())
    xml = client.get(f"/api/exchange/cvat?project_ids={pid}").content
    assert b'label="label_text"' in xml
    _put(client, pid, _symbols(label_box=None, label=None))
    applied = client.post("/api/exchange/cvat/import", data={"apply": "true"},
                          files={"file": ("a.xml", xml, "application/xml")}).json()
    assert applied["images"][0]["applied"]
    sym = next(s for s in client.get(f"/api/projects/{pid}").json()["symbols"] if s["ref"] == "C1")
    assert sym["label"] == "83DX-1 P33P22"
    assert all(abs(sym["label_box"][k] - LABEL_BOX[k]) < 0.01 for k in LABEL_BOX)


def test_label_links_suggests_the_text_next_to_the_mark(client):
    from database import SessionLocal
    from services.structure_service import save_structure

    pid = create_project(client, w=W, h=H)
    texts = [
        {"text": "9", "kind": "number", "box": [84, 94, 90, 104], "score": 0.9},
        {"text": "83DX-1", "kind": "device_number", "box": [110, 96, 150, 108], "score": 0.9},
        {"text": "P33P22", "kind": "device_number", "box": [110, 108, 150, 120], "score": 0.9},
        {"text": "起動指令", "kind": "note", "box": [96, 40, 108, 90], "score": 0.9},
    ]
    db = SessionLocal()
    try:
        save_structure(db, pid, {"texts": texts})
        db.commit()
    finally:
        db.close()
    symbols = _symbols(label_box=None, label=None)
    res = client.post(f"/api/projects/{pid}/label-links", json={"symbols": symbols})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "analysis"
    link = next(lk for lk in body["links"] if lk["ref"] == "C1")
    assert link["label"] == "83DX-1 P33P22" and link["status"] == "linked" and link["direction"] == "right"
    assert abs(link["label_box"]["cx"] - 130 / W) < 1e-6
    assert [lk["ref"] for lk in body["links"]] == ["C1"]  # 分岐点は器具番号を持たない


def test_label_links_without_text_or_ocr_says_so(client, monkeypatch):
    from services import label_link_service

    def no_ocr(*_args, **_kwargs):
        raise RuntimeError("文字認識（OCR）を使えない環境です。")

    monkeypatch.setattr(label_link_service, "_ocr_items", no_ocr)
    pid = create_project(client, w=W, h=H)
    res = client.post(f"/api/projects/{pid}/label-links", json={"symbols": _symbols(label_box=None)})
    assert res.status_code == 400 and "OCR" in res.json()["detail"]
