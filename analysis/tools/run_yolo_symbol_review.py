from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from build_e2e_demo_outputs import load_font, write_json
from prepare_yolo_dataset import suppress_text_like_components


FONT_SM = load_font(15)
FONT_MD = load_font(18)


def parse_pages(values: list[str]) -> list[int]:
    pages: set[int] = set()
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                start, end = (int(item) for item in part.split("-", 1))
                if end < start:
                    raise ValueError(f"invalid page range: {part}")
                pages.update(range(start, end + 1))
            else:
                pages.add(int(part))
    return sorted(pages)


def render_pdf_page(pdf_path: Path, page_number: int, dpi: int) -> Image.Image:
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf  # type: ignore[no-redef]

    with pymupdf.open(pdf_path) as doc:
        if page_number < 1 or page_number > doc.page_count:
            raise ValueError(f"page {page_number} is outside PDF page range 1-{doc.page_count}")
        page = doc.load_page(page_number - 1)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def box_center(box: dict[str, int | float]) -> tuple[float, float]:
    return ((float(box["x0"]) + float(box["x1"])) / 2, (float(box["y0"]) + float(box["y1"])) / 2)


def box_area(box: dict[str, int | float]) -> float:
    return max(0.0, float(box["x1"]) - float(box["x0"])) * max(0.0, float(box["y1"]) - float(box["y0"]))


def intersection_area(a: dict[str, int | float], b: dict[str, int | float]) -> float:
    x0 = max(float(a["x0"]), float(b["x0"]))
    y0 = max(float(a["y0"]), float(b["y0"]))
    x1 = min(float(a["x1"]), float(b["x1"]))
    y1 = min(float(a["y1"]), float(b["y1"]))
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def should_treat_as_overlap(
    a: dict[str, int | float],
    b: dict[str, int | float],
    *,
    iou_threshold: float,
    min_overlap_threshold: float,
) -> bool:
    inter = intersection_area(a, b)
    if inter <= 0:
        return False
    area_a = box_area(a)
    area_b = box_area(b)
    union = max(1.0, area_a + area_b - inter)
    iou = inter / union
    min_overlap = inter / max(1.0, min(area_a, area_b))
    return iou >= iou_threshold or min_overlap >= min_overlap_threshold


def dedupe_overlapping_symbols(
    symbols: list[dict[str, Any]],
    *,
    iou_threshold: float,
    min_overlap_threshold: float,
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for symbol in sorted(symbols, key=lambda item: float(item.get("confidence", 0)), reverse=True):
        if any(
            should_treat_as_overlap(
                symbol["bbox"],
                kept_symbol["bbox"],
                iou_threshold=iou_threshold,
                min_overlap_threshold=min_overlap_threshold,
            )
            for kept_symbol in kept
        ):
            continue
        kept.append(symbol)
    return sorted(kept, key=lambda item: item["id"])


def run_yolo_prediction(
    model_path: Path,
    image_path: Path,
    *,
    imgsz: int,
    conf: float,
    iou: float,
    max_det: int,
    device: str,
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    from ultralytics import YOLO

    model = YOLO(str(model_path))
    result = model.predict(
        source=str(image_path),
        imgsz=imgsz,
        conf=conf,
        iou=iou,
        max_det=max_det,
        device=device,
        verbose=False,
    )[0]
    names = {int(key): str(value) for key, value in result.names.items()}
    symbols: list[dict[str, Any]] = []
    if result.boxes is None:
        return symbols, names

    for index, box in enumerate(result.boxes, start=1):
        xyxy = box.xyxy[0].tolist()
        class_id = int(box.cls[0].item())
        confidence = float(box.conf[0].item())
        bbox = {
            "x0": round(float(xyxy[0])),
            "y0": round(float(xyxy[1])),
            "x1": round(float(xyxy[2])),
            "y1": round(float(xyxy[3])),
        }
        center = box_center(bbox)
        class_name = names.get(class_id, str(class_id))
        symbols.append(
            {
                "id": f"symbol_{index:03d}",
                "type": class_name,
                "class_name": class_name,
                "class_id": class_id,
                "bbox": bbox,
                "center": [round(center[0], 1), round(center[1], 1)],
                "confidence": round(confidence, 4),
                "status": "detected_by_yolo",
            }
        )
    return symbols, names


def make_yolo_input(
    image: Image.Image,
    out_dir: Path,
    *,
    text_suppression: str,
    text_suppression_scope: str,
) -> tuple[Path, dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    if text_suppression == "none":
        image_path = out_dir / "_yolo_input.png"
        image.save(image_path)
        return image_path, {"text_suppression": "none", "text_suppression_scope": text_suppression_scope}

    processed, stats = suppress_text_like_components(
        image,
        [],
        mode=text_suppression,
        scope=text_suppression_scope,
    )
    image_path = out_dir / "_yolo_input.png"
    processed.save(image_path)
    return image_path, {"text_suppression": text_suppression, "text_suppression_scope": text_suppression_scope, **stats}


def resize_panel(image: Image.Image, target_w: int) -> Image.Image:
    scale = target_w / image.width
    resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
    return image.resize((target_w, round(image.height * scale)), resample)


def draw_yolo_overlay(source: Image.Image, symbols: list[dict[str, Any]]) -> Image.Image:
    canvas = source.convert("RGB").copy()
    draw = ImageDraw.Draw(canvas)
    scale = max(1.0, source.width / 1800)
    line_width = max(4, round(4 * scale))
    font = load_font(max(15, round(15 * scale)))
    palette = {
        "relay_coil": (220, 38, 38),
        "contact_a": (37, 99, 235),
        "contact_b": (5, 150, 105),
        "solenoid": (217, 119, 6),
        "terminal": (8, 145, 178),
        "junction": (124, 58, 237),
        "connector": (234, 88, 12),
    }
    fallback = (124, 58, 237)
    for symbol in symbols:
        box = symbol["bbox"]
        class_name = str(symbol.get("class_name", "symbol"))
        color = palette.get(class_name, fallback)
        x0, y0, x1, y1 = int(box["x0"]), int(box["y0"]), int(box["x1"]), int(box["y1"])
        draw.rectangle((x0, y0, x1, y1), outline=color, width=line_width)
        label = f"{class_name} {float(symbol.get('confidence', 0)):.2f}"
        label_y = max(0, y0 - round(22 * scale))
        text_box = draw.textbbox((x0, label_y), label, font=font)
        pad = max(2, round(2 * scale))
        draw.rectangle(
            (text_box[0] - pad, text_box[1] - pad, text_box[2] + pad, text_box[3] + pad),
            fill=color,
        )
        draw.text((x0, label_y), label, fill="white", font=font)
    return canvas


def make_review_sheet(
    source: Image.Image,
    all_symbols: list[dict[str, Any]],
    deduped_symbols: list[dict[str, Any]],
    *,
    target_w: int,
) -> Image.Image:
    panels = [
        ("original", source.convert("RGB")),
        ("all YOLO bboxes", draw_yolo_overlay(source, all_symbols)),
        ("deduped bboxes: highest score wins", draw_yolo_overlay(source, deduped_symbols)),
    ]
    header_h = 42
    rendered = []
    for title, image in panels:
        resized = resize_panel(image, target_w)
        panel = Image.new("RGB", (target_w, resized.height + header_h), "white")
        panel.paste(resized, (0, header_h))
        draw = ImageDraw.Draw(panel)
        draw.text((12, 10), title, fill=(25, 25, 25), font=FONT_MD)
        rendered.append(panel)

    sheet_h = max(panel.height for panel in rendered)
    sheet = Image.new("RGB", (target_w * len(rendered), sheet_h), (245, 245, 245))
    for index, panel in enumerate(rendered):
        sheet.paste(panel, (index * target_w, 0))
    return sheet


def class_counts(symbols: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(symbol["class_name"]) for symbol in symbols).items()))


def process_page(args: argparse.Namespace, page_number: int) -> dict[str, Any]:
    page_dir = args.out_dir / f"page_{page_number:03d}"
    page_dir.mkdir(parents=True, exist_ok=True)
    source = render_pdf_page(args.pdf, page_number, args.dpi)
    yolo_input, preprocess_info = make_yolo_input(
        source,
        page_dir,
        text_suppression=args.text_suppression,
        text_suppression_scope=args.text_suppression_scope,
    )
    all_symbols, model_names = run_yolo_prediction(
        args.model,
        yolo_input,
        imgsz=args.imgsz,
        conf=args.conf,
        iou=args.iou,
        max_det=args.max_det,
        device=args.device,
    )
    yolo_input.unlink(missing_ok=True)
    deduped_symbols = dedupe_overlapping_symbols(
        all_symbols,
        iou_threshold=args.dedupe_iou,
        min_overlap_threshold=args.dedupe_min_overlap,
    )
    review_path = page_dir / "review.png"
    deduped_only_path = page_dir / "deduped_only.png"
    output_path = page_dir / "final_output.json"
    make_review_sheet(
        source,
        all_symbols,
        deduped_symbols,
        target_w=args.panel_width,
    ).save(review_path)
    draw_yolo_overlay(source, deduped_symbols).save(deduped_only_path)
    payload = {
        "schema_version": "todensekkei.yolo_symbol_review.v1",
        "pdf": str(args.pdf),
        "page": page_number,
        "image_size": {"width": source.width, "height": source.height},
        "model": str(args.model),
        "model_class_names": model_names,
        "settings": {
            "dpi": args.dpi,
            "imgsz": args.imgsz,
            "conf": args.conf,
            "iou": args.iou,
            "device": args.device,
            "text_preprocessing": preprocess_info,
            "dedupe_iou": args.dedupe_iou,
            "dedupe_min_overlap": args.dedupe_min_overlap,
        },
        "quality": {
            "all_symbol_count": len(all_symbols),
            "deduped_symbol_count": len(deduped_symbols),
            "all_class_counts": class_counts(all_symbols),
            "deduped_class_counts": class_counts(deduped_symbols),
            "removed_by_dedupe": len(all_symbols) - len(deduped_symbols),
        },
        "all_symbols": all_symbols,
        "deduped_symbols": deduped_symbols,
        "outputs": {
            "review": str(review_path),
            "deduped_only": str(deduped_only_path),
            "final_json": str(output_path),
        },
    }
    write_json(output_path, payload)
    return {"page": page_number, "quality": payload["quality"], "outputs": payload["outputs"]}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render PDF pages and review YOLO symbol detections only.")
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--pages", nargs="+", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--conf", type=float, default=0.10)
    parser.add_argument("--iou", type=float, default=0.45)
    parser.add_argument("--max-det", type=int, default=300)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--dedupe-iou", type=float, default=0.25)
    parser.add_argument("--dedupe-min-overlap", type=float, default=0.65)
    parser.add_argument("--panel-width", type=int, default=900)
    parser.add_argument(
        "--text-suppression",
        choices=("none", "light", "medium", "heavy", "erase"),
        default="none",
    )
    parser.add_argument(
        "--text-suppression-scope",
        choices=("whole-image", "symbol-boxes"),
        default="whole-image",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.pdf.exists():
        raise SystemExit(f"PDF not found: {args.pdf}")
    if not args.model.exists():
        raise SystemExit(f"YOLO model not found: {args.model}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pages = parse_pages(args.pages)
    page_summaries = [process_page(args, page) for page in pages]
    summary = {
        "schema_version": "todensekkei.yolo_symbol_review.summary.v1",
        "pdf": str(args.pdf),
        "pages": pages,
        "model": str(args.model),
        "page_summaries": page_summaries,
    }
    write_json(args.out_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
