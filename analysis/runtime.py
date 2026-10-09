"""Application adapter for the existing standalone analysis tools.

Only an image and detector predictions enter this path. Manual annotations and
reference connections are never used as inputs to production inference.
"""

from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

# The CLI tools use sibling imports. Keep that entry point intact; PyInstaller
# resolves the same imports via its tools pathex, without shipping private data.
_TOOLS = Path(__file__).resolve().parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.append(str(_TOOLS))

from analyze_pdf_structure import analyze_image
from build_e2e_demo_outputs import build_final_output_from_analysis
from filter_wire_decorations import filter_wire_decorations, rapidocr_text_confirmation
from run_yolo_e2e_pipeline import (
    DEFAULT_INFERRED_TERMINAL_CLASSES,
    DEFAULT_WIRE_TEXT_MASK,
    OCR_ALLOWLIST,
    build_direct_from_to,
    enrich_symbols_and_graph,
    enrich_symbols_with_label_ocr,
    load_symbol_ocr_reader,
    port_inference_config,
)


def analyze_detections(
    image_path: Path,
    detections: list[dict[str, Any]],
    *,
    source: dict[str, Any],
    work_dir: Path,
) -> dict[str, Any]:
    """Reuse wire/port/from-to logic on exactly the image shown in the app."""
    with Image.open(image_path) as opened:
        image = opened.convert("RGB")
    width, height = image.size
    symbols = []
    for index, detection in enumerate(detections, 1):
        cx, cy, w, h = (float(detection[key]) for key in ("cx", "cy", "w", "h"))
        symbols.append({
            "id": f"symbol_{index:03d}",
            "type": detection["class_key"],
            "class_name": detection["class_key"],
            "class_label": detection["class_label"],
            "bbox": {
                "x0": round((cx - w / 2) * width), "y0": round((cy - h / 2) * height),
                "x1": round((cx + w / 2) * width), "y1": round((cy + h / 2) * height),
            },
            "center": [cx * width, cy * height],
            "confidence": detection.get("confidence"),
            "status": "detected_by_yolo",
        })

    work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="analysis-", dir=work_dir) as tmp:
        analyze_image(
            image, 1, 150, Path(tmp), False, DEFAULT_WIRE_TEXT_MASK,
            write_debug_images=False,
        )
        payload = build_final_output_from_analysis(Path(tmp) / "page_001", "registered_image")
    # Do not export temporary paths or treat a project number as a PDF page number.
    payload["source"] = source
    warnings = []
    reader = None
    try:
        reader, engine, warning = load_symbol_ocr_reader("rapidocr", "en")
        if warning:
            warnings.append("ラベル読み取りを実行できませんでした。OCRの実行環境を確認してください。")
        ocr_count = enrich_symbols_with_label_ocr(
            image, symbols, reader=reader, engine=engine,
            classes={"connector", "contact_a"}, padding=8, preprocess="gray", scale=4,
            allowlist=OCR_ALLOWLIST, label_source="structured_match",
        )
    except Exception:  # OCR failure must not discard successfully detected wires.
        logging.getLogger(__name__).exception("Symbol label OCR failed")
        engine, ocr_count = "none", 0
        warnings.append("ラベル読み取りに失敗しました。シンボル・配線の結果は確認できます。")

    payload = filter_wire_decorations(
        payload, image, symbols,
        confirm_text=rapidocr_text_confirmation(reader) if reader is not None and engine == "rapidocr" else None,
    )
    if payload["quality"]["wire_decoration_filter"]["label_rule_ocr_failed"]:
        warnings.append("罫線の文字確認に失敗したため、判定できない罫線は配線候補として残しています。")

    payload = enrich_symbols_and_graph(
        payload, symbols, terminal_wire_threshold=10.0, terminal_node_threshold=28.0,
        terminal_source="inferred", inferred_terminal_classes=DEFAULT_INFERRED_TERMINAL_CLASSES,
        inferred_terminal_config=port_inference_config("template_confident_relaxed_span_ink02"),
        symbol_terminal_bridge_classes=set(), junction_node_policy="explicit",
        junction_node_classes={"junction"}, junction_node_threshold=24.0, source_image=image,
    )
    payload["from_to"] = build_direct_from_to(payload)
    payload.update({
        "schema_version": "todensekkei.sequence_extraction.v1",
        "status": "partial" if warnings else "completed",
        "warnings": warnings,
        "external_references": [],
        "capabilities": {
            "symbol_detection": "completed", "wire_detection": "completed",
            "from_to": "predicted", "label_ocr": "unavailable" if warnings else "completed",
            "external_references": "not_resolved",
        },
        "notes": [
            "Predictions only; no manual symbols, terminals or reference connections were used.",
            "Wire geometry and terminal-to-terminal connectivity are distinct prediction outputs.",
            "Cross-sheet connections are not resolved by this image-only runtime.",
        ],
    })
    payload["quality"].update({
        "symbol_count": len(symbols),
        "from_to_connection_count": payload["from_to"]["connection_count"],
        "symbol_label_ocr": {"engine": engine, "count": ocr_count},
        "terminal_source": "inferred", "annotation_assistance": False,
    })
    return payload
