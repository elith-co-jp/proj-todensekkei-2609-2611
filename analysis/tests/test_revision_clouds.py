"""Revision clouds (雲マーク) on synthetic pages: reported as regions and never wired."""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw


def _draw_cloud(draw: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int, arc: int = 16) -> None:
    """Semicircles along each side, bulging outward."""
    r = arc // 2
    for x in range(x0 + r, x1 - r + 1, arc):
        draw.arc((x - r, y0 - r, x + r, y0 + r), 180, 360, fill="black", width=2)
        draw.arc((x - r, y1 - r, x + r, y1 + r), 0, 180, fill="black", width=2)
    for y in range(y0 + r, y1 - r + 1, arc):
        draw.arc((x0 - r, y - r, x0 + r, y + r), 90, 270, fill="black", width=2)
        draw.arc((x1 - r, y - r, x1 + r, y + r), 270, 450, fill="black", width=2)


def _page(tmp_path, with_cloud: bool):
    image = Image.new("RGB", (1755, 1241), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((60, 60, 1660, 1150), outline="black", width=3)
    draw.line([(150, 250), (1000, 250)], fill="black", width=3)
    for x in (400, 700):
        draw.line([(x, 250), (x, 650)], fill="black", width=3)
    if with_cloud:
        _draw_cloud(draw, 352, 400, 752, 528)
    path = tmp_path / ("cloud.png" if with_cloud else "plain.png")
    image.save(path)
    return path


def _analyze(tmp_path, monkeypatch, path):
    pytest.importorskip("cv2")
    from analysis import runtime

    monkeypatch.setattr(runtime, "load_symbol_ocr_reader", lambda *_: (None, "none", "not available"))
    return runtime.analyze_detections(path, [], source={}, work_dir=tmp_path / "work")


def _wires_along(result, axis: float) -> list[dict]:
    return [w for w in result["wires"] if w["orientation"] == "h" and abs(w["axis"] - axis) <= 14]


def test_cloud_is_a_region_and_its_edges_are_not_wires(tmp_path, monkeypatch):
    result = _analyze(tmp_path, monkeypatch, _page(tmp_path, with_cloud=True))

    clouds = [r for r in result["regions"] if r["region_type"] == "cloud"]
    assert len(clouds) == 1 and clouds[0]["source"] == "image"
    box = clouds[0]["bbox"]
    assert box["x0"] <= 352 and box["y0"] <= 400 and box["x1"] >= 752 and box["y1"] >= 528
    assert not _wires_along(result, 400) and not _wires_along(result, 528), "cloud edges must not join the wires they cross"
    for x in (400, 700):
        assert any(w["orientation"] == "v" and abs(w["axis"] - x) <= 4 and w["polyline"][0][1] < 400 and w["polyline"][-1][1] > 528
                   for w in result["wires"]), "a wire crossing the cloud stays whole"


def test_page_without_cloud_reports_none(tmp_path, monkeypatch):
    result = _analyze(tmp_path, monkeypatch, _page(tmp_path, with_cloud=False))

    assert not [r for r in result["regions"] if r["region_type"] == "cloud"]


def test_detector_sees_the_page_without_the_cloud(tmp_path):
    pytest.importorskip("cv2")
    from analysis.runtime import page_for_detection

    plain = _page(tmp_path, with_cloud=False)
    assert page_for_detection(plain) == str(plain)
    clouded = _page(tmp_path, with_cloud=True)
    apex = (552, 392)
    assert Image.open(clouded).convert("L").getpixel(apex) < 128
    cleaned = page_for_detection(clouded).convert("L")
    assert cleaned.getpixel(apex) == 255, "cloud arc erased"
    assert all(cleaned.getpixel((400, y)) < 128 for y in range(380, 550)), "the wire crossing the cloud stays"

