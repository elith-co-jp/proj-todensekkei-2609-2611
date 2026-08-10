from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# DB / 画像ディレクトリをテスト用に隔離してから import する
_TMP = tempfile.mkdtemp(prefix="seqanno_test_")
os.environ["SEQANNO_DATA_DIR"] = _TMP
os.environ["SEQANNO_DATABASE_URL"] = f"sqlite:///{Path(_TMP) / 'test.db'}"

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

import database  # noqa: E402
from main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_db():
    database.Base.metadata.drop_all(bind=database.engine)
    database.init_db()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def make_png(w: int = 400, h: int = 300, color: tuple[int, int, int] = (255, 255, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def make_pdf(page_count: int = 2, w: int = 400, h: int = 300) -> bytes:
    """テスト用の複数ページ PDF を生成する。"""
    pages = [Image.new("RGB", (w, h), (255 - i * 20, 255, 255)) for i in range(page_count)]
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:], resolution=72)
    return buf.getvalue()


@pytest.fixture
def png_bytes():
    return make_png


def create_project(client, name: str = "ER21216.png", w: int = 400, h: int = 300) -> int:
    res = client.post("/api/projects", files={"files": (name, make_png(w, h), "image/png")})
    assert res.status_code == 200, res.text
    return res.json()["project_ids"][0]


SAMPLE_ANNOTATION = {
    "symbols": [
        {
            "ref": "SYM-0001",
            "class_key": "relay_coil",
            "label": "33HB",
            "cx": 0.25,
            "cy": 0.5,
            "w": 0.1,
            "h": 0.1,
            "terminals": [
                {"ref": "SYM-0001-T1", "name": "13", "tx": 0.25, "ty": 0.55},
                {"ref": "SYM-0001-T2", "name": "14", "tx": 0.25, "ty": 0.45},
            ],
        },
        {
            "ref": "SYM-0002",
            "class_key": "terminal",
            "label": "TB16",
            "cx": 0.75,
            "cy": 0.5,
            "w": 0.05,
            "h": 0.05,
            "terminals": [{"ref": "SYM-0002-T1", "name": "101", "tx": 0.75, "ty": 0.5}],
        },
        {"ref": "SYM-0003", "class_key": "junction", "cx": 0.5, "cy": 0.2, "w": 0.02, "h": 0.02},
    ],
    "connections": [
        {
            "from_symbol_ref": "SYM-0001",
            "from_terminal_ref": "SYM-0001-T2",
            "to_symbol_ref": "SYM-0002",
            "to_terminal_ref": "SYM-0002-T1",
            "wire_no": "610",
            "kind": "wire",
        },
        {
            "from_symbol_ref": "SYM-0002",
            "to_symbol_ref": "SYM-0003",
            "kind": "sheet_ref",
            "external_ref": "(610-2F-9)",
        },
    ],
}
