"""図面下部の文字からシート番号の候補を選ぶ処理と API を確認する。"""

from __future__ import annotations

import io
import sys

import pytest
from PIL import Image, ImageDraw, ImageFont

import database
from models import AnnotationImage
from services import sheet_number_service
from services.sheet_number_service import pick_sheet_number_candidates
from tests.conftest import create_project


def _item(text, score, box):
    return {"text": text, "score": score, "box": box}


def test_ranks_title_block_labelled_and_repeated_numbers():
    items = [
        _item("SHEET NO.", 0.95, (600, 900, 690, 920)),
        _item("ER21226", 0.9, (700, 900, 800, 920)),
        _item("図番", 0.9, (850, 760, 900, 780)),
        _item("ＥＲ２１２２８", 1.0, (850, 800, 950, 820)),
        _item("ER 21227", 0.8, (100, 950, 200, 970)),
        _item("ER21227", 0.6, (300, 950, 400, 970)),
        _item("ZZ999", 0.5, (700, 960, 760, 980)),
        _item("AB123", 0.99, (300, 790, 360, 810)),
        _item("CD456", 0.99, (500, 300, 560, 320)),
        _item("TOKYOELECTRIC", 0.99, (700, 850, 900, 870)),
    ]
    assert pick_sheet_number_candidates(items, 1000, 1000) == [
        {"text": "ER21228", "score": 1.0, "region": "title_block"},
        {"text": "ER21226", "score": 0.95, "region": "title_block"},
        {"text": "ER21227", "score": 0.6, "region": "margin"},
    ]


def test_reads_number_merged_with_its_label_and_clamps_score():
    items = [
        _item("DWG NO. ER21226-2", 1.0, (700, 900, 900, 920)),
        _item("ER21226-2", 0.8, (100, 950, 200, 970)),
    ]
    assert pick_sheet_number_candidates(items, 1000, 1000) == [
        {"text": "ER21226-2", "score": 1.0, "region": "title_block"},
    ]


def test_suggest_returns_candidates_without_writing_sheet_no(client, monkeypatch):
    pid = create_project(client, w=400, h=300)
    items = [_item("SHEET", 0.9, (240, 270, 290, 285)), _item("ER21226", 0.9, (300, 270, 380, 285))]
    monkeypatch.setattr(sheet_number_service, "read_ocr_items", lambda path: (items, 400, 300))

    res = client.post(f"/api/projects/{pid}/sheet-number/suggest")
    assert res.status_code == 200, res.text
    assert res.json() == {"candidates": [{"text": "ER21226", "score": 0.95, "region": "title_block"}]}
    assert client.get(f"/api/projects/{pid}").json()["sheet_no"] is None


def test_suggest_rejects_when_ocr_is_unavailable(client, monkeypatch):
    pid = create_project(client)
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)
    res = client.post(f"/api/projects/{pid}/sheet-number/suggest")
    assert res.status_code == 400
    assert "OCR" in res.json()["detail"]


def test_suggest_rejects_drawing_without_image(client):
    pid = create_project(client)
    with database.SessionLocal() as db:
        db.query(AnnotationImage).filter(AnnotationImage.project_id == pid).delete()
        db.commit()
    res = client.post(f"/api/projects/{pid}/sheet-number/suggest")
    assert res.status_code == 400
    assert "画像" in res.json()["detail"]
    assert client.post("/api/projects/9999/sheet-number/suggest").status_code == 404


def test_real_ocr_reads_sheet_number_from_title_block(client):
    pytest.importorskip("rapidocr_onnxruntime")
    width, height = 2000, 1400
    page = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(page)
    font = ImageFont.load_default(size=40)
    draw.rectangle((20, 20, width - 20, height - 20), outline="black", width=3)
    draw.text((300, 400), "TB16", fill="black", font=font)
    draw.rectangle((1300, 1150, 1980, 1380), outline="black", width=3)
    draw.line((1300, 1270, 1980, 1270), fill="black", width=3)
    draw.line((1540, 1270, 1540, 1380), fill="black", width=3)
    draw.text((1320, 1190), "TOKYO ELECTRIC", fill="black", font=font)
    draw.text((1320, 1300), "SHEET NO.", fill="black", font=font)
    draw.text((1580, 1300), "ER21226", fill="black", font=font)
    buf = io.BytesIO()
    page.save(buf, format="PNG")
    res = client.post("/api/projects", files={"files": ("title.png", buf.getvalue(), "image/png")})
    pid = res.json()["project_ids"][0]

    res = client.post(f"/api/projects/{pid}/sheet-number/suggest")
    assert res.status_code == 200, res.text
    top = res.json()["candidates"][0]
    assert (top["text"], top["region"]) == ("ER21226", "title_block")
    assert top["score"] >= 0.9
