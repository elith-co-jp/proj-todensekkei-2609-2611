"""Synthetic inputs only: no drawing or reference annotation fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw


def _runtime():
    pytest.importorskip("cv2")
    from analysis import runtime

    return runtime


def test_adapter_reuses_wire_detector_and_removes_temp_files(tmp_path, monkeypatch):
    runtime = _runtime()
    image = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 1160, 760), outline="black", width=3)
    draw.line([(150, 250), (1000, 250)], fill="black", width=3)
    draw.line([(500, 150), (500, 650)], fill="black", width=3)
    image_path = tmp_path / "input.png"
    image.save(image_path)
    monkeypatch.setattr(runtime, "load_symbol_ocr_reader", lambda *_: (None, "none", "not available"))
    work = tmp_path / "work"
    result = runtime.analyze_detections(image_path, [{
        "class_key": "connector", "class_label": "Connector", "cx": 0.125,
        "cy": 0.3125, "w": 0.03, "h": 0.05, "confidence": 0.9,
    }], source={"project_id": 123}, work_dir=work)
    assert result["symbols"][0]["bbox"] == {"x0": 132, "y0": 230, "x1": 168, "y1": 270}
    assert result["status"] == "partial" and result["warnings"]
    assert result["quality"]["annotation_assistance"] is False
    assert all(t["source"] != "annotation" for s in result["symbols"] for t in s["terminals"])
    assert result["wires"]
    assert not list(work.iterdir())
    assert result["source"] == {"project_id": 123}
    assert result["from_to"]["source"] == {"project_id": 123}
    assert "base_image" not in result["source"]
    assert result["capabilities"]["external_references"] == "not_resolved"


def test_image_and_pdf_entry_points_produce_identical_wire_geometry(tmp_path, monkeypatch):
    runtime = _runtime()
    import analyze_pdf_structure as detector
    from build_e2e_demo_outputs import build_final_output_from_analysis

    image = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 1160, 760), outline="black", width=3)
    draw.line([(150, 250), (1000, 250)], fill="black", width=3)
    draw.line([(600, 150), (600, 650)], fill="black", width=3)
    monkeypatch.setattr(detector, "render_page", lambda *_: image)
    detector.analyze_page(Path("synthetic.pdf"), 1, 150, tmp_path / "pdf", False, "components")
    runtime.analyze_image(image, 1, 150, tmp_path / "image", False, "components", write_debug_images=False)
    original = build_final_output_from_analysis(tmp_path / "pdf" / "page_001", "test")
    registered = build_final_output_from_analysis(tmp_path / "image" / "page_001", "test")
    for key in ("wires", "nodes", "edges", "text_regions"):
        assert original[key] == registered[key]


def test_text_extraction_hook_reads_relay_table_and_keeps_text_zone_symbols_unwired(tmp_path, monkeypatch):
    runtime = _runtime()
    image = Image.new("RGB", (900, 600), "white")
    ImageDraw.Draw(image).line([(150, 250), (800, 250)], fill="black", width=3)
    image_path = tmp_path / "input.png"
    image.save(image_path)
    page_items = [("(ER21212)", (160, 230, 215, 244)), ("33HB", (45, 485, 70, 497)), ("FV2306", (44, 500, 72, 512))]
    for i, (terminal, contact, sheet, position) in enumerate(
        [("5-9-1", "a", "本", "10D"), ("8-12-4", "a", "本", "140"), ("7-11-3", "", "", ""), ("6-10-2", "", "", "")]
    ):
        y = 470 + 15 * i
        page_items.append((terminal, (75, y, 100, y + 12)))
        page_items += [(text, (x0, y, x1, y + 12)) for text, x0, x1 in ((contact, 104, 110), (sheet, 112, 125), (position, 127, 140)) if text]

    def reader(array, **_):
        if array.shape[:2] != (1200, 1800):
            return [], None
        return [([[x0 * 2, y0 * 2], [x1 * 2, y0 * 2], [x1 * 2, y1 * 2], [x0 * 2, y1 * 2]], text, 0.99)
                for text, (x0, y0, x1, y1) in page_items], None

    monkeypatch.setattr(runtime, "load_symbol_ocr_reader", lambda *_: (reader, "rapidocr", None))
    detections = [
        {"class_key": "connector", "class_label": "Connector", "cx": 0.07, "cy": 0.8, "w": 0.03, "h": 0.03, "confidence": 0.9},
        {"class_key": "contact_a", "class_label": "Contact", "cx": 0.5, "cy": 0.42, "w": 0.04, "h": 0.06, "confidence": 0.9},
    ]
    result = runtime.analyze_detections(image_path, detections, source={}, work_dir=tmp_path / "work")
    assert result["relay_table"]["rows"][:2] == [
        {"device": "33HB FV2306", "terminal": "5-9-1", "contact": "a", "sheet": "本", "position": "10D"},
        {"device": "33HB FV2306", "terminal": "8-12-4", "contact": "a", "sheet": "本", "position": "14D"},
    ]
    assert [(r["target_sheet"], r["kind"]) for r in result["sheet_references"]] == [("ER21212", "wire")]
    assert [s["id"] for s in result["symbols"]] == ["symbol_001", "symbol_002"]
    assert result["symbols"][0]["status"] == "suppressed_text_zone"
    assert result["quality"]["text_zone_suppressed_count"] == 1
    assert {r["region_type"] for r in result["regions"]} == {"relay_table"}
