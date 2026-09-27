"""Fetch the pinned Ultralytics YOLOv8n training base for Windows packaging."""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

URL = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt"
SHA256 = "f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36"
MAX_BYTES = 20 * 1024 * 1024
DEST = Path(__file__).resolve().parents[1] / ".build-assets" / "yolov8n.pt"


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    DEST.parent.mkdir(parents=True, exist_ok=True)
    if DEST.is_file() and _digest(DEST) == SHA256:
        return

    temporary = DEST.with_suffix(".download")
    temporary.unlink(missing_ok=True)
    try:
        with urllib.request.urlopen(URL, timeout=60) as source, temporary.open("wb") as output:
            total = 0
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError("Base model exceeds the expected size")
                output.write(chunk)
        if _digest(temporary) != SHA256:
            raise ValueError("Base model checksum does not match the pinned release")
        temporary.replace(DEST)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
