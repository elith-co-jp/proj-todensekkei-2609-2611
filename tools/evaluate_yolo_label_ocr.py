from __future__ import annotations

import argparse
import io
import json
import re
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from analyze_annotation_graph import load_zip_payload, symbol_bbox, write_json
from run_yolo_symbol_review import (
    dedupe_overlapping_symbols,
    run_yolo_prediction,
)


DEFAULT_TARGET_CLASSES = ("connector", "contact_a")


@dataclass(frozen=True)
class GoldSymbol:
    ref: str
    class_key: str
    label: str
    bbox: dict[str, int]


def load_font(size: int) -> ImageFont.ImageFont:
    for font_name in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "Arial.ttf",
    ):
        try:
            return ImageFont.truetype(font_name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


FONT = load_font(18)
FONT_SMALL = load_font(14)


def normalize_label(text: str | None) -> str:
    if not text:
        return ""
    normalized = text.upper()
    normalized = normalized.replace("（", "(").replace("）", ")")
    normalized = normalized.replace("－", "-").replace("ー", "-")
    normalized = normalized.replace("O", "0")
    normalized = normalized.replace("Ⅰ", "I").replace("Ｉ", "I")
    normalized = re.sub(r"[^A-Z0-9()/.-]+", "", normalized)
    return normalized


def canonical_token(token: str) -> str:
    token = token.upper()
    if token.startswith("CN"):
        suffix = token[2:].translate(str.maketrans({"O": "0", "I": "1", "L": "1", "Z": "2", "J": "3", "G": "6"}))
        return f"CN{suffix}"
    if token.startswith("TB"):
        suffix = token[2:].translate(str.maketrans({"O": "0", "I": "1", "L": "1", "Z": "2", "J": "3", "G": "6"}))
        return f"TB{suffix}"
    if token.startswith("ER"):
        suffix = token[2:].translate(str.maketrans({"O": "0", "I": "1", "L": "1", "Z": "2", "J": "3", "G": "6", "S": "5"}))
        return f"ER{suffix}"
    if re.fullmatch(r"[A-Z]?[0-9A-Z]+", token):
        return token.translate(str.maketrans({"O": "0", "I": "1", "L": "1", "Z": "2", "J": "3", "G": "6"}))
    return token


def label_tokens(text: str | None) -> list[str]:
    if not text:
        return []
    normalized = text.upper()
    normalized = normalized.replace("（", " ").replace("）", " ")
    normalized = normalized.replace("(", " ").replace(")", " ")
    normalized = normalized.replace("/", " ").replace("-", " ")
    normalized = normalized.replace("－", " ").replace("ー", " ")
    tokens = re.findall(r"[A-Z0-9]+", normalized)
    return [canonical_token(token) for token in tokens if token]


def edit_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def token_matches(expected: str, observed: str) -> bool:
    if expected == observed:
        return True
    distance = edit_distance(expected, observed)
    if len(expected) <= 2:
        return distance == 0
    if len(expected) <= 4:
        return distance <= 1
    return distance / max(len(expected), len(observed), 1) <= 0.28


def label_match(expected: str, observed: str) -> dict[str, Any]:
    expected_norm = normalize_label(expected)
    observed_norm = normalize_label(observed)
    expected_tokens = label_tokens(expected)
    observed_tokens = set(label_tokens(observed))
    if not expected_norm:
        return {
            "status": "not_evaluable",
            "correct": True,
            "exact": False,
            "token_recall": None,
            "matched_tokens": [],
            "expected_tokens": [],
        }
    exact = expected_norm == observed_norm
    matched_tokens = [
        token
        for token in expected_tokens
        if any(token_matches(token, observed_token) for observed_token in observed_tokens)
    ]
    token_recall = len(matched_tokens) / max(1, len(expected_tokens))
    return {
        "status": "correct" if exact or token_recall >= 0.8 else "wrong",
        "correct": exact or token_recall >= 0.8,
        "exact": exact,
        "token_recall": round(token_recall, 4),
        "matched_tokens": matched_tokens,
        "expected_tokens": expected_tokens,
    }


DIGIT_CONFUSION_TRANS = str.maketrans(
    {
        "O": "0",
        "Q": "0",
        "I": "1",
        "L": "1",
        "Z": "2",
        "J": "3",
        "G": "6",
        "S": "5",
    }
)


def clean_ocr_text(text: str) -> str:
    cleaned = text.upper()
    cleaned = cleaned.replace("（", "(").replace("）", ")")
    cleaned = cleaned.replace("－", "-").replace("ー", "-")
    return re.sub(r"[^A-Z0-9()/.-]+", " ", cleaned).strip()


def fragment_center(fragment: dict[str, Any]) -> tuple[float, float]:
    points = fragment.get("bbox") or []
    if not points:
        return 0.0, 0.0
    return (
        sum(float(point[0]) for point in points) / len(points),
        sum(float(point[1]) for point in points) / len(points),
    )


def add_candidate_token(
    tokens: list[dict[str, Any]],
    *,
    text: str,
    kind: str,
    fragment: dict[str, Any],
    primary: bool = True,
    reason: str | None = None,
) -> None:
    if not text:
        return
    token = {
        "text": text,
        "kind": kind,
        "source_text": fragment.get("text", ""),
        "confidence": fragment.get("confidence"),
        "bbox": fragment.get("bbox"),
        "primary": primary,
    }
    if reason:
        token["reason"] = reason
    tokens.append(token)


def add_rejected_token(
    rejected: list[dict[str, Any]],
    *,
    text: str,
    reason: str,
    fragment: dict[str, Any],
) -> None:
    if not text:
        return
    rejected.append(
        {
            "text": text,
            "reason": reason,
            "source_text": fragment.get("text", ""),
            "confidence": fragment.get("confidence"),
            "bbox": fragment.get("bbox"),
        }
    )


def normalize_er_token(match_text: str) -> str:
    digits = re.sub(r"\D", "", match_text[2:].translate(DIGIT_CONFUSION_TRANS))
    return f"ER{digits[:5]}" if len(digits) >= 5 else ""


def normalize_cn_token(match_text: str) -> str:
    digits = re.sub(r"\D", "", match_text[2:].translate(DIGIT_CONFUSION_TRANS))
    return f"CN{digits}" if digits else ""


def normalize_tb_token(match_text: str) -> str:
    if re.match(r"T[B8]", match_text):
        suffix = match_text[2:]
    else:
        suffix = match_text[1:]
    digits = re.sub(r"\D", "", suffix.translate(DIGIT_CONFUSION_TRANS))
    return f"TB{digits}" if digits else ""


def normalized_number_candidates(value: str) -> list[str]:
    digits = re.sub(r"\D", "", value.translate(DIGIT_CONFUSION_TRANS))
    if not digits:
        return []
    candidates: list[str] = []
    stripped = digits.lstrip("0") or "0"
    candidates.append(stripped)
    if len(digits) == 3 and digits.endswith("0"):
        candidates.append(digits[:2])
    if len(digits) == 3 and digits[0] == digits[-1]:
        candidates.append(digits[:2])
    deduped: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped


def designator_tokens_from_text(text: str) -> list[tuple[str, str, tuple[int, int]]]:
    tokens: list[tuple[str, str, tuple[int, int]]] = []
    for match in re.finditer(r"[CO0]N[0-9A-Z]+", text):
        normalized = normalize_cn_token(f"CN{match.group(0)[2:]}")
        if normalized:
            tokens.append((normalized, "connector_ref", match.span()))
    for match in re.finditer(r"T[B8][0-9A-Z]+", text):
        normalized = normalize_tb_token(match.group(0))
        if normalized:
            tokens.append((normalized, "terminal_block_ref", match.span()))
    return tokens


def masked_text(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        for index in range(max(0, start), min(len(chars), end)):
            chars[index] = " "
    return "".join(chars)


def build_connector_label_candidate(ocr: dict[str, Any]) -> dict[str, Any]:
    tokens: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for fragment in ocr.get("fragments", []):
        text = clean_ocr_text(str(fragment.get("text", ""))).replace(" ", "")
        if not text:
            continue
        spans: list[tuple[int, int]] = []
        for token, kind, span in designator_tokens_from_text(text):
            add_candidate_token(tokens, text=token, kind=kind, fragment=fragment)
            if kind == "connector_ref":
                suffix = token[2:]
                if len(suffix) >= 5:
                    add_candidate_token(
                        tokens,
                        text=f"CN{suffix[:2]}",
                        kind="connector_ref_alt",
                        fragment=fragment,
                        primary=False,
                        reason="merged_connector_and_pin_candidate",
                    )
                    add_candidate_token(
                        tokens,
                        text=suffix[-2:],
                        kind="number_alt",
                        fragment=fragment,
                        primary=False,
                        reason="merged_connector_and_pin_candidate",
                    )
            if kind == "terminal_block_ref" and re.fullmatch(r"TB[0-9]+", token):
                add_candidate_token(
                    tokens,
                    text="TB",
                    kind="terminal_block_ref_alt",
                    fragment=fragment,
                    primary=False,
                    reason="terminal_block_suffix_noise_candidate",
                )
            spans.append(span)
        remaining = masked_text(text, spans)

        code_spans: list[tuple[int, int]] = []
        for match in re.finditer(r"[0-9A-Z]*[A-Z][0-9A-Z-]*", remaining):
            part = match.group(0)
            if not part or re.fullmatch(r"[CNTOQBILZJGS]{1,2}", part):
                continue
            at_split = re.match(r"^([0-9]*AT[0-9])([0-9]{1,3})$", part)
            if at_split:
                add_candidate_token(tokens, text=at_split.group(1), kind="label_code", fragment=fragment)
                add_candidate_token(tokens, text=at_split.group(2), kind="number", fragment=fragment)
                code_spans.append(match.span())
                continue
            if re.fullmatch(r"AT[0-9]", part):
                add_candidate_token(tokens, text=part, kind="label_code", fragment=fragment)
                add_candidate_token(tokens, text=f"2{part}", kind="label_code_alt", fragment=fragment, primary=False, reason="missing_prefix_candidate")
                code_spans.append(match.span())
                continue
            add_candidate_token(tokens, text=part, kind="label_code", fragment=fragment)
            code_spans.append(match.span())

        remaining = masked_text(remaining, code_spans)
        for match in re.finditer(r"[0-9OQILZJGS]{1,4}", remaining):
            values = normalized_number_candidates(match.group(0))
            if not values:
                continue
            add_candidate_token(tokens, text=values[0], kind="number", fragment=fragment)
            for alt in values[1:]:
                add_candidate_token(tokens, text=alt, kind="number_alt", fragment=fragment, primary=False, reason="short_number_cleanup")

    primary = [token["text"] for token in tokens if token.get("primary")]
    match_tokens = [token["text"] for token in tokens]
    return {
        "text": " ".join(primary),
        "match_text": " ".join(match_tokens),
        "tokens": tokens,
        "rejected_tokens": rejected,
    }


def strip_leading_terminal_noise(token: str) -> tuple[str, str | None]:
    if re.fullmatch(r"0[0-9]{2}", token):
        return token.lstrip("0"), "leading_zero"
    if len(token) >= 5 and token[:2].isdigit():
        candidate = token[2:].lstrip("0")
        if re.match(r"[0-9]{2}[A-Z]+", candidate):
            return candidate, "leading_terminal_number"
    if re.fullmatch(r"[0-9]{3,4}[A-Z]+[0-9A-Z-]*", token):
        candidate = token[1:].lstrip("0")
        if re.match(r"[0-9]{2}[A-Z]+", candidate):
            return candidate, "leading_digit_noise"
    return token, None


def build_contact_label_candidate(ocr: dict[str, Any]) -> dict[str, Any]:
    tokens: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    processed_size = ocr.get("processed_size")
    if processed_size:
        crop_width = max(1, float(processed_size[0]))
    else:
        crop_width = max(
            1.0,
            max(
                (float(point[0]) for fragment in ocr.get("fragments", []) for point in fragment.get("bbox", [])),
                default=1.0,
            ),
        )
    for fragment in ocr.get("fragments", []):
        raw = str(fragment.get("text", ""))
        text = clean_ocr_text(raw)
        if not text:
            continue
        center_x, _center_y = fragment_center(fragment)
        at_edge = center_x / crop_width < 0.22 or center_x / crop_width > 0.78

        for match in re.finditer(r"ER[0-9OQILZJGS]{5,}", text):
            token = normalize_er_token(match.group(0))
            add_candidate_token(tokens, text=token, kind="sheet_ref", fragment=fragment)
            text = text.replace(match.group(0), " ")

        for part in re.findall(r"[A-Z0-9]+(?:-[0-9]+)?", text):
            if not part:
                continue
            if re.fullmatch(r"[0-9OQILZJGS]{1,2}", part) and at_edge:
                add_rejected_token(rejected, text=part, reason="edge_terminal_number", fragment=fragment)
                continue

            normalized_part, reason = strip_leading_terminal_noise(part)
            if normalized_part != part:
                add_rejected_token(rejected, text=part, reason=reason or "prefix_noise", fragment=fragment)

            if re.fullmatch(r"[0-9OQIZJGS]{1,3}", normalized_part):
                values = normalized_number_candidates(normalized_part)
                if not values:
                    continue
                if at_edge and len(values[0]) <= 2:
                    add_rejected_token(rejected, text=part, reason="edge_terminal_number", fragment=fragment)
                    continue
                add_candidate_token(tokens, text=values[0], kind="number_label", fragment=fragment)
                continue

            add_candidate_token(tokens, text=normalized_part, kind="label_token", fragment=fragment)

    primary = [token["text"] for token in tokens if token.get("primary")]
    match_tokens = [token["text"] for token in tokens]
    return {
        "text": " ".join(primary),
        "match_text": " ".join(match_tokens),
        "tokens": tokens,
        "rejected_tokens": rejected,
    }


def build_label_candidate(class_key: str, ocr: dict[str, Any]) -> dict[str, Any]:
    if class_key == "connector":
        return build_connector_label_candidate(ocr)
    if class_key == "contact_a":
        return build_contact_label_candidate(ocr)
    return {
        "text": ocr.get("text", ""),
        "match_text": ocr.get("text", ""),
        "tokens": [],
        "rejected_tokens": [],
    }


def label_text_for_match(ocr: dict[str, Any], candidate: dict[str, Any], source: str) -> str:
    if source == "structured":
        return str(candidate.get("text", ""))
    if source == "structured_match":
        return str(candidate.get("match_text", candidate.get("text", "")))
    return str(ocr.get("text", ""))


def box_area(box: dict[str, int | float]) -> float:
    return max(0.0, float(box["x1"]) - float(box["x0"])) * max(0.0, float(box["y1"]) - float(box["y0"]))


def intersection_area(a: dict[str, int | float], b: dict[str, int | float]) -> float:
    x0 = max(float(a["x0"]), float(b["x0"]))
    y0 = max(float(a["y0"]), float(b["y0"]))
    x1 = min(float(a["x1"]), float(b["x1"]))
    y1 = min(float(a["y1"]), float(b["y1"]))
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def box_iou(a: dict[str, int | float], b: dict[str, int | float]) -> float:
    inter = intersection_area(a, b)
    return inter / max(1.0, box_area(a) + box_area(b) - inter)


def min_overlap(a: dict[str, int | float], b: dict[str, int | float]) -> float:
    inter = intersection_area(a, b)
    return inter / max(1.0, min(box_area(a), box_area(b)))


def expand_box(box: dict[str, int], width: int, height: int, padding: int) -> tuple[int, int, int, int]:
    return (
        max(0, int(box["x0"]) - padding),
        max(0, int(box["y0"]) - padding),
        min(width, int(box["x1"]) + padding),
        min(height, int(box["y1"]) + padding),
    )


OCR_ALLOWLIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789()-/. "


def preprocess_ocr_crop(crop: Image.Image, mode: str, scale: int) -> np.ndarray:
    crop = ImageOps.grayscale(crop)
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)
    arr = np.array(crop)
    if mode == "gray":
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2RGB)

    arr = cv2.GaussianBlur(arr, (3, 3), 0)
    if mode == "otsu":
        _, thresholded = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return cv2.cvtColor(thresholded, cv2.COLOR_GRAY2RGB)

    if mode.startswith("line"):
        foreground = cv2.adaptiveThreshold(
            arr,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            31,
            9,
        )
        height, width = foreground.shape
        vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(35, height // 3)))
        horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(55, width // 3), 1))
        vertical_lines = cv2.morphologyEx(foreground, cv2.MORPH_OPEN, vertical_kernel)
        horizontal_lines = cv2.morphologyEx(foreground, cv2.MORPH_OPEN, horizontal_kernel)
        line_mask = cv2.bitwise_or(vertical_lines, horizontal_lines)
        if mode == "line_remove_wide":
            line_mask = cv2.dilate(line_mask, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1)
        cleaned = foreground.copy()
        cleaned[line_mask > 0] = 0
        return cv2.cvtColor(255 - cleaned, cv2.COLOR_GRAY2RGB)

    thresholded = cv2.adaptiveThreshold(
        arr,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        9,
    )
    return cv2.cvtColor(thresholded, cv2.COLOR_GRAY2RGB)


def read_label(
    reader: Any,
    image: Image.Image,
    box: dict[str, int],
    padding: int,
    engine: str,
    preprocess_mode: str,
    scale: int,
    allowlist: str,
) -> dict[str, Any]:
    x0, y0, x1, y1 = expand_box(box, image.width, image.height, padding)
    crop = image.crop((x0, y0, x1, y1))
    processed = preprocess_ocr_crop(crop, preprocess_mode, scale)
    if engine == "easyocr":
        results = reader.readtext(
            processed,
            detail=1,
            paragraph=False,
            decoder="greedy",
            allowlist=allowlist,
            min_size=4,
            text_threshold=0.35,
            low_text=0.20,
            link_threshold=0.25,
        )
    else:
        rapid_results, _elapsed = reader(processed, use_cls=False)
        results = [] if rapid_results is None else [(item[0], item[1], item[2]) for item in rapid_results]
    fragments: list[dict[str, Any]] = []
    for bbox, text, confidence in results:
        fragments.append(
            {
                "text": str(text),
                "confidence": round(float(confidence), 4),
                "bbox": [[round(float(x)), round(float(y))] for x, y in bbox],
            }
        )
    fragments.sort(key=lambda item: (min(point[1] for point in item["bbox"]), min(point[0] for point in item["bbox"])))
    joined = " ".join(item["text"] for item in fragments)
    return {
        "text": joined,
        "fragments": fragments,
        "crop_box": [x0, y0, x1, y1],
        "processed_size": [int(processed.shape[1]), int(processed.shape[0])],
    }


def load_zip_images(annotation_zip: Path) -> dict[str, Image.Image]:
    images: dict[str, Image.Image] = {}
    with zipfile.ZipFile(annotation_zip) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".png") or "/images/" not in f"/{name}":
                continue
            with archive.open(name) as raw:
                image = Image.open(io.BytesIO(raw.read())).convert("RGB")
            stem = Path(name).stem
            match = re.search(r"p\d+_(.+?)_page_\d+", stem)
            if match:
                images[match.group(1)] = image
    return images


def gold_symbols_for_project(project: dict[str, Any], target_classes: set[str]) -> list[GoldSymbol]:
    width = int(project["image_width"])
    height = int(project["image_height"])
    output: list[GoldSymbol] = []
    for symbol in project.get("symbols", []):
        class_key = str(symbol.get("class_key") or "")
        if class_key not in target_classes:
            continue
        output.append(
            GoldSymbol(
                ref=str(symbol["ref"]),
                class_key=class_key,
                label=str(symbol.get("label") or ""),
                bbox=symbol_bbox(symbol, width, height),
            )
        )
    return output


def match_predictions(
    gold_symbols: list[GoldSymbol],
    predictions: list[dict[str, Any]],
    *,
    iou_threshold: float,
    min_overlap_threshold: float,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    matched: dict[str, dict[str, Any]] = {}
    used_prediction_ids: set[str] = set()
    for gold in gold_symbols:
        candidates = []
        for pred in predictions:
            if pred["id"] in used_prediction_ids:
                continue
            if str(pred.get("class_name")) != gold.class_key:
                continue
            iou = box_iou(gold.bbox, pred["bbox"])
            overlap = min_overlap(gold.bbox, pred["bbox"])
            if iou >= iou_threshold or overlap >= min_overlap_threshold:
                candidates.append((max(iou, overlap), iou, overlap, pred))
        if not candidates:
            continue
        _score, iou, overlap, pred = max(candidates, key=lambda item: item[0])
        used_prediction_ids.add(pred["id"])
        matched[gold.ref] = {
            **pred,
            "match_iou": round(iou, 4),
            "match_min_overlap": round(overlap, 4),
        }
    false_positives = [pred for pred in predictions if pred["id"] not in used_prediction_ids]
    return matched, false_positives


def draw_legend(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    items = [
        ("missed", (220, 38, 38)),
        ("detected / label wrong", (249, 115, 22)),
        ("detected / label ok", (37, 99, 235)),
    ]
    cursor = x
    for text, color in items:
        draw.rectangle((cursor, y, cursor + 22, y + 14), fill=color)
        draw.text((cursor + 28, y - 3), text, fill=(30, 30, 30), font=FONT_SMALL)
        cursor += 220


def draw_evaluation_overlay(image: Image.Image, evaluations: list[dict[str, Any]], summary: dict[str, Any]) -> Image.Image:
    header = 44
    canvas = Image.new("RGB", (image.width, image.height + header), "white")
    canvas.paste(image.convert("RGB"), (0, header))
    draw = ImageDraw.Draw(canvas)
    title = (
        f"missed={summary['missed']}  label_wrong={summary['label_wrong']}  "
        f"label_ok={summary['label_ok']}  not_evaluable={summary['label_not_evaluable']}"
    )
    draw.text((12, 9), title, fill=(30, 30, 30), font=FONT)
    draw_legend(draw, 650, 13)
    colors = {
        "missed": (220, 38, 38),
        "label_wrong": (249, 115, 22),
        "label_ok": (37, 99, 235),
        "label_not_evaluable": (37, 99, 235),
    }
    scale = max(1.0, image.width / 1800)
    line_width = max(4, round(4 * scale))
    for item in evaluations:
        box = item["gold_bbox"]
        color = colors[item["visual_status"]]
        draw.rectangle(
            (box["x0"], box["y0"] + header, box["x1"], box["y1"] + header),
            outline=color,
            width=line_width,
        )
    return canvas


def process_project(
    *,
    project: dict[str, Any],
    image: Image.Image,
    model_path: Path,
    out_dir: Path,
    reader: Any,
    target_classes: set[str],
    args: argparse.Namespace,
) -> dict[str, Any]:
    page_dir = out_dir / str(project["sheet_no"])
    page_dir.mkdir(parents=True, exist_ok=True)
    input_path = page_dir / "_input.png"
    image.save(input_path)
    all_predictions, model_names = run_yolo_prediction(
        model_path,
        input_path,
        imgsz=args.imgsz,
        conf=args.conf,
        iou=args.yolo_iou,
        max_det=args.max_det,
        device=args.device,
    )
    input_path.unlink(missing_ok=True)
    target_predictions = [
        pred for pred in all_predictions if str(pred.get("class_name")) in target_classes
    ]
    predictions = dedupe_overlapping_symbols(
        target_predictions,
        iou_threshold=args.dedupe_iou,
        min_overlap_threshold=args.dedupe_min_overlap,
    )
    gold_symbols = gold_symbols_for_project(project, target_classes)
    matches, false_positives = match_predictions(
        gold_symbols,
        predictions,
        iou_threshold=args.match_iou,
        min_overlap_threshold=args.match_min_overlap,
    )

    evaluations: list[dict[str, Any]] = []
    for gold in gold_symbols:
        prediction = matches.get(gold.ref)
        if prediction is None:
            evaluations.append(
                {
                    "ref": gold.ref,
                    "class_key": gold.class_key,
                    "expected_label": gold.label,
                    "gold_bbox": gold.bbox,
                    "detected": False,
                    "visual_status": "missed",
                }
            )
            continue
        ocr = read_label(
            reader,
            image,
            prediction["bbox"],
            args.ocr_padding,
            args.ocr_engine,
            args.ocr_preprocess,
            args.ocr_scale,
            args.ocr_allowlist,
        )
        label_candidate = build_label_candidate(gold.class_key, ocr)
        evaluated_label_text = label_text_for_match(ocr, label_candidate, args.label_source)
        match = label_match(gold.label, evaluated_label_text)
        visual_status = "label_ok" if match["correct"] else "label_wrong"
        if match["status"] == "not_evaluable":
            visual_status = "label_not_evaluable"
        evaluations.append(
            {
                "ref": gold.ref,
                "class_key": gold.class_key,
                "expected_label": gold.label,
                "gold_bbox": gold.bbox,
                "detected": True,
                "prediction": prediction,
                "ocr": ocr,
                "label_candidate": label_candidate,
                "evaluated_label_text": evaluated_label_text,
                "label_match": match,
                "visual_status": visual_status,
            }
        )

    counts = Counter(item["visual_status"] for item in evaluations)
    class_summary: dict[str, dict[str, Any]] = {}
    for class_name in sorted(target_classes):
        class_items = [item for item in evaluations if item["class_key"] == class_name]
        class_counts = Counter(item["visual_status"] for item in class_items)
        class_detected = sum(1 for item in class_items if item["detected"])
        class_fp = sum(1 for item in false_positives if str(item.get("class_name")) == class_name)
        class_label_evaluable = sum(
            1
            for item in class_items
            if item["detected"] and item.get("label_match", {}).get("status") != "not_evaluable"
        )
        class_summary[class_name] = {
            "gold_count": len(class_items),
            "detected_count": class_detected,
            "missed": class_counts["missed"],
            "label_ok": class_counts["label_ok"],
            "label_wrong": class_counts["label_wrong"],
            "label_not_evaluable": class_counts["label_not_evaluable"],
            "false_positive_count": class_fp,
            "detection_recall": round(class_detected / max(1, len(class_items)), 4),
            "detection_precision": round(class_detected / max(1, class_detected + class_fp), 4),
            "label_accuracy_on_detected": round(class_counts["label_ok"] / max(1, class_label_evaluable), 4)
            if class_label_evaluable
            else None,
        }
    detected = sum(1 for item in evaluations if item["detected"])
    label_evaluable = sum(1 for item in evaluations if item.get("label_match", {}).get("status") != "not_evaluable" and item["detected"])
    label_ok = counts["label_ok"]
    page_summary = {
        "sheet_no": project["sheet_no"],
        "source_file": project.get("source_file"),
        "gold_count": len(gold_symbols),
        "detected_count": detected,
        "missed": counts["missed"],
        "label_ok": label_ok,
        "label_wrong": counts["label_wrong"],
        "label_not_evaluable": counts["label_not_evaluable"],
        "false_positive_count": len(false_positives),
        "detection_recall": round(detected / max(1, len(gold_symbols)), 4),
        "detection_precision": round(detected / max(1, detected + len(false_positives)), 4),
        "label_accuracy_on_detected": round(label_ok / max(1, label_evaluable), 4) if label_evaluable else None,
        "class_summary": class_summary,
        "model_class_names": model_names,
    }
    overlay = draw_evaluation_overlay(image, evaluations, page_summary)
    overlay_path = page_dir / "label_ocr_evaluation.png"
    overlay.save(overlay_path)
    payload = {
        "schema_version": "todensekkei.yolo_label_ocr_eval.page.v1",
        "summary": page_summary,
        "target_classes": sorted(target_classes),
        "settings": {
            "imgsz": args.imgsz,
            "conf": args.conf,
            "yolo_iou": args.yolo_iou,
            "match_iou": args.match_iou,
            "match_min_overlap": args.match_min_overlap,
            "ocr_padding": args.ocr_padding,
            "ocr_engine": args.ocr_engine,
            "ocr_lang": args.ocr_lang,
            "ocr_preprocess": args.ocr_preprocess,
            "ocr_scale": args.ocr_scale,
            "ocr_allowlist": args.ocr_allowlist,
            "label_source": args.label_source,
        },
        "evaluations": evaluations,
        "false_positive_predictions": false_positives,
        "outputs": {
            "overlay": str(overlay_path),
        },
    }
    write_json(page_dir / "label_ocr_evaluation.json", payload)
    return {
        **page_summary,
        "outputs": {
            "overlay": str(overlay_path),
            "json": str(page_dir / "label_ocr_evaluation.json"),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate YOLO symbol detection plus OCR label reading against seq annotation labels.")
    parser.add_argument("--annotation-zip", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--target-classes", nargs="+", default=list(DEFAULT_TARGET_CLASSES))
    parser.add_argument("--sheets", nargs="*", default=[])
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--conf", type=float, default=0.10)
    parser.add_argument("--yolo-iou", type=float, default=0.45)
    parser.add_argument("--max-det", type=int, default=400)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--dedupe-iou", type=float, default=0.25)
    parser.add_argument("--dedupe-min-overlap", type=float, default=0.65)
    parser.add_argument("--match-iou", type=float, default=0.25)
    parser.add_argument("--match-min-overlap", type=float, default=0.65)
    parser.add_argument("--ocr-engine", choices=("easyocr", "rapidocr"), default="easyocr")
    parser.add_argument("--ocr-lang", default="ja")
    parser.add_argument("--ocr-padding", type=int, default=8)
    parser.add_argument("--ocr-preprocess", choices=("adaptive", "gray", "otsu", "line_remove", "line_remove_wide"), default="adaptive")
    parser.add_argument("--ocr-scale", type=int, default=4)
    parser.add_argument("--ocr-allowlist", default=OCR_ALLOWLIST)
    parser.add_argument("--label-source", choices=("raw", "structured", "structured_match"), default="raw")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target_classes = set(args.target_classes)
    bundle, _csv_rows, _netlist = load_zip_payload(args.annotation_zip)
    images = load_zip_images(args.annotation_zip)
    projects = [
        project
        for project in bundle.get("projects", [])
        if (not args.sheets or str(project["sheet_no"]) in set(args.sheets))
    ]
    missing_images = [project["sheet_no"] for project in projects if project["sheet_no"] not in images]
    if missing_images:
        raise SystemExit(f"missing images for sheets: {missing_images}")

    if args.ocr_engine == "easyocr":
        import easyocr

        reader = easyocr.Reader([args.ocr_lang], gpu=False, verbose=False)
    else:
        from rapidocr_onnxruntime import RapidOCR

        reader = RapidOCR()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    page_summaries = [
        process_project(
            project=project,
            image=images[project["sheet_no"]],
            model_path=args.model,
            out_dir=args.out_dir,
            reader=reader,
            target_classes=target_classes,
            args=args,
        )
        for project in projects
    ]
    totals = Counter()
    class_totals: dict[str, Counter] = {class_name: Counter() for class_name in sorted(target_classes)}
    for page in page_summaries:
        totals["gold_count"] += page["gold_count"]
        totals["detected_count"] += page["detected_count"]
        totals["missed"] += page["missed"]
        totals["label_ok"] += page["label_ok"]
        totals["label_wrong"] += page["label_wrong"]
        totals["label_not_evaluable"] += page["label_not_evaluable"]
        totals["false_positive_count"] += page["false_positive_count"]
        for class_name, item in page["class_summary"].items():
            for key in (
                "gold_count",
                "detected_count",
                "missed",
                "label_ok",
                "label_wrong",
                "label_not_evaluable",
                "false_positive_count",
            ):
                class_totals[class_name][key] += item[key]
    label_evaluable = totals["detected_count"] - totals["label_not_evaluable"]
    aggregate_classes = {}
    for class_name, counts in class_totals.items():
        class_label_evaluable = counts["detected_count"] - counts["label_not_evaluable"]
        aggregate_classes[class_name] = {
            **dict(counts),
            "detection_recall": round(counts["detected_count"] / max(1, counts["gold_count"]), 4),
            "detection_precision": round(
                counts["detected_count"] / max(1, counts["detected_count"] + counts["false_positive_count"]),
                4,
            ),
            "label_accuracy_on_detected": round(counts["label_ok"] / max(1, class_label_evaluable), 4)
            if class_label_evaluable
            else None,
        }
    summary = {
        "schema_version": "todensekkei.yolo_label_ocr_eval.summary.v1",
        "annotation_zip": str(args.annotation_zip),
        "model": str(args.model),
        "target_classes": sorted(target_classes),
        "aggregate": {
            **dict(totals),
            "detection_recall": round(totals["detected_count"] / max(1, totals["gold_count"]), 4),
            "detection_precision": round(
                totals["detected_count"] / max(1, totals["detected_count"] + totals["false_positive_count"]),
                4,
            ),
            "label_accuracy_on_detected": round(totals["label_ok"] / max(1, label_evaluable), 4)
            if label_evaluable
            else None,
            "classes": aggregate_classes,
        },
        "pages": page_summaries,
    }
    write_json(args.out_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
