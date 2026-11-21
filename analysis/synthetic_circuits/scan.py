"""スキャンした図面に似せる劣化（線の太さ・読み取りの粗さ・ぼけ・ノイズ）。座標は動かさない。

受領図面はスキャン（TIF 由来）なので、合成図面の評価でもこの劣化を掛けた図面で配線の読み取りを測る（DD-0038）。
"""

from __future__ import annotations

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
