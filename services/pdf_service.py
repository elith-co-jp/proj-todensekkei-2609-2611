"""PDF をページ単位の PNG に変換する。

exe 配布（Python 非同梱の Windows）でも動くよう、既定は **PyMuPDF**（pip の
ホイールに MuPDF を同梱。外部バイナリ不要）で描画する。PyMuPDF が無い開発環境
では従来どおり Poppler（``pdfinfo`` / ``pdftoppm``）にフォールバックする。
"""

from __future__ import annotations

import importlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

MAX_PDF_BYTES = 256 * 1024 * 1024
MAX_PDF_PAGES = 500
PDF_DPI = 150


def _load_pymupdf():
    """PyMuPDF を読み込む。未導入なら None。"""
    for module_name in ("pymupdf", "fitz"):
        try:
            return importlib.import_module(module_name)
        except ImportError:
            continue
    return None


def _validate_raw(raw: bytes) -> None:
    if not raw:
        raise ValueError("PDF が空です")
    if len(raw) > MAX_PDF_BYTES:
        raise ValueError("PDF サイズが上限を超えています")
    if not raw[:1024].lstrip().startswith(b"%PDF-"):
        raise ValueError("PDF を読み込めませんでした")


def _render_with_pymupdf(fitz, raw: bytes, dpi: int) -> list[bytes]:
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pages: list[bytes] = []
    try:
        doc = fitz.open(stream=raw, filetype="pdf")
    except Exception as exc:
        raise ValueError("PDF を読み込めませんでした") from exc
    with doc:
        count = doc.page_count
        if count < 1:
            raise ValueError("PDF にページがありません")
        if count > MAX_PDF_PAGES:
            raise ValueError(f"PDF のページ数が上限（{MAX_PDF_PAGES} ページ）を超えています")
        for page in doc:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            pages.append(pix.tobytes("png"))
    return pages


# ------------------------------------------------------------------ Poppler 経路
def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "LC_ALL": "C"}
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("PDF の変換がタイムアウトしました") from exc


def _page_count(pdfinfo: str, pdf_path: Path) -> int:
    result = _run([pdfinfo, str(pdf_path)], timeout=30)
    if result.returncode != 0:
        raise ValueError("PDF を読み込めませんでした")
    match = re.search(r"^Pages:\s+(\d+)\s*$", result.stdout, re.MULTILINE)
    if match is None:
        raise ValueError("PDF のページ数を取得できませんでした")
    count = int(match.group(1))
    if count < 1:
        raise ValueError("PDF にページがありません")
    if count > MAX_PDF_PAGES:
        raise ValueError(f"PDF のページ数が上限（{MAX_PDF_PAGES} ページ）を超えています")
    return count


def _page_number(path: Path) -> int:
    match = re.search(r"-(\d+)\.png$", path.name)
    if match is None:
        raise ValueError("PDF のページ画像名を解析できませんでした")
    return int(match.group(1))


def _render_with_poppler(raw: bytes, dpi: int) -> list[bytes]:
    pdfinfo = shutil.which("pdfinfo")
    pdftoppm = shutil.which("pdftoppm")
    if pdfinfo is None or pdftoppm is None:
        raise ValueError("PDF 変換機能（Poppler）が利用できません")

    with tempfile.TemporaryDirectory(prefix="seqanno_pdf_") as tmp:
        tmp_dir = Path(tmp)
        pdf_path = tmp_dir / "source.pdf"
        output_prefix = tmp_dir / "page"
        pdf_path.write_bytes(raw)
        expected_count = _page_count(pdfinfo, pdf_path)

        result = _run(
            [pdftoppm, "-png", "-r", str(dpi), str(pdf_path), str(output_prefix)],
            timeout=min(600, max(120, expected_count * 10)),
        )
        if result.returncode != 0:
            raise ValueError("PDF のページ画像化に失敗しました")

        page_paths = sorted(tmp_dir.glob("page-*.png"), key=_page_number)
        if len(page_paths) != expected_count:
            raise ValueError("PDF の全ページを画像化できませんでした")
        return [path.read_bytes() for path in page_paths]


def render_pdf_pages(raw: bytes, dpi: int = PDF_DPI) -> list[bytes]:
    """PDF 全ページを表示・アノテーション用の PNG として返す。

    PyMuPDF が使えればそれを使い（外部依存なし）、無ければ Poppler にフォールバックする。
    """
    _validate_raw(raw)

    fitz = _load_pymupdf()
    if fitz is not None:
        return _render_with_pymupdf(fitz, raw, dpi)
    return _render_with_poppler(raw, dpi)
