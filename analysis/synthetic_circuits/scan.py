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


# 強い劣化（scan_hard_variant、DD-0054）の大きさ。受領図面のスキャンに見られる傷み方を、座標をほとんど動かさずにまねる。
HARD_SKEW_DEG = 0.3          # 傾きの最大（度）。1755 px の母線の両端で約 9 px ずれる。
HARD_FADE_MIN = 0.45         # かすれ: インクの濃さに掛ける割合の最小（ゆっくり変わる面で掛ける）。
HARD_DROPOUTS_PER_MPX = 60   # 線の途切れ（インクの上の白い小円）の数（100 万画素あたり）。
HARD_DROPOUT_RADIUS = (1, 3)  # 途切れの半径（px）。
HARD_SPECKS_PER_MPX = 400    # ごみ（黒い点）の数（100 万画素あたり）。
HARD_BACKGROUND = (232, 250)  # 紙の地の明るさ（黄ばみ・裏写りで白くない）。


def scan_hard_variant(image: np.ndarray, seed: int) -> np.ndarray:
    """scan_variant に、傾き・かすれ・線の途切れ・ごみ・紙の地の暗さを足した強い劣化（DD-0054）。"""
    rng = np.random.default_rng(seed + 7_000_000)
    height, width = image.shape
    ink = 255 - scan_variant(image, seed).astype(np.float32)
    # かすれ: 粗い格子の乱数を拡大した、ゆっくり変わる濃さの面。
    grid = rng.uniform(HARD_FADE_MIN, 1.0, (4, 6)).astype(np.float32)
    ink *= cv2.resize(grid, (width, height), interpolation=cv2.INTER_CUBIC).clip(HARD_FADE_MIN, 1.0)
    # 線の途切れ: インクのある画素を選んで白い小円で消す。
    ys, xs = np.nonzero(ink > 128)
    area = height * width / 1e6
    if len(ys):
        for i in rng.choice(len(ys), size=min(len(ys), int(HARD_DROPOUTS_PER_MPX * area)), replace=False):
            cv2.circle(ink, (int(xs[i]), int(ys[i])), int(rng.integers(*HARD_DROPOUT_RADIUS, endpoint=True)), 0, -1)
    # ごみ: 小さな黒い点。
    for _ in range(int(HARD_SPECKS_PER_MPX * area)):
        cv2.circle(ink, (int(rng.integers(width)), int(rng.integers(height))), int(rng.integers(0, 2)),
                   float(rng.uniform(80, 220)), -1)
    paper = float(rng.uniform(*HARD_BACKGROUND))
    gray = np.clip(paper - ink * (paper / 255.0), 0, 255)
    # 傾き: 画像の中心で回す。
    angle = float(rng.uniform(-HARD_SKEW_DEG, HARD_SKEW_DEG))
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    gray = cv2.warpAffine(gray, matrix, (width, height), flags=cv2.INTER_LINEAR, borderValue=paper)
    return np.uint8(np.clip(gray + rng.normal(0, 3, gray.shape), 0, 255))
