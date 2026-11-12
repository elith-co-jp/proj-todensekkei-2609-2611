from analysis.tools.check_wiring_recall_gate import check_gate


BASELINE_PRECISION = 1697 / 1703


def summary(hit=1665, gold=1698, precision=0.9965, judged=1703, wrong=6, errors=0):
    return {
        "errors": errors,
        "all": {
            "wire_nodes": {
                "conv_hit": hit,
                "conv_gold_wires": gold,
                "precision": precision,
                "judged_imported": judged,
                "wrong_imported": wrong,
            }
        },
    }


def test_wiring_recall_gate_uses_exact_counts_instead_of_rounded_recall():
    passed, details = check_gate(
        summary(), min_recall=0.98, min_precision=BASELINE_PRECISION, expected_gold=1698
    )
    assert passed
    assert details["required_hit"] == 1665

    passed, details = check_gate(
        summary(hit=1664), min_recall=0.98, min_precision=BASELINE_PRECISION, expected_gold=1698
    )
    assert not passed
    assert details["recall"] < 0.98


def test_wiring_recall_gate_rejects_incomplete_or_precision_regressed_evaluation():
    assert not check_gate(
        summary(judged=1702),
        min_recall=0.98,
        min_precision=BASELINE_PRECISION,
        expected_gold=1698,
    )[0]
    assert not check_gate(
        summary(errors=1), min_recall=0.98, min_precision=BASELINE_PRECISION, expected_gold=1698
    )[0]
