"""Create scan-style training copies of drawings that contain a chosen symbol class."""

from __future__ import annotations

from pathlib import Path

import cv2

from analysis.synthetic_circuits.scan import scan_variant


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
