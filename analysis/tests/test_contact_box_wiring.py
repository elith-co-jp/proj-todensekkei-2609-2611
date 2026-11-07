"""接点を囲む四角が配線を兼ねる描き方（ER21282）の配線を取り出せることを確かめる（#49）。"""

from __future__ import annotations

# ruff: noqa: I001

import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from analyze_pdf_structure import Box, LineSegment, analyze_image, merge_candidate_lines
from build_e2e_demo_outputs import build_final_output_from_analysis
from experiment_symbol_port_inference import infer_ports_for_symbol
from run_yolo_e2e_pipeline import port_inference_config


def wires_of(image: Image.Image) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        analyze_image(image, 1, 150, Path(tmp), False, "components", write_debug_images=False)
        return build_final_output_from_analysis(Path(tmp) / "page_001", "registered_image")["wires"]


def horizontal_at(wires: list[dict], y: float) -> list[dict]:
    return [w for w in wires if abs(w["polyline"][0][1] - y) <= 3 and abs(w["polyline"][-1][1] - y) <= 3]


def contact_box_page() -> Image.Image:
    """A contact line (left) and its return line (right) joined by the top of the box drawn around the contact."""
    image = Image.new("RGB", (800, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 780, 580), outline="black", width=2)
    draw.line((300, 200, 300, 450), fill="black", width=2)
    draw.line((360, 200, 360, 450), fill="black", width=2)
    draw.line((300, 200, 360, 200), fill="black", width=1)
    draw.line((200, 450, 460, 450), fill="black", width=2)
    return image


def test_top_of_a_box_around_a_contact_is_a_wire():
    tops = horizontal_at(wires_of(contact_box_page()), 200)
    assert len(tops) == 1
    assert tops[0]["reason"] == "short_corner_link"


def test_short_line_crossing_two_wires_mid_way_is_not_a_wire():
    image = contact_box_page()
    ImageDraw.Draw(image).line((293, 320, 367, 320), fill="black", width=1)
    assert horizontal_at(wires_of(image), 320) == []


def test_text_fragment_beside_a_thin_wire_keeps_the_wire_axis():
    wire = LineSegment("v", Box(336, 235, 346, 530))
    fragment = LineSegment("v", Box(344, 347, 365, 402))
    merged = merge_candidate_lines([wire, fragment], "v", axis_tolerance=16, max_gap=82)
    assert [(m.box.x0, m.box.x1, m.box.y0, m.box.y1) for m in merged] == [(336, 346, 235, 530)]


def test_two_port_contact_takes_ports_on_its_own_line_only():
    image = Image.new("RGB", (900, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.line((735, 291, 735, 526), fill="black", width=2)
    draw.line((735, 230, 735, 281), fill="black", width=2)
    draw.line((791, 230, 791, 525), fill="black", width=2)
    wires = [
        {"id": "own", "orientation": "v", "polyline": [[735, 291], [735, 526]], "confidence": 0.78, "visible_coverage": 0.98},
        {"id": "return", "orientation": "v", "polyline": [[791, 230], [791, 525]], "confidence": 0.78, "visible_coverage": 0.99},
    ]
    symbol = {"id": "symbol_002", "class_name": "contact_a", "bbox": {"x0": 715, "y0": 265, "x1": 793, "y1": 312}}
    ports = infer_ports_for_symbol(symbol, wires, image, config=port_inference_config("template_confident_relaxed_span_ink02"))
    assert ports
    assert {p["wire_id"] for p in ports} == {"own"}
