"""Create scan-style training copies of drawings that contain a chosen symbol class."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def scan_variant(image: np.ndarray, seed: int) -> np.ndarray:
    """Vary line width and scanner sampling without moving annotation coordinates."""
    rng = np.random.default_rng(seed)
    height, width = image.shape
    ink = 255 - image
    operation = cv2.MORPH_DILATE if seed % 2 else cv2.MORPH_ERODE
    ink = cv2.morphologyEx(ink, operation, np.ones((2, 2), np.uint8))
    scale = float(rng.uniform(.78, .93))
    small = cv2.resize(ink, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    ink = cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)
    ink = cv2.GaussianBlur(ink, (3, 3), float(rng.uniform(.15, .55)))
    noise = rng.normal(0, rng.uniform(1.5, 4), (height, width))
    return np.uint8(np.clip(255 - ink + noise, 0, 255))


def add_scan_copies(images: Path, labels: Path, class_index: int, copies: int, *, seed: int = 2609) -> int:
    """Add copies from training images only; labels stay identical because geometry stays fixed."""
    if copies < 0:
        raise ValueError("copies must be nonnegative")
    originals = sorted(p for p in images.glob("*.png") if not p.stem.endswith(("_r90", "_r270"))
                       and "_scan" not in p.stem)
    made = 0
    for index, image_path in enumerate(originals):
        label_path = labels / f"{image_path.stem}.txt"
        label_text = label_path.read_text(encoding="utf-8")
        if not any(int(line.split()[0]) == class_index for line in label_text.splitlines() if line.strip()):
            continue
        gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            raise ValueError(f"cannot read training image: {image_path}")
        for copy in range(copies):
            stem = f"{image_path.stem}_scan{copy}"
            target_image = images / f"{stem}.png"
            target_label = labels / f"{stem}.txt"
            if not cv2.imwrite(str(target_image), scan_variant(gray, seed + index + copy * 997)):
                raise OSError(f"cannot write synthetic image: {target_image}")
            target_label.write_text(label_text, encoding="utf-8")
            made += 1
    return made
