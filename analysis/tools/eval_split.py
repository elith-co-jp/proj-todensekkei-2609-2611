"""調整用（tune）と報告用（report）の図面の分け方（DD-0026）。

規則を調整用の図面で選び、報告用の図面で確かめる。正解の図面を足しても、前からある図面の側が変わらないように、
最初の 30 枚の側は eval_split.json に固定し、あとから足した図面はその中だけで図番の順に交互に分ける
（最初の 1 枚が調整用）。前からある図面の値は、足す前と後でそのまま比べられる。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

SPLIT_FILE = Path(__file__).with_name("eval_split.json")


def tune_sheets(sheets: Iterable[str], split_file: Path = SPLIT_FILE) -> set[str]:
    """sheets のうち調整用の図面。残りが報告用。"""
    fixed = json.loads(split_file.read_text(encoding="utf-8"))
    known_tune, known = set(fixed["tune"]), set(fixed["tune"]) | set(fixed["report"])
    sheets = set(sheets)
    added = sorted(sheets - known)
    return (sheets & known_tune) | set(added[0::2])
