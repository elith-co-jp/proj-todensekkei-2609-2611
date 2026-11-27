"""表の読み取りの確認（DD-0051）: OCR を元の並びで返し、人が直して確定したリレー表を解析結果に使う。"""

from __future__ import annotations

import json

import database
from analysis import text_extraction as tx
from models import StructurePrediction, TableReview
from services.structure_service import get_structure, save_structure
from tests.conftest import create_project

W, H = 1755, 1241


def item(text, x0, y0, x1, y1, score=0.99):
    return {"text": text, "score": score, "box": [x0, y0, x1, y1], "zone": "relay_table"}


def _relay_items():
    x, y = 60, 960
    out = [item("器具番号", x, y, x + 50, y + 18)]
    rows = [("5-9-1", "a", "本", "10D"), ("8-12-4", "a", "ER21237", "140"), ("7-11-3", "", "", ""), ("6-10-2", "", "", "")]
    for i, (terminal, contact, sheet, position) in enumerate(rows):
        top = y + 20 + 18 * i
        out.append(item(terminal, x + 57, top, x + 100, top + 17))
        for text, x0, x1, score in ((contact, x + 107, x + 120, 0.99), (sheet, x + 130, x + 180, 0.99),
                                    (position, x + 182, x + 202, 0.55)):
            if text:
                out.append(item(text, x0, top, x1, top + 17, score))
    out += [item("33HB", x + 10, y + 38, x + 45, y + 55), item("FV2306", x + 10, y + 56, x + 45, y + 73)]
    return out


def _payload(project_id):
    texts = _relay_items() + [{"text": "起動指令", "score": 0.9, "box": [600, 300, 660, 318], "zone": None}]
    rows = tx.parse_relay_table(texts, W, H)
    return {
        "status": "completed", "source": {"project_id": project_id}, "image_size": {"width": W, "height": H},
        "symbols": [], "wires": [], "from_to": {"connection_count": 0, "connections": []}, "warnings": [],
        "texts": texts,
        "regions": [{"bbox": {"x0": 50, "y0": 950, "x1": 280, "y1": 1060}, "region_type": "relay_table", "source": "text"}],
        "relay_table": {"rows": rows, "links": tx.link_relay_table(rows, [])},
    }


def _seed(client):
    project_id = create_project(client, w=400, h=300)
    with database.SessionLocal() as db:
        db.add(StructurePrediction(project_id=project_id, payload_json=json.dumps(_payload(project_id))))
        db.commit()
    return project_id


def _cell(table, text):
    return next((c["row"], c["col"]) for c in table["grid"]["cells"] if c["text"] == text)


def test_tables_follow_the_original_layout_and_flag_low_scores(client):
    project_id = _seed(client)
    body = client.get(f"/api/projects/{project_id}/tables").json()
    assert body["analyzed"] is True
    [table] = body["tables"]
    assert (table["key"], table["label"], table["status"]) == ("relay_table", "リレー表", "unreviewed")
    # 見出し＋端子 4 行、器具番号・端子・接点・シート・行列の 5 列
    assert (len(table["grid"]["rows"]), len(table["grid"]["cols"])) == (5, 5)
    assert _cell(table, "5-9-1") == (1, 1) and _cell(table, "33HB") == (2, 0) and _cell(table, "140") == (2, 4)
    assert table["low_score_count"] == 2 and table["changed_count"] == 0
    assert body["relay_source"] == "ocr"
    assert body["relay_rows"][1]["position"] == "14D"  # AI の読みでも 140 は 14D に直している


def test_confirmed_relay_table_is_used_and_survives_reanalysis(client):
    project_id = _seed(client)
    table = client.get(f"/api/projects/{project_id}/tables").json()["tables"][0]
    values = {(v["row"], v["col"]): v["text"] for v in table["values"]}
    values[_cell(table, "ER21237")] = "ER21238"   # 読み違いを直す
    values[(3, 2)] = "b"                          # 読み落とした接点を書き足す（文字の無かった欄）
    payload = [{"row": r, "col": c, "text": t} for (r, c), t in values.items()]

    draft = client.put(f"/api/projects/{project_id}/tables/relay_table", json={"values": payload}).json()
    assert draft["tables"][0]["status"] == "draft" and draft["tables"][0]["changed_count"] == 2
    assert draft["relay_source"] == "ocr"  # 確定するまでは AI の読み

    done = client.put(f"/api/projects/{project_id}/tables/relay_table", json={"values": payload, "confirm": True}).json()
    assert done["tables"][0]["status"] == "confirmed" and done["tables"][0]["confirmed_at"]
    assert done["relay_source"] == "confirmed"
    assert done["relay_rows"][1]["sheet"] == "ER21238"
    assert done["relay_rows"][2] == {"device": "33HB FV2306", "terminal": "7-11-3", "contact": "b", "sheet": "", "position": ""}

    # 解析し直しても（新しい AI の読みでも）確定したリレー表を使う
    with database.SessionLocal() as db:
        db.query(StructurePrediction).filter_by(project_id=project_id).delete()
        save_structure(db, project_id, _payload(project_id))
        db.commit()
        structure = get_structure(db, project_id)
    assert structure["relay_table"]["confirmed"] is True
    assert structure["relay_table"]["rows"][1]["sheet"] == "ER21238"
    assert structure["relay_table"]["ocr_rows"][1]["sheet"] == "ER21237"

    # 確定の後に直して保存すると確認中に戻り、AI の読みに戻る
    reopened = client.put(f"/api/projects/{project_id}/tables/relay_table", json={"values": payload}).json()
    assert reopened["tables"][0]["status"] == "draft" and reopened["tables"][0]["confirmed_at"] is None
    assert reopened["relay_source"] == "ocr" and reopened["relay_rows"][1]["sheet"] == "ER21237"


def test_reset_starts_again_from_the_ai_reading(client):
    project_id = _seed(client)
    client.put(f"/api/projects/{project_id}/tables/relay_table", json={"values": [], "confirm": True})
    body = client.delete(f"/api/projects/{project_id}/tables/relay_table").json()
    assert body["tables"][0]["status"] == "unreviewed" and body["relay_source"] == "ocr"
    with database.SessionLocal() as db:
        assert db.query(TableReview).count() == 0


def test_rejects_unknown_tables_cells_and_unanalyzed_drawings(client):
    project_id = _seed(client)
    assert client.put(f"/api/projects/{project_id}/tables/io_table", json={"values": []}).status_code == 404
    res = client.put(f"/api/projects/{project_id}/tables/relay_table", json={"values": [{"row": 99, "col": 0, "text": "x"}]})
    assert res.status_code == 400
    other = create_project(client, name="ER21217.png")
    assert client.get(f"/api/projects/{other}/tables").json() == {
        "project_id": other, "analyzed": False, "image_size": None, "tables": [], "relay_rows": []}
    assert client.put(f"/api/projects/{other}/tables/relay_table", json={"values": []}).status_code == 404


def test_reviews_are_deleted_with_the_drawing(client):
    project_id = _seed(client)
    client.put(f"/api/projects/{project_id}/tables/relay_table", json={"values": []})
    assert client.delete(f"/api/projects/{project_id}").status_code in (200, 204)
    with database.SessionLocal() as db:
        assert db.query(TableReview).count() == 0
