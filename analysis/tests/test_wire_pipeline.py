from __future__ import annotations

# ruff: noqa: I001

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from run_yolo_e2e_pipeline import (
    DEFAULT_MODEL_PATH,
    _analysis_cache_key,
    draw_wire_overlay,
    ensure_analysis_outputs,
    load_symbol_ocr_reader,
    parse_args,
)


class WirePipelineTests(unittest.TestCase):
    def test_default_model_is_the_shared_distribution_weight(self) -> None:
        expected = TOOLS_DIR.parents[1] / "assets" / "models" / "yolo11n_all_symbols_best.pt"
        self.assertEqual(DEFAULT_MODEL_PATH, expected)
        self.assertTrue(DEFAULT_MODEL_PATH.is_absolute())
        self.assertTrue(DEFAULT_MODEL_PATH.is_file())
        with patch.object(sys, "argv", ["run_yolo_e2e_pipeline.py"]):
            self.assertEqual(parse_args().model, expected)

    def test_default_model_does_not_depend_on_working_directory(self) -> None:
        probe = (
            "import json, sys; "
            "sys.path.insert(0, sys.argv.pop()); "
            "from run_yolo_e2e_pipeline import parse_args; "
            "model = parse_args().model; "
            "print(json.dumps({'path': str(model), 'exists': model.is_file()}))"
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [sys.executable, "-c", probe, str(TOOLS_DIR)],
                cwd=temporary, capture_output=True, text=True, check=True,
            )
        self.assertEqual(json.loads(result.stdout), {"path": str(DEFAULT_MODEL_PATH), "exists": True})

    def test_explicit_model_overrides_shared_default(self) -> None:
        with patch.object(sys, "argv", ["run_yolo_e2e_pipeline.py", "--model", "custom.pt"]):
            self.assertEqual(parse_args().model, Path("custom.pt"))

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
            (page_dir / "analysis_key.json").write_text(
                json.dumps(_analysis_cache_key(pdf, 150, False, "components")),
                encoding="utf-8",
            )

            with patch("run_yolo_e2e_pipeline.analyze_page") as analyze_page:
                ensure_analysis_outputs(pdf, [1], root / "analysis", 150, False, "components")
                analyze_page.assert_not_called()

                old_key = _analysis_cache_key(pdf, 150, False, "components")
                del old_key["wire_geometry_revision"]
                (page_dir / "analysis_key.json").write_text(json.dumps(old_key), encoding="utf-8")
                ensure_analysis_outputs(pdf, [1], root / "analysis", 150, False, "components")
                analyze_page.assert_called_once()
                analyze_page.reset_mock()

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
