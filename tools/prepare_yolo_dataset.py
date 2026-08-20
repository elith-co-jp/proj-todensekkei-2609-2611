from __future__ import annotations

import argparse
import html
import io
import json
import random
import shutil
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

import cv2
import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFont, ImageOps


DEFAULT_OUT_DIR = Path("data/private/yolo/symbol_detection")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
UNSAFE_NAMES = {".DS_Store", "Thumbs.db"}


@dataclass(frozen=True)
class YoloBox:
    class_id: int
    cx: float
    cy: float
    w: float
    h: float


@dataclass(frozen=True)
class SourceImage:
    zip_path: Path
    image_member: str
    label_member: str | None
    image: Image.Image
    boxes: list[YoloBox]
    source_classes: list[str]


@dataclass(frozen=True)
class PreparedImage:
    split: str
    image_path: Path
    label_path: Path
    width: int
    height: int
    boxes: list[YoloBox]
    source: str
    augmented: bool


def safe_member_name(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        return False
    if any(part.startswith("__MACOSX") for part in path.parts):
        return False
    return path.name not in UNSAFE_NAMES and not path.name.startswith("._")


def clean_output_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


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


def normalize_member(name: str) -> str:
    return str(PurePosixPath(name))


def parse_classes(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def parse_yolo_labels(text: str) -> list[YoloBox]:
    boxes: list[YoloBox] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) != 5:
            raise ValueError(f"invalid YOLO label line {line_number}: {line!r}")
        class_id = int(parts[0])
        cx, cy, w, h = (float(value) for value in parts[1:])
        boxes.append(YoloBox(class_id, cx, cy, w, h))
    return boxes


def yolo_label_text(boxes: Iterable[YoloBox]) -> str:
    lines = [
        f"{box.class_id} {box.cx:.6f} {box.cy:.6f} {box.w:.6f} {box.h:.6f}"
        for box in boxes
        if box.w > 0 and box.h > 0
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def find_classes_member(names: set[str]) -> str | None:
    if "classes.txt" in names:
        return "classes.txt"
    candidates = [name for name in names if PurePosixPath(name).name == "classes.txt"]
    return sorted(candidates, key=len)[0] if candidates else None


def label_member_for_image(image_member: str, names: set[str]) -> str | None:
    path = PurePosixPath(image_member)
    candidates: list[str] = []
    parts = list(path.parts)
    for index, part in enumerate(parts):
        if part == "images":
            replaced = [*parts]
            replaced[index] = "labels"
            label_path = PurePosixPath(*replaced).with_suffix(".txt")
            candidates.append(str(label_path))
    candidates.append(str(path.with_suffix(".txt")))
    candidates.append(str(PurePosixPath("labels") / path.with_suffix(".txt").name))
    for candidate in candidates:
        if candidate in names:
            return candidate
    return None


def remap_boxes(boxes: Iterable[YoloBox], source_classes: list[str], global_class_to_id: dict[str, int]) -> list[YoloBox]:
    remapped: list[YoloBox] = []
    for box in boxes:
        if box.class_id < 0 or box.class_id >= len(source_classes):
            raise ValueError(f"class_id {box.class_id} is outside classes.txt range")
        class_name = source_classes[box.class_id]
        remapped.append(YoloBox(global_class_to_id[class_name], box.cx, box.cy, box.w, box.h))
    return remapped


def read_export_zip(path: Path, global_class_to_id: dict[str, int]) -> list[SourceImage]:
    with zipfile.ZipFile(path) as zf:
        names = {normalize_member(info.filename) for info in zf.infolist() if safe_member_name(info.filename)}
        classes_member = find_classes_member(names)
        if classes_member is None:
            raise ValueError(f"{path} does not contain classes.txt")
        source_classes = parse_classes(zf.read(classes_member).decode("utf-8-sig"))
        if not source_classes:
            raise ValueError(f"{path} has empty classes.txt")
        for class_name in source_classes:
            global_class_to_id.setdefault(class_name, len(global_class_to_id))

        images: list[SourceImage] = []
        for name in sorted(names):
            suffix = PurePosixPath(name).suffix.lower()
            if suffix not in IMAGE_EXTENSIONS:
                continue
            if "/images/" not in f"/{name}":
                continue
            label_member = label_member_for_image(name, names)
            label_text = zf.read(label_member).decode("utf-8-sig") if label_member else ""
            boxes = remap_boxes(parse_yolo_labels(label_text), source_classes, global_class_to_id)
            with zf.open(name) as raw:
                image = Image.open(io.BytesIO(raw.read())).convert("RGB")
            images.append(SourceImage(path, name, label_member, image, boxes, source_classes))
    return images


def box_to_corners(box: YoloBox, width: int, height: int) -> np.ndarray:
    cx = box.cx * width
    cy = box.cy * height
    bw = box.w * width
    bh = box.h * height
    x0 = cx - bw / 2
    y0 = cy - bh / 2
    x1 = cx + bw / 2
    y1 = cy + bh / 2
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float32)


def corners_to_box(class_id: int, corners: np.ndarray, width: int, height: int) -> YoloBox | None:
    xs = np.clip(corners[:, 0], 0, width - 1)
    ys = np.clip(corners[:, 1], 0, height - 1)
    x0, x1 = float(xs.min()), float(xs.max())
    y0, y1 = float(ys.min()), float(ys.max())
    bw = x1 - x0
    bh = y1 - y0
    if bw < 3 or bh < 3:
        return None
    return YoloBox(
        class_id=class_id,
        cx=((x0 + x1) / 2) / width,
        cy=((y0 + y1) / 2) / height,
        w=bw / width,
        h=bh / height,
    )


def transform_boxes(boxes: Iterable[YoloBox], matrix: np.ndarray, width: int, height: int) -> list[YoloBox]:
    transformed: list[YoloBox] = []
    affine = np.vstack([matrix, np.array([0.0, 0.0, 1.0], dtype=np.float32)])
    for box in boxes:
        corners = box_to_corners(box, width, height)
        homogeneous = np.concatenate([corners, np.ones((4, 1), dtype=np.float32)], axis=1)
        mapped = (affine @ homogeneous.T).T[:, :2]
        next_box = corners_to_box(box.class_id, mapped, width, height)
        if next_box is not None:
            transformed.append(next_box)
    return transformed


def random_affine_matrix(width: int, height: int, rng: random.Random, strength: str) -> np.ndarray:
    if strength == "heavy":
        angle = rng.uniform(-2.2, 2.2)
        scale = rng.uniform(0.94, 1.06)
        tx = rng.uniform(-0.025, 0.025) * width
        ty = rng.uniform(-0.025, 0.025) * height
    elif strength == "medium":
        angle = rng.uniform(-1.3, 1.3)
        scale = rng.uniform(0.97, 1.03)
        tx = rng.uniform(-0.015, 0.015) * width
        ty = rng.uniform(-0.015, 0.015) * height
    else:
        angle = rng.uniform(-0.6, 0.6)
        scale = rng.uniform(0.985, 1.015)
        tx = rng.uniform(-0.008, 0.008) * width
        ty = rng.uniform(-0.008, 0.008) * height

    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, scale).astype(np.float32)
    matrix[0, 2] += tx
    matrix[1, 2] += ty
    return matrix


def vary_line_weight(gray: np.ndarray, rng: random.Random, strength: str) -> np.ndarray:
    if strength == "light":
        choices = ("none", "none", "dilate", "erode")
    else:
        choices = ("none", "dilate", "dilate", "erode")
    op = rng.choice(choices)
    if op == "none":
        return gray
    kernel_size = 2 if strength != "heavy" else rng.choice((2, 3))
    kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
    ink = 255 - gray
    if op == "dilate":
        adjusted = cv2.dilate(ink, kernel, iterations=1)
    else:
        adjusted = cv2.erode(ink, kernel, iterations=1)
    return 255 - adjusted


def augment_image(
    image: Image.Image,
    boxes: list[YoloBox],
    rng: random.Random,
    strength: str,
) -> tuple[Image.Image, list[YoloBox]]:
    width, height = image.size
    matrix = random_affine_matrix(width, height, rng, strength)
    arr = np.array(image.convert("RGB"))
    warped = cv2.warpAffine(
        arr,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
    transformed_boxes = transform_boxes(boxes, matrix, width, height)

    gray = cv2.cvtColor(warped, cv2.COLOR_RGB2GRAY)
    gray = vary_line_weight(gray, rng, strength)
    if rng.random() < (0.45 if strength == "heavy" else 0.25):
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
    if rng.random() < (0.85 if strength == "heavy" else 0.55):
        noise_sigma = 4.5 if strength == "heavy" else 2.5
        noise = rng.normalvariate(0, noise_sigma)
        gray = np.clip(gray.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    contrast = rng.uniform(0.78, 1.22) if strength == "heavy" else rng.uniform(0.9, 1.1)
    brightness = rng.uniform(-16, 16) if strength == "heavy" else rng.uniform(-8, 8)
    gray = np.clip((gray.astype(np.float32) - 127.5) * contrast + 127.5 + brightness, 0, 255).astype(np.uint8)
    return Image.fromarray(gray, mode="L").convert("RGB"), transformed_boxes


def split_sources(sources: list[SourceImage], val_ratio: float, seed: int) -> tuple[list[SourceImage], list[SourceImage]]:
    if len(sources) <= 1 or val_ratio <= 0:
        return sources, []
    rng = random.Random(seed)
    shuffled = [*sources]
    rng.shuffle(shuffled)
    val_count = max(1, round(len(shuffled) * val_ratio))
    val_count = min(val_count, len(shuffled) - 1)
    return shuffled[val_count:], shuffled[:val_count]


def safe_stem(source: SourceImage, index: int) -> str:
    zip_stem = source.zip_path.stem.replace(" ", "_")
    image_stem = PurePosixPath(source.image_member).stem.replace(" ", "_")
    return f"{index:05d}_{zip_stem}_{image_stem}"


def save_prepared_image(
    dataset_dir: Path,
    split: str,
    stem: str,
    image: Image.Image,
    boxes: list[YoloBox],
    source: str,
    augmented: bool,
) -> PreparedImage:
    image_dir = dataset_dir / "images" / split
    label_dir = dataset_dir / "labels" / split
    image_dir.mkdir(parents=True, exist_ok=True)
    label_dir.mkdir(parents=True, exist_ok=True)
    image_path = image_dir / f"{stem}.png"
    label_path = label_dir / f"{stem}.txt"
    image.save(image_path)
    label_path.write_text(yolo_label_text(boxes), encoding="utf-8")
    return PreparedImage(split, image_path, label_path, image.width, image.height, boxes, source, augmented)


def build_data_yaml(dataset_dir: Path, class_names: list[str]) -> dict:
    return {
        "path": str(dataset_dir.resolve()),
        "train": "images/train",
        "val": "images/val",
        "nc": len(class_names),
        "names": {index: name for index, name in enumerate(class_names)},
    }


def count_boxes_by_class(prepared: Iterable[PreparedImage], class_names: list[str]) -> dict[str, int]:
    counts = Counter()
    for item in prepared:
        for box in item.boxes:
            counts[class_names[box.class_id]] += 1
    return {name: counts.get(name, 0) for name in class_names}


def draw_boxes(image: Image.Image, boxes: list[YoloBox], class_names: list[str]) -> Image.Image:
    canvas = ImageOps.contain(image.convert("RGB"), (900, 650))
    scale_x = canvas.width / image.width
    scale_y = canvas.height / image.height
    draw = ImageDraw.Draw(canvas)
    font = load_font(14)
    palette = [
        (220, 38, 38),
        (37, 99, 235),
        (5, 150, 105),
        (217, 119, 6),
        (124, 58, 237),
        (8, 145, 178),
        (190, 24, 93),
        (77, 124, 15),
    ]
    for box in boxes:
        x0 = (box.cx - box.w / 2) * image.width * scale_x
        y0 = (box.cy - box.h / 2) * image.height * scale_y
        x1 = (box.cx + box.w / 2) * image.width * scale_x
        y1 = (box.cy + box.h / 2) * image.height * scale_y
        color = palette[box.class_id % len(palette)]
        draw.rectangle((x0, y0, x1, y1), outline=color, width=3)
        text = class_names[box.class_id]
        bbox = draw.textbbox((x0, y0), text, font=font)
        draw.rectangle((bbox[0] - 2, bbox[1] - 2, bbox[2] + 2, bbox[3] + 2), fill=color)
        draw.text((x0, y0), text, fill="white", font=font)
    return canvas


def create_review_ui(
    out_dir: Path,
    prepared: list[PreparedImage],
    class_names: list[str],
    summary: dict,
    max_items: int,
) -> Path:
    review_dir = out_dir / "review"
    thumbs_dir = review_dir / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(17)
    selected = [*prepared]
    rng.shuffle(selected)
    selected = selected[:max_items]

    cards: list[str] = []
    for index, item in enumerate(selected, start=1):
        image = Image.open(item.image_path)
        preview = draw_boxes(image, item.boxes, class_names)
        thumb_path = thumbs_dir / f"{index:04d}.jpg"
        preview.save(thumb_path, quality=90)
        rel_thumb = thumb_path.relative_to(review_dir)
        class_counter = Counter(class_names[box.class_id] for box in item.boxes)
        tags = " ".join(
            f"<span>{html.escape(name)}: {count}</span>" for name, count in sorted(class_counter.items())
        )
        cards.append(
            f"""
            <article class="card">
              <img src="{html.escape(str(rel_thumb))}" alt="{html.escape(item.image_path.name)}">
              <div class="meta">
                <strong>{html.escape(item.image_path.name)}</strong>
                <span>{html.escape(item.split)} / {'augmented' if item.augmented else 'original'}</span>
              </div>
              <div class="tags">{tags}</div>
            </article>
            """
        )

    train_counts = summary["splits"]["train"]["class_box_counts"]
    val_counts = summary["splits"]["val"]["class_box_counts"]
    rows = []
    for name in class_names:
        rows.append(
            "<tr>"
            f"<td>{html.escape(name)}</td>"
            f"<td>{train_counts.get(name, 0)}</td>"
            f"<td>{val_counts.get(name, 0)}</td>"
            f"<td>{summary['original_class_box_counts'].get(name, 0)}</td>"
            "</tr>"
        )

    train_command = html.escape(summary["train_command"])
    html_text = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>YOLO Dataset Review</title>
  <style>
    body {{ margin: 0; font-family: Inter, "Noto Sans JP", system-ui, sans-serif; color: #172033; background: #f6f7f9; }}
    header {{ padding: 24px 32px; background: #101827; color: white; }}
    h1 {{ margin: 0 0 8px; font-size: 24px; }}
    main {{ padding: 24px 32px 48px; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin-bottom: 24px; }}
    .metric, .panel, .card {{ background: white; border: 1px solid #dce1e8; border-radius: 8px; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04); }}
    .metric {{ padding: 16px; }}
    .metric span {{ display: block; color: #64748b; font-size: 12px; }}
    .metric strong {{ display: block; font-size: 28px; margin-top: 4px; }}
    .panel {{ padding: 18px; margin-bottom: 24px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ padding: 8px 10px; border-bottom: 1px solid #e5e9f0; text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
    code, pre {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    pre {{ white-space: pre-wrap; background: #0f172a; color: #dbeafe; padding: 14px; border-radius: 6px; overflow: auto; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }}
    .card img {{ width: 100%; display: block; border-bottom: 1px solid #e5e9f0; background: #fff; }}
    .meta {{ display: flex; justify-content: space-between; gap: 12px; padding: 10px 12px; font-size: 12px; color: #64748b; }}
    .meta strong {{ color: #172033; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .tags {{ display: flex; flex-wrap: wrap; gap: 6px; padding: 0 12px 12px; }}
    .tags span {{ background: #eef2ff; color: #3730a3; border-radius: 999px; padding: 3px 8px; font-size: 11px; }}
    @media (max-width: 900px) {{ .grid {{ grid-template-columns: repeat(2, 1fr); }} main, header {{ padding-left: 16px; padding-right: 16px; }} }}
  </style>
</head>
<body>
  <header>
    <h1>YOLO Dataset Review</h1>
    <div>Local-only generated UI. Do not upload review assets when source drawings are confidential.</div>
  </header>
  <main>
    <section class="grid">
      <div class="metric"><span>Original images</span><strong>{summary['source_image_count']}</strong></div>
      <div class="metric"><span>Train images</span><strong>{summary['splits']['train']['image_count']}</strong></div>
      <div class="metric"><span>Val images</span><strong>{summary['splits']['val']['image_count']}</strong></div>
      <div class="metric"><span>Classes</span><strong>{len(class_names)}</strong></div>
    </section>

    <section class="panel">
      <h2>Class Counts</h2>
      <table>
        <thead><tr><th>symbol</th><th>train boxes</th><th>val boxes</th><th>original boxes</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </section>

    <section class="panel">
      <h2>From-scratch training command</h2>
      <pre>{train_command}</pre>
    </section>

    <section class="cards">
      {''.join(cards)}
    </section>
  </main>
</body>
</html>
"""
    index_path = review_dir / "index.html"
    index_path.write_text(html_text, encoding="utf-8")
    return index_path


def prepare_dataset(args: argparse.Namespace) -> dict:
    out_dir: Path = args.out_dir
    dataset_dir = out_dir / "dataset"
    clean_output_dir(out_dir)

    global_class_to_id: dict[str, int] = {}
    sources: list[SourceImage] = []
    for zip_path in args.exports:
        sources.extend(read_export_zip(zip_path, global_class_to_id))
    if not sources:
        raise ValueError("no images were found in annotation export ZIP")

    class_names = [name for name, _index in sorted(global_class_to_id.items(), key=lambda item: item[1])]
    original_class_counts = Counter()
    for source in sources:
        for box in source.boxes:
            original_class_counts[class_names[box.class_id]] += 1

    train_sources, val_sources = split_sources(sources, args.val_ratio, args.seed)
    rng = random.Random(args.seed)
    prepared: list[PreparedImage] = []

    for index, source in enumerate(train_sources, start=1):
        stem = safe_stem(source, index)
        prepared.append(
            save_prepared_image(
                dataset_dir,
                "train",
                stem,
                source.image,
                source.boxes,
                f"{source.zip_path.name}:{source.image_member}",
                augmented=False,
            )
        )
        for copy_index in range(1, args.augment_copies + 1):
            augmented_image, augmented_boxes = augment_image(source.image, source.boxes, rng, args.augment_strength)
            prepared.append(
                save_prepared_image(
                    dataset_dir,
                    "train",
                    f"{stem}_aug{copy_index:03d}",
                    augmented_image,
                    augmented_boxes,
                    f"{source.zip_path.name}:{source.image_member}",
                    augmented=True,
                )
            )

    for index, source in enumerate(val_sources, start=1):
        stem = safe_stem(source, index)
        prepared.append(
            save_prepared_image(
                dataset_dir,
                "val",
                stem,
                source.image,
                source.boxes,
                f"{source.zip_path.name}:{source.image_member}",
                augmented=False,
            )
        )

    data_yaml = build_data_yaml(dataset_dir, class_names)
    data_yaml_path = out_dir / "data.yaml"
    data_yaml_path.write_text(yaml.safe_dump(data_yaml, sort_keys=False, allow_unicode=True), encoding="utf-8")
    (out_dir / "classes.txt").write_text("\n".join(class_names) + "\n", encoding="utf-8")

    train_prepared = [item for item in prepared if item.split == "train"]
    val_prepared = [item for item in prepared if item.split == "val"]
    train_command = (
        "yolo detect train "
        f"data={data_yaml_path.resolve()} "
        "model=yolo11n.yaml pretrained=False "
        f"epochs={args.epochs} imgsz={args.imgsz} batch={args.batch} "
        f"project={(out_dir / 'runs').resolve()} name=from_scratch"
    )
    summary = {
        "schema_version": "todensekkei.yolo_dataset.v1",
        "source_exports": [str(path) for path in args.exports],
        "source_image_count": len(sources),
        "source_box_count": sum(len(source.boxes) for source in sources),
        "class_names": class_names,
        "original_class_box_counts": {name: original_class_counts.get(name, 0) for name in class_names},
        "augmentation": {
            "copies_per_train_image": args.augment_copies,
            "strength": args.augment_strength,
            "seed": args.seed,
        },
        "splits": {
            "train": {
                "image_count": len(train_prepared),
                "box_count": sum(len(item.boxes) for item in train_prepared),
                "class_box_counts": count_boxes_by_class(train_prepared, class_names),
            },
            "val": {
                "image_count": len(val_prepared),
                "box_count": sum(len(item.boxes) for item in val_prepared),
                "class_box_counts": count_boxes_by_class(val_prepared, class_names),
            },
        },
        "paths": {
            "out_dir": str(out_dir),
            "dataset_dir": str(dataset_dir),
            "data_yaml": str(data_yaml_path),
            "classes": str(out_dir / "classes.txt"),
        },
        "train_command": train_command,
        "notes": [
            "No source drawing or generated dataset should be committed.",
            "The generated command uses a YOLO architecture yaml and pretrained=False.",
            "Validation images are not augmented.",
        ],
    }
    summary_path = out_dir / "dataset_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    review_path = create_review_ui(out_dir, prepared, class_names, summary, args.review_items)
    summary["paths"]["review"] = str(review_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare a local YOLO dataset from seq-annotator export ZIPs.")
    parser.add_argument("exports", nargs="+", type=Path, help="seq-annotator export ZIP path(s)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--augment-copies", type=int, default=12)
    parser.add_argument("--augment-strength", choices=("light", "medium", "heavy"), default="heavy")
    parser.add_argument("--seed", type=int, default=2609)
    parser.add_argument("--epochs", type=int, default=180)
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--review-items", type=int, default=80)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = prepare_dataset(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
