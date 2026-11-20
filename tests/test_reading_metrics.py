"""読み取りの正解率を分けて測る（DD-0035）。"""

import copy
import json
import subprocess
import sys
from pathlib import Path

from analysis.synthetic_circuits import logic
from services import reading_metrics as rm

ROOT = Path(__file__).resolve().parents[1]


def _gold():
    data = logic.build(logic.make_spec(3))
    return data["pages"]


def test_identical_pages_score_perfectly():
    gold = _gold()
    r = rm.score(gold, copy.deepcopy(gold))
    s = r["summary"]
    assert s["symbols_all"]["precision"] == 1.0 and s["symbols_all"]["recall"] == 1.0
    assert s["wires"]["precision"] == 1.0 and s["wires"]["wrong_connections"] == 0
    assert s["text"]["label_exact"]["rate"] == 1.0
    assert s["sheet_links"]["recall"] in (1.0, None) and s["coil_contact"]["recall"] == 1.0
    assert s["corrections"] == 0


def test_errors_are_counted_in_their_own_metric():
    gold = _gold()
    pred = copy.deepcopy(gold)
    page = pred[0]
    contact = next(s for s in page["symbols"] if s["class_key"] in ("contact_a", "contact_b"))
    contact["label"] = "ZZ99 " + contact["label"]                    # 文字の誤り
    coil = next((s for p in pred for s in p["symbols"] if s["class_key"] == "relay_coil"), None)
    coil["class_key"] = "solenoid"                                     # 記号の種類の誤り
    a = page["connections"][0]
    other = page["connections"][-1]
    page["connections"].append({**a, "to_symbol_ref": other["to_symbol_ref"], "to_terminal_ref": other["to_terminal_ref"]})
    del page["connections"][1]                                         # 線の見落とし
    s = rm.score(gold, pred)["summary"]
    assert s["text"]["label_exact"]["rate"] < 1.0
    assert s["symbols"]["relay_coil"]["fn"] == 1 and s["symbols"]["solenoid"]["fp"] == 1
    assert s["wires"]["fn"] >= 1 and s["wires"]["fp"] >= 1
    assert s["corrections"] >= 4


def test_unmatched_symbol_boxes_are_missing():
    gold = _gold()
    pred = copy.deepcopy(gold)
    pred[0]["symbols"][5]["cx"] += 0.2
    s = rm.score(gold, pred)["summary"]
    assert s["symbols_all"]["fn"] == 1 and s["symbols_all"]["fp"] == 1


def test_leakage_by_drawing_set():
    out = rm.leakage({"DS01": ["ER1", "ER2"], "DS02": ["ER3"]}, ["ER2"], ["DS01", "DS02"])
    assert out == [{"drawing_set": "DS01", "pages_in_training": ["ER2"],
                    "message": "図面セット DS01 の 1 ページが学習に使われています（評価は未使用のセットで）"}]


def test_cli_writes_report(tmp_path):
    gold = _gold()
    (tmp_path / "gold.json").write_text(json.dumps({"projects": gold}), encoding="utf-8")
    (tmp_path / "pred.json").write_text(json.dumps({"projects": gold}), encoding="utf-8")
    (tmp_path / "train.txt").write_text(gold[0].get("sheet_no") or gold[0]["name"], encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "analysis/tools/eval_reading.py"), "--gold", str(tmp_path / "gold.json"),
                    "--pred", str(tmp_path / "pred.json"), "--train-pages", str(tmp_path / "train.txt"),
                    "--out", str(tmp_path / "out")], check=True, capture_output=True)
    report = json.loads((tmp_path / "out/reading_metrics.json").read_text(encoding="utf-8"))
    assert report["summary"]["wires"]["recall"] == 1.0 and report["leakage"]
    assert "誤結線" in (tmp_path / "out/reading_metrics.md").read_text(encoding="utf-8")
