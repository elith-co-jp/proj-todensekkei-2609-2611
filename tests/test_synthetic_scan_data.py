from __future__ import annotations

import hashlib
import json

import cv2
import numpy as np
import pytest

from analysis.tools.synthetic_scan_data import add_scan_copies
from analysis.tools.holdout_cv import fine_tune_weights


def test_scan_copies_use_only_selected_training_images_and_keep_labels(tmp_path):
    images = tmp_path / "images" / "train"
    labels = tmp_path / "labels" / "train"
    images.mkdir(parents=True)
    labels.mkdir(parents=True)
    for stem, class_index in (("with_b", 1), ("without_b", 0), ("with_b_r90", 1)):
        image = np.full((32, 40), 255, dtype=np.uint8)
        cv2.line(image, (8, 12), (30, 12), 0, 1)
        assert cv2.imwrite(str(images / f"{stem}.png"), image)
        (labels / f"{stem}.txt").write_text(f"{class_index} 0.5 0.5 0.2 0.2\n")

    assert add_scan_copies(images, labels, class_index=1, copies=2) == 2
    made = sorted(images.glob("*_scan*.png"))
    assert [p.name for p in made] == ["with_b_scan0.png", "with_b_scan1.png"]
    assert all((labels / f"{p.stem}.txt").read_text() == (labels / "with_b.txt").read_text() for p in made)
    assert all(cv2.imread(str(p), cv2.IMREAD_GRAYSCALE).shape == (32, 40) for p in made)
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in made]
    assert add_scan_copies(images, labels, class_index=1, copies=2) == 2
    assert before == [hashlib.sha256(p.read_bytes()).hexdigest() for p in made]


def test_fine_tune_requires_matching_cross_validation_folds(tmp_path):
    source = tmp_path / "source"
    weights = source / "fold0" / "train" / "weights" / "last.pt"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"placeholder")
    folds = [["sheet-a"], ["sheet-b"]]
    (source / "summary.json").write_text(json.dumps({"mode": "cross-validation", "folds": folds}))
    assert fine_tune_weights(source, folds, 0) == weights

    (source / "summary.json").write_text(json.dumps({"mode": "fixed-weights", "folds": folds}))
    with pytest.raises(ValueError, match="cross-validation"):
        fine_tune_weights(source, folds, 0)
    (source / "summary.json").write_text(json.dumps({"mode": "cross-validation", "folds": folds}))
    with pytest.raises(ValueError, match="same drawing-level"):
        fine_tune_weights(source, [["sheet-b"], ["sheet-a"]], 0)
