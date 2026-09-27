"""コンテンツアドレス方式（sha256 名）のファイル保管の共通部品。

画像と ML モデルで共通の「sha256 をファイル名にして冪等保存する」パターンをここに集約。
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def store_blob(directory: Path, raw: bytes, suffix: str) -> tuple[str, int]:
    """raw を <directory>/<sha256><suffix> へ保存し、(sha256, size) を返す。

    同名（=同一内容）のファイルが既にあれば再書き込みしない。
    """
    digest = hashlib.sha256(raw).hexdigest()
    path = directory / f"{digest}{suffix}"
    if not path.exists():
        path.write_bytes(raw)
    return digest, len(raw)
