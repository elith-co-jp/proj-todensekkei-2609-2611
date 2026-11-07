"""Fetch the pinned model files bundled into the Windows exe (checksums are verified).

- yolo11s.pt: the training base when no model is active (Ultralytics, AGPL-3.0)
- ch_PP-OCRv5_rec_mobile.onnx: text recognition used to re-read Japanese names and notes
  (PaddleOCR PP-OCRv5 converted by RapidOCR, Apache-2.0)
"""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path
from typing import NamedTuple

BUILD_ASSETS = Path(__file__).resolve().parents[1] / ".build-assets"


class Asset(NamedTuple):
    url: str
    sha256: str
    max_bytes: int
    name: str


ASSETS = (
    Asset("https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11s.pt",
          "85a76fe86dd8afe384648546b56a7a78580c7cb7b404fc595f97969322d502d5", 32 * 1024 * 1024, "yolo11s.pt"),
    Asset("https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/master/onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile.onnx",
          "5825fc7ebf84ae7a412be049820b4d86d77620f204a041697b0494669b1742c5", 32 * 1024 * 1024, "ch_PP-OCRv5_rec_mobile.onnx"),
)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(asset: Asset) -> None:
    dest = BUILD_ASSETS / asset.name
    if dest.is_file() and _digest(dest) == asset.sha256:
        return
    temporary = dest.with_suffix(".download")
    temporary.unlink(missing_ok=True)
    try:
        with urllib.request.urlopen(asset.url, timeout=60) as source, temporary.open("wb") as output:
            total = 0
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > asset.max_bytes:
                    raise ValueError(f"{asset.name} exceeds the expected size")
                output.write(chunk)
        if _digest(temporary) != asset.sha256:
            raise ValueError(f"{asset.name} checksum does not match the pinned release")
        temporary.replace(dest)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    BUILD_ASSETS.mkdir(parents=True, exist_ok=True)
    for asset in ASSETS:
        fetch(asset)


if __name__ == "__main__":
    main()

