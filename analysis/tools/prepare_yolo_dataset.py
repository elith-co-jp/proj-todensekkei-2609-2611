from __future__ import annotations

import argparse
import html
import io
import json
import random
import re
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
    source_index: int


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
        image_members = [
            name
            for name in sorted(names)
            if PurePosixPath(name).suffix.lower() in IMAGE_EXTENSIONS and "/images/" in f"/{name}"
        ]
        for source_index, name in enumerate(image_members, start=1):
            label_member = label_member_for_image(name, names)
            label_text = zf.read(label_member).decode("utf-8-sig") if label_member else ""
            boxes = remap_boxes(parse_yolo_labels(label_text), source_classes, global_class_to_id)
            with zf.open(name) as raw:
                image = Image.open(io.BytesIO(raw.read())).convert("RGB")
            images.append(SourceImage(path, name, label_member, image, boxes, source_classes, source_index))
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


def parse_page_numbers(values: Iterable[str]) -> set[int]:
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
    return pages


def source_page_number(source: SourceImage) -> int | None:
    match = re.search(r"page_(\d+)", PurePosixPath(source.image_member).stem)
    return int(match.group(1)) if match else None


def split_sources_by_page_numbers(
    sources: list[SourceImage],
    train_pages: set[int],
    val_pages: set[int],
    test_pages: set[int],
) -> tuple[list[SourceImage], list[SourceImage], list[SourceImage], dict[str, list[int]]]:
    overlap = (train_pages & val_pages) | (train_pages & test_pages) | (val_pages & test_pages)
    if overlap:
        raise ValueError(f"page split groups overlap: {sorted(overlap)}")

    seen_pages = {page for source in sources if (page := source_page_number(source)) is not None}
    requested_pages = train_pages | val_pages | test_pages
    missing_pages = sorted(requested_pages - seen_pages)
    if missing_pages:
        raise ValueError(f"requested page(s) are not in source export: {missing_pages}")

    train_sources: list[SourceImage] = []
    val_sources: list[SourceImage] = []
    test_sources: list[SourceImage] = []
    for source in sources:
        page = source_page_number(source)
        if page in val_pages:
            val_sources.append(source)
        elif page in test_pages:
            test_sources.append(source)
        elif not train_pages or page in train_pages:
            train_sources.append(source)

    if not train_sources:
        raise ValueError("page split produced no train sources")
    if not val_sources:
        raise ValueError("page split produced no val sources")

    return train_sources, val_sources, test_sources, {
        "train": sorted({page for source in train_sources if (page := source_page_number(source)) is not None}),
        "val": sorted({page for source in val_sources if (page := source_page_number(source)) is not None}),
        "test": sorted({page for source in test_sources if (page := source_page_number(source)) is not None}),
    }


def parse_source_indices(values: Iterable[str]) -> set[int]:
    return parse_page_numbers(values)


def split_sources_by_source_indices(
    sources: list[SourceImage],
    train_indices: set[int],
    val_indices: set[int],
    test_indices: set[int],
) -> tuple[list[SourceImage], list[SourceImage], list[SourceImage], dict[str, list[int]]]:
    overlap = (train_indices & val_indices) | (train_indices & test_indices) | (val_indices & test_indices)
    if overlap:
        raise ValueError(f"source-index split groups overlap: {sorted(overlap)}")

    seen_indices = {source.source_index for source in sources}
    requested_indices = train_indices | val_indices | test_indices
    missing_indices = sorted(requested_indices - seen_indices)
    if missing_indices:
        raise ValueError(f"requested source index(es) are not in source export: {missing_indices}")

    train_sources: list[SourceImage] = []
    val_sources: list[SourceImage] = []
    test_sources: list[SourceImage] = []
    for source in sources:
        if source.source_index in val_indices:
            val_sources.append(source)
        elif source.source_index in test_indices:
            test_sources.append(source)
        elif not train_indices or source.source_index in train_indices:
            train_sources.append(source)

    if not train_sources:
        raise ValueError("source-index split produced no train sources")
    if not val_sources:
        raise ValueError("source-index split produced no val sources")

    return train_sources, val_sources, test_sources, {
        "train": sorted(source.source_index for source in train_sources),
        "val": sorted(source.source_index for source in val_sources),
        "test": sorted(source.source_index for source in test_sources),
    }


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
    data = {
        "path": str(dataset_dir.resolve()),
        "train": "images/train",
        "val": "images/val",
        "nc": len(class_names),
        "names": {index: name for index, name in enumerate(class_names)},
    }
    if (dataset_dir / "images" / "test").exists():
        data["test"] = "images/test"
    return data


def count_boxes_by_class(prepared: Iterable[PreparedImage], class_names: list[str]) -> dict[str, int]:
    counts = Counter()
    for item in prepared:
        for box in item.boxes:
            counts[class_names[box.class_id]] += 1
    return {name: counts.get(name, 0) for name in class_names}


def parse_class_targets(values: Iterable[str], class_names: list[str]) -> dict[str, int]:
    class_name_set = set(class_names)
    targets: dict[str, int] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"class target must use name=count format: {value!r}")
        name, count_text = value.split("=", 1)
        name = name.strip()
        if name not in class_name_set:
            raise ValueError(f"unknown class in --focus-class-targets: {name!r}")
        count = int(count_text)
        if count < 0:
            raise ValueError(f"class target must be non-negative: {value!r}")
        targets[name] = count
    return targets


def drop_empty_classes(sources: list[SourceImage], class_names: list[str]) -> tuple[list[SourceImage], list[str]]:
    used_class_ids = sorted({box.class_id for source in sources for box in source.boxes})
    if len(used_class_ids) == len(class_names):
        return sources, class_names

    old_to_new = {old_id: new_id for new_id, old_id in enumerate(used_class_ids)}
    next_class_names = [class_names[old_id] for old_id in used_class_ids]
    next_sources: list[SourceImage] = []
    for source in sources:
        next_boxes = [
            YoloBox(old_to_new[box.class_id], box.cx, box.cy, box.w, box.h)
            for box in source.boxes
            if box.class_id in old_to_new
        ]
        next_sources.append(
            SourceImage(
                source.zip_path,
                source.image_member,
                source.label_member,
                source.image,
                next_boxes,
                next_class_names,
                source.source_index,
            )
        )
    return next_sources, next_class_names


def filter_classes(
    sources: list[SourceImage],
    class_names: list[str],
    include_classes: Iterable[str],
    exclude_classes: Iterable[str],
) -> tuple[list[SourceImage], list[str], list[str]]:
    include_set = set(include_classes)
    exclude_set = set(exclude_classes)
    class_name_set = set(class_names)
    unknown = sorted((include_set | exclude_set) - class_name_set)
    if unknown:
        raise ValueError(f"unknown class in class filter: {', '.join(unknown)}")

    keep_names = [
        class_name
        for class_name in class_names
        if (not include_set or class_name in include_set) and class_name not in exclude_set
    ]
    if not keep_names:
        raise ValueError("class filter removed all classes")

    old_to_new = {
        old_id: keep_names.index(class_name)
        for old_id, class_name in enumerate(class_names)
        if class_name in keep_names
    }
    next_sources: list[SourceImage] = []
    for source in sources:
        next_boxes = [
            YoloBox(old_to_new[box.class_id], box.cx, box.cy, box.w, box.h)
            for box in source.boxes
            if box.class_id in old_to_new
        ]
        next_sources.append(
            SourceImage(
                source.zip_path,
                source.image_member,
                source.label_member,
                source.image,
                next_boxes,
                keep_names,
                source.source_index,
            )
        )
    dropped = [class_name for class_name in class_names if class_name not in set(keep_names)]
    return next_sources, keep_names, dropped


def _component_is_text_like(width: int, height: int, area: int) -> bool:
    if width < 2 or height < 6 or width > 180 or height > 52:
        return False
    if area < 8:
        return False
    if width >= 46 and height <= 5:
        return False
    if height >= 46 and width <= 5:
        return False
    aspect = width / max(1, height)
    if aspect < 0.07 or aspect > 13.0:
        return False
    fill_ratio = area / max(1, width * height)
    return 0.03 <= fill_ratio <= 0.78


def _box_pixel_bounds(box: YoloBox, image_width: int, image_height: int, padding: int = 0) -> tuple[int, int, int, int]:
    corners = box_to_corners(box, image_width, image_height)
    x0 = max(0, int(np.floor(corners[:, 0].min())) - padding)
    y0 = max(0, int(np.floor(corners[:, 1].min())) - padding)
    x1 = min(image_width, int(np.ceil(corners[:, 0].max())) + padding)
    y1 = min(image_height, int(np.ceil(corners[:, 1].max())) + padding)
    return x0, y0, x1, y1


def _component_in_symbol_scope(
    left: int,
    top: int,
    width: int,
    height: int,
    boxes: list[YoloBox],
    image_width: int,
    image_height: int,
) -> bool:
    cx = left + width / 2
    cy = top + height / 2
    for box in boxes:
        x0, y0, x1, y1 = _box_pixel_bounds(box, image_width, image_height, padding=8)
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            return True
    return False


def text_like_component_mask(
    image: Image.Image,
    boxes: list[YoloBox],
    scope: str,
) -> np.ndarray:
    gray = np.array(image.convert("L"))
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    _threshold, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
    mask = np.zeros_like(binary)
    image_height, image_width = binary.shape
    for component_id in range(1, count):
        left = int(stats[component_id, cv2.CC_STAT_LEFT])
        top = int(stats[component_id, cv2.CC_STAT_TOP])
        width = int(stats[component_id, cv2.CC_STAT_WIDTH])
        height = int(stats[component_id, cv2.CC_STAT_HEIGHT])
        area = int(stats[component_id, cv2.CC_STAT_AREA])
        if not _component_is_text_like(width, height, area):
            continue
        if scope == "symbol-boxes" and not _component_in_symbol_scope(
            left,
            top,
            width,
            height,
            boxes,
            image_width,
            image_height,
        ):
            continue
        mask[labels == component_id] = 255
    kernel = np.ones((2, 2), dtype=np.uint8)
    return cv2.dilate(mask, kernel, iterations=1)


def suppress_text_like_components(
    image: Image.Image,
    boxes: list[YoloBox],
    *,
    mode: str,
    scope: str,
) -> tuple[Image.Image, dict[str, float | int | str]]:
    if mode == "none":
        return image.convert("RGB"), {"mode": mode, "scope": scope, "masked_pixel_ratio": 0.0, "masked_pixels": 0}

    mask = text_like_component_mask(image, boxes, scope)
    arr = np.array(image.convert("RGB"))
    weights = {
        "light": 0.70,
        "medium": 0.45,
        "heavy": 0.20,
        "erase": 0.0,
    }
    keep_weight = weights[mode]
    selected = mask > 0
    if np.any(selected):
        arr[selected] = np.clip(arr[selected].astype(np.float32) * keep_weight + 255.0 * (1.0 - keep_weight), 0, 255)
    masked_pixels = int(np.count_nonzero(selected))
    total_pixels = int(mask.shape[0] * mask.shape[1])
    stats = {
        "mode": mode,
        "scope": scope,
        "masked_pixels": masked_pixels,
        "masked_pixel_ratio": round(masked_pixels / max(1, total_pixels), 6),
    }
    return Image.fromarray(arr.astype(np.uint8), mode="RGB"), stats


def crop_boxes_for_window(
    boxes: Iterable[YoloBox],
    left: float,
    top: float,
    crop_width: float,
    crop_height: float,
    image_width: int,
    image_height: int,
) -> list[YoloBox]:
    cropped: list[YoloBox] = []
    for box in boxes:
        corners = box_to_corners(box, image_width, image_height)
        x0, x1 = float(corners[:, 0].min()), float(corners[:, 0].max())
        y0, y1 = float(corners[:, 1].min()), float(corners[:, 1].max())
        ix0 = max(x0, left)
        iy0 = max(y0, top)
        ix1 = min(x1, left + crop_width)
        iy1 = min(y1, top + crop_height)
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        original_area = max(1.0, (x1 - x0) * (y1 - y0))
        visible_ratio = ((ix1 - ix0) * (iy1 - iy0)) / original_area
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        center_inside = left <= cx <= left + crop_width and top <= cy <= top + crop_height
        if not center_inside and visible_ratio < 0.7:
            continue
        next_box = YoloBox(
            class_id=box.class_id,
            cx=(((ix0 + ix1) / 2) - left) / crop_width,
            cy=(((iy0 + iy1) / 2) - top) / crop_height,
            w=(ix1 - ix0) / crop_width,
            h=(iy1 - iy0) / crop_height,
        )
        if next_box.w > 0 and next_box.h > 0:
            cropped.append(next_box)
    return cropped


def focused_crop(
    source: SourceImage,
    focus_box: YoloBox,
    rng: random.Random,
    crop_size: int,
    jitter: float,
) -> tuple[Image.Image, list[YoloBox]]:
    image_width, image_height = source.image.size
    side = min(float(crop_size), float(image_width), float(image_height))
    corners = box_to_corners(focus_box, image_width, image_height)
    focus_cx = float(corners[:, 0].mean())
    focus_cy = float(corners[:, 1].mean())
    focus_cx += rng.uniform(-jitter, jitter) * side
    focus_cy += rng.uniform(-jitter, jitter) * side
    left = min(max(0.0, focus_cx - side / 2), max(0.0, image_width - side))
    top = min(max(0.0, focus_cy - side / 2), max(0.0, image_height - side))
    right = left + side
    bottom = top + side
    crop = source.image.crop((round(left), round(top), round(right), round(bottom)))
    crop_boxes = crop_boxes_for_window(source.boxes, left, top, side, side, image_width, image_height)
    return crop, crop_boxes


def random_negative_crop(
    source: SourceImage,
    rng: random.Random,
    crop_size: int,
    attempts: int = 120,
) -> Image.Image | None:
    image_width, image_height = source.image.size
    side = min(float(crop_size), float(image_width), float(image_height))
    if side < 16:
        return None
    max_left = max(0.0, image_width - side)
    max_top = max(0.0, image_height - side)
    for _attempt in range(attempts):
        left = rng.uniform(0, max_left) if max_left > 0 else 0.0
        top = rng.uniform(0, max_top) if max_top > 0 else 0.0
        if crop_boxes_for_window(source.boxes, left, top, side, side, image_width, image_height):
            continue
        right = left + side
        bottom = top + side
        return source.image.crop((round(left), round(top), round(right), round(bottom)))
    return None


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
    filtered_classes: list[str] = []
    if args.include_classes or args.exclude_classes:
        sources, class_names, filtered_classes = filter_classes(
            sources,
            class_names,
            args.include_classes,
            args.exclude_classes,
        )
    dropped_empty_classes: list[str] = []
    if args.drop_empty_classes:
        before_class_names = [*class_names]
        sources, class_names = drop_empty_classes(sources, class_names)
        dropped_empty_classes = [name for name in before_class_names if name not in set(class_names)]

    text_suppression_stats: list[dict[str, float | int | str]] = []
    if args.text_suppression != "none":
        suppressed_sources: list[SourceImage] = []
        for source in sources:
            suppressed_image, stats = suppress_text_like_components(
                source.image,
                source.boxes,
                mode=args.text_suppression,
                scope=args.text_suppression_scope,
            )
            text_suppression_stats.append(
                {
                    **stats,
                    "image_member": source.image_member,
                }
            )
            suppressed_sources.append(
                SourceImage(
                    source.zip_path,
                    source.image_member,
                    source.label_member,
                    suppressed_image,
                    source.boxes,
                    source.source_classes,
                    source.source_index,
                )
            )
        sources = suppressed_sources
    original_class_counts = Counter()
    for source in sources:
        for box in source.boxes:
            original_class_counts[class_names[box.class_id]] += 1

    page_split = {"train": [], "val": [], "test": []}
    source_index_split = {"train": [], "val": [], "test": []}
    if args.train_source_indices or args.val_source_indices or args.test_source_indices:
        train_sources, val_sources, test_sources, source_index_split = split_sources_by_source_indices(
            sources,
            parse_source_indices(args.train_source_indices),
            parse_source_indices(args.val_source_indices),
            parse_source_indices(args.test_source_indices),
        )
    elif args.train_page_numbers or args.val_page_numbers or args.test_page_numbers:
        train_sources, val_sources, test_sources, page_split = split_sources_by_page_numbers(
            sources,
            parse_page_numbers(args.train_page_numbers),
            parse_page_numbers(args.val_page_numbers),
            parse_page_numbers(args.test_page_numbers),
        )
    else:
        train_sources, val_sources = split_sources(sources, args.val_ratio, args.seed)
        test_sources = []
    single_source_validation_fallback = len(sources) == 1 and not val_sources and args.augment_copies > 0
    if single_source_validation_fallback:
        train_sources = sources
        val_sources = sources
    rng = random.Random(args.seed)
    prepared: list[PreparedImage] = []
    train_class_counts = Counter()
    for source in train_sources:
        for box in source.boxes:
            train_class_counts[box.class_id] += 1
    focus_classes = set(args.focus_classes)
    unknown_focus_classes = sorted(focus_classes - set(class_names))
    if unknown_focus_classes:
        raise ValueError(f"unknown class in --focus-classes: {', '.join(unknown_focus_classes)}")
    focus_class_targets = parse_class_targets(args.focus_class_targets, class_names)
    focus_repeats_by_class: dict[int, int] = {}
    for class_id, class_name in enumerate(class_names):
        count = train_class_counts.get(class_id, 0)
        target_boxes = focus_class_targets.get(class_name, args.balance_target_boxes)
        if focus_classes and class_name not in focus_classes:
            focus_repeats_by_class[class_id] = 0
            continue
        if target_boxes <= 0 or count <= 0 or count >= target_boxes:
            focus_repeats_by_class[class_id] = 0
            continue
        repeats = (target_boxes - count + count - 1) // count
        focus_repeats_by_class[class_id] = min(args.focus_crops_max_per_box, repeats)
    focused_crop_count = 0
    negative_crop_count = 0

    for index, source in enumerate(train_sources, start=1):
        stem = safe_stem(source, index)
        if not single_source_validation_fallback:
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
        for box_index, box in enumerate(source.boxes, start=1):
            repeats = focus_repeats_by_class.get(box.class_id, 0)
            for crop_index in range(1, repeats + 1):
                crop_image, crop_boxes = focused_crop(
                    source,
                    box,
                    rng,
                    args.focus_crop_size,
                    args.focus_crop_jitter,
                )
                if not crop_boxes:
                    continue
                augmented_crop, augmented_crop_boxes = augment_image(
                    crop_image,
                    crop_boxes,
                    rng,
                    args.augment_strength,
                )
                focused_crop_count += 1
                prepared.append(
                    save_prepared_image(
                        dataset_dir,
                        "train",
                        f"{stem}_focus{box_index:03d}_{crop_index:02d}",
                        augmented_crop,
                        augmented_crop_boxes,
                        f"{source.zip_path.name}:{source.image_member}:focus:{class_names[box.class_id]}",
                        augmented=True,
                    )
                )
        for negative_index in range(1, args.negative_crops_per_source + 1):
            negative_crop = random_negative_crop(source, rng, args.negative_crop_size)
            if negative_crop is None:
                continue
            augmented_negative, _negative_boxes = augment_image(negative_crop, [], rng, args.augment_strength)
            negative_crop_count += 1
            prepared.append(
                save_prepared_image(
                    dataset_dir,
                    "train",
                    f"{stem}_neg{negative_index:03d}",
                    augmented_negative,
                    [],
                    f"{source.zip_path.name}:{source.image_member}:negative",
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

    for index, source in enumerate(test_sources, start=1):
        stem = safe_stem(source, index)
        prepared.append(
            save_prepared_image(
                dataset_dir,
                "test",
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
    test_prepared = [item for item in prepared if item.split == "test"]
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
        "class_filtering": {
            "drop_empty_classes": args.drop_empty_classes,
            "dropped_empty_classes": dropped_empty_classes,
            "include_classes": args.include_classes,
            "exclude_classes": args.exclude_classes,
            "filtered_classes": filtered_classes,
        },
        "text_suppression": {
            "mode": args.text_suppression,
            "scope": args.text_suppression_scope,
            "image_stats": text_suppression_stats,
        },
        "class_balancing": {
            "target_boxes_per_class": args.balance_target_boxes,
            "focus_classes": sorted(focus_classes),
            "focus_class_targets": focus_class_targets,
            "focus_crop_size": args.focus_crop_size,
            "focus_crop_jitter": args.focus_crop_jitter,
            "focus_crops_max_per_box": args.focus_crops_max_per_box,
            "focused_crop_image_count": focused_crop_count,
            "focus_repeats_by_class": {
                class_names[class_id]: repeats
                for class_id, repeats in sorted(focus_repeats_by_class.items())
            },
        },
        "negative_crops": {
            "per_source": args.negative_crops_per_source,
            "crop_size": args.negative_crop_size,
            "image_count": negative_crop_count,
        },
        "single_source_validation_fallback": single_source_validation_fallback,
        "page_split": page_split,
        "source_index_split": source_index_split,
        "source_images": [
            {
                "source_index": source.source_index,
                "image_member": source.image_member,
                "label_member": source.label_member,
                "box_count": len(source.boxes),
                "class_box_counts": {
                    name: Counter(class_names[box.class_id] for box in source.boxes).get(name, 0)
                    for name in class_names
                },
            }
            for source in sources
        ],
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
            "test": {
                "image_count": len(test_prepared),
                "box_count": sum(len(item.boxes) for item in test_prepared),
                "class_box_counts": count_boxes_by_class(test_prepared, class_names),
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
            "When only one source image is available, the original image is used for validation and augmented copies are used for training.",
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
    parser.add_argument(
        "--train-page-numbers",
        nargs="*",
        default=[],
        help="explicit source page numbers for train split, e.g. 1 2 4-6",
    )
    parser.add_argument(
        "--val-page-numbers",
        nargs="*",
        default=[],
        help="explicit source page numbers for val split",
    )
    parser.add_argument(
        "--test-page-numbers",
        nargs="*",
        default=[],
        help="explicit source page numbers for test split",
    )
    parser.add_argument(
        "--train-source-indices",
        nargs="*",
        default=[],
        help="explicit source image indices for train split, based on sorted image members",
    )
    parser.add_argument(
        "--val-source-indices",
        nargs="*",
        default=[],
        help="explicit source image indices for val split, based on sorted image members",
    )
    parser.add_argument(
        "--test-source-indices",
        nargs="*",
        default=[],
        help="explicit source image indices for test split, based on sorted image members",
    )
    parser.add_argument("--augment-copies", type=int, default=12)
    parser.add_argument("--augment-strength", choices=("light", "medium", "heavy"), default="heavy")
    parser.add_argument(
        "--include-classes",
        nargs="*",
        default=[],
        help="train only these class names; other labels become background",
    )
    parser.add_argument(
        "--exclude-classes",
        nargs="*",
        default=[],
        help="remove these class names from labels so they become background",
    )
    parser.add_argument(
        "--text-suppression",
        choices=("none", "light", "medium", "heavy", "erase"),
        default="none",
        help="lighten text-like connected components before dataset export",
    )
    parser.add_argument(
        "--text-suppression-scope",
        choices=("whole-image", "symbol-boxes"),
        default="whole-image",
        help="where text-like component suppression is applied",
    )
    parser.add_argument("--drop-empty-classes", action="store_true", help="remove classes that have no boxes")
    parser.add_argument(
        "--balance-target-boxes",
        type=int,
        default=0,
        help="add focused crops for classes below this train box count",
    )
    parser.add_argument(
        "--focus-classes",
        nargs="*",
        default=[],
        help="if set, add focused crops only for these class names",
    )
    parser.add_argument(
        "--focus-class-targets",
        nargs="*",
        default=[],
        help="per-class focused crop targets, e.g. relay_coil=240 solenoid=240",
    )
    parser.add_argument(
        "--focus-crops-max-per-box",
        type=int,
        default=8,
        help="maximum focused crop copies per rare-class box",
    )
    parser.add_argument("--focus-crop-size", type=int, default=1280)
    parser.add_argument("--focus-crop-jitter", type=float, default=0.2)
    parser.add_argument("--negative-crops-per-source", type=int, default=0)
    parser.add_argument("--negative-crop-size", type=int, default=512)
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
