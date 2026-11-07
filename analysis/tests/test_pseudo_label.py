"""確かめた接点の種類（a・b）が、AI の下書きの種類を置き換えることを確かめる（#48）。"""

from __future__ import annotations

# ruff: noqa: I001

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from pseudo_label import apply_reviewed

CONTACT_A, CONTACT_B, CONNECTOR = 1, 2, 7


def test_checked_class_replaces_the_overlapping_draft_box():
    draft = [(CONTACT_A, 0.50, 0.50, 0.04, 0.08), (CONNECTOR, 0.20, 0.20, 0.03, 0.03)]
    checked = [(CONTACT_B, 0.501, 0.502, 0.042, 0.078)]
    out = apply_reviewed(draft, checked)
    assert out == [(CONTACT_B, 0.50, 0.50, 0.04, 0.08), (CONNECTOR, 0.20, 0.20, 0.03, 0.03)]


def test_checked_contact_the_draft_missed_is_added():
    draft = [(CONNECTOR, 0.20, 0.20, 0.03, 0.03)]
    checked = [(CONTACT_B, 0.70, 0.40, 0.04, 0.08)]
    assert apply_reviewed(draft, checked) == [*draft, *checked]


def test_draft_box_that_barely_touches_a_checked_contact_keeps_its_class():
    draft = [(CONTACT_A, 0.50, 0.50, 0.04, 0.08)]
    checked = [(CONTACT_B, 0.53, 0.55, 0.04, 0.08)]
    assert apply_reviewed(draft, checked) == [*draft, *checked]
