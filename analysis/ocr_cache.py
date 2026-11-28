"""OCR の結果を、読んだ画素と OCR の設定ごとにディスクへ残し、同じものをもう一度読むときは OCR を省く（DD-0056）。

解析の時間の 9 割は文字の読み取り（RapidOCR）で、配線の処理は 1 図面で 1〜2 秒にとどまる（DD-0012）。
同じ図面を解析し直すとき（モデルを入れ替えた後、全図面の解析のやり直し、表の読み直しなど）は、ページのタイルや
記号の枠の画素がまったく同じなので、前の読みをそのまま使える。キーは画素のハッシュ・形・OCR の呼び出しの
引数・モデル（版と重みのファイル）で、どれかが違えば読み直す。結果は OCR が返す値そのもの（list・float・str）を
JSON にしたもので、読み戻した値は元と同じ（float は repr で往復する）。

アプリ（main.py）が起動時に configure で置き場を決めたときだけ働く。評価のツールは configure しないので、
いつも OCR を実行する。止めるときは SEQANNO_OCR_CACHE=0、上限は SEQANNO_OCR_CACHE_MB（既定 1024 MB）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from functools import cache
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)
_directory: Path | None = None
DEFAULT_LIMIT_MB = 1024
# 上限を超えたら、古く使われたものから消して上限のこの割合まで減らす。
PRUNE_TO = 0.8


def configure(directory: Path | None) -> None:
    """置き場を決める（None か SEQANNO_OCR_CACHE=0 で使わない）。上限を超えていれば裏で古いものを消す。"""
    global _directory
    if directory is None or os.environ.get("SEQANNO_OCR_CACHE", "1") == "0":
        _directory = None
        return
    _directory = Path(directory)
    threading.Thread(target=prune, args=(_directory, limit_bytes()), name="ocr-cache-prune", daemon=True).start()


def directory() -> Path | None:
    return _directory


def limit_bytes() -> int:
    try:
        return max(0, int(os.environ.get("SEQANNO_OCR_CACHE_MB", DEFAULT_LIMIT_MB))) * 1024 * 1024
    except ValueError:
        return DEFAULT_LIMIT_MB * 1024 * 1024


def prune(root: Path, limit: int) -> int:
    """root の合計が limit を超えていれば、最後に使った時刻の古いものから消す。消した数を返す。"""
    try:
        entries = [(p.stat(), p) for p in root.glob("*/*.json")]
    except OSError:
        return 0
    total = sum(s.st_size for s, _p in entries)
    if total <= limit:
        return 0
    removed = 0
    for stat, path in sorted(entries, key=lambda e: e[0].st_mtime):
        if total <= limit * PRUNE_TO:
            break
        try:
            path.unlink()
        except OSError:
            continue
        total -= stat.st_size
        removed += 1
    return removed


@cache
def file_identity(path: str) -> str:
    """重みのファイルの中身のハッシュ（同じ名前で入れ替えたときも読み直すため）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def engine_tag(name: str, rec_model_path: str | os.PathLike[str] | None = None) -> str:
    """OCR の版と、差し替えた文字認識の重み。キーに入れる。"""
    try:
        from importlib.metadata import version

        engine_version = version("rapidocr-onnxruntime")
    except Exception:  # noqa: BLE001  版がわからなければ版なしのキーにする（同じ環境の中では同じ値）
        engine_version = "unknown"
    tag = f"{name}:rapidocr-onnxruntime=={engine_version}"
    if rec_model_path is not None:
        tag += f":rec={file_identity(str(rec_model_path))}"
    return tag


class CachedReader:
    """RapidOCR と同じ呼び方（reader(画素, use_det=..., ...) -> (結果, 時間)）で、結果をディスクに残す。"""

    def __init__(self, reader: Any, tag: str) -> None:
        self.reader = reader
        self.tag = tag

    def __getattr__(self, name: str) -> Any:
        return getattr(self.reader, name)

    def key(self, pixels: Any, kwargs: dict[str, Any]) -> str:
        import numpy as np

        array = np.ascontiguousarray(pixels)
        digest = hashlib.blake2b(digest_size=20)
        digest.update(self.tag.encode())
        digest.update(repr((array.shape, array.dtype.str, sorted(kwargs.items()))).encode())
        digest.update(array.data)
        return digest.hexdigest()

    def __call__(self, pixels: Any, **kwargs: Any) -> tuple[Any, Any]:
        root = _directory
        if root is None:
            return self.reader(pixels, **kwargs)
        key = self.key(pixels, kwargs)
        path = root / key[:2] / f"{key}.json"
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            os.utime(path)  # 消すときの順（最後に使った時刻）
            return cached["result"], cached["elapse"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        result, elapse = self.reader(pixels, **kwargs)
        _store(path, {"result": result, "elapse": elapse})
        return result, elapse


def _store(path: Path, value: dict[str, Any]) -> None:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):  # JSON にできない値（版が変わって numpy の値が混じったなど）は残さない
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    except OSError:  # 書けなくても（ディスクがいっぱいなど）読んだ結果はそのまま使う
        _logger.debug("OCR cache write failed: %s", path, exc_info=True)


def wrap(reader: Any, tag: str) -> Any:
    return reader if reader is None or isinstance(reader, CachedReader) else CachedReader(reader, tag)
