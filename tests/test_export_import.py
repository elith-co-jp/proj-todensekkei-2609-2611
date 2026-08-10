from __future__ import annotations

import csv
import io
import json
import zipfile

import yaml

from tests.conftest import SAMPLE_ANNOTATION, create_project


def _export(client, ids, **kw):
    res = client.post("/api/export", json={"ids": ids, **kw})
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(res.content))


def _seed(client, n: int = 1):
    ids = []
    for i in range(n):
        pid = create_project(client, f"ER2121{i}.png", 500, 400)
        client.put(f"/api/projects/{pid}/meta", json={"sheet_no": f"ER2121{i}", "page_no": str(130 + i)})
        client.put(f"/api/projects/{pid}/annotations", json=SAMPLE_ANNOTATION)
        ids.append(pid)
    return ids


# ------------------------------------------------------------------ YOLO 出力
def test_export_contains_expected_layout(client):
    ids = _seed(client, 1)
    zf = _export(client, ids)
    names = set(zf.namelist())
    assert "bundle.json" in names
    assert "classes.txt" in names
    assert "data.yaml" in names
    assert "README.txt" in names
    assert "connections/connections.csv" in names
    assert "connections/netlist.json" in names
    assert any(n.startswith("dataset/images/train/") for n in names)
    assert any(n.startswith("dataset/labels/train/") for n in names)
    # project_<id>/ のような別レイアウトは出力しない
    assert not any(n.startswith(f"project_{ids[0]}/") for n in names)


def test_label_file_is_valid_yolo_format(client):
    ids = _seed(client, 1)
    zf = _export(client, ids)
    label_name = next(n for n in zf.namelist() if n.startswith("dataset/labels/train/"))
    text = zf.read(label_name).decode()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 3
    classes = zf.read("classes.txt").decode().splitlines()
    for ln in lines:
        parts = ln.split(" ")
        assert len(parts) == 5
        idx = int(parts[0])
        assert 0 <= idx < len(classes)
        for v in parts[1:]:
            assert len(v.split(".")[1]) == 6  # 小数 6 桁
            assert 0.0 <= float(v) <= 1.0
    # relay_coil の行が classes.txt の index と一致すること
    coil_idx = classes.index("relay_coil")
    assert any(ln.startswith(f"{coil_idx} ") for ln in lines)


def test_data_yaml_is_ultralytics_compatible(client):
    ids = _seed(client, 1)
    zf = _export(client, ids)
    doc = yaml.safe_load(zf.read("data.yaml").decode())
    classes = zf.read("classes.txt").decode().splitlines()
    assert doc["path"] == "./dataset"
    assert doc["train"] == "images/train"
    # val 分割はしないため、val は train と同じ場所を指す
    assert doc["val"] == "images/train"
    assert doc["nc"] == len(classes)
    assert doc["names"][0] == classes[0]
    assert list(doc["names"].keys()) == list(range(len(classes)))


def test_export_puts_all_images_in_train_without_val(client):
    ids = _seed(client, 5)
    zf = _export(client, ids)
    names = zf.namelist()
    tr = [n for n in names if n.startswith("dataset/images/train/")]
    va = [n for n in names if n.startswith("dataset/images/val/")]
    assert len(tr) == 5
    assert va == []


def test_connections_csv_and_netlist(client):
    ids = _seed(client, 1)
    zf = _export(client, ids)
    text = zf.read("connections/connections.csv").decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 2
    assert rows[0]["from_symbol_ref"] == "SYM-0001"
    assert rows[0]["from_terminal"] == "SYM-0001-T2"
    assert rows[0]["to_symbol_ref"] == "SYM-0002"
    assert rows[0]["wire_no"] == "610"
    assert rows[0]["from_class"] == "relay_coil"
    assert rows[1]["kind"] == "sheet_ref"
    assert rows[1]["external_ref"] == "(610-2F-9)"

    net = json.loads(zf.read("connections/netlist.json").decode())
    members = {m for p in net["projects"] for n in p["nets"] for m in n["members"]}
    assert "SYM-0001:SYM-0001-T2" in members
    assert "SYM-0002:SYM-0002-T1" in members


# ------------------------------------------------------------------ 往復復元
def test_bundle_roundtrip_restores_everything(client):
    ids = _seed(client, 2)
    before = [client.get(f"/api/projects/{i}").json() for i in ids]

    res = client.post("/api/export", json={"ids": ids})
    blob = res.content
    for i in ids:
        client.delete(f"/api/projects/{i}")
    assert client.get("/api/projects").json() == []

    imported = client.post("/api/import", files={"archive": ("export.zip", blob, "application/zip")})
    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["mode"] == "bundle"
    assert body["count"] == 2

    after = [client.get(f"/api/projects/{i}").json() for i in body["project_ids"]]
    for b, a in zip(before, after):
        assert a["name"] == b["name"]
        assert a["sheet_no"] == b["sheet_no"]
        assert a["page_no"] == b["page_no"]
        assert a["image_width"] == b["image_width"]
        assert a["image_height"] == b["image_height"]
        assert len(a["images"]) == len(b["images"])
        assert a["images"][0]["sha256"] == b["images"][0]["sha256"]
        assert a["symbols"] == b["symbols"]
        assert a["connections"] == b["connections"]


def test_roundtrip_keeps_class_index_for_new_class(client):
    pid = create_project(client)
    client.put(
        f"/api/projects/{pid}/annotations",
        json={"symbols": [{"class_key": "timer_relay", "cx": 0.4, "cy": 0.4, "w": 0.1, "h": 0.1}], "connections": []},
    )
    blob = client.post("/api/export", json={"ids": [pid]}).content
    client.delete(f"/api/projects/{pid}")
    new_id = client.post("/api/import", files={"archive": ("e.zip", blob, "application/zip")}).json()["project_ids"][0]
    detail = client.get(f"/api/projects/{new_id}").json()
    assert detail["symbols"][0]["class_key"] == "timer_relay"


def test_import_rejects_zip_without_bundle(client):
    """bundle.json を含まない ZIP（このツール以外の出力）は取り込まない。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("classes.txt", "relay_coil\nterminal\n")
        zf.writestr("images/sheet.png", b"not really a png but enough to be listed")
        zf.writestr("labels/sheet.txt", "0 0.500000 0.500000 0.200000 0.300000\n")
    res = client.post("/api/import", files={"archive": ("yolo.zip", buf.getvalue(), "application/zip")})
    assert res.status_code == 400
    assert "bundle.json" in res.json()["detail"]


def test_import_rejects_non_zip(client):
    res = client.post("/api/import", files={"archive": ("a.txt", b"hello", "text/plain")})
    assert res.status_code == 400
    assert "ZIP" in res.json()["detail"]


def test_import_rejects_path_traversal(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", "x")
    res = client.post("/api/import", files={"archive": ("bad.zip", buf.getvalue(), "application/zip")})
    assert res.status_code == 400
    assert "不正なパス" in res.json()["detail"]


def test_import_ignores_macosx_and_dsstore(client):
    ids = _seed(client, 1)
    blob = client.post("/api/export", json={"ids": ids}).content
    for i in ids:
        client.delete(f"/api/projects/{i}")
    # 書き出した ZIP に __MACOSX / .DS_Store を足しても無視されること
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as src, zipfile.ZipFile(buf, "w") as dst:
        for name in src.namelist():
            dst.writestr(name, src.read(name))
        dst.writestr("__MACOSX/._x.png", b"junk")
        dst.writestr(".DS_Store", b"junk")
    res = client.post("/api/import", files={"archive": ("z.zip", buf.getvalue(), "application/zip")})
    assert res.status_code == 200, res.text
    assert res.json()["count"] == 1


def test_import_zip_without_bundle_is_rejected(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "nothing here")
    res = client.post("/api/import", files={"archive": ("z.zip", buf.getvalue(), "application/zip")})
    assert res.status_code == 400
    assert "bundle.json" in res.json()["detail"]


def test_single_project_export_endpoint(client):
    ids = _seed(client, 1)
    res = client.get(f"/api/projects/{ids[0]}/export")
    assert res.status_code == 200
    assert "attachment" in res.headers["content-disposition"]
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    assert "bundle.json" in zf.namelist()
    bundle = json.loads(zf.read("bundle.json"))
    assert bundle["schema_version"] == "1.0"
    assert bundle["tool"] == "seq-annotator"
    assert len(bundle["projects"]) == 1
    assert len(bundle["projects"][0]["connections"]) == 2
