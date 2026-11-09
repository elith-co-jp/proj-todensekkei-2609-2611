"""回路シミュレーションの採点: 器具番号の無い接点の入力を、枠の対応で正解と AI の間で読み替える（DD-0023）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from eval_circuit_sim import unlabeled_input_names  # noqa: E402


def test_unlabeled_contact_input_is_renamed_by_box_matching():
    gold = [{"id": 3, "sheet_no": "ER21261", "name": "ER21261-001"}]
    ai = [{"id": 3, "sheet_no": "ER21261", "name": "ER21261-001", "gold_ref_of": {"SYM-0023": "SYM-0006"}}]
    assert unlabeled_input_names(gold, ai) == {"ER21261 SYM-0006（器具番号なし）": "ER21261 SYM-0023（器具番号なし）"}
