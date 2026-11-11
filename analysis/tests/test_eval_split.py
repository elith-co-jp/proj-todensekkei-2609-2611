"""調整用と報告用の分け方（eval_split）が、図面を足しても前からある図面の側を変えないことを確かめる。"""

from __future__ import annotations

# ruff: noqa: I001

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from eval_split import SPLIT_FILE, tune_sheets


def test_the_first_thirty_keep_the_split_they_had_by_sheet_order():
    fixed = json.loads(SPLIT_FILE.read_text(encoding="utf-8"))
    thirty = sorted(fixed["tune"] + fixed["report"])
    assert len(thirty) == 30
    assert tune_sheets(thirty) == set(thirty[0::2])


def test_added_drawings_alternate_among_themselves_and_leave_the_thirty_alone():
    fixed = json.loads(SPLIT_FILE.read_text(encoding="utf-8"))
    thirty = fixed["tune"] + fixed["report"]
    added = ["ER21026", "ER21056", "ER21066"]  # 奇数枚: 図番の順に全体で交互にすると 30 枚の側が入れ替わる
    tune = tune_sheets(thirty + added)
    assert tune & set(thirty) == set(fixed["tune"])
    assert tune & set(added) == {"ER21026", "ER21066"}
