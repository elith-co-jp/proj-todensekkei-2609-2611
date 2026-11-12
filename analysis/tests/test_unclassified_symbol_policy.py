import sys
import json
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from holdout_cv import rescore, score_symbols  # noqa: E402


def _item(class_name: str) -> dict:
    return {
        "class_name": class_name,
        "kind": "symbol",
        "label": "",
        "bbox": {"x0": 10.0, "y0": 10.0, "x1": 30.0, "y1": 30.0},
    }


def test_evaluation_scores_unagreed_equipment_as_other():
    result = score_symbols([_item("other")], [_item("breaker")])

    assert result["aware"] == {"tp": 1, "fp": 0, "fn": 0}
    assert result["type_correct"] == 1
    assert set(result["per_class"]) == {"other"}


def test_evaluation_scores_horizontal_symbol_as_existing_class():
    result = score_symbols([_item("contact_a")], [_item("horizontal_contact_a")])

    assert result["aware"] == {"tp": 1, "fp": 0, "fn": 0}
    assert result["type_correct"] == 1
    assert set(result["per_class"]) == {"contact_a"}


def test_rescore_reports_large_other_equipment_as_a_region(tmp_path, capsys):
    predictions = tmp_path / "fold0" / "pred"
    predictions.mkdir(parents=True)
    (predictions / "ER00001.json").write_text(
        json.dumps(
            [
                {
                    "id": "p0",
                    "class_name": "breaker",
                    "kind": "region",
                    "cx": 0.5,
                    "cy": 0.5,
                    "w": 0.5,
                    "h": 0.5,
                }
            ]
        ),
        encoding="utf-8",
    )
    drawings = [
        {
            "sheet": "ER00001",
            "project": {
                "image_width": 100,
                "image_height": 100,
                "symbols": [
                    {
                        "ref": "S1",
                        "class_key": "other",
                        "label": "MCCB",
                        "cx": 0.5,
                        "cy": 0.5,
                        "w": 0.5,
                        "h": 0.5,
                    }
                ],
            },
        }
    ]

    rescore(SimpleNamespace(work=tmp_path), drawings)

    result = json.loads(capsys.readouterr().out)
    assert result["region_f1"] == {
        "tp": 1,
        "fp": 0,
        "fn": 0,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
    }
