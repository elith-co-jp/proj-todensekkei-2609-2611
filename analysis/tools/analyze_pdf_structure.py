from __future__ import annotations

import _repo_path  # noqa: F401  (リポジトリの直下を import できるようにする)

import argparse
import json
import os
import re
import warnings
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFont, ImageOps
from analysis.wiring.detect import analyze_image  # noqa: E402


DEFAULT_PDF_PATH = Path("private/inputs/reference/reference.pdf")
DEFAULT_OUT_DIR = Path("private/results/pdf_structure/pdf_structure")
PDF_ENV_VAR = "TODENSEKKEI_REFERENCE_PDF"
WIRE_GEOMETRY_REVISION = "vertical_spine_corner_links_v4"
















def default_pdf_path() -> Path:
    if env_path := os.environ.get(PDF_ENV_VAR):
        return Path(env_path)
    return DEFAULT_PDF_PATH


def render_page(pdf_path: Path, page_number: int, dpi: int) -> Image.Image:
    with pymupdf.open(pdf_path) as doc:
        if page_number < 1 or page_number > doc.page_count:
            raise ValueError(f"page {page_number} is outside PDF page range 1-{doc.page_count}")
        page = doc[page_number - 1]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False)
        mode = "RGB" if pix.n >= 3 else "L"
        return Image.frombytes(mode, (pix.width, pix.height), pix.samples)




def cv_mask_to_pil(mask: np.ndarray) -> Image.Image:
    return Image.fromarray(mask.astype(np.uint8), mode="L")














































































































def analyze_page(
    pdf_path: Path,
    page_number: int,
    dpi: int,
    out_dir: Path,
    run_ocr: bool,
    wire_text_mask: str = "none",
) -> dict:
    return analyze_image(
        render_page(pdf_path, page_number, dpi),
        page_number,
        dpi,
        out_dir,
        run_ocr,
        wire_text_mask,
        source_name=str(pdf_path),
    )




def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze scanned sequence drawing PDF and create review artifacts.")
    parser.add_argument("--pdf", type=Path, default=default_pdf_path())
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--ocr", choices=("none", "easyocr"), default="none")
    parser.add_argument(
        "--wire-text-mask",
        choices=(
            "none",
            "components",
            "ocr",
            "components_ocr",
            "ocr_erase",
            "components_ocr_erase",
            "ocr_pixel_erase",
            "components_ocr_pixel_erase",
            "ocr_component_erase",
            "components_ocr_component_erase",
            "ocr_box_ink_erase",
            "components_ocr_box_ink_erase",
        ),
        default="none",
    )
    parser.add_argument("--print", choices=("summary", "full", "none"), default="summary")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.pdf.exists():
        raise SystemExit(
            f"PDF not found: {args.pdf}. Pass --pdf or set {PDF_ENV_VAR}; "
            "do not commit private PDFs or generated artifacts."
        )
    summary = analyze_page(
        args.pdf,
        args.page,
        args.dpi,
        args.out_dir,
        run_ocr=args.ocr == "easyocr",
        wire_text_mask=args.wire_text_mask,
    )
    if args.print == "none":
        return
    if args.print == "full":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    concise = {
        "pdf": summary["pdf"],
        "page": summary["page"],
        "dpi": summary["dpi"],
        "ocr_status": summary["ocr_status"],
        "wire_text_mask": summary["wire_text_mask"],
        "detected_text_region_count": summary["detected_text_region_count"],
        "detected_wire_count": summary["detected_wire_count"],
        "detected_horizontal_wire_count": summary["detected_horizontal_wire_count"],
        "detected_vertical_wire_count": summary["detected_vertical_wire_count"],
        "detected_label_count": summary["detected_label_count"],
        "connection_graph_node_count": summary["connection_graph_node_count"],
        "connection_graph_edge_count": summary["connection_graph_edge_count"],
        "outputs": summary["outputs"],
    }
    print(json.dumps(concise, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
