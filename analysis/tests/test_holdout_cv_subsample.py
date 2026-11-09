"""学習曲線のために学習の図面を減らすとき（holdout_cv.subsample_train）、評価の図面に触れず、同じ指定なら同じ図面を選ぶことを確かめる。"""

from __future__ import annotations

# ruff: noqa: I001

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from holdout_cv import subsample_train


def test_fewer_training_drawings_are_a_fixed_subset_and_the_default_keeps_all():
    train = {f"ER{n}" for n in range(24)}
    twelve = subsample_train(train, 12, 7, 0)
    assert len(twelve) == 12 and twelve <= train
    assert twelve == subsample_train(set(sorted(train, reverse=True)), 12, 7, 0)
    assert subsample_train(train, 12, 7, 1) != twelve
    assert subsample_train(train, None, 7, 0) == train
    assert subsample_train(train, 30, 7, 0) == train
