"""正解付けする図面の候補の選び方（rank_gold_candidates）が、迷い・見落としやすさ・新しさで選び、評価用を先に取り分けることを確かめる。"""

from __future__ import annotations

# ruff: noqa: I001

import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from rank_gold_candidates import page_counts, rank_candidates, reserve_for_evaluation


def unit(*xs):
    v = np.array(xs, dtype=np.float32)
    return v / np.linalg.norm(v)


def counts(confident=None, borderline=None):
    return {"confident": Counter(confident or {}), "borderline": Counter(borderline or {})}


def test_evaluation_drawings_are_spread_over_the_numbers_and_always_the_same():
    sheets = [f"ER21{n:03d}" for n in range(100)]
    reserved = reserve_for_evaluation(list(reversed(sheets)), 20)
    assert reserved == reserve_for_evaluation(sheets, 20)
    assert len(set(reserved)) == 20
    assert reserved[0] < "ER21010" and reserved[-1] > "ER21090"


def test_pages_already_used_for_training_are_never_kept_for_evaluation():
    sheets = [f"ER21{n:03d}" for n in range(100)]
    trained = set(reserve_for_evaluation(sheets, 20))
    reserved = reserve_for_evaluation(sheets, 20, trained)
    assert len(reserved) == 20 and not set(reserved) & trained


def test_a_connector_just_under_its_own_floor_is_borderline_while_a_contact_at_the_same_score_counts():
    c = page_counts([{"class_key": "connector", "confidence": 0.55}, {"class_key": "contact_a", "confidence": 0.55},
                     {"class_key": "contact_a", "confidence": 0.1}], {"connector": 0.6}, 0.5, 0.2)
    assert c["confident"] == Counter({"contact_a": 1})
    assert c["borderline"] == Counter({"connector": 1})


def test_a_drawing_with_more_unsure_symbols_comes_first():
    same = unit(1, 0, 0)
    picked = rank_candidates({"A": counts(borderline={"junction": 5}), "B": counts(borderline={"junction": 1})},
                             {"A": same, "B": same}, [unit(0, 1, 0)], {}, 1)
    assert picked[0]["sheet"] == "A"


def test_symbols_the_detector_often_misses_weigh_more_than_common_ones():
    same = unit(1, 0, 0)
    picked = rank_candidates({"A": counts(confident={"contact_b": 4}), "B": counts(confident={"connector": 4})},
                             {"A": same, "B": same}, [unit(0, 1, 0)], {"connector": 0.01, "contact_b": 0.4}, 1)
    assert picked[0]["sheet"] == "A"


def test_a_copy_of_a_drawing_already_picked_waits_behind_a_different_drawing():
    layouts = {"A": unit(1, 0, 0), "A2": unit(1, 0, 0), "B": unit(0, 0, 1)}
    c = {"A": counts(borderline={"junction": 9}), "A2": counts(borderline={"junction": 8}), "B": counts(borderline={"junction": 1})}
    picked = [p["sheet"] for p in rank_candidates(c, layouts, [unit(0, 1, 0)], {}, 3)]
    assert picked == ["A", "B", "A2"]
