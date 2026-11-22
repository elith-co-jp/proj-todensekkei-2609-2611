"""PDF → ページ画像の変換。PyMuPDF（既定）と Poppler（PyMuPDF の無い開発環境）の両方を実物で試す。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from services import pdf_service


def _pdf(pages: int = 2) -> bytes:
    doc = pymupdf.open()
    for k in range(pages):
        page = doc.new_page(width=200, height=100)
        page.insert_text((20, 50), f"ER2100{k}", fontsize=12)
    return doc.tobytes()


@pytest.mark.parametrize("raw, message", [
    (b"", "PDF が空です"),
    (b"hello", "PDF を読み込めませんでした"),
])
def test_rejects_empty_and_non_pdf_bytes(raw, message):
    with pytest.raises(ValueError, match=message):
        pdf_service.render_pdf_pages(raw)


def test_rejects_oversized_pdf(monkeypatch):
    monkeypatch.setattr(pdf_service, "MAX_PDF_BYTES", 10)
    with pytest.raises(ValueError, match="サイズが上限"):
        pdf_service.render_pdf_pages(_pdf())


def test_pymupdf_renders_every_page_and_enforces_page_limits(monkeypatch):
    pages = pdf_service.render_pdf_pages(_pdf(2), dpi=72)
    assert len(pages) == 2 and all(p.startswith(b"\x89PNG") for p in pages)
    with pytest.raises(ValueError, match="PDF を読み込めませんでした"):
        pdf_service.render_pdf_pages(b"%PDF-1.4 broken")
    monkeypatch.setattr(pdf_service, "MAX_PDF_PAGES", 1)
    with pytest.raises(ValueError, match="上限（1 ページ）"):
        pdf_service.render_pdf_pages(_pdf(2))


def test_pymupdf_reports_a_document_without_pages():
    class Doc:
        page_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    fake = SimpleNamespace(Matrix=lambda *_a: None, open=lambda **_k: Doc())
    with pytest.raises(ValueError, match="ページがありません"):
        pdf_service._render_with_pymupdf(fake, b"%PDF-", 72)


def test_without_pymupdf_imports_fall_back_to_none(monkeypatch):
    def missing(_name):
        raise ImportError("no module")

    monkeypatch.setattr(pdf_service.importlib, "import_module", missing)
    assert pdf_service._load_pymupdf() is None


@pytest.mark.skipif(shutil.which("pdftoppm") is None or shutil.which("pdfinfo") is None, reason="Poppler が無い環境")
def test_poppler_renders_every_page_when_pymupdf_is_missing(monkeypatch):
    monkeypatch.setattr(pdf_service, "_load_pymupdf", lambda: None)
    pages = pdf_service.render_pdf_pages(_pdf(3), dpi=50)
    assert len(pages) == 3 and all(p.startswith(b"\x89PNG") for p in pages)
    with pytest.raises(ValueError, match="PDF を読み込めませんでした"):
        pdf_service.render_pdf_pages(b"%PDF-1.4 broken")


def test_poppler_missing_tools(monkeypatch):
    monkeypatch.setattr(pdf_service.shutil, "which", lambda _name: None)
    with pytest.raises(ValueError, match="Poppler"):
        pdf_service._render_with_poppler(_pdf(1), 72)


@pytest.mark.parametrize("stdout, message", [
    ("Title: x\n", "ページ数を取得できませんでした"),
    ("Pages:          0\n", "ページがありません"),
    ("Pages:          999\n", "上限（500 ページ）"),
])
def test_poppler_page_count_errors(monkeypatch, stdout, message):
    monkeypatch.setattr(pdf_service, "_run", lambda *_a, **_k: subprocess.CompletedProcess([], 0, stdout, ""))
    with pytest.raises(ValueError, match=message):
        pdf_service._page_count("pdfinfo", Path("x.pdf"))


def test_poppler_conversion_failures(monkeypatch):
    calls = []

    def fake_run(command, timeout):
        calls.append(command)
        if len(calls) == 1:
            return subprocess.CompletedProcess(command, 0, "Pages: 2\n", "")
        return subprocess.CompletedProcess(command, 1, "", "error")

    monkeypatch.setattr(pdf_service.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pdf_service, "_run", fake_run)
    with pytest.raises(ValueError, match="ページ画像化に失敗しました"):
        pdf_service._render_with_poppler(_pdf(2), 72)

    def fake_run_one_page(command, timeout):
        if "pdfinfo" in command[0]:
            return subprocess.CompletedProcess(command, 0, "Pages: 2\n", "")
        Path(f"{command[-1]}-1.png").write_bytes(b"png")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(pdf_service, "_run", fake_run_one_page)
    with pytest.raises(ValueError, match="全ページを画像化できませんでした"):
        pdf_service._render_with_poppler(_pdf(2), 72)


def test_poppler_timeout_and_page_names(monkeypatch):
    def slow(*_a, **_k):
        raise subprocess.TimeoutExpired("pdftoppm", 1)

    monkeypatch.setattr(pdf_service.subprocess, "run", slow)
    with pytest.raises(ValueError, match="タイムアウト"):
        pdf_service._run(["pdftoppm"], timeout=1)
    assert pdf_service._page_number(Path("page-12.png")) == 12
    with pytest.raises(ValueError, match="ページ画像名"):
        pdf_service._page_number(Path("page.png"))
