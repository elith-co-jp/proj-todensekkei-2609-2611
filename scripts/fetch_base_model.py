"""Fetch the pinned model files bundled into the Windows exe (checksums are verified).

- yolo11s.pt: the training base when no model is active (Ultralytics, AGPL-3.0)
- ch_PP-OCRv5_rec_mobile.onnx: text recognition used to re-read Japanese names and notes
  (PaddleOCR PP-OCRv5 converted by RapidOCR, Apache-2.0)
"""

from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple

BUILD_ASSETS = Path(__file__).resolve().parents[1] / ".build-assets"
# 配布元が一時的に 504 を返す・途中で切れることがある（2026-11-09 のビルドで 3 回）。間を空けてやり直す。
ATTEMPTS = 4
BACKOFF_SECONDS = (15, 45, 90)


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


class TransientError(Exception):
    """配布元の一時的な失敗（5xx、接続の切断、途中で切れて照合値が合わない）。"""


def fetch(asset: Asset, *, attempts: int = ATTEMPTS, sleep=time.sleep) -> None:
    """照合値の合うファイルが取れるまで、一時的な失敗なら間を空けてやり直す。"""
    for attempt in range(1, attempts + 1):
        try:
            _fetch_once(asset)
            return
        except TransientError as error:
            if attempt == attempts:
                raise RuntimeError(f"{asset.name}: {attempts} 回取得に失敗しました（最後: {error}）") from error
            wait = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
            print(f"{asset.name}: 取得に失敗（{error}）。{wait} 秒後にやり直します（{attempt}/{attempts}）", flush=True)
            sleep(wait)


def _fetch_once(asset: Asset) -> None:
    dest = BUILD_ASSETS / asset.name
    if dest.is_file() and _digest(dest) == asset.sha256:
        return
    temporary = dest.with_suffix(".download")
    temporary.unlink(missing_ok=True)
    try:
        try:
            with urllib.request.urlopen(asset.url, timeout=60) as source, temporary.open("wb") as output:
                total = 0
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > asset.max_bytes:
                        raise ValueError(f"{asset.name} exceeds the expected size")
                    output.write(chunk)
        except urllib.error.HTTPError as error:
            if error.code >= 500:
                raise TransientError(f"HTTP {error.code}") from error
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            raise TransientError(str(error)) from error
        if _digest(temporary) != asset.sha256:
            # 途中で切れたファイルも照合値が合わない。やり直して合わなければ最後に止める。
            raise TransientError(f"{asset.name} checksum does not match the pinned release")
        temporary.replace(dest)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    BUILD_ASSETS.mkdir(parents=True, exist_ok=True)
    for asset in ASSETS:
        fetch(asset)


if __name__ == "__main__":
    main()

