"""ベクター PDF の線・文字・黒丸と接続の候補（DD-0035）。"""

import pymupdf
import pytest

from services import vector_pdf


def _pdf(rotate: int = 0) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(5, 5, 595, 395))          # 図枠
    shape.finish(color=(0, 0, 0), width=1)
    shape.draw_line((100, 100), (300, 100))                # 母線
    shape.draw_line((200, 100), (200, 300))                # T 字で母線に着く
    shape.draw_line((150, 200), (250, 200))                # 縦の線と十字に交わる（黒丸なし → 交差）
    shape.draw_line((150, 250), (250, 250))                # 縦の線と十字に交わる（黒丸あり → 接続）
    shape.finish(color=(0, 0, 0), width=1)
    shape.draw_circle((200, 250), 2)                       # 黒丸
    shape.finish(color=(0, 0, 0), fill=(0, 0, 0))
    shape.draw_line((400, 100), (400, 300))                # 点線（盤の枠）
    shape.finish(color=(0, 0, 0), width=1, dashes="[3 3] 0")
    shape.commit()
    page.insert_text((105, 95), "PER2100100", fontsize=8)
    page.insert_text((210, 160), "33HB FV2306", fontsize=8, rotate=90)
    if rotate:
        page.set_rotation(rotate)
    return doc.tobytes()


def test_lines_texts_dots_and_connection_rules():
    page = vector_pdf.extract(_pdf(), dpi=72)[0]
    assert page["kind"] == "vector" and (page["width"], page["height"]) == (600, 400)
    assert page["stats"]["frame"] == 4 and page["stats"]["dashed"] == 1
    assert page["stats"]["dots"] == 1
    wires = page["wires"]
    idx = {(round(s["x1"]), round(s["y1"]), round(s["x2"]), round(s["y2"])): i for i, s in enumerate(wires)}
    bus, vert = idx[(100, 100, 300, 100)], idx[(200, 100, 200, 300)]
    cross, dotted = idx[(150, 200, 250, 200)], idx[(150, 250, 250, 250)]
    net_of = {i: k for k, net in enumerate(page["nets"]) for i in net}
    assert net_of[bus] == net_of[vert] == net_of[dotted]
    assert net_of[cross] != net_of[vert]                    # 黒丸の無い十字は単なる交差
    assert {(c["x"], c["y"]) for c in page["crossings"]} == {(200.0, 200.0)}
    texts = {t["text"]: t for t in page["texts"]}
    assert texts["PER2100100"]["direction"] == "horizontal"
    assert texts["33HB FV2306"]["direction"] == "vertical"
    assert vector_pdf.structure_texts(page)[0]["source"] == "pdf_text"


@pytest.mark.parametrize("turn", [90, 270])
def test_coordinates_follow_registration_turn(turn):
    plain = vector_pdf.extract(_pdf(), dpi=72)[0]
    turned = vector_pdf.extract(_pdf(), dpi=72, turns=[turn])[0]
    assert (turned["width"], turned["height"]) == (400, 600)
    x, _y = vector_pdf._turn_point(200, 100, 600, 400, turn)
    assert any(abs(s["x1"] - x) < 1 or abs(s["x2"] - x) < 1 for s in turned["wires"])
    assert len(turned["nets"]) == len(plain["nets"])


def test_scanned_pdf_is_reported_as_scan():
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 30, 20), False)
    pix.clear_with(255)
    page.insert_image(page.rect, pixmap=pix)
    assert vector_pdf.extract(doc.tobytes())[0]["kind"] == "scan"


def test_upload_stores_vector_pdf_and_serves_it(client):
    res = client.post("/api/projects", files={"files": ("図面.pdf", _pdf(), "application/pdf")})
    assert res.status_code == 200, res.text
    pid = res.json()["project_ids"][0]
    got = client.get(f"/api/projects/{pid}/vector-pdf")
    assert got.status_code == 200
    body = got.json()
    assert body["kind"] == "vector" and body["stats"]["crossings"] == 1
    assert any(t["text"] == "PER2100100" for t in body["texts"])
    assert client.get("/api/projects/999999/vector-pdf").status_code == 404


def test_stored_lines_are_ignored_for_a_different_image():
    vector_pdf.save(424242, {"kind": "vector", "texts": []}, "aaa")
    assert vector_pdf.load(424242, "aaa")["kind"] == "vector"
    assert vector_pdf.load(424242, "bbb") is None and vector_pdf.load(424242, None) is None
    vector_pdf.remove(424242)
    assert vector_pdf.load(424242, "aaa") is None


def test_geometry_helpers_edge_cases():
    assert vector_pdf._turn_point(10, 20, 600, 400, 180) == (590, 380)
    assert vector_pdf._seg((0, 0), (0.5, 0.5), 1, False) is None
    assert vector_pdf._seg((0, 0), (30, 40), 1, False)["orient"] == "d"
    point = {"x1": 5, "y1": 5, "x2": 5, "y2": 5, "orient": "h"}
    assert vector_pdf._on_segment(50, 50, point, 1) is None
    line = {"x1": 0, "y1": 0, "x2": 10, "y2": 0, "orient": "h"}
    assert vector_pdf._on_segment(20, 0, line, 1) is None
    assert vector_pdf._intersection(line, dict(line)) is None
    far = {"x1": 50, "y1": 50, "x2": 50, "y2": 60, "orient": "v"}
    assert vector_pdf._intersection(line, far) is None


def test_quads_curves_blank_texts_and_mixed_pages():
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    shape = page.new_shape()
    shape.draw_quad(pymupdf.Rect(100, 100, 140, 130).quad)
    shape.draw_circle(pymupdf.Point(300, 200), 30)                    # 塗らない円（曲線）は配線にしない
    for k in range(25):
        shape.draw_line(pymupdf.Point(50 + 20 * k, 300), pymupdf.Point(50 + 20 * k, 320))
    shape.finish(color=(0, 0, 0), width=1)
    shape.commit()
    page.insert_text((400, 50), "   ", fontsize=8)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 30, 20), False)
    pix.clear_with(255)
    page.insert_image(page.rect, pixmap=pix, overlay=False)
    result = vector_pdf.extract(doc.tobytes(), dpi=72)[0]
    assert result["kind"] == "mixed"
    assert result["texts"] == []
    quad_edges = [s for s in result["wires"] if 100 <= s["x1"] <= 140 and 100 <= s["y1"] <= 130]
    assert len(quad_edges) == 4


def test_extract_needs_pymupdf(monkeypatch):
    monkeypatch.setattr(vector_pdf, "_load_pymupdf", lambda: None)
    with pytest.raises(RuntimeError, match="PyMuPDF"):
        vector_pdf.extract(b"%PDF")


def test_crossing_diagonals_are_neither_joined_nor_crossings():
    a = vector_pdf._seg((0, 0), (40, 40), 1, False)
    b = vector_pdf._seg((0, 40), (40, 0), 1, False)
    graph = vector_pdf.connectivity([a, b], [])
    assert graph == {"nets": [[0], [1]], "junctions": [], "crossings": []}
