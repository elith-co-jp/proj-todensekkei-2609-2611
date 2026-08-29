from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from analyze_annotation_graph import write_json
from evaluate_yolo_label_ocr import (
    FONT,
    FONT_SMALL,
    clean_ocr_text,
    load_zip_images,
    normalize_cn_token,
    normalize_er_token,
    normalize_tb_token,
)


TARGET_CLASSES = {"connector", "contact_a"}


def clean_token_text(text: str) -> str:
    return clean_ocr_text(text).replace(" ", "")


def token_bounds(token: dict[str, Any]) -> tuple[float, float, float, float]:
    points = token.get("bbox") or []
    if not points:
        return 0.0, 0.0, 0.0, 0.0
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def token_sort_key(token: dict[str, Any]) -> tuple[float, float]:
    x0, y0, _x1, _y1 = token_bounds(token)
    return y0, x0


def normalize_display_token(text: str) -> str:
    token = clean_token_text(text)
    if not token:
        return ""
    if re.fullmatch(r"[CO0]N[0-9A-Z]+", token):
        return normalize_cn_token(f"CN{token[2:]}")
    if re.fullmatch(r"T[B8][0-9A-Z]+", token):
        return normalize_tb_token(token)
    if re.fullmatch(r"ER[0-9OQILZJGS]{5,}", token):
        return normalize_er_token(token)
    if token in {"IESD", "1ESD", "LESD"}:
        return "ESD"
    if token.startswith("I") and re.fullmatch(r"I[A-Z]{3,}", token):
        return token[1:]
    if re.fullmatch(r"[A-Z]{3,}[0-9]", token):
        return token[:-1]
    contact_match = re.fullmatch(r"([0-9]{2})[0O](-[0-9]+)", token)
    if contact_match:
        return f"{contact_match.group(1)}D{contact_match.group(2)}"
    if re.fullmatch(r"20V[0O]", token):
        return "20VO"
    return token


def normalize_compare_token(text: str) -> str:
    token = normalize_display_token(text)
    if token.startswith(("CN", "TB", "ER")):
        return token
    # OCR often cannot separate O/0 in these scans. Keep this as comparison-only
    # normalization; the emitted label still keeps the display token.
    return token.replace("O", "0")


def label_content_tokens(text: str | None) -> list[str]:
    if not text:
        return []
    cleaned = clean_ocr_text(str(text))
    cleaned = cleaned.replace("(", " ").replace(")", " ").replace("/", " ")
    return [
        normalize_display_token(match.group(0))
        for match in re.finditer(r"[A-Z0-9]+(?:-[A-Z0-9]+)?", cleaned)
        if normalize_display_token(match.group(0))
    ]


def compare_tokens(tokens: list[str]) -> list[str]:
    return [normalize_compare_token(token) for token in tokens]


def contact_source_token(token: dict[str, Any]) -> str:
    text = clean_token_text(str(token.get("text", "")))
    source = clean_token_text(str(token.get("source_text", "")))
    if not source:
        return text
    if token.get("kind") == "number_label" and "ER" in source:
        return text
    if token.get("kind") == "sheet_ref":
        return text
    if source.startswith(("10", "11", "12", "120", "1")) and re.fullmatch(r"[0-9]+[A-Z][0-9A-Z-]*", source):
        return text
    if any(ch.isalpha() for ch in source):
        return source
    return text


def repair_contact_token(raw_token: str, has_b_code: bool) -> str:
    token = normalize_display_token(raw_token)
    if has_b_code and re.fullmatch(r"0[0-9]{3}", token):
        return f"D{token[-3:]}"
    if re.fullmatch(r"[DO0][0-9OQILZJGS]{3}", token):
        digits = re.sub(
            r"\D",
            "",
            token[1:].translate(str.maketrans({"O": "0", "Q": "0", "I": "1", "L": "1", "Z": "2", "J": "3", "G": "6", "S": "5"})),
        )
        if len(digits) >= 3:
            return f"D{digits[:3]}"
    return token


def is_contact_noise(token: str) -> bool:
    return (
        token in {"NO", "NC", "COM", "C", "P", "L", "Q", "G"}
        or re.fullmatch(r"(NO|NC|HC|COM)[0-9]+", token) is not None
        or re.fullmatch(r"-?P", token) is not None
        or re.fullmatch(r"L[0-9]+", token) is not None
        or len(token) == 1
    )


def build_contact_final(tokens: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted([token for token in tokens if token.get("primary", True)], key=token_sort_key)
    source_tokens = [contact_source_token(token) for token in ordered]
    has_b_code = any(re.fullmatch(r"B[0-9OQILZJGS]{3}", clean_token_text(source)) for source in source_tokens)
    has_hv_code = any(re.fullmatch(r"H[A-Z][0-9]+", normalize_display_token(source)) for source in source_tokens)

    selected: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []
    for token, source_token in zip(ordered, source_tokens, strict=False):
        repaired = repair_contact_token(source_token, has_b_code)
        kind = str(token.get("kind") or "")
        source = str(token.get("source_text") or "")
        confidence = float(token.get("confidence") or 0.0)
        reason = ""
        if not repaired:
            reason = "empty_after_cleanup"
        elif is_contact_noise(repaired):
            reason = "state_or_fragment_noise"
        elif kind == "number_label" and any(ch.isalpha() for ch in repaired):
            reason = ""
        elif kind == "number_label":
            if "ER" in source.upper():
                reason = "sheet_ref_trailing_number"
            elif has_hv_code and len(repaired) >= 2 and confidence >= 0.85:
                reason = ""
            else:
                reason = "contact_terminal_number_noise"
        elif re.fullmatch(r"[0-9]+", repaired):
            reason = "pure_number_without_contact_context"

        record = {
            "text": repaired,
            "source_text": source,
            "kind": kind,
            "confidence": token.get("confidence"),
            "bbox": token.get("bbox"),
        }
        if reason:
            record["reason"] = reason
            discarded.append(record)
        else:
            selected.append(record)

    return {
        "final_label": " ".join(token["text"] for token in selected),
        "final_tokens": [token["text"] for token in selected],
        "selected_tokens": selected,
        "discarded_tokens": discarded,
    }


def repair_connector_number(token: str) -> str:
    if re.fullmatch(r"0[0-9]", token):
        return str(int(token))
    if re.fullmatch(r"[1-9][0-9]0", token) and int(token) < 600:
        return token[:2]
    if re.fullmatch(r"[0-9]{3}", token) and token[0] == token[-1]:
        return token[:2]
    return token


def finalize_connector_tokens(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned = [token for token in selected if token["text"] != "0"]
    has_connector_ref = any(re.fullmatch(r"CN[0-9]+", token["text"]) for token in cleaned)
    has_terminal_block_ref = any(re.fullmatch(r"TB[0-9]+", token["text"]) for token in cleaned)
    has_at_ref = any(re.fullmatch(r"[0-9]*AT[0-9]", token["text"]) for token in cleaned)
    if has_connector_ref and has_terminal_block_ref:
        single_digits = {token["text"] for token in cleaned if re.fullmatch(r"[1-9]", token["text"])}
        for token in cleaned:
            if re.fullmatch(r"[1-9]0", token["text"]) and token["text"][0] in single_digits:
                token["text"] = token["text"][0]
    numbers = [token for token in cleaned if re.fullmatch(r"[0-9]+", token["text"])]
    has_large_wire_number = any(len(token["text"]) >= 3 and int(token["text"]) >= 600 for token in numbers)
    if (
        has_connector_ref
        and has_terminal_block_ref
        and not has_at_ref
        and not has_large_wire_number
        and len(numbers) == 1
        and len(numbers[0]["text"]) <= 2
    ):
        duplicate = {**numbers[0], "reason": "duplicated_single_pin_number_for_cn_tb_pair"}
        cleaned.append(duplicate)
    return cleaned


def build_connector_final(tokens: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted([token for token in tokens if token.get("primary", True)], key=token_sort_key)
    selected: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []
    for token in ordered:
        text = normalize_display_token(str(token.get("text", "")))
        kind = str(token.get("kind") or "")
        emitted: list[str] = []
        reason = ""

        if kind in {"connector_ref", "terminal_block_ref"}:
            if text.startswith("CN") and len(text[2:]) >= 5:
                emitted.extend([f"CN{text[2:4]}", repair_connector_number(text[-2:])])
            else:
                emitted.append(text)
        elif kind == "number":
            source = clean_token_text(str(token.get("source_text") or ""))
            if re.fullmatch(r"[0-9]{4,}", text):
                reason = "long_number_noise"
            elif (
                source != text
                and re.search(r"(CN|ON|T[B8])[0-9]+", source)
                and len(text) <= 2
                and not re.fullmatch(r"[0-9]+CN", source)
            ):
                reason = "designator_prefix_number_noise"
            elif source.isalpha() and len(text) <= 2:
                reason = "letter_fragment_number_noise"
            elif "AT" in source and len(text) == 1:
                reason = "at_suffix_fragment_noise"
            else:
                emitted.append(repair_connector_number(text))
        elif kind == "label_code":
            split_match = re.fullmatch(r"([0-9]{1,2})(?:CN|C|CH)", text)
            if split_match:
                emitted.append(repair_connector_number(split_match.group(1)))
            elif re.fullmatch(r"AT[0-9]", text):
                emitted.append(f"2{text}")
            elif re.fullmatch(r"[0-9]*AT[0-9]", text):
                emitted.append(text)
            else:
                reason = "connector_non_label_code_noise"
        else:
            reason = "unsupported_connector_token_kind"

        if reason:
            discarded.append(
                {
                    "text": text,
                    "source_text": token.get("source_text"),
                    "kind": kind,
                    "confidence": token.get("confidence"),
                    "bbox": token.get("bbox"),
                    "reason": reason,
                }
            )
            continue

        for out_text in emitted:
            if not out_text:
                continue
            selected.append(
                {
                    "text": out_text,
                    "source_text": token.get("source_text"),
                    "kind": kind,
                    "confidence": token.get("confidence"),
                    "bbox": token.get("bbox"),
                }
            )

    selected = finalize_connector_tokens(selected)
    return {
        "final_label": " ".join(token["text"] for token in selected),
        "final_tokens": [token["text"] for token in selected],
        "selected_tokens": selected,
        "discarded_tokens": discarded,
    }


def build_word_rule_label(item: dict[str, Any]) -> dict[str, Any]:
    tokens = item.get("label_candidate", {}).get("tokens") or []
    if item.get("class_key") == "contact_a":
        return build_contact_final(tokens)
    if item.get("class_key") == "connector":
        return build_connector_final(tokens)
    return {"final_label": "", "final_tokens": [], "selected_tokens": [], "discarded_tokens": []}


def multiset_intersection(left: list[str], right: list[str]) -> Counter[str]:
    left_counter = Counter(left)
    right_counter = Counter(right)
    return left_counter & right_counter


def evaluate_final_label(expected_label: str, final_tokens: list[str]) -> dict[str, Any]:
    expected_tokens = label_content_tokens(expected_label)
    expected_cmp = compare_tokens(expected_tokens)
    final_cmp = compare_tokens(final_tokens)
    if not expected_tokens:
        return {
            "status": "not_evaluable",
            "content_exact": False,
            "sequence_exact": False,
            "matched_token_count": 0,
            "expected_token_count": 0,
            "final_token_count": len(final_tokens),
            "token_precision": None,
            "token_recall": None,
            "expected_tokens": expected_tokens,
            "final_tokens": final_tokens,
            "missing_tokens": [],
            "extra_tokens": final_tokens,
        }
    matched = multiset_intersection(expected_cmp, final_cmp)
    missing_counter = Counter(expected_cmp) - matched
    extra_counter = Counter(final_cmp) - matched
    missing_tokens = list(missing_counter.elements())
    extra_tokens = list(extra_counter.elements())
    token_precision = sum(matched.values()) / max(1, len(final_cmp))
    token_recall = sum(matched.values()) / max(1, len(expected_cmp))
    content_exact = not missing_tokens and not extra_tokens
    sequence_exact = expected_cmp == final_cmp
    if content_exact:
        status = "exact_ok"
    elif not missing_tokens:
        status = "extra_or_order_only"
    else:
        status = "missing_or_wrong"
    return {
        "status": status,
        "content_exact": content_exact,
        "sequence_exact": sequence_exact,
        "matched_token_count": sum(matched.values()),
        "expected_token_count": len(expected_cmp),
        "final_token_count": len(final_cmp),
        "token_precision": round(token_precision, 4),
        "token_recall": round(token_recall, 4),
        "expected_tokens": expected_tokens,
        "expected_compare_tokens": expected_cmp,
        "final_tokens": final_tokens,
        "final_compare_tokens": final_cmp,
        "missing_tokens": missing_tokens,
        "extra_tokens": extra_tokens,
    }


def draw_legend(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    items = [
        ("missed / missing", (220, 38, 38)),
        ("extra/order only", (249, 115, 22)),
        ("exact content ok", (37, 99, 235)),
    ]
    cursor = x
    for text, color in items:
        draw.rectangle((cursor, y, cursor + 22, y + 14), fill=color)
        draw.text((cursor + 28, y - 3), text, fill=(30, 30, 30), font=FONT_SMALL)
        cursor += 210


def draw_overlay(image: Image.Image, evaluations: list[dict[str, Any]], summary: dict[str, Any]) -> Image.Image:
    header = 54
    canvas = Image.new("RGB", (image.width, image.height + header), "white")
    canvas.paste(image.convert("RGB"), (0, header))
    draw = ImageDraw.Draw(canvas)
    title = (
        f"exact={summary['label_content_exact']}  extra/order={summary['label_extra_or_order_only']}  "
        f"wrong={summary['label_missing_or_wrong']}  missed={summary['missed']}"
    )
    draw.text((12, 9), title, fill=(30, 30, 30), font=FONT)
    draw_legend(draw, 760, 16)
    colors = {
        "missed": (220, 38, 38),
        "missing_or_wrong": (220, 38, 38),
        "extra_or_order_only": (249, 115, 22),
        "exact_ok": (37, 99, 235),
        "not_evaluable": (110, 110, 110),
    }
    scale = max(1.0, image.width / 1800)
    line_width = max(4, round(4 * scale))
    for item in evaluations:
        box = item["gold_bbox"]
        status = item["word_rule_match"]["status"] if item.get("detected") else "missed"
        color = colors.get(status, (220, 38, 38))
        draw.rectangle(
            (box["x0"], box["y0"] + header, box["x1"], box["y1"] + header),
            outline=color,
            width=line_width,
        )
    return canvas


def summarize_items(items: list[dict[str, Any]], false_positive_count: int) -> dict[str, Any]:
    counts = Counter()
    precision_values: list[float] = []
    recall_values: list[float] = []
    matched_token_count = 0
    expected_token_count = 0
    final_token_count = 0
    for item in items:
        if not item.get("detected"):
            counts["missed"] += 1
            continue
        match = item["word_rule_match"]
        counts[match["status"]] += 1
        if match.get("sequence_exact"):
            counts["sequence_exact"] += 1
        matched_token_count += int(match.get("matched_token_count") or 0)
        expected_token_count += int(match.get("expected_token_count") or 0)
        final_token_count += int(match.get("final_token_count") or 0)
        if match["token_precision"] is not None:
            precision_values.append(float(match["token_precision"]))
        if match["token_recall"] is not None:
            recall_values.append(float(match["token_recall"]))

    detected = len(items) - counts["missed"]
    evaluable = detected - counts["not_evaluable"]
    return {
        "gold_count": len(items),
        "detected_count": detected,
        "missed": counts["missed"],
        "false_positive_count": false_positive_count,
        "label_content_exact": counts["exact_ok"],
        "label_extra_or_order_only": counts["extra_or_order_only"],
        "label_missing_or_wrong": counts["missing_or_wrong"],
        "label_not_evaluable": counts["not_evaluable"],
        "label_sequence_exact": counts["sequence_exact"],
        "label_content_exact_accuracy_on_detected": round(counts["exact_ok"] / max(1, evaluable), 4)
        if evaluable
        else None,
        "label_sequence_exact_accuracy_on_detected": round(counts["sequence_exact"] / max(1, evaluable), 4)
        if evaluable
        else None,
        "token_match_count": matched_token_count,
        "expected_token_count": expected_token_count,
        "final_token_count": final_token_count,
        "micro_token_precision": round(matched_token_count / max(1, final_token_count), 4),
        "micro_token_recall": round(matched_token_count / max(1, expected_token_count), 4),
        "mean_token_precision": round(sum(precision_values) / max(1, len(precision_values)), 4)
        if precision_values
        else None,
        "mean_token_recall": round(sum(recall_values) / max(1, len(recall_values)), 4) if recall_values else None,
    }


def process_page(page_json: Path, image: Image.Image, out_dir: Path) -> dict[str, Any]:
    payload = json.loads(page_json.read_text())
    page_summary = payload["summary"]
    evaluations: list[dict[str, Any]] = []
    csv_rows: list[dict[str, Any]] = []
    for item in payload["evaluations"]:
        if item.get("class_key") not in TARGET_CLASSES:
            continue
        out_item = {
            "ref": item["ref"],
            "class_key": item["class_key"],
            "expected_label": item.get("expected_label", ""),
            "gold_bbox": item["gold_bbox"],
            "detected": bool(item.get("detected")),
        }
        if item.get("detected"):
            final = build_word_rule_label(item)
            match = evaluate_final_label(str(item.get("expected_label") or ""), list(final["final_tokens"]))
            out_item.update(
                {
                    "ocr_text": item.get("ocr", {}).get("text", ""),
                    "previous_candidate_label": item.get("label_candidate", {}).get("text", ""),
                    "word_rule_label": final,
                    "word_rule_match": match,
                }
            )
            csv_rows.append(
                {
                    "sheet_no": page_summary["sheet_no"],
                    "ref": item["ref"],
                    "class_key": item["class_key"],
                    "expected_label": item.get("expected_label", ""),
                    "ocr_text": item.get("ocr", {}).get("text", ""),
                    "final_label": final["final_label"],
                    "status": match["status"],
                    "token_precision": match["token_precision"],
                    "token_recall": match["token_recall"],
                    "missing_tokens": " ".join(match["missing_tokens"]),
                    "extra_tokens": " ".join(match["extra_tokens"]),
                }
            )
        else:
            out_item["word_rule_match"] = {
                "status": "missed",
                "content_exact": False,
                "sequence_exact": False,
                "expected_tokens": label_content_tokens(item.get("expected_label")),
                "final_tokens": [],
                "missing_tokens": compare_tokens(label_content_tokens(item.get("expected_label"))),
                "extra_tokens": [],
            }
            csv_rows.append(
                {
                    "sheet_no": page_summary["sheet_no"],
                    "ref": item["ref"],
                    "class_key": item["class_key"],
                    "expected_label": item.get("expected_label", ""),
                    "ocr_text": "",
                    "final_label": "",
                    "status": "missed",
                    "token_precision": None,
                    "token_recall": None,
                    "missing_tokens": " ".join(out_item["word_rule_match"]["missing_tokens"]),
                    "extra_tokens": "",
                }
            )
        evaluations.append(out_item)

    false_positives = [
        item
        for item in payload.get("false_positive_predictions", [])
        if item.get("class_name") in TARGET_CLASSES
    ]
    summary = summarize_items(evaluations, len(false_positives))
    summary.update(
        {
            "sheet_no": page_summary["sheet_no"],
            "source_file": page_summary.get("source_file"),
            "classes": {},
        }
    )
    for class_key in sorted(TARGET_CLASSES):
        class_items = [item for item in evaluations if item["class_key"] == class_key]
        class_fp = sum(1 for item in false_positives if item.get("class_name") == class_key)
        summary["classes"][class_key] = summarize_items(class_items, class_fp)

    page_dir = out_dir / str(page_summary["sheet_no"])
    page_dir.mkdir(parents=True, exist_ok=True)
    overlay = draw_overlay(image, evaluations, summary)
    overlay_path = page_dir / "label_word_rule_evaluation.png"
    overlay.save(overlay_path)
    output_payload = {
        "schema_version": "todensekkei.label_word_rule_eval.page.v1",
        "source_page_json": str(page_json),
        "summary": summary,
        "evaluations": evaluations,
        "false_positive_predictions": false_positives,
        "outputs": {
            "overlay": str(overlay_path),
            "csv": str(page_dir / "label_word_rule_evaluation.csv"),
        },
    }
    write_json(page_dir / "label_word_rule_evaluation.json", output_payload)
    with (page_dir / "label_word_rule_evaluation.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "sheet_no",
                "ref",
                "class_key",
                "expected_label",
                "ocr_text",
                "final_label",
                "status",
                "token_precision",
                "token_recall",
                "missing_tokens",
                "extra_tokens",
            ],
        )
        writer.writeheader()
        writer.writerows(csv_rows)
    return {
        **summary,
        "outputs": {
            "overlay": str(overlay_path),
            "json": str(page_dir / "label_word_rule_evaluation.json"),
            "csv": str(page_dir / "label_word_rule_evaluation.csv"),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Post-process YOLO/OCR label output with word-level cleanup rules.")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--annotation-zip", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    images = load_zip_images(args.annotation_zip)
    page_jsons = sorted(args.input_dir.glob("*/label_ocr_evaluation.json"))
    if not page_jsons:
        raise SystemExit(f"no label_ocr_evaluation.json found under {args.input_dir}")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    pages: list[dict[str, Any]] = []
    all_csv_rows: list[dict[str, Any]] = []
    for page_json in page_jsons:
        payload = json.loads(page_json.read_text())
        sheet_no = str(payload["summary"]["sheet_no"])
        image = images.get(sheet_no)
        if image is None:
            raise SystemExit(f"missing image for sheet {sheet_no}")
        page_summary = process_page(page_json, image, args.out_dir)
        pages.append(page_summary)
        page_csv = Path(page_summary["outputs"]["csv"])
        with page_csv.open(encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            all_csv_rows.extend(reader)

    aggregate = summarize_items_from_page_summaries(pages)
    summary = {
        "schema_version": "todensekkei.label_word_rule_eval.summary.v1",
        "input_dir": str(args.input_dir),
        "annotation_zip": str(args.annotation_zip),
        "aggregate": aggregate,
        "pages": pages,
    }
    write_json(args.out_dir / "summary.json", summary)
    with (args.out_dir / "label_word_rule_evaluation.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "sheet_no",
                "ref",
                "class_key",
                "expected_label",
                "ocr_text",
                "final_label",
                "status",
                "token_precision",
                "token_recall",
                "missing_tokens",
                "extra_tokens",
            ],
        )
        writer.writeheader()
        writer.writerows(all_csv_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def summarize_items_from_page_summaries(pages: list[dict[str, Any]]) -> dict[str, Any]:
    total = Counter()
    class_totals: dict[str, Counter] = {class_key: Counter() for class_key in sorted(TARGET_CLASSES)}
    class_precision_values: dict[str, list[float]] = {class_key: [] for class_key in sorted(TARGET_CLASSES)}
    class_recall_values: dict[str, list[float]] = {class_key: [] for class_key in sorted(TARGET_CLASSES)}
    precision_values: list[float] = []
    recall_values: list[float] = []
    for page in pages:
        for key in (
            "gold_count",
            "detected_count",
            "missed",
            "false_positive_count",
            "label_content_exact",
            "label_extra_or_order_only",
            "label_missing_or_wrong",
            "label_not_evaluable",
            "label_sequence_exact",
            "token_match_count",
            "expected_token_count",
            "final_token_count",
        ):
            total[key] += page[key]
        if page.get("mean_token_precision") is not None:
            precision_values.append(float(page["mean_token_precision"]))
        if page.get("mean_token_recall") is not None:
            recall_values.append(float(page["mean_token_recall"]))
        for class_key, class_summary in page["classes"].items():
            for key in (
                "gold_count",
                "detected_count",
                "missed",
                "false_positive_count",
                "label_content_exact",
                "label_extra_or_order_only",
                "label_missing_or_wrong",
                "label_not_evaluable",
                "label_sequence_exact",
                "token_match_count",
                "expected_token_count",
                "final_token_count",
            ):
                class_totals[class_key][key] += class_summary[key]
            if class_summary.get("mean_token_precision") is not None:
                class_precision_values[class_key].append(float(class_summary["mean_token_precision"]))
            if class_summary.get("mean_token_recall") is not None:
                class_recall_values[class_key].append(float(class_summary["mean_token_recall"]))

    aggregate = summary_from_counter(total, precision_values, recall_values)
    aggregate["classes"] = {
        class_key: summary_from_counter(
            counts,
            class_precision_values[class_key],
            class_recall_values[class_key],
        )
        for class_key, counts in class_totals.items()
    }
    return aggregate


def summary_from_counter(counts: Counter[str], precision_values: list[float], recall_values: list[float]) -> dict[str, Any]:
    evaluable = counts["detected_count"] - counts["label_not_evaluable"]
    return {
        **dict(counts),
        "detection_recall": round(counts["detected_count"] / max(1, counts["gold_count"]), 4),
        "detection_precision": round(
            counts["detected_count"] / max(1, counts["detected_count"] + counts["false_positive_count"]),
            4,
        ),
        "label_content_exact_accuracy_on_detected": round(counts["label_content_exact"] / max(1, evaluable), 4)
        if evaluable
        else None,
        "label_sequence_exact_accuracy_on_detected": round(counts["label_sequence_exact"] / max(1, evaluable), 4)
        if evaluable
        else None,
        "micro_token_precision": round(counts["token_match_count"] / max(1, counts["final_token_count"]), 4),
        "micro_token_recall": round(counts["token_match_count"] / max(1, counts["expected_token_count"]), 4),
        "mean_token_precision": round(sum(precision_values) / max(1, len(precision_values)), 4)
        if precision_values
        else None,
        "mean_token_recall": round(sum(recall_values) / max(1, len(recall_values)), 4) if recall_values else None,
    }


if __name__ == "__main__":
    main()
