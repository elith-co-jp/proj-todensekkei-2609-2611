from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from run_yolo_e2e_pipeline import draw_wire_overlay, ensure_analysis_outputs, load_symbol_ocr_reader  # noqa: E402


class WirePipelineTests(unittest.TestCase):
    def test_missing_symbol_ocr_dependency_does_not_block_pipeline(self) -> None:
        with patch.dict("sys.modules", {"rapidocr_onnxruntime": None}):
            reader, effective_engine, warning = load_symbol_ocr_reader("rapidocr", "en")

        self.assertIsNone(reader)
        self.assertEqual(effective_engine, "none")
        self.assertIn("continuing without symbol label OCR", warning or "")

    def test_wire_overlay_draws_predictions_on_source(self) -> None:
        source = Image.new("RGB", (100, 60), "white")
        payload = {
            "wires": [
                {
                    "orientation": "h",
                    "span_start": 10,
                    "span_end": 90,
                    "axis": 30,
                    "confidence": 0.9,
                }
            ]
        }

        overlay = draw_wire_overlay(source, payload)

        self.assertEqual(source.getpixel((50, 30)), (255, 255, 255))
        self.assertEqual(overlay.getpixel((50, 30)), (220, 40, 35))

    def test_analysis_cache_requires_matching_source_and_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "drawing.pdf"
            pdf.write_bytes(b"test-pdf")
            page_dir = root / "analysis" / "page_001"
            page_dir.mkdir(parents=True)
            for name in ("base.png", "connection_graph.json", "detected_wires.json", "final_extraction_review.png"):
                (page_dir / name).write_bytes(b"present")
            source_stat = pdf.stat()
            (page_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "page": 1,
                        "dpi": 150,
                        "wire_text_mask": "components",
                        "ocr_requested": False,
                        "pdf": str(pdf),
                        "pdf_size_bytes": source_stat.st_size,
                        "pdf_mtime_ns": source_stat.st_mtime_ns,
                    }
                ),
                encoding="utf-8",
            )

            with patch("run_yolo_e2e_pipeline.analyze_page") as analyze_page:
                ensure_analysis_outputs(pdf, [1], root / "analysis", 150, False, "components")
                analyze_page.assert_not_called()

                ensure_analysis_outputs(pdf, [1], root / "analysis", 200, False, "components")
                analyze_page.assert_called_once_with(
                    pdf,
                    1,
                    200,
                    root / "analysis",
                    run_ocr=False,
                    wire_text_mask="components",
                )


if __name__ == "__main__":
    unittest.main()
