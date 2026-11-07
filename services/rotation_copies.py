"""横書き（90° 回して描いた）図面とシンボルを読むための、学習用の回転コピー（#42）。

図面単位の交差検証（DD-0007 §4.6、§4.11）で、回した図面を学習していない検出器は 90° 回した図面で F1 0.04、
90° のコピーだけを足すと 90° は 0.97 でも 270° は 0.77 だった。両方向のコピーを学習に足す。
"""

from __future__ import annotations

import io

from PIL import Image

ROTATED_COPY_DEGREES = (90, 270)
_TRANSPOSE = {90: Image.Transpose.ROTATE_90, 270: Image.Transpose.ROTATE_270}


def rotate_box(cx: float, cy: float, w: float, h: float, degrees: int) -> tuple[float, float, float, float]:
    """0〜1 の枠（中心と幅・高さ）を、画像を反時計回りに degrees（90 か 270）回したときの枠に直す。"""
    x, y = (cy, 1 - cx) if degrees == 90 else (1 - cy, cx)
    return x, y, h, w


def rotate_label_text(label_text: str, degrees: int) -> str:
    """YOLO 形式（cls cx cy w h、0〜1）のラベルを、画像を反時計回りに degrees 回したときの位置に直す。"""
    lines = []
    for line in label_text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        x, y, w, h = rotate_box(*(float(v) for v in parts[1:5]), degrees)
        lines.append(f"{parts[0]} {x:.6f} {y:.6f} {w:.6f} {h:.6f}")
    return "\n".join(lines) + "\n" if lines else ""


def rotate_image(png: bytes, degrees: int) -> bytes:
    with Image.open(io.BytesIO(png)) as image:
        out = io.BytesIO()
        image.transpose(_TRANSPOSE[degrees]).save(out, format="PNG")
    return out.getvalue()


def rotated_copy(png: bytes, label_text: str, degrees: int) -> tuple[bytes, str]:
    """図面を反時計回りに degrees（90 か 270）回した PNG と、それに合わせたラベルを返す。"""
    return rotate_image(png, degrees), rotate_label_text(label_text, degrees)
