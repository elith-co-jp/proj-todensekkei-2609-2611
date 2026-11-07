"""横書き（ページ全体を 90° 回して描いた）図面を、登録時に縦向きに直すことを確かめる（#42）。"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw, ImageFont

pytest.importorskip("rapidocr_onnxruntime")


def _page_png(transpose: Image.Transpose | None = None) -> bytes:
    """文字が左から右に書かれた図面らしいページ。transpose で回す。"""
    image = Image.new("RGB", (1400, 1000), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=28)
    for row in range(14):
        draw.text((60, 40 + row * 65), f"TB{row + 10} CN2{row} RELAY {row * 7 + 101}", fill="black", font=font)
    if transpose is not None:
        image = image.transpose(transpose)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


@pytest.mark.parametrize(("transpose", "degrees"), [(Image.Transpose.ROTATE_90, 270), (Image.Transpose.ROTATE_270, 90)])
def test_a_sideways_page_is_stored_upright(client, transpose, degrees):
    res = client.post("/api/projects", files={"files": ("横書き.png", _page_png(transpose), "image/png")})
    assert res.status_code == 200, res.text
    pid = res.json()["project_ids"][0]
    assert res.json()["turned"] == [{"project_id": pid, "degrees": degrees}]
    project = client.get(f"/api/projects/{pid}").json()
    assert (project["image_width"], project["image_height"]) == (1400, 1000)
    assert "横書き" in project["note"]


def test_an_upright_page_is_stored_as_it_is(client):
    res = client.post("/api/projects", files={"files": ("縦向き.png", _page_png(), "image/png")})
    assert res.status_code == 200, res.text
    assert res.json()["turned"] == []
    project = client.get(f"/api/projects/{res.json()['project_ids'][0]}").json()
    assert (project["image_width"], project["image_height"], project["note"]) == (1400, 1000, None)

