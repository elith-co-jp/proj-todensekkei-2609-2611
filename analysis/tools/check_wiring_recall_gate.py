"""Check the wiring cross-validation adoption gate from eval_editor_import JSON."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

DEFAULT_MIN_PRECISION = 1697 / 1703


def check_gate(
    summary: dict[str, Any],
    *,
    min_recall: float,
    min_precision: float,
    expected_gold: int,
) -> tuple[bool, dict[str, Any]]:
    nodes = summary.get("all", {}).get("wire_nodes", {})
    hit = nodes.get("conv_hit")
    gold = nodes.get("conv_gold_wires")
    judged = nodes.get("judged_imported")
    wrong = nodes.get("wrong_imported")
    precision = (
        (judged - wrong) / judged
        if isinstance(judged, int) and isinstance(wrong, int) and judged > 0
        else None
    )
    required_hit = math.ceil(expected_gold * min_recall - 1e-12)
    recall = hit / gold if isinstance(hit, int) and isinstance(gold, int) and gold else None
    reasons = []
    if summary.get("errors") != 0:
        reasons.append("evaluation_errors")
    if gold != expected_gold:
        reasons.append("unexpected_gold_count")
    if recall is None or hit < required_hit:
        reasons.append("recall_below_target")
    if not isinstance(precision, (int, float)) or precision < min_precision:
        reasons.append("precision_below_target")
    details = {
        "passed": not reasons,
        "conv_hit": hit,
        "conv_gold_wires": gold,
        "required_hit": required_hit,
        "recall": recall,
        "precision": precision,
        "judged_imported": judged,
        "wrong_imported": wrong,
        "reasons": reasons,
    }
    return not reasons, details


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument("--min-recall", type=float, default=0.98)
    parser.add_argument("--min-precision", type=float, default=DEFAULT_MIN_PRECISION)
    parser.add_argument("--expected-gold", type=int, default=1698)
    args = parser.parse_args()
    passed, details = check_gate(
        json.loads(args.summary.read_text(encoding="utf-8")),
        min_recall=args.min_recall,
        min_precision=args.min_precision,
        expected_gold=args.expected_gold,
    )
    print(json.dumps(details, ensure_ascii=False))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
